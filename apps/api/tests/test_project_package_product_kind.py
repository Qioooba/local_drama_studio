"""The project package protocol must carry an EXPLAINER project (PKG-02).

Reproduced defect on the audit snapshot.  ``export()`` and ``inspect_path()`` did not
look at ``product_kind`` at all, and the package state carried the DRAMA domain only.
A real ``EXPLAINER`` project with one explainer video therefore exported and imported
"successfully":

```json
{
  "source_product_kind": "EXPLAINER",
  "copied_product_kind": "DRAMA",
  "source_explainer_videos": 1,
  "copied_explainer_videos": 0,
  "copy_status": "IMPORTED",
  "missing_fields": [],
  "state_includes_product_kind": false
}
```

The result was a DRAMA shell with no seasons and no episodes, while the explainer
video, research packet, claims, script, narration beats and editions were nowhere in
the package.  An interim guard refused the product kind outright; these tests assert
the adapter that replaced the guard: the copy restores the real explainer domain, a
product kind with no adapter is still refused, and a copy never replays a run or
inherits a media "verified" verdict.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from local_drama.application.project_packages import (
    EXPLAINER_STATE_SCHEMA,
    ProjectPackageService,
)
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError


def _drama_project(database, workspace, code: str) -> str:
    return str(
        ProjectService(database, workspace.projects_root).create_project(
            code=code,
            title=code,
            episode_count=1,
            aspect_ratio="16:9",
            fps_num=24,
            fps_den=1,
            target_duration_ms=60_000,
            allow_unconfigured_capabilities=True,
        )["id"]
    )


def _explainer_project(database, workspace, code: str) -> str:
    return str(
        ProjectService(database, workspace.projects_root).create_project(
            code=code,
            title=code,
            episode_count=0,
            season_count=0,
            aspect_ratio="16:9",
            fps_num=25,
            fps_den=1,
            target_duration_ms=300_000,
            allow_unconfigured_capabilities=True,
            product_kind="EXPLAINER",
        )["id"]
    )


def _seed_explainer_domain(database, workspace, project_id: str) -> dict[str, str]:
    """One complete explainer workspace: source, claim, script, beat and edition."""

    ids = {
        "video": "vid-0001",
        "packet": "pkt-0001",
        "source": "src-0001",
        "span": "span-0001",
        "claim": "claim-0001",
        "script": "script-0001",
        "segment": "seg-0001",
        "beat": "beat-0001",
        "edition": "edition-0001",
        "media_asset": "mav-0001",
        "media_version": "mvr-0001",
    }
    now = "2026-01-01T00:00:00+00:00"
    # The media version's declared hash must match real bytes on disk, because the
    # package manifest is compared against the media row during export.
    service = ProjectPackageService(database, workspace.projects_root)
    project_root = service._root(service._project(project_id))  # noqa: SLF001
    media_path = project_root / "04_media" / "fixture.mp4"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_bytes = b"explainer-media-fixture" * 64
    media_path.write_bytes(media_bytes)
    media_sha = hashlib.sha256(media_bytes).hexdigest()
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO explainer_videos (id,project_id,title,topic,content_kind,source_locale,input_kind,
            input_payload_json,duration_mode,target_seconds,tolerance_percent,automation_mode,inference_mode,
            research_mode,research_allowed_domains_json,current_script_revision_id,current_channel_profile_version_id,
            status,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (
                ids["video"], project_id, "灯塔解说", "值班记录为什么缺页", "FACTUAL_EXPLAINER", "zh-CN", "TOPIC",
                json.dumps({"topic": "值班记录为什么缺页"}), "TARGET", 300, 5.0, "AUTO_WITH_EXCEPTIONS",
                "LOCAL_ONLY", "OFFLINE_IMPORT", "[]", ids["script"], None, "ACTIVE", now, now, "tester",
            ),
        )
        connection.execute(
            """INSERT INTO explainer_research_packets (id,video_id,revision_no,status,mode,topic,
            allowed_domains_json,external_request_count,max_external_requests,content_hash,blockers_json,
            created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (ids["packet"], ids["video"], 1, "READY", "OFFLINE_IMPORT", "值班记录为什么缺页", "[]", 0, 0,
             "c" * 64, "[]", now, now, "tester"),
        )
        connection.execute(
            """INSERT INTO explainer_sources (id,packet_id,video_id,project_id,source_kind,url,title,
            author_or_publisher,published_at,updated_at_source,event_date,event_date_precision,fetched_at,language,
            body_sha256,rel_path,byte_size,credibility_kind,rights_json,upstream_source_id,import_session_id,
            source_document_version_id,retrieved_via,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (
                ids["source"], ids["packet"], ids["video"], project_id, "WEB_PAGE",
                "https://example.invalid/light", "灯塔值守记录（原始档案）", "港务档案室", "2025-11-02",
                None, "2025-11-01", "DAY", now, "zh-CN", "a" * 64, None, 1024, "PRIMARY", "{}", None, None,
                None, "OFFLINE_IMPORT", now, now, "tester",
            ),
        )
        connection.execute(
            """INSERT INTO explainer_source_spans (id,source_id,packet_id,ordinal,start_offset,end_offset,
            quote_text,span_hash,page_no,paragraph_no,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (ids["span"], ids["source"], ids["packet"], 1, 120, 168,
             "记录显示当夜值守页缺失一页，编号不连续。", "b" * 64, 3, 2, now, now, "tester"),
        )
        connection.execute(
            """INSERT INTO explainer_claims (id,video_id,packet_id,code,statement,statement_kind,status,importance,
            confidence_reason,verified_as_history,disambiguation_json,verification_json,created_at,updated_at,
            created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (ids["claim"], ids["video"], ids["packet"], "C001", "值班记录在事故当晚被撕掉一页。", "FACT",
             "UNVERIFIED", "CORE", "只有一家转载。", 0, "{}", "{}", now, now, "tester"),
        )
        connection.execute(
            """INSERT INTO claim_evidence (id,claim_id,source_id,source_span_id,stance,independence_key,note,
            created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            ("ev-0001", ids["claim"], ids["source"], ids["span"], "SUPPORTS", ids["source"], "原始档案", now, now,
             "tester"),
        )
        connection.execute(
            """INSERT INTO explainer_script_revisions (id,video_id,revision_no,locale,source_script_revision_id,
            title,outline_json,terminology_json,status,frozen_at,frozen_by,content_hash,parent_plan_id,
            provenance_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (ids["script"], ids["video"], 1, "zh-CN", None, "灯塔解说", json.dumps({"sections": 3}), "{}", "FROZEN",
             now, "tester", "d" * 64, None, "{}", now, now, "tester"),
        )
        connection.execute(
            """INSERT INTO narration_segments (id,video_id,script_revision_id,chapter_id,canonical_segment_id,
            locale,ordinal,display_text,spoken_text,statement_type,claim_ids_json,pronunciation_map_json,speaker,
            emotion,pause_after_ms,target_duration_ms,content_locked_by_human,locked_by,locked_at,segment_hash,
            previous_segment_id,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (ids["segment"], ids["video"], ids["script"], None, "seg_001", "zh-CN", 0,
             "值班记录缺了一页。", "值班记录缺了一页。", "FACT", json.dumps(["C001"]), "[]", None, None, 0, 3200,
             0, None, None, "e" * 64, None, now, now, "tester"),
        )
        connection.execute(
            """INSERT INTO explainer_visual_beats (id,video_id,code,ordinal,render_type,visual_intent,
            must_be_motion,reference_policy,allowed_fallbacks_json,preferred_duration_ms,entity_refs_json,
            claim_refs_json,visual_factuality,shot_grammar_json,prompt_intent,status,actual_fallback_type,
            fallback_reason,locked_by_human,locked_by,locked_at,origin,created_at,updated_at,created_by,revision,
            schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (ids["beat"], ids["video"], "BEAT_001", 0, "I2V", "值班员推开门，光线短暂中断。", 1, "DOCUMENTED",
             "[]", 3200, "[]", json.dumps(["C001"]), "DOCUMENTED", "{}", "值班室门口", "PLANNED", None, None, 0,
             None, None, "PLAN", now, now, "tester"),
        )
        connection.execute(
            """INSERT INTO beat_narration_links (id,beat_id,narration_segment_id,video_id,ordinal,created_at,
            updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,1,'v2')""",
            ("link-0001", ids["beat"], ids["segment"], ids["video"], 0, now, now, "tester"),
        )
        connection.execute(
            """INSERT INTO explainer_editions (id,video_id,edition_key,revision_no,voice_locale,
            subtitle_locales_json,subtitle_mode,aspect_ratio,fps_num,fps_den,width,height,audio_sample_rate_hz,
            duration_policy,target_seconds,tolerance_percent,allow_soft_subtitle_fallback,
            frozen_script_revision_id,frozen_narration_take_ids_json,frozen_subtitle_revision_id,status,
            created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (ids["edition"], ids["video"], "main-169", 1, "zh-CN", json.dumps(["zh-CN"]), "BURNED", "16:9", 25, 1,
             1920, 1080, 48000, "NATURAL_NARRATION", 300, 5.0, 0, ids["script"], "[]", None, "DRAFT", now, now,
             "tester"),
        )
        connection.execute(
            """INSERT INTO media_assets (id,project_id,owner_type,owner_id,purpose,media_kind,version_counter,
            metadata_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (ids["media_asset"], project_id, "EXPLAINER_VIDEO", ids["video"], "SHOT_VIDEO", "VIDEO", 1,
             json.dumps({"origin": "fixture"}), now, now, "tester"),
        )
        connection.execute(
            """INSERT INTO media_versions (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,
            sha256,duration_ms,fps_num,fps_den,parent_version_id,integrity_status,source_name,import_source,
            probe_json,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            (ids["media_version"], ids["media_asset"], 1, 0, "SOURCE", "04_media/fixture.mp4", "video/mp4",
             len(media_bytes), media_sha, 3200, 25, 1, None, "VERIFIED", "fixture.mp4", "GENERATED", "{}", now, now,
             "tester"),
        )
    return ids


def _copy_explainer(database, workspace, source_project_id: str, export_name: str) -> dict:
    packages = ProjectPackageService(database, workspace.projects_root)
    exported = packages.export(source_project_id)
    source_archive = Path(str(exported["artifact"]["server_absolute_path"]))
    inbox = packages.staging_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    staged_name = f"{export_name}.ldspkg"
    (inbox / staged_name).write_bytes(source_archive.read_bytes())
    staged = packages.stage_from_inbox(staged_name)
    return packages.import_as_copy(
        str(staged["stage_token"]), code=f"{export_name}_copy", title=export_name, actor="tester"
    )


def test_a_drama_project_still_exports_normally(database, workspace) -> None:
    project_id = _drama_project(database, workspace, "pkg02_drama")
    exported = ProjectPackageService(database, workspace.projects_root).export(project_id)
    assert exported["status"] == "EXPORTED"
    assert Path(str(exported["artifact"]["server_absolute_path"])).is_file()


def test_an_explainer_package_carries_its_own_domain(database, workspace) -> None:
    project_id = _explainer_project(database, workspace, "pkg02_explainer")
    _seed_explainer_domain(database, workspace, project_id)
    service = ProjectPackageService(database, workspace.projects_root)
    state = service._explainer_state(project_id)  # noqa: SLF001

    assert state["schema_version"] == EXPLAINER_STATE_SCHEMA
    assert len(state["videos"]) == 1
    assert len(state["sources"]) == 1
    assert len(state["claims"]) == 1
    assert len(state["claims"]) == 1 and state["claims"][0]["statement"]
    assert len(state["script_revisions"]) == 1
    assert len(state["narration_segments"]) == 1
    assert len(state["visual_beats"]) == 1
    assert len(state["editions"]) == 1
    assert len(state["media_versions"]) == 1
    # The report says exactly which domains were carried and which were not.
    assert "videos" in state["included_domains"]
    assert "jobs" in state["excluded_domains"]
    incomplete = {item["domain"] for item in state["incomplete_domains"]}
    assert {"composition_renders", "publication_packages", "explainer_qc_reports"} <= incomplete
    assert state["domain_counts"]["editions"] == 1


def test_a_product_kind_without_an_adapter_is_still_refused(database, workspace) -> None:
    """The guard remains for kinds the protocol genuinely cannot restore."""

    from local_drama.application.project_packages import _require_package_supported_product

    with pytest.raises(DomainRuleError) as error:
        _require_package_supported_product(
            {"id": "p1", "product_kind": "PODCAST"}, project_id="p1"
        )
    assert error.value.code == "PROJECT_PACKAGE_PRODUCT_UNSUPPORTED"
    assert error.value.details["product_kind"] == "PODCAST"
    assert "EXPLAINER" in error.value.details["supported_product_kinds"]


def test_an_explainer_round_trip_restores_the_whole_workspace(database, workspace) -> None:
    """The audit's acceptance case: cross-database round trip keeps the business data."""

    source_project_id = _explainer_project(database, workspace, "pkg02_explainer")
    ids = _seed_explainer_domain(database, workspace, source_project_id)
    copy = _copy_explainer(database, workspace, source_project_id, "pkg02_explainer")
    assert copy["copy_status"] == "IMPORTED"
    assert copy["product_kind"] == "EXPLAINER"
    copied_project_id = str(copy["target_project_id"])
    assert copied_project_id != source_project_id

    with database.connect() as connection:
        project = connection.execute(
            "SELECT product_kind FROM projects WHERE id=?", (copied_project_id,)
        ).fetchone()
        # The copy is an EXPLAINER project, not a DRAMA shell.
        assert str(project["product_kind"]) == "EXPLAINER"
        videos = connection.execute(
            "SELECT id,title,topic,current_script_revision_id FROM explainer_videos WHERE project_id=?",
            (copied_project_id,),
        ).fetchall()
        assert len(videos) == 1
        assert str(videos[0]["title"]) == "灯塔解说"
        assert str(videos[0]["topic"]) == "值班记录为什么缺页"
        # No fake seasons or episodes were invented.
        seasons = connection.execute(
            "SELECT COUNT(*) AS n FROM seasons WHERE project_id=?", (copied_project_id,)
        ).fetchone()
        assert int(seasons["n"]) == 0

        copied_video_id = str(videos[0]["id"])
        claims = connection.execute(
            "SELECT id,code,statement,status FROM explainer_claims WHERE video_id=?", (copied_video_id,)
        ).fetchall()
        assert [(str(row["code"]), str(row["statement"])) for row in claims] == [
            ("C001", "值班记录在事故当晚被撕掉一页。")
        ]
        # Claim -> evidence -> source -> span: the whole citation chain survives and
        # points at the COPY's own rows.
        evidence = connection.execute(
            """SELECT ce.id, s.id AS source_id, sp.id AS span_id, sp.quote_text
            FROM claim_evidence ce JOIN explainer_sources s ON s.id=ce.source_id
            JOIN explainer_source_spans sp ON sp.id=ce.source_span_id
            WHERE ce.claim_id=?""",
            (str(claims[0]["id"]),),
        ).fetchall()
        assert len(evidence) == 1
        assert str(evidence[0]["quote_text"]) == "记录显示当夜值守页缺失一页，编号不连续。"
        assert str(evidence[0]["source_id"]) != ids["source"]
        assert str(evidence[0]["span_id"]) != ids["span"]

        segments = connection.execute(
            "SELECT id,canonical_segment_id,display_text FROM narration_segments WHERE video_id=?",
            (copied_video_id,),
        ).fetchall()
        assert [(str(row["canonical_segment_id"]), str(row["display_text"])) for row in segments] == [
            ("seg_001", "值班记录缺了一页。")
        ]
        beats = connection.execute(
            "SELECT id,code,render_type FROM explainer_visual_beats WHERE video_id=?", (copied_video_id,)
        ).fetchall()
        assert [(str(row["code"]), str(row["render_type"])) for row in beats] == [("BEAT_001", "I2V")]
        # beat -> narration link now points at the copied segment.
        link = connection.execute(
            "SELECT narration_segment_id FROM beat_narration_links WHERE beat_id=?", (str(beats[0]["id"]),)
        ).fetchone()
        assert str(link["narration_segment_id"]) == str(segments[0]["id"])
        editions = connection.execute(
            "SELECT id,edition_key,voice_locale,frozen_script_revision_id FROM explainer_editions WHERE video_id=?",
            (copied_video_id,),
        ).fetchall()
        assert [(str(row["edition_key"]), str(row["voice_locale"])) for row in editions] == [("main-169", "zh-CN")]
        # The frozen script pointer follows the copied revision, not the source one.
        assert str(editions[0]["frozen_script_revision_id"]) != ids["script"]
        assert str(videos[0]["current_script_revision_id"]) == str(editions[0]["frozen_script_revision_id"])

        media = connection.execute(
            """SELECT mv.id,mv.rel_path,mv.sha256,mv.integrity_status,ma.project_id
            FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
            WHERE ma.project_id=?""",
            (copied_project_id,),
        ).fetchall()
        assert len(media) == 1
        assert str(media[0]["rel_path"]) == "04_media/fixture.mp4"
        assert str(media[0]["sha256"]) == hashlib.sha256(
            (b"explainer-media-fixture" * 64)
        ).hexdigest()
        # The copied bytes are not "verified" merely because the source was.
        assert str(media[0]["integrity_status"]) == "UNVERIFIED"
        assert str(media[0]["id"]) != ids["media_version"]

    # The payload files really landed in the copy's own root.
    copied_root = workspace.projects_root / str(copy["target_root_rel"])
    assert (copied_root / "04_media" / "fixture.mp4").is_file()


def test_a_copy_never_replays_a_run_or_restores_publication_authority(database, workspace) -> None:
    """PKG-02: runs are carried as history, never as executable state."""

    source_project_id = _explainer_project(database, workspace, "pkg02_runs")
    ids = _seed_explainer_domain(database, workspace, source_project_id)
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO explainer_runs (id,project_id,video_id,status,automation_mode,plan_hash,
            plan_json,plan_revision,frozen_inputs_json,policy_snapshot_json,capability_snapshot_json,budget_json,
            inference_mode,research_mode,inference_egress_denied_count,current_stage_code,progress_json,
            blockers_json,budget_used_json,idempotency_key,created_at,updated_at,created_by,revision,schema_version)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,'v2')""",
            ("run-0001", source_project_id, ids["video"], "RUNNING", "AUTO_WITH_EXCEPTIONS", "h" * 64, "{}", 1,
             "{}", "{}", "{}", "{}", "LOCAL_ONLY", "OFFLINE_IMPORT", 0, "NARRATION_TTS", "{}", "[]", "{}",
             "idem-0001", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "tester"),
        )
    copy = _copy_explainer(database, workspace, source_project_id, "pkg02_runs")
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT status,current_stage_code,automation_workflow_run_id,idempotency_key FROM explainer_runs WHERE project_id=?",
            (str(copy["target_project_id"]),),
        ).fetchall()
    assert len(rows) == 1
    # History is kept, but the run is terminal and bound to no workflow execution.
    assert str(rows[0]["status"]) == "CANCELLED"
    assert str(rows[0]["current_stage_code"]) == "IMPORTED_HISTORY"
    assert rows[0]["automation_workflow_run_id"] is None
    assert rows[0]["idempotency_key"] is None


def test_an_explainer_package_missing_a_domain_is_rejected(database, workspace) -> None:
    """A package that omits a declared domain is malformed, never "empty"."""

    source_project_id = _explainer_project(database, workspace, "pkg02_missing")
    _seed_explainer_domain(database, workspace, source_project_id)
    service = ProjectPackageService(database, workspace.projects_root)
    state = service._explainer_state(source_project_id)  # noqa: SLF001
    state.pop("claims")
    with pytest.raises(DomainRuleError) as error:
        service._validate_state_structure(state)  # noqa: SLF001
    assert error.value.code == "PROJECT_PACKAGE_STATE_INVALID"
    assert "claims" in error.value.details["missing"]


def test_a_drama_package_still_declares_its_product_kind(database, workspace) -> None:
    project_id = _drama_project(database, workspace, "pkg02_state")
    service = ProjectPackageService(database, workspace.projects_root)
    state = service._state(project_id)  # noqa: SLF001
    assert state["project"].get("product_kind") in {None, "DRAMA"}
    assert str(state["schema_version"]).endswith((".v2", ".v3"))


def test_an_explainer_archive_declares_the_explainer_schema(database, workspace, tmp_path: Path) -> None:
    """The written archive really carries the explainer state, not a DRAMA one."""

    source_project_id = _explainer_project(database, workspace, "pkg02_archive")
    _seed_explainer_domain(database, workspace, source_project_id)
    exported = ProjectPackageService(database, workspace.projects_root).export(source_project_id)
    archive_path = Path(str(exported["artifact"]["server_absolute_path"]))
    with zipfile.ZipFile(archive_path) as archive:
        state = json.loads(archive.read("project-state.json").decode("utf-8"))
    assert state["schema_version"] == EXPLAINER_STATE_SCHEMA
    assert [item["title"] for item in state["videos"]] == ["灯塔解说"]
