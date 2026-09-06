"""Integration coverage for the explicit delivery canvas in episode compose."""

from __future__ import annotations

import json
import subprocess

import pytest

from local_drama.application.compose import ComposeService
from local_drama.application.documents import DocumentImportService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.application.worker import _worker_error_detail
from local_drama.domain.errors import DomainRuleError


def _video(workspace, name: str, seconds: float = 1.0, *, fps: int | None = None):
    output = workspace.work_root / name
    command = [workspace.ffmpeg_path, "-f", "lavfi", "-i", f"color=c=navy:s=160x90:d={seconds}", "-pix_fmt", "yuv420p", "-an"]
    if fps is not None:
        command += ["-r", str(fps)]
    command += ["-y", str(output)]
    subprocess.run(command, check=True, capture_output=True)
    return output


def _episode(workspace, database):
    project = ProjectService(database, workspace.projects_root).create_project(
        code="production_spec_timeline",
        title="Production spec timeline",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )
    project_service = ProjectService(database, workspace.projects_root)
    season = project_service.list_seasons(str(project["id"]))[0]
    return project, project_service.list_episodes(str(season["id"]))[0]


def _upscale_spec(*, source_width: int = 160, source_height: int = 90, fit: str | None = None):
    upscale = {
        "enabled": True,
        "required": True,
        "stage": "COMPOSE_QC",
        "executor": "builtin:ffmpeg",
        "target": "PRESENTATION_SPEC",
    }
    if fit is not None:
        upscale["fit"] = fit
    return {
        "schema_version": "localdrama.production-spec-snapshot.v1",
        "status": "READY",
        "delivery": {"aspect_ratio": "16:9", "width": 2560, "height": 1440, "fps": {"numerator": 24, "denominator": 1}},
        "generation": {
            "mode": "UPSCALE_COMPOSE",
            "actual": {"width": source_width, "height": source_height, "fps": 24.0},
            "semantic_inputs": {},
            "upscale": upscale,
        },
        "blockers": [],
        "warnings": [],
    }


def test_render_applies_compose_qc_upscale_and_validates_real_probe(workspace, database, monkeypatch) -> None:
    project, episode = _episode(workspace, database)
    media = MediaService(database, workspace).import_file(str(project["id"]), _video(workspace, "production-spec-source.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    monkeypatch.setattr(service, "_production_spec_for_episode", lambda _episode: _upscale_spec())
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(media["media_version_id"]), "start_us": 0, "end_us": 1_000_000, "parameters": {}}],
        {"source": "production-spec-timeline-test"},
    )

    render = service.render_episode(str(timeline["id"]))

    stream = next(item for item in render["probe"]["streams"] if item.get("codec_type") == "video")
    assert (stream["width"], stream["height"]) == (2560, 1440)
    assert render["input_snapshot"]["geometry"]["source"] == {"width": 160, "height": 90, "fps": 24.0}
    assert render["input_snapshot"]["geometry"]["delivery"] == {"width": 2560, "height": 1440, "fps": 24.0}
    stages = [step["stage"] for step in json.loads(render["execution_log"])["steps"]]
    assert "COMPOSE_QC_UPSCALE" in stages


def test_compose_preflight_exposes_production_blocker_and_never_queues(workspace, database, monkeypatch) -> None:
    project, episode = _episode(workspace, database)
    media = MediaService(database, workspace).import_file(str(project["id"]), _video(workspace, "production-spec-blocked.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    timeline_service = TimelineService(database, workspace)
    timeline = timeline_service.create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(media["media_version_id"]), "start_us": 0, "end_us": 1_000_000, "parameters": {}}],
        {"source": "production-spec-blocked-test"},
    )
    blocked = {
        "schema_version": "localdrama.production-spec-snapshot.v1",
        "status": "BLOCKED",
        "delivery": {"aspect_ratio": "16:9", "width": 2560, "height": 1440, "fps": {"numerator": 24, "denominator": 1}},
        "generation": {"mode": "BLOCKED", "actual": {"width": 864, "height": 480, "fps": 24.0}, "semantic_inputs": {}, "upscale": None},
        "blockers": [{"code": "PRODUCTION_UPSCALE_PATH_REQUIRED", "message": "缺少受审计的放大路径"}],
        "warnings": [],
    }
    monkeypatch.setattr(TimelineService, "_production_spec_for_episode", lambda _service, _episode: blocked)

    preflight = ComposeService(database, workspace).preflight(str(timeline["id"]))
    assert preflight["status"] == "BLOCKED"
    assert preflight["production_spec"]["status"] == "BLOCKED"
    assert preflight["blockers"][0]["code"] == "PRODUCTION_UPSCALE_PATH_REQUIRED"
    with pytest.raises(DomainRuleError) as error:
        ComposeService(database, workspace).submit(str(timeline["id"]))
    assert error.value.code == "COMPOSE_PRODUCTION_SPEC_BLOCKED"


def test_upscale_requires_explicit_fit_for_aspect_mismatch(workspace, database, monkeypatch) -> None:
    service = TimelineService(database, workspace)
    blocked = _upscale_spec(source_width=480, source_height=832)
    with pytest.raises(DomainRuleError) as error:
        service._upscale_to_delivery(workspace.work_root / "proxy.mp4", workspace.work_root / "delivery.mp4", blocked)
    assert error.value.code == "PRODUCTION_COMPOSITION_POLICY_REQUIRED"

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(service, "_run_ffmpeg", lambda args, timeout: calls.append({"args": args, "timeout": timeout}) or calls[-1])
    ready = service._upscale_to_delivery(
        workspace.work_root / "proxy.mp4",
        workspace.work_root / "delivery.mp4",
        _upscale_spec(source_width=480, source_height=832, fit="LETTERBOX"),
    )
    assert ready["args"] == calls[-1]["args"]
    assert "force_original_aspect_ratio=decrease" in ready["args"][ready["args"].index("-vf") + 1]


def test_timeline_concat_pins_total_duration_for_non_frame_aligned_clips(workspace, database) -> None:
    """Per-clip frame rounding must not extend a multi-clip timeline."""

    service = TimelineService(database, workspace)
    source = _video(workspace, "production-spec-duration-source.mp4", fps=24)
    durations_us = [90_909] * 10 + [90_910]
    items: list[dict[str, object]] = []
    cursor_us = 0
    for duration_us in durations_us:
        items.append(
            {
                "start_us": cursor_us,
                "end_us": cursor_us + duration_us,
                "parameters": {"transition_in": "CUT"},
            }
        )
        cursor_us += duration_us
    assert cursor_us == 1_000_000

    output = workspace.work_root / "production-spec-duration-concat.mp4"
    service._concat_timeline_videos([source] * len(items), items, workspace.work_root, output)

    probe = service._probe(output)
    assert abs(int(probe["duration_ms"]) - 1_000) <= 20
    frame_probe = subprocess.run(
        [
            workspace.ffprobe_path,
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    frame_stream = json.loads(frame_probe.stdout)["streams"][0]
    assert int(frame_stream["nb_read_frames"]) == 24


def test_timeline_duration_uses_frozen_span_when_video_items_overlap(workspace, database) -> None:
    """Overlapping clips must not inflate the immutable episode duration."""

    service = TimelineService(database, workspace)
    items = [
        {"start_us": 0, "end_us": 550_000, "parameters": {"transition_in": "CUT"}},
        {"start_us": 500_000, "end_us": 1_000_000, "parameters": {"transition_in": "CUT"}},
    ]

    assert service._timeline_video_duration_us(items) == 1_000_000
    assert service._timeline_video_duration_us(items) != sum(item["end_us"] - item["start_us"] for item in items)

    source = _video(workspace, "production-spec-overlap-source.mp4", fps=24)
    output = workspace.work_root / "production-spec-overlap-concat.mp4"
    service._concat_timeline_videos([source, source], items, workspace.work_root, output)
    assert abs(int(service._probe(output)["duration_ms"]) - 1_000) <= 20
    frame_probe = subprocess.run(
        [
            workspace.ffprobe_path,
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "json",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert int(json.loads(frame_probe.stdout)["streams"][0]["nb_read_frames"]) == 24


def test_probe_failure_keeps_redacted_stderr_and_input_facts_for_worker_diagnostics(
    workspace, database, monkeypatch
) -> None:
    """A failed final probe must be actionable without leaking host paths."""

    service = TimelineService(database, workspace)
    path = workspace.work_root / "probe-failure-input.mp4"
    path.write_bytes(b"not a video")

    def failed_probe(command, **_kwargs):
        assert str(path) == command[-1]
        return type(
            "ProbeResult",
            (),
            {
                "returncode": 1,
                "stdout": "",
                "stderr": f"[mov,mp4,m4a] {path}: Invalid data found when processing input",
            },
        )()

    monkeypatch.setattr("local_drama.application.timeline.subprocess.run", failed_probe)

    with pytest.raises(DomainRuleError) as caught:
        service._probe(path)

    details = caught.value.details
    assert details["returncode"] == 1
    assert details["path_suffix"] == ".mp4"
    assert details["path_exists"] is True
    assert details["path_size_bytes"] == len(b"not a video")
    assert "Invalid data found when processing input" in details["stderr_redacted"]
    assert str(path) not in details["stderr_redacted"]

    persisted = _worker_error_detail(caught.value)
    assert "Invalid data found when processing input" in persisted
    assert "suffix=.mp4" in persisted
    assert "exists=true" in persisted
    assert "size_bytes=11" in persisted
    assert str(path) not in persisted


def test_v4_overlap_subtitle_upscale_pipeline_cleans_temp_files_and_pins_span(workspace, database, monkeypatch) -> None:
    """The V4 span must survive every temporary render stage."""

    project, episode = _episode(workspace, database)
    media = MediaService(database, workspace).import_file(
        str(project["id"]), _video(workspace, "v4-overlap-pipeline-source.mp4", seconds=0.6),
        purpose="SHOT_VIDEO", media_kind="VIDEO",
    )
    script_path = workspace.work_root / "v4-overlap-pipeline-script.txt"
    script_path.write_text("V4 管线字幕", encoding="utf-8")
    script = DocumentImportService(database, workspace).import_document(str(project["id"]), script_path)
    service = TimelineService(database, workspace)
    monkeypatch.setattr(service, "_production_spec_for_episode", lambda _episode: _upscale_spec())
    subtitle = service.create_subtitle_revision(
        str(episode["id"]),
        [{"start_us": 0, "end_us": 1_000_000, "text": "V4 管线字幕"}],
        format="ASS",
        authority={"text_authority": "SCRIPT", "source_document_version_id": script["source_document_version_id"]},
    )
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [
            {"track_type": "VIDEO", "media_version_id": str(media["media_version_id"]), "start_us": 0, "end_us": 550_000, "parameters": {"transition_in": "CUT"}},
            {"track_type": "VIDEO", "media_version_id": str(media["media_version_id"]), "start_us": 500_000, "end_us": 1_000_000, "parameters": {"transition_in": "CUT"}},
        ],
        {"source": "v4-overlap-pipeline-test", "subtitle_revision_id": subtitle["id"]},
    )

    render = service.render_episode(str(timeline["id"]))

    assert render["input_snapshot"]["renderer_contract"] == "TIMELINE_SOURCE_COVERAGE_V5"
    assert render["input_snapshot"]["timeline_duration_us"] == 1_000_000
    assert abs(int(render["probe"]["duration_ms"]) - 1_000) <= 20
    stream = next(item for item in render["probe"]["streams"] if item.get("codec_type") == "video")
    assert (stream["width"], stream["height"]) == (2560, 1440)
    stages = [step["stage"] for step in json.loads(render["execution_log"])["steps"]]
    assert stages == ["timeline-duration", "timeline-duration", "concat", "subtitle", "COMPOSE_QC_UPSCALE"]
    render_dir = workspace.projects_root / str(project["root_rel"]) / "05_timelines" / "renders"
    assert not list(render_dir.glob(".partial-*"))
