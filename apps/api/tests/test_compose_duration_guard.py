"""No media generation or database access: reject short sources before work."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from local_drama.application.compose import ComposeService
from local_drama.application.timeline import TimelineService
from local_drama.domain.errors import DomainRuleError


def _short_probe(_path):
    return {"streams": [{"codec_type": "video", "duration": "4.458333", "avg_frame_rate": "24/1"}]}


def test_worker_rejects_short_clip_before_ffmpeg(monkeypatch):
    service = object.__new__(TimelineService)
    monkeypatch.setattr(service, "_probe", _short_probe)
    monkeypatch.setattr(service, "_run_ffmpeg", lambda *args, **kwargs: pytest.fail("must not render"))
    with pytest.raises(DomainRuleError, match="缺少") as caught:
        service._concat_timeline_videos(
            [Path("unused.mp4")], [{"start_us": 0, "end_us": 12_632_000}],
            Path("unused-directory"), Path("unused-output.mp4"),
        )
    assert caught.value.code == "TIMELINE_SOURCE_DURATION_INSUFFICIENT"


def test_segment_concat_cannot_fill_episode_with_frozen_tail(monkeypatch):
    service = object.__new__(TimelineService)
    monkeypatch.setattr(service, "_probe", _short_probe)
    with pytest.raises(DomainRuleError) as caught:
        service._concat_videos([Path("unused.mp4")] * 11, Path("unused/output.mp4"), duration_seconds=120)
    assert caught.value.code == "TIMELINE_SOURCE_DURATION_INSUFFICIENT"


def test_compose_submission_reports_coverage_not_disk_failure(monkeypatch):
    service = object.__new__(ComposeService)
    blocker = {"code": "TIMELINE_SOURCE_DURATION_INSUFFICIENT", "message": "源视频缺少 8 秒"}
    monkeypatch.setattr(service, "preflight", lambda *args, **kwargs: {"status": "BLOCKED", "blockers": [blocker]})
    with pytest.raises(DomainRuleError) as caught:
        service.submit("isolated-unit-test")
    assert caught.value.code == "COMPOSE_SOURCE_DURATION_BLOCKED"
    assert caught.value.details["blockers"] == [blocker]


def test_preflight_returns_source_deficit_without_execution(monkeypatch):
    service = object.__new__(TimelineService)
    item = {"track_type": "VIDEO", "media_version_id": "source", "start_us": 0, "end_us": 12_632_000, "parameters": {}}
    timeline = {"episode_id": "episode", "items": [item], "revision_hash": "hash", "input_snapshot": {}}
    monkeypatch.setattr(service, "get_timeline", lambda _: timeline)
    monkeypatch.setattr(service, "_assert_timeline_renderable", lambda _: None)
    monkeypatch.setattr(service, "_episode", lambda _: {"id": "episode", "project_id": "project", "root_rel": "test"})
    monkeypatch.setattr(service, "_production_spec_for_episode", lambda _: None)
    monkeypatch.setattr(service, "_media_for_episode", lambda *args: {"id": "source", "media_kind": "VIDEO", "sha256": "hash", "byte_size": 1})
    monkeypatch.setattr(service, "_probe", _short_probe)
    monkeypatch.setattr(service, "_audio_bindings_for_timeline", lambda *args: [])
    monkeypatch.setattr(service, "_subtitle_for_render", lambda *args: None)
    monkeypatch.setattr(service, "_existing_render", lambda *args: None)
    monkeypatch.setattr("local_drama.application.timeline._hash_file", lambda _: ("hash", 1))
    service.media = SimpleNamespace(content_path=lambda _: ({}, Path("unused.mp4")))
    service.settings = SimpleNamespace(resolve_project_root=lambda _: Path("unused"))
    plan = service.preflight_episode_render("timeline")
    assert plan["blockers"][0]["code"] == "TIMELINE_SOURCE_DURATION_INSUFFICIENT"
    assert plan["blockers"][0]["details"]["required_us"] == 12_632_000
    assert plan["would_execute_ffmpeg"] is False
