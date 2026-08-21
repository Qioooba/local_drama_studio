from __future__ import annotations

import json
import subprocess
from pathlib import Path
from time import perf_counter

import pytest
from fastapi.testclient import TestClient

from local_drama.api.routes.media import _stream
from local_drama.application.configuration import ConfigurationService
from local_drama.application.documents import DocumentImportService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.projects import ProjectService
from local_drama.application.read_models import ProductionReadModelService, SearchService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database, code: str = "g3-project") -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="G3 project",
        episode_count=3,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60000,
        allow_unconfigured_capabilities=True,
    )


def _real_video(path: Path, workspace) -> Path:
    output = workspace.work_root / path.name
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "color=c=blue:s=160x90:d=1", "-pix_fmt", "yuv420p", "-an", "-y", str(output)],
        check=True,
        capture_output=True,
    )
    return output


def test_manifest_sync_keeps_h3_as_unpublished_candidate(workspace, database) -> None:
    synced = ProfileService(database, workspace.manifest_path).sync_manifest()
    assert synced["manifest"]["read_only_inventory"] is True
    assert synced["runtime"]["status"] in {"AVAILABLE", "BLOCKED_OFFLINE"}
    assert synced["profiles"]
    assert all(item["published"] is False for item in synced["profiles"])
    assert all(item["status"] != "PUBLISHED" for item in synced["profiles"])
    listed = ProfileService(database, workspace.manifest_path).list_profiles()
    assert len(listed) >= 4
    assert all(item["worker_policy"] for item in listed)


def test_manifest_sync_never_mutates_published_profile_version(workspace, database, tmp_path) -> None:
    source = json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
    manifest_path = tmp_path / "model_manifest.json"
    manifest_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    profiles = ProfileService(database, manifest_path)
    first = profiles.sync_manifest()
    published_id = str(first["profiles"][0]["version_id"])
    with database.transaction() as connection:
        connection.execute(
            "UPDATE execution_profile_versions SET status='PUBLISHED', input_contract_json=?, revision=revision+1 WHERE id=?",
            (json.dumps({"input_slots": {"FIRST_FRAME": {"min": 1, "max": 1}}}), published_id),
        )
    profiles.sync_manifest()
    with database.connect() as connection:
        unchanged = connection.execute(
            "SELECT status, input_contract_json, model_bundle_json, version_no FROM execution_profile_versions WHERE id=?", (published_id,)
        ).fetchone()
        assert unchanged["status"] == "PUBLISHED"
        assert json.loads(unchanged["input_contract_json"])["input_slots"]["FIRST_FRAME"]["min"] == 1
        assert unchanged["version_no"] == 1
        original_artifact_ids = json.loads(unchanged["model_bundle_json"])["artifact_ids"]

    source["manifest_version"] = f"{source['manifest_version']}-changed"
    manifest_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    changed = profiles.sync_manifest()
    next_version = next(item for item in changed["profiles"] if item["id"] == first["profiles"][0]["id"])
    assert next_version["version_no"] == 2
    assert next_version["version_id"] != published_id
    assert next_version["status"] in {"CANDIDATE_BLOCKED", "CANDIDATE_UNVERIFIED"}
    with database.connect() as connection:
        historical = connection.execute(
            "SELECT status, model_bundle_json FROM execution_profile_versions WHERE id=?", (published_id,)
        ).fetchone()
        candidate = connection.execute(
            "SELECT model_bundle_json FROM execution_profile_versions WHERE id=?", (next_version["version_id"],)
        ).fetchone()
    assert historical["status"] == "PUBLISHED"
    assert json.loads(historical["model_bundle_json"])["artifact_ids"] == original_artifact_ids
    assert set(json.loads(historical["model_bundle_json"])["artifact_ids"]).isdisjoint(json.loads(candidate["model_bundle_json"])["artifact_ids"])


def test_explicit_plan_delivery_and_profile_binding_blocker(workspace, database) -> None:
    project = _project(workspace, database, "g3_config")
    project_id = str(project["id"])
    configuration = ConfigurationService(database)
    plan = configuration.create_plan_binding(project_id, "g3-plan", "G3 Plan", {"aspect_ratio": "16:9", "fps": "24/1"})
    target = configuration.create_delivery_target(project_id, "g3-local", "G3 local delivery", "LOCAL_FILESYSTEM", {"path_rel": "06_delivery/g3"})
    assert plan["production_plan_version_id"]
    assert target["status"] == "ACTIVE"
    assert configuration.blockers(project_id) == ["PROFILE_NOT_BOUND"]
    ProfileService(database, workspace.manifest_path).sync_manifest()
    profile = ProfileService(database, workspace.manifest_path).list_profiles()[0]
    binding = configuration.bind_profile(project_id, str(profile["capability"]), str(profile["version_id"]), confirm_candidate=True)
    assert binding["status"] == "SELECTED_CANDIDATE"
    assert configuration.blockers(project_id) == []


def test_real_media_probe_range_thumbnail_and_production_read_model(workspace, database) -> None:
    project = _project(workspace, database, "g3_media")
    project_id = str(project["id"])
    source = _real_video(Path("g3.mp4"), workspace)
    media = MediaService(database, workspace).import_file(project_id, source)
    assert media["probe"]["probe_status"] == "PASS"
    assert media["sha256"]
    project_service = ProjectService(database, workspace.projects_root)
    episode = project_service.list_episodes(str(project_service.list_seasons(project_id)[0]["id"]))[0]
    shot = project_service.create_shot(str(episode["id"]), "S001", 1000)
    production = ProductionReadModelService(database).episode(str(episode["id"]))
    assert production["items"][0]["blockers"]
    assert production["request_shape"] == "bounded_cursor_read_model"
    assert production["page"]["cursor"] == 0
    page = ProductionReadModelService(database).episode(str(episode["id"]), limit=1, cursor=1)
    assert page["page"]["cursor"] == 1
    with TestClient(create_app(workspace)) as client:
        paged_production = client.get(f"/api/v1/episodes/{episode['id']}/production", params={"cursor": 0, "limit": 1})
        assert paged_production.status_code == 200
        assert paged_production.json()["page"]["cursor"] == 0
        assert paged_production.json()["page"]["limit"] == 1
        version_id = str(media["media_version_id"])
        ranged = client.get(f"/api/v1/media-versions/{version_id}/content", headers={"Range": "bytes=0-15"})
        assert ranged.status_code == 206
        assert len(ranged.content) == 16
        assert ranged.headers["content-range"].startswith("bytes 0-15/")
        suffix = client.get(f"/api/v1/media-versions/{version_id}/content", headers={"Range": "bytes=-16"})
        assert suffix.status_code == 206
        assert len(suffix.content) == 16
        assert suffix.headers["content-range"] == f"bytes {int(media['byte_size']) - 16}-{int(media['byte_size']) - 1}/{media['byte_size']}"
        matching_if_range = client.get(
            f"/api/v1/media-versions/{version_id}/content",
            headers={"Range": "bytes=0-15", "If-Range": f'"{media["sha256"]}"'},
        )
        assert matching_if_range.status_code == 206
        date_if_range = client.get(
            f"/api/v1/media-versions/{version_id}/content",
            headers={"Range": "bytes=0-15", "If-Range": ranged.headers["last-modified"]},
        )
        assert date_if_range.status_code == 206
        stale_if_range = client.get(
            f"/api/v1/media-versions/{version_id}/content",
            headers={"Range": "bytes=0-15", "If-Range": '"stale-media-version"'},
        )
        assert stale_if_range.status_code == 200
        assert len(stale_if_range.content) == int(media["byte_size"])
        assert "content-range" not in stale_if_range.headers
        head = client.head(f"/api/v1/media-versions/{version_id}/content")
        assert head.status_code == 200
        assert head.headers["accept-ranges"] == "bytes"
        assert head.headers["etag"] == f'"{media["sha256"]}"'
        invalid_range = client.get(f"/api/v1/media-versions/{version_id}/content", headers={"Range": "bytes=999999999-"})
        assert invalid_range.status_code == 416
        started = perf_counter()
        poster = client.get(f"/api/v1/media-versions/{version_id}/thumbnail?frame=first")
        first_frame_elapsed_ms = (perf_counter() - started) * 1000
        assert poster.status_code == 200
        assert poster.headers["content-type"].startswith("image/webp")
        # This is a local smoke target for a tiny proxy fixture, not a Windows
        # benchmark or a release-level performance claim.
        assert first_frame_elapsed_ms < 2000
        filmstrip = client.get(f"/api/v1/media-versions/{version_id}/filmstrip")
        assert filmstrip.status_code == 200
        assert filmstrip.headers["content-type"].startswith("image/webp")
    audio_source = workspace.work_root / "g3_audio.wav"
    subprocess.run(
        [workspace.ffmpeg_path, "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-y", str(audio_source)],
        check=True,
        capture_output=True,
    )
    audio = MediaService(database, workspace).import_file(project_id, audio_source)
    with TestClient(create_app(workspace)) as client:
        waveform = client.get(f"/api/v1/media-versions/{audio['media_version_id']}/waveform")
        assert waveform.status_code == 200
        assert waveform.headers["content-type"].startswith("image/png")
    assert shot["code"] == "S001"


def test_media_range_stream_is_bounded_and_registered_path_cannot_escape(workspace, database, tmp_path) -> None:
    payload = bytes((offset % 251 for offset in range(2 * 1024 * 1024 + 17)))
    path = tmp_path / "large-proxy.mp4"
    path.write_bytes(payload)
    chunks = list(_stream(path, 37, len(payload) - 11))
    streamed = b"".join(chunks)
    assert streamed == payload[37:-10]
    assert chunks
    assert max(len(chunk) for chunk in chunks) <= 1024 * 1024

    project = _project(workspace, database, "g3_range_path_safety")
    source = _real_video(Path("g3-path-safe.mp4"), workspace)
    media = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="VIDEO")
    with database.transaction() as connection:
        connection.execute("UPDATE media_versions SET rel_path=? WHERE id=?", ("../../outside.mp4", media["media_version_id"]))
    with TestClient(create_app(workspace)) as client:
        escaped = client.get(f"/api/v1/media-versions/{media['media_version_id']}/content")
    assert escaped.status_code == 422
    assert escaped.json()["error"]["code"] == "MEDIA_FILE_MISSING"


def test_media_integrity_detection_is_read_only_and_repair_is_explicit(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database, "g3_integrity_repair")
    source = _real_video(Path("g3-integrity-repair.mp4"), workspace)
    media_service = MediaService(database, workspace)
    media = media_service.import_file(str(project["id"]), source, media_kind="VIDEO")
    media_version_id = str(media["media_version_id"])
    registered = workspace.projects_root / str(project["root_rel"]) / str(media["rel_path"])
    original = registered.read_bytes()

    registered.unlink()
    with TestClient(create_app(workspace)) as client:
        missing = client.get(f"/api/v1/projects/{project['id']}/health")
    assert missing.status_code == 200
    assert missing.json()["blockers"] == ["MEDIA_MISSING"]
    assert missing.json()["mutated"] is False
    with database.connect() as connection:
        assert connection.execute(
            "SELECT integrity_status FROM media_versions WHERE id=?", (media_version_id,),
        ).fetchone()["integrity_status"] == "VERIFIED"

    registered.write_bytes(b"tampered-but-not-adopted")
    with TestClient(create_app(workspace)) as client:
        tampered = client.get(f"/api/v1/projects/{project['id']}/health")
    assert "MEDIA_SIZE_MISMATCH" in tampered.json()["blockers"]
    with pytest.raises(DomainRuleError) as failed:
        media_service.verify_content_integrity(media_version_id)
    assert failed.value.code == "SOURCE_INTEGRITY_FAILED"
    corrupt = media_service.get_version(media_version_id)
    assert corrupt["integrity_status"] == "CORRUPT"
    assert corrupt["sha256"] == media["sha256"]

    registered.write_bytes(original)
    original_read_bytes = Path.read_bytes

    def reject_whole_media_read(path: Path) -> bytes:
        if path.resolve() == registered.resolve():
            raise AssertionError("project health must stream media hashes")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", reject_whole_media_read)
    with TestClient(create_app(workspace)) as client:
        restored_health = client.get(f"/api/v1/projects/{project['id']}/health")
    assert restored_health.status_code == 200
    assert restored_health.json()["status"] == "HEALTHY"
    with pytest.raises(DomainRuleError) as repair_required:
        media_service.verify_content_integrity(media_version_id)
    assert repair_required.value.code == "MEDIA_INTEGRITY_REPAIR_REQUIRED"
    assert media_service.get_version(media_version_id)["integrity_status"] == "CORRUPT"

    with TestClient(create_app(workspace)) as client:
        conflict = client.post(
            f"/api/v1/media-versions/{media_version_id}:repair-integrity",
            json={"expected_revision": int(corrupt["revision"]) - 1},
        )
        repaired = client.post(
            f"/api/v1/media-versions/{media_version_id}:repair-integrity",
            json={"expected_revision": int(corrupt["revision"])},
        )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "REVISION_CONFLICT"
    assert repaired.status_code == 200
    assert repaired.json()["media_version"]["repaired"] is True
    assert repaired.json()["media_version"]["integrity_status"] == "VERIFIED"
    assert media_service.verify_content_integrity(media_version_id)["integrity_status"] == "VERIFIED"
    with database.connect() as connection:
        event = connection.execute(
            "SELECT action,metadata_redacted_json FROM audit_events WHERE subject_id=? ORDER BY event_id DESC LIMIT 1",
            (media_version_id,),
        ).fetchone()
    assert event["action"] == "MEDIA_INTEGRITY_REPAIRED"
    assert json.loads(event["metadata_redacted_json"])["adopted_new_content"] is False


def test_video_thumbnail_frame_parameter_resolves_distinct_local_frames(workspace, database) -> None:
    project = _project(workspace, database, "g3_thumbnail_frames")
    source = workspace.work_root / "g3-thumbnail-frames.mp4"
    subprocess.run(
        [
            workspace.ffmpeg_path,
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x90:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=160x90:d=1",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=160x90:d=1",
            "-filter_complex",
            "[0:v][1:v][2:v]concat=n=3:v=1:a=0,fps=24,format=yuv420p",
            "-an",
            "-y",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    media = MediaService(database, workspace).import_file(str(project["id"]), source, media_kind="VIDEO")
    version_id = str(media["media_version_id"])
    with TestClient(create_app(workspace)) as client:
        first = client.get(f"/api/v1/media-versions/{version_id}/thumbnail?size=small&frame=first")
        poster = client.get(f"/api/v1/media-versions/{version_id}/thumbnail?size=small&frame=poster")
        middle = client.get(f"/api/v1/media-versions/{version_id}/thumbnail?size=small&frame=middle")
        last = client.get(f"/api/v1/media-versions/{version_id}/thumbnail?size=small&frame=last")
    assert all(response.status_code == 200 for response in (first, poster, middle, last))
    assert first.content == poster.content
    assert len({first.content, middle.content, last.content}) == 3

    with TestClient(create_app(workspace)) as client:
        invalid = client.get(f"/api/v1/media-versions/{version_id}/thumbnail?frame=quarter")
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "THUMBNAIL_FRAME_UNSUPPORTED"


def test_script_import_preview_search_and_no_fake_llm_result(workspace, database) -> None:
    project = _project(workspace, database, "g3_script")
    source = workspace.work_root / "episode.md"
    source.write_text("# 第一场\n\n人物走进院子。\n\n对白：你好。", encoding="utf-8")
    imported = DocumentImportService(database, workspace).import_document(str(project["id"]), source)
    assert imported["status"] == "PREVIEW_READY"
    assert imported["preview"]["paragraph_count"] == 3
    assert SearchService(database).search("人物")
    with TestClient(create_app(workspace)) as client:
        response = client.post(
            f"/api/v1/import-sessions/{imported['import_session_id']}:request-breakdown",
            json={},
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "LOCAL_LLM_PROFILE_REQUIRED"


@pytest.mark.comfyui
def test_diagnostics_persists_local_only_and_comfy_blocker(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.post("/api/v1/diagnostics/runs")
        assert response.status_code == 200
        run = response.json()["run"]
        assert run["status"] in {"HEALTHY", "DEGRADED"}
        checks = {item["code"]: item for item in run["checks"]}
        assert checks["MODE_LOCAL_ONLY"]["status"] == "PASS"
        assert checks["REMOTE_PROVIDER"]["status"] == "PASS"
        assert checks["COMFYUI_LOOPBACK"]["status"] in {"PASS", "BLOCKED"}
        latest = client.get("/api/v1/diagnostics/latest")
        assert latest.status_code == 200
        assert latest.json()["run"]["id"] == run["id"]
