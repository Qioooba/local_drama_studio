"""MED-09: OTIO/EDL exports must declare what they drop instead of doing it silently.

The original defect: the exported OTIO had ``effects=[]`` on every clip and every
EDL event was a hard ``C``, so a DISSOLVE plus a per-track gain and fades vanished
without a single warning.  These tests assert the correct behaviour: transitions
that the format can carry are encoded, and everything else is listed in an explicit
loss report that the caller can show before the user trusts the export.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.application.timeline_exports import TimelineExportService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="real FFmpeg is required to import the fixture media",
)


def _video(workspace, name: str) -> Path:
    output = workspace.work_root / name
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


def _audio(workspace, name: str) -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-ar", "48000", "-ac", "1", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _fixture(workspace, database, code: str, *, track_type: str = "BGM", **audio_parameters):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=8000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _video(workspace, f"{code}-a.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _video(workspace, f"{code}-b.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    music = media.import_file(str(project["id"]), _audio(workspace, f"{code}-music.wav"), purpose="AUDIO", media_kind="AUDIO")

    service = TimelineService(database, workspace)
    items = [
        {"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}},
        {
            "track_type": "VIDEO",
            "media_version_id": str(second["media_version_id"]),
            "start_us": 2_000_000,
            "end_us": 4_000_000,
            "parameters": {"transition_in": "DISSOLVE", "transition_duration_seconds": 0.5},
        },
        {
            "track_type": track_type,
            "media_version_id": str(music["media_version_id"]),
            "start_us": 0,
            "end_us": 2_000_000,
            "parameters": {"gain_db": -24.0, "fade_in_us": 250_000, "fade_out_us": 250_000, **audio_parameters},
        },
    ]
    timeline = service.create_timeline_revision(str(episode["id"]), items, {"source": code})
    return project, service, timeline


def _export_dir(workspace, project, exported) -> Path:
    return workspace.projects_root / str(project["root_rel"]) / str(exported["rel_path"])


def test_export_result_and_manifest_report_every_dropped_effect(workspace, database) -> None:
    """The loss report must name the affected item and the lost feature."""

    project, _service, timeline = _fixture(workspace, database, "med09_losses")
    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="standard")

    losses = exported["losses"]
    assert losses, "an export with gain/fades must report losses"
    features = {str(item["feature"]) for item in losses}
    assert {"AUDIO_GAIN_DB", "AUDIO_FADE_IN_US", "AUDIO_FADE_OUT_US"} <= features
    for item in losses:
        assert item["timeline_item_id"]
        assert item["rel_path"]
        assert item["reason"]
        assert item["label"]

    assert "AUDIO_GAIN_DB" in exported["unsupported_features"]
    assert exported["fidelity"] == "BASE_EDITING_INTERCHANGE_WITH_LOSSES"

    # The same report is persisted in the export manifest for later audit.
    manifest = json.loads((_export_dir(workspace, project, exported) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["losses"] == losses
    assert manifest["unsupported_features"] == exported["unsupported_features"]
    # The authored parameters are preserved so a human can restore them.
    assert manifest["localdrama_item_parameters"]


def test_transition_is_encoded_in_otio_and_edl_not_silently_dropped(workspace, database) -> None:
    """A DISSOLVE that the format CAN express must be encoded, not listed as loss.

    TM-06 replaced the old ``LinearTimeWarp`` clip effect (OTIO's *speed change*
    object, which every official reader took for a hard cut) with a real
    ``Transition`` between the two clips.
    """

    project, _service, timeline = _fixture(workspace, database, "med09_transition")
    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="standard")
    export_dir = _export_dir(workspace, project, exported)

    otio_file = next(export_dir / str(item["rel_path"]) for item in exported["files"] if str(item["rel_path"]).endswith(".otio"))
    timeline_payload = json.loads(otio_file.read_text(encoding="utf-8"))
    video_track = next(track for track in timeline_payload["tracks"]["children"] if track["name"] == "VIDEO")
    kinds = [child["OTIO_SCHEMA"] for child in video_track["children"]]
    assert kinds == ["Clip.2", "Transition.1", "Clip.2"], kinds
    transition = video_track["children"][1]
    assert transition["transition_type"] == "SMPTE_Dissolve"
    assert transition["in_offset"]["value"] == 6  # 0.5 s at 24 fps, half per neighbour
    assert transition["out_offset"]["value"] == 6
    assert transition["metadata"]["localdrama_transition"] == "DISSOLVE"
    assert transition["metadata"]["duration_seconds"] == 0.5
    # Neither clip carries a speed-change effect any more, and the authored
    # parameters still travel with the clip.
    dissolve_clip = video_track["children"][2]
    assert [effect["OTIO_SCHEMA"] for effect in dissolve_clip["effects"]] == []
    assert dissolve_clip["metadata"]["localdrama_item_parameters"]["transition_in"] == "DISSOLVE"

    edl_file = next(export_dir / str(item["rel_path"]) for item in exported["files"] if str(item["rel_path"]).endswith(".edl"))
    edl_text = edl_file.read_text(encoding="utf-8")
    # The second event is a dissolve with a frame count, not a hard CUT.
    dissolve_lines = [line for line in edl_text.splitlines() if " D    " in line]
    assert dissolve_lines, f"the EDL must express the dissolve, got:\n{edl_text}"
    assert "012" in dissolve_lines[0]  # 0.5 s at 24 fps
    assert "* LOCALDRAMA TRANSITION: DISSOLVE" in edl_text

    # An expressible transition is not a loss.
    assert "TRANSITION_DISSOLVE" not in {str(item["feature"]) for item in exported["losses"]}


def test_a_transition_the_writer_cannot_encode_is_reported_as_a_loss(workspace, database) -> None:
    """A transition the format cannot express faithfully must be listed.

    TM-06: the export now reads the frozen plan rather than the item's authored
    number, so a DISSOLVE with no explicit duration IS encoded for OTIO.  What a
    writer genuinely cannot carry — here, a FADE, which OTIO has no distinct object
    for — must still be listed instead of being silently written as something else.
    """

    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="med09_noduration", title="med09 noduration", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=8000, allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _video(workspace, "med09-nd-a.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _video(workspace, "med09-nd-b.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [
            {"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}},
            # FADE has no dedicated OTIO object: it must not masquerade as a dissolve.
            {"track_type": "VIDEO", "media_version_id": str(second["media_version_id"]), "start_us": 2_000_000, "end_us": 4_000_000, "parameters": {"transition_in": "FADE"}},
        ],
        {"source": "med09_noduration"},
    )
    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="standard")
    features = {str(item["feature"]) for item in exported["losses"]}
    assert "TRANSITION_FADE" in features
    assert "TRANSITION_DISSOLVE" not in features
    assert exported["fidelity"] == "BASE_EDITING_INTERCHANGE_WITH_LOSSES"


def test_a_rule_derived_dissolve_is_encoded_not_listed_as_a_loss(workspace, database) -> None:
    """The audit's own fixture: a DISSOLVE with no authored duration is expressible."""

    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="med09_plan_dissolve", title="plan dissolve", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=8000, allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _video(workspace, "med09-pd-a.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _video(workspace, "med09-pd-b.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [
            {"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}},
            {"track_type": "VIDEO", "media_version_id": str(second["media_version_id"]), "start_us": 2_000_000, "end_us": 4_000_000, "parameters": {"transition_in": "DISSOLVE"}},
        ],
        {"source": "med09_plan_dissolve"},
    )
    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="standard")
    assert "TRANSITION_DISSOLVE" not in {str(item["feature"]) for item in exported["losses"]}
    payload = json.loads((_export_dir(workspace, project, exported) / str(exported["files"][0]["rel_path"])).read_text(encoding="utf-8"))
    video_track = next(track for track in payload["tracks"]["children"] if track["name"] == "VIDEO")
    assert [child["OTIO_SCHEMA"] for child in video_track["children"]] == ["Clip.2", "Transition.1", "Clip.2"]


def test_export_without_effects_reports_an_empty_loss_list(workspace, database) -> None:
    """A clean CUT-only export must not claim losses it does not have."""

    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="med09_clean", title="med09 clean", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=8000, allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    media = MediaService(database, workspace)
    source = media.import_file(str(project["id"]), _video(workspace, "med09-clean.mp4"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [{"track_type": "VIDEO", "media_version_id": str(source["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}}],
        {"source": "med09-clean"},
    )
    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="standard")
    assert exported["losses"] == []
    assert exported["unsupported_features"] == []
    assert exported["fidelity"] == "BASE_EDITING_INTERCHANGE"


def test_jianying_export_keeps_its_best_effort_boundary_and_reports_losses(workspace, database) -> None:
    """剪映 stays best-effort, but its manifest must still list what it drops."""

    project, _service, timeline = _fixture(workspace, database, "med09_jianying")
    exported = TimelineExportService(database, workspace).export_revision(str(timeline["id"]), format="jianying")
    manifest = json.loads((_export_dir(workspace, project, exported) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["best_effort"] is True
    assert manifest["media_copy"] == "BUNDLED"
    assert manifest["losses"] == exported["losses"]
    assert {str(item["feature"]) for item in exported["losses"]} >= {"AUDIO_GAIN_DB"}
