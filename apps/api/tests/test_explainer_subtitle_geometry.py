"""Captions must carry the geometry they were laid out with.

Every cue in a real run was reported ``SUBTITLE_GEOMETRY_UNKNOWN``.  The burn-in
script laid the captions out correctly, but ``create_revision`` persisted only
``start_ms``/``end_ms``/``text``/``paired_text``/``segment_canonical_id``, so the
QC subtitle layer had no box or font size to verify — the layout layer could never
pass, and the machine policy refused a perfectly laid out captioned film (design
§6.2/§7.4).

Requirement mapping: §6.2 (文字可读性 uses a deterministic text layer whose layout
is checked) and §7.4 (wrap, safe area and font are verified before burn-in).
"""

from __future__ import annotations

from local_drama.application.explainers.narration import ExplainerNarrationService
from local_drama.application.explainers.quality import ExplainerQualityService
from local_drama.application.explainers.subtitles import (
    ExplainerSubtitleService,
    caption_metrics,
    cue_geometry,
    default_safe_area,
)
from local_drama.domain.explainers.contracts import ProductKind
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository
from local_drama.infrastructure.database.sqlite import Database

PROJECT_ID = "subtitle-geometry-project"
VIDEO_ID = "subtitle-geometry-video"
EDITION_ID = "subtitle-geometry-edition"
SCRIPT_ID = "subtitle-geometry-script"
NOW = "2026-01-01T00:00:00Z"

LONG_CJK = "当晚只有沈砚、林澄与周禾三人在塔内，观察窗短暂无光的原因需要逐条核对。" * 2


def _seed(database: Database, *, width: int = 1920, height: int = 1080) -> None:
    with database.transaction() as connection:
        connection.execute("DELETE FROM projects WHERE id=?", (PROJECT_ID,))
        connection.execute(
            """INSERT INTO projects (id, code, title, status, template_version, root_rel, target_duration_ms,
            product_kind, created_at, updated_at, created_by)
            VALUES (?, 'sub_geo_proj', '字幕几何', 'DRAFT', 'v2', ?, 300000, ?,
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (PROJECT_ID, PROJECT_ID, ProductKind.EXPLAINER.value),
        )
        connection.execute(
            """INSERT INTO explainer_videos (id, project_id, title, topic, content_kind, source_locale, input_kind,
            duration_mode, target_seconds, tolerance_percent, automation_mode, inference_mode, research_mode,
            status, created_at, updated_at, created_by)
            VALUES (?, ?, '字幕几何', '为什么', 'FACTUAL_EXPLAINER', 'zh-CN', 'TOPIC',
            'FIXED', 300, 5, 'AUTO_WITH_EXCEPTIONS', 'LOCAL_ONLY', 'OFFLINE_IMPORT', 'DRAFT',
            '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 'test')""",
            (VIDEO_ID, PROJECT_ID),
        )
        repo = ExplainerRepository(connection)
        service = ExplainerNarrationService(repo)
        created = service.create_script_revision(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            locale="zh-CN",
            title="字幕几何",
            outline=["几何"],
            segments=[
                {
                    "canonical_segment_id": "seg_001",
                    "display_text": LONG_CJK,
                    "spoken_text": LONG_CJK,
                    "statement_type": "QUESTION",
                }
            ],
        )
        revision = created["script_revision"]
        service.freeze_script(script_revision_id=str(revision["id"]), actor="test")
        repo.insert(
            "explainer_editions",
            {
                "id": EDITION_ID,
                "video_id": VIDEO_ID,
                "edition_key": "zh-captioned-169",
                "voice_locale": "zh-CN",
                "subtitle_mode": "BURNED",
                "subtitle_locales_json": ["zh-CN"],
                "aspect_ratio": "16:9",
                "width": width,
                "height": height,
                "fps_num": 25,
                "fps_den": 1,
                "frozen_script_revision_id": str(revision["id"]),
            },
        )
        repo.update(
            "explainer_videos", VIDEO_ID, {"current_script_revision_id": str(revision["id"])}
        )


def _cues() -> list[dict[str, object]]:
    return [
        {"start_ms": 0, "end_ms": 3000, "text": LONG_CJK, "paired_text": "", "segment_canonical_id": "seg_001"},
        {"start_ms": 3000, "end_ms": 5000, "text": "第二句。", "paired_text": "", "segment_canonical_id": "seg_001"},
    ]


# --------------------------------------------------------------------------- #
# the geometry itself
# --------------------------------------------------------------------------- #
def test_geometry_stays_inside_the_declared_safe_area() -> None:
    """Captions belong inside the safe area, not inside their own private margin.

    The burn-in used a 4% bottom margin while the product declares 8% for 16:9, so
    a bottom-aligned caption was laid out inside the very margin the layout
    detector checks.
    """

    area_169 = default_safe_area("16:9")
    geometry = cue_geometry(text=LONG_CJK, width=1920, height=1080, safe_area=area_169)
    box = geometry["box"]
    assert box["x"] >= round(1920 * area_169["left"])
    assert box["x"] + box["width"] <= 1920 - round(1920 * area_169["right"]) + 1
    assert box["y"] >= round(1080 * area_169["top"])
    assert box["y"] + box["height"] <= 1080 - round(1080 * area_169["bottom"]) + 1
    # Long text wraps rather than running off the frame.
    assert geometry["line_count"] > 1

    area_916 = default_safe_area("9:16")
    portrait = cue_geometry(text=LONG_CJK, width=1080, height=1920, safe_area=area_916)
    assert portrait["box"]["y"] + portrait["box"]["height"] <= 1920 - round(1920 * area_916["bottom"]) + 1
    # A taller canvas scales the font size, exactly like the burn-in style does.
    assert cue_geometry(text="短句", width=1920, height=1080)["font_size_px"] == 48
    assert cue_geometry(text="短句", width=1080, height=1920)["font_size_px"] == 85


def test_the_burn_in_uses_the_same_metrics_as_the_recorded_geometry() -> None:
    """One formula, so QC cannot verify something the film does not contain."""

    style = {"size": 48}
    area = default_safe_area("16:9")
    metrics = caption_metrics(width=1920, height=1080, style=style, safe_area=area)
    geometry = cue_geometry(text=LONG_CJK, width=1920, height=1080, style=style, safe_area=area)
    assert metrics["font_size_px"] == geometry["font_size_px"]
    assert metrics["characters_per_line"] == geometry["characters_per_line"]
    assert metrics["margin_h_px"] == geometry["margin_h_px"]
    assert metrics["margin_bottom_px"] == round(1080 * area["bottom"])
    # The ASS style's MarginV is the same number the geometry was placed with.
    assert metrics["margin_v_px"] == metrics["margin_bottom_px"]

    with_style = cue_geometry(text="短句", width=1920, height=1080, style={"size": 72})
    assert with_style["font_size_px"] == 72


# --------------------------------------------------------------------------- #
# persistence
# --------------------------------------------------------------------------- #
def _create_revision(database: Database, *, cues: list[dict[str, object]] | None = None) -> dict[str, object]:
    with database.transaction() as connection:
        repo = ExplainerRepository(connection)
        script_revision_id = str(
            repo.list_where("explainer_script_revisions", {"video_id": VIDEO_ID})[0]["id"]
        )
        service = ExplainerSubtitleService(repo)
        return service.create_revision(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            edition_id=EDITION_ID,
            locale="zh-CN",
            cues=cues or _cues(),
            text_authority="NARRATION_SCRIPT",
            script_revision_id=script_revision_id,
            format="JSON",
            status="FROZEN",
            actor="test",
        )


def test_create_revision_records_the_cue_geometry(database: Database) -> None:
    _seed(database)
    created = _create_revision(database)
    persisted = created["subtitle_revision"]["cues_json"]
    assert len(persisted) == 2
    for cue in persisted:
        assert cue["box"] is not None, cue
        assert {"x", "y", "width", "height"} <= set(cue["box"])
        assert int(cue["font_size_px"]) > 0
        assert int(cue["line_count"]) >= 1
    # The wide CJK cue wrapped; the short one stayed on a single line.
    assert int(persisted[0]["line_count"]) > 1
    assert int(persisted[1]["line_count"]) == 1


def test_a_caller_supplied_layout_is_preserved(database: Database) -> None:
    _seed(database)
    supplied = _cues()
    supplied[0]["box"] = {"x": 100, "y": 200, "width": 300, "height": 60}
    supplied[0]["font_size_px"] = 40
    supplied[0]["line_count"] = 1
    created = _create_revision(database, cues=supplied)
    first = created["subtitle_revision"]["cues_json"][0]
    assert first["box"] == {"x": 100, "y": 200, "width": 300, "height": 60}
    assert int(first["font_size_px"]) == 40
    # The caller owned the layout, so the computed one must not overwrite it.
    assert first["line_count"] == 1


# --------------------------------------------------------------------------- #
# the detector that used to report UNKNOWN for every cue
# --------------------------------------------------------------------------- #
def test_the_layout_detector_no_longer_reports_unknown_geometry(database: Database) -> None:
    """The exact exit criterion: no SUBTITLE_GEOMETRY_UNKNOWN for a laid out cue."""

    _seed(database)
    created = _create_revision(database)
    persisted = created["subtitle_revision"]["cues_json"]
    with database.transaction() as connection:
        report = ExplainerQualityService(ExplainerRepository(connection)).run_subtitle_check(
            project_id=PROJECT_ID,
            video_id=VIDEO_ID,
            edition_id=EDITION_ID,
            subject_hash="c" * 64,
            cues=persisted,
            # The 16:9 default safe area in pixels: 5% side/top, 8% bottom.
            safe_area={"left_px": 96, "top_px": 54, "right_px": 1824, "bottom_px": 994},
            fps_num=25,
            fps_den=1,
            total_frames=125,
            current_revision_id=str(created["subtitle_revision"]["id"]),
        )
    kinds = [str(issue["issue_kind"]) for issue in report["issues"]]
    assert "SUBTITLE_GEOMETRY_UNKNOWN" not in kinds
    assert "SUBTITLE_OVERFLOWS_SAFE_AREA" not in kinds
