"""P1-12 subtitle style templates: style param, ASS Style block, persistence, validation."""

from __future__ import annotations

import pytest

from local_drama.application.documents import DocumentImportService
from local_drama.application.projects import ProjectService
from local_drama.application.subtitle_styles import (
    DEFAULT_SUBTITLE_STYLE,
    SubtitleStyleTemplateService,
    validate_style,
)
from local_drama.application.timeline import TimelineService
from local_drama.domain.errors import DomainRuleError


def _project_and_episode(workspace, database) -> tuple[dict[str, object], dict[str, object]]:
    project = ProjectService(database, workspace.projects_root).create_project(
        code="subtitle_styles",
        title="Subtitle styles",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )
    project_service = ProjectService(database, workspace.projects_root)
    season = project_service.list_seasons(str(project["id"]))[0]
    episode = project_service.list_episodes(str(season["id"]))[0]
    return project, episode


def _authority(workspace, database, project_id: str) -> dict[str, object]:
    script_path = workspace.work_root / "subtitle-style-script.txt"
    script_path.write_text("你好，世界", encoding="utf-8")
    script = DocumentImportService(database, workspace).import_document(project_id, script_path)
    return {"text_authority": "SCRIPT", "source_document_version_id": script["source_document_version_id"]}


def _subtitle(workspace, database, episode_id: str, format: str, authority, style=None) -> dict[str, object]:
    return TimelineService(database, workspace).create_subtitle_revision(
        episode_id,
        [{"start_us": 0, "end_us": 1_000_000, "text": "你好，世界"}],
        format=format,
        authority=authority,
        style=style,
    )


def test_ass_render_emits_style_block_and_srt_ignores_style(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    authority = _authority(workspace, database, str(project["id"]))
    style = {"font": "SimHei", "size": 60, "color": "#FFD700", "position": "TOP", "outline": 4}

    ass = _subtitle(workspace, database, str(episode["id"]), "ASS", authority, style)
    content = ass["content_text"]
    assert "[V4+ Styles]" in content
    assert "Style: Default,SimHei,60,&H0000D7FF&,&H000000FF&,&H00000000&,&H80000000&,0,0,0,0,100,100,0,0,1,4,0,8,10,10,10,1" in content
    # Legacy Dialogue line layout is preserved.
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,你好，世界" in content

    plain_srt = _subtitle(workspace, database, str(episode["id"]), "SRT", authority)
    styled_srt = _subtitle(workspace, database, str(episode["id"]), "SRT", authority, style)
    assert styled_srt["content_text"] == plain_srt["content_text"]
    assert "00:00:00,000 --> 00:00:01,000" in styled_srt["content_text"]


def test_cue_style_json_is_persisted(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    authority = _authority(workspace, database, str(project["id"]))
    style = {"font": "KaiTi", "size": 40, "color": "#00FF00", "position": "BOTTOM", "outline": 0}
    revision = _subtitle(workspace, database, str(episode["id"]), "ASS", authority, style)
    assert revision["cues"][0]["style"] == style
    loaded = TimelineService(database, workspace).get_subtitles(str(revision["id"]))
    assert loaded["cues"][0]["style"] == style
    assert loaded["input_snapshot"]["style"] == style


def test_default_style_is_applied_when_omitted(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    authority = _authority(workspace, database, str(project["id"]))
    revision = _subtitle(workspace, database, str(episode["id"]), "ASS", authority)
    assert revision["cues"][0]["style"] == DEFAULT_SUBTITLE_STYLE
    assert f"Style: Default,{DEFAULT_SUBTITLE_STYLE['font']},{DEFAULT_SUBTITLE_STYLE['size']}" in revision["content_text"]


def test_style_validation_rejects_invalid_fields(workspace, database) -> None:
    project, episode = _project_and_episode(workspace, database)
    authority = _authority(workspace, database, str(project["id"]))
    for bad_style in (
        {"size": 200},
        {"size": "large"},
        {"color": "red"},
        {"color": "#12345"},
        {"position": "SIDE"},
        {"outline": -1},
        {"mystery": 1},
    ):
        with pytest.raises(DomainRuleError) as error:
            _subtitle(workspace, database, str(episode["id"]), "ASS", authority, bad_style)
        assert error.value.code == "SUBTITLE_STYLE_INVALID"
    assert validate_style({"font": "Arial"})["font"] == "Arial"


def test_style_template_crud_and_audit(workspace, database) -> None:
    project, _ = _project_and_episode(workspace, database)
    project_id = str(project["id"])
    service = SubtitleStyleTemplateService(database)
    style = {"font": "Microsoft YaHei", "size": 48, "color": "#FFFFFF", "position": "BOTTOM", "outline": 2}

    created = service.save_template(project_id, "DEFAULT_STYLE", "默认样式", style, "初始模板")
    assert created["kind"] == "STYLE"
    assert created["revision_no"] == 1
    assert created["content"]["schema_version"] == "localdrama.subtitle-style.v1"
    assert created["content"]["size"] == 48

    listed = service.list_templates(project_id)
    assert [item["id"] for item in listed] == [created["id"]]
    assert service.get_template(created["id"])["content"]["font"] == "Microsoft YaHei"

    updated = service.save_template(project_id, "DEFAULT_STYLE", "默认样式 v2", {**style, "size": 56}, "增大字号")
    assert updated["revision_no"] == 2
    assert updated["content"]["size"] == 56
    assert len(service.list_templates(project_id)) == 1

    with database.connect() as connection:
        actions = [row[0] for row in connection.execute(
            "SELECT action FROM audit_events WHERE subject_type='creative_entry' AND subject_id=? ORDER BY event_id",
            (created["id"],),
        ).fetchall()]
        assert actions == ["SUBTITLE_STYLE_TEMPLATE_CREATED", "SUBTITLE_STYLE_TEMPLATE_UPDATED"]

    deleted = service.delete_template(created["id"])
    assert deleted["deleted"] is True
    assert service.list_templates(project_id) == []
    with pytest.raises(DomainRuleError) as error:
        service.get_template(created["id"])
    assert error.value.code == "CREATIVE_ENTRY_NOT_FOUND"
