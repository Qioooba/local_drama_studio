"""MED-08: every registered delivery file must be downloadable by a remote browser.

The original defect: ``/delivery-packages/{id}/download`` selected the first
``%.mp4`` row, so a package built with SIDECAR/BOTH subtitles exposed the .srt and
the manifest only as inert path text.  A server-local path is not a download.  This
test drives the real HTTP surface with a real render, real subtitle sidecar and
real ZIP, and asserts the whole file set is retrievable and integrity-checked.
"""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from local_drama.application.configuration import ConfigurationService
from local_drama.application.documents import DocumentImportService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.main import create_app


def _video(workspace) -> Path:
    output = workspace.work_root / "med08-source.mp4"
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "color=c=navy:s=160x90:r=24:d=2",
            "-pix_fmt", "yuv420p", "-an", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _approve_render(client: TestClient, render: dict) -> None:
    targets = client.get(
        f"/api/v2/episodes/{render['episode_id']}/review-targets",
        params={"target_kind": "EPISODE_RENDER_VERSION", "include_resolved": True},
    )
    assert targets.status_code == 200, targets.text
    target = next(item for item in targets.json()["items"] if item["target_id"] == render["id"])
    response = client.post(
        "/api/v2/review-decisions",
        json={
            "target_kind": "EPISODE_RENDER_VERSION",
            "target_id": render["id"],
            "template_version_id": target["template_version_id"],
            "expected_revision": target["subject_revision"],
            "decision": "APPROVED",
            "checks": [{"item_id": item["id"], "result": "PASS"} for item in target["template_items"]],
            "comment": "MED-08 delivery download approval",
            "idempotency_key": f"approve-render:{render['id']}",
        },
    )
    assert response.status_code == 201, response.text


def _build_both_subtitle_delivery(workspace, database, client: TestClient):
    """End-to-end: render with a frozen SRT, approve, then build a BOTH package."""

    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="med08_delivery", title="MED-08 delivery", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=4000, allow_unconfigured_capabilities=True,
    )
    project_id = str(project["id"])
    # BOTH burns the subtitles into the render AND emits the sidecar, which is
    # exactly the situation MED-08 is about.
    with database.transaction() as connection:
        connection.execute("UPDATE projects SET subtitle_mode='BOTH', subtitle_language='zh-CN' WHERE id=?", (project_id,))
    season = projects.list_seasons(project_id)[0]
    episode = projects.list_episodes(str(season["id"]))[0]

    source = MediaService(database, workspace).import_file(
        project_id, _video(workspace), purpose="SHOT_VIDEO", media_kind="VIDEO"
    )

    service = TimelineService(database, workspace)
    script_path = workspace.work_root / "med08-script.txt"
    script_path.write_text("HELLO DELIVERY", encoding="utf-8")
    script = DocumentImportService(database, workspace).import_document(project_id, script_path)
    subtitle = service.create_subtitle_revision(
        str(episode["id"]),
        [{"start_us": 0, "end_us": 1_500_000, "text": "HELLO DELIVERY"}],
        format="SRT",
        authority={"text_authority": "SCRIPT", "source_document_version_id": script["source_document_version_id"]},
    )
    timeline = client.post(
        f"/api/v1/episodes/{episode['id']}/timeline-revisions",
        json={
            "items": [
                {
                    "track_type": "VIDEO",
                    "media_version_id": source["media_version_id"],
                    "start_us": 0,
                    "end_us": 2_000_000,
                    "parameters": {},
                }
            ],
            "input_snapshot": {"source": "med08", "subtitle_revision_id": str(subtitle["id"])},
        },
    ).json()["timeline"]

    render = client.post(f"/api/v1/timeline-revisions/{timeline['id']}:render").json()["render"]
    _approve_render(client, render)
    target = ConfigurationService(database).create_delivery_target(
        project_id, "med08", "MED-08 target", "LOCAL_FILESYSTEM",
        {"path_rel": "06_delivery/med08", "width": 320, "height": 180, "fps": 24, "bitrate": "1M",
         "audio_codec": "AAC", "subtitles": "BOTH"},
    )
    built = client.post(
        "/api/v1/delivery-packages",
        json={"episode_render_version_id": render["id"], "target_version_id": target["version_id"]},
    )
    assert built.status_code == 201, built.text
    return project, episode, built.json()["delivery"]


def test_both_subtitle_package_exposes_mp4_srt_and_manifest_downloads(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        _project, _episode, package = _build_both_subtitle_delivery(workspace, database, client)
        package_id = package["id"]

        files = client.get(f"/api/v1/delivery-packages/{package_id}/files")
        assert files.status_code == 200, files.text
        payload = files.json()
        items = payload["items"]
        suffixes = {Path(str(item["rel_path"])).suffix.lower() for item in items}
        assert {".mp4", ".srt", ".json"} <= suffixes, f"BOTH must register MP4 + SRT + manifest, got {suffixes}"

        # Every row carries a controlled download URL, so the page can link them.
        for item in items:
            assert item["download_url"] == f"/api/v1/delivery-packages/{package_id}/files/{item['id']}/download"
            assert item["download_filename"]
            assert item["media_type"]
        assert payload["archive_download_url"] == f"/api/v1/delivery-packages/{package_id}/archive:download"

        # Download each registered file and verify it against its manifest digest.
        seen: dict[str, int] = {}
        for item in items:
            response = client.get(item["download_url"])
            assert response.status_code == 200, f"{item['rel_path']}: {response.text}"
            body = response.content
            assert len(body) == int(item["byte_size"]), f"{item['rel_path']} size mismatch"
            import hashlib

            assert hashlib.sha256(body).hexdigest() == str(item["sha256"]), f"{item['rel_path']} digest mismatch"
            seen[Path(str(item["rel_path"])).suffix.lower()] = len(body)

        # The subtitle download is genuinely the frozen cue content.
        srt_item = next(item for item in items if str(item["rel_path"]).lower().endswith(".srt"))
        srt_body = client.get(srt_item["download_url"]).content.decode("utf-8")
        assert "HELLO DELIVERY" in srt_body

        # The subtitle media type is a subtitle type, not a generic octet-stream.
        assert client.get(srt_item["download_url"]).headers["content-type"].startswith("application/x-subrip")

        # The manifest names the MP4 and SRT with matching digests.
        manifest_item = next(item for item in items if str(item["rel_path"]).lower().endswith(".json"))
        manifest = json.loads(client.get(manifest_item["download_url"]).content.decode("utf-8"))
        listed = {str(entry["rel_path"]): str(entry["sha256"]) for entry in manifest["files"]}
        for item in items:
            if str(item["rel_path"]).lower().endswith(".json"):
                continue
            assert str(item["rel_path"]) in listed
            assert listed[str(item["rel_path"])] == str(item["sha256"])


def test_complete_delivery_zip_contains_the_verified_file_set(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        _project, _episode, package = _build_both_subtitle_delivery(workspace, database, client)
        package_id = package["id"]

        files = client.get(f"/api/v1/delivery-packages/{package_id}/files").json()
        archive_url = files["archive_download_url"]
        response = client.get(archive_url)
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"

        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            names = archive.namelist()
            assert names, "the delivery ZIP must not be empty"
            suffixes = {Path(name).suffix.lower() for name in names}
            assert {".mp4", ".srt", ".json"} <= suffixes
            # Members are relative to the episode directory and never absolute.
            for name in names:
                assert not name.startswith("/") and ":" not in name.split("/")[0]
            # The manifest inside the ZIP is verifiable and matches the package.
            manifest_name = next(name for name in names if name.endswith("manifest.json"))
            manifest = json.loads(archive.read(manifest_name).decode("utf-8"))
            assert manifest["manifest_sha256"]
            embedded = dict(manifest)
            embedded.pop("manifest_sha256", None)
            import hashlib

            assert hashlib.sha256(
                json.dumps(embedded, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest() == str(manifest["manifest_sha256"])
            # Every non-manifest member has a matching digest in the manifest.
            listed = {str(entry["rel_path"]): str(entry["sha256"]) for entry in manifest["files"]}
            for name in names:
                if name.endswith("manifest.json"):
                    continue
                digest = hashlib.sha256(archive.read(name)).hexdigest()
                assert any(digest == expected for expected in listed.values()) or digest in listed.values()


def test_tampered_delivery_file_is_refused_by_single_and_zip_download(workspace, database) -> None:
    """A tampered file must never be handed out as a "complete" package."""

    with TestClient(create_app(workspace)) as client:
        project, _episode, package = _build_both_subtitle_delivery(workspace, database, client)
        package_id = package["id"]
        items = client.get(f"/api/v1/delivery-packages/{package_id}/files").json()["items"]

        project_root = workspace.projects_root / str(project["root_rel"])
        srt_item = next(item for item in items if str(item["rel_path"]).lower().endswith(".srt"))

        # Corrupt exactly one registered file, preserving its length semantics.
        victim = project_root / str(srt_item["rel_path"])
        original = victim.read_bytes()
        victim.write_bytes(original + b"\nCORRUPTED\n")

        single = client.get(srt_item["download_url"])
        assert single.status_code != 200, "a tampered file must not download successfully"

        archive = client.get(f"/api/v1/delivery-packages/{package_id}/archive:download")
        assert archive.status_code != 200, "a tampered package must not produce a 'complete' ZIP"


def test_missing_delivery_file_is_refused(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        project, _episode, package = _build_both_subtitle_delivery(workspace, database, client)
        package_id = package["id"]
        items = client.get(f"/api/v1/delivery-packages/{package_id}/files").json()["items"]
        project_root = workspace.projects_root / str(project["root_rel"])

        srt_item = next(item for item in items if str(item["rel_path"]).lower().endswith(".srt"))
        (project_root / str(srt_item["rel_path"])).unlink()

        assert client.get(srt_item["download_url"]).status_code != 200
        assert client.get(f"/api/v1/delivery-packages/{package_id}/archive:download").status_code != 200


def test_unknown_file_id_is_rejected(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        _project, _episode, package = _build_both_subtitle_delivery(workspace, database, client)
        response = client.get(f"/api/v1/delivery-packages/{package['id']}/files/not-a-real-file/download")
        assert response.status_code != 200
        assert client.get(f"/api/v1/delivery-packages/{package['id']}/files/../../etc/passwd/download").status_code in {400, 404}
