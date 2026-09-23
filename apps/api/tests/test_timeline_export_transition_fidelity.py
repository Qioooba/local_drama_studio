"""TS-06/TM-06: the exchange writers must not claim fidelity they do not have.

Reproduced defect on the audit snapshot, with a two-clip frozen timeline whose
second item is ``DISSOLVE + transition_duration_seconds=0.5``:

* OTIO parsed (OpenTimelineIO 0.18.1) as ``[Clip, Clip]`` — **zero** ``Transition``
  objects.  The "dissolve" was a ``LinearTimeWarp`` with ``time_scalar=1.0``, which
  in OTIO is a *speed change*, so every official adapter read the export as a hard
  cut.  The track was 4.0 s where the same revision's MP4 was 3.5 s.
* ``export_revision`` still returned ``losses=[]``.
* The Jianying draft wrote no transition and also reported ``losses=[]``, because
  the loss rule was global: "a positive transition duration exists" was taken to
  mean "this format preserved it".

These tests parse the real ``.otio`` with the official reader and assert the
official ``otio.schema.Transition`` objects, their in/out offsets and the timeline
duration.  The Jianying writer must keep reporting the transition as a loss until
it is implemented and verified in the target client.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.timeline import TimelineService
from local_drama.application.timeline_exports import TimelineExportService

otio = pytest.importorskip("opentimelineio", reason="the OTIO acceptance criterion is parsed by the official reader")

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real FFmpeg/FFprobe are required to create the source media",
)


def _clip(workspace, name: str, *, colour: str) -> Path:
    output = workspace.work_root / name
    subprocess.run(
        [
            workspace.ffmpeg_path, "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"color=c={colour}:s=160x90:r=24:d=2",
            "-pix_fmt", "yuv420p", "-an", "-y", str(output),
        ],
        check=True,
        capture_output=True,
    )
    return output


def _dissolve_timeline(workspace, database, code: str, *, duration_seconds: float | None = 0.5):
    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code=code, title=code, episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=4000,
        allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _clip(workspace, f"{code}-a.mp4", colour="navy"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _clip(workspace, f"{code}-b.mp4", colour="maroon"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    dissolve: dict[str, object] = {"transition_in": "DISSOLVE"}
    if duration_seconds is not None:
        dissolve["transition_duration_seconds"] = duration_seconds
    service = TimelineService(database, workspace)
    timeline = service.create_timeline_revision(
        str(episode["id"]),
        [
            {"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}},
            {"track_type": "VIDEO", "media_version_id": str(second["media_version_id"]), "start_us": 2_000_000, "end_us": 4_000_000, "parameters": dissolve},
        ],
        {"source": code},
    )
    return TimelineExportService(database, workspace), timeline, project


def _read_otio(export: dict[str, Any], project_root: Path):
    directory = project_root / str(export["rel_path"])
    otio_file = next(directory.glob("*.otio"))
    return otio.adapters.read_from_file(str(otio_file)), otio_file


def test_otio_contains_a_real_transition_with_offsets(workspace, database) -> None:
    """The audit's parse: ``isinstance(child, otio.schema.Transition)`` must hold."""

    service, timeline, project = _dissolve_timeline(workspace, database, "tm06_otio")
    project_root = workspace.projects_root / str(project["root_rel"])
    export = service.export_revision(str(timeline["id"]), "otio")
    timeline_object, otio_file = _read_otio(export, project_root)
    assert otio_file.suffix == ".otio"

    track = timeline_object.tracks[0]

    track = timeline_object.tracks[0]
    classes = [type(child).__name__ for child in track]
    assert classes == ["Clip", "Transition", "Clip"], classes
    transition = track[1]
    assert isinstance(transition, otio.schema.Transition)
    assert transition.transition_type == "SMPTE_Dissolve"
    # A 0.5 s dissolve at 24 fps gives each neighbour 6 frames of handle.
    assert transition.in_offset == otio.opentime.RationalTime(6, 24)
    assert transition.out_offset == otio.opentime.RationalTime(6, 24)

    # The official reader's own duration: the two clips contribute their visible
    # (handle-trimmed) picture and the transition contributes its window, which is
    # the rendered film's 3.5 s rather than the raw 4.0 s of source.
    print(
        "DBG",
        [
            (
                type(c).__name__,
                str(getattr(getattr(c, "source_range", None), "start_time", None)),
                str(getattr(getattr(c, "source_range", None), "duration", None)),
                str(getattr(c, "in_offset", None)),
                str(getattr(c, "out_offset", None)),
            )
            for c in track
        ],
        track.duration(),
    )
    assert timeline_object.duration() == otio.opentime.RationalTime(84, 24), timeline_object.duration()
    assert abs(timeline_object.duration().to_seconds() - 3.5) <= 1e-9
    first, last = track[0], track[2]
    assert first.source_range.duration == otio.opentime.RationalTime(42, 24)
    assert last.source_range.start_time == otio.opentime.RationalTime(6, 24)
    assert last.source_range.duration == otio.opentime.RationalTime(42, 24)

    # The dissolve is no longer smuggled in as a speed change.
    for child in track:
        assert not isinstance(child, otio.schema.LinearTimeWarp)
        if isinstance(child, otio.schema.Clip):
            assert [type(effect).__name__ for effect in child.effects] == []

    manifest = json.loads((project_root / str(export["rel_path"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["losses"] == []
    assert manifest["writer_version"] == 4


def test_a_rule_derived_dissolve_is_exported_even_without_an_authored_number(workspace, database) -> None:
    """The export must read the frozen plan, not only the item's own parameter.

    A timeline whose dissolve comes from the editor's rule carries no
    ``transition_duration_seconds``; the old writer therefore exported it as a hard
    cut even though the renderer really dissolves.
    """

    service, timeline, project = _dissolve_timeline(workspace, database, "tm06_plan", duration_seconds=None)
    project_root = workspace.projects_root / str(project["root_rel"])
    export = service.export_revision(str(timeline["id"]), "otio")
    timeline_object, _ = _read_otio(export, project_root)
    track = timeline_object.tracks[0]
    assert [type(child).__name__ for child in track] == ["Clip", "Transition", "Clip"]
    assert track[1].transition_type == "SMPTE_Dissolve"
    assert timeline_object.duration() == otio.opentime.RationalTime(84, 24)


def test_the_exported_duration_matches_the_rendered_film(workspace, database) -> None:
    """The exact disagreement the audit measured: OTIO 4.0 s versus MP4 3.5 s."""

    service, timeline, project = _dissolve_timeline(workspace, database, "tm06_len")
    project_root = workspace.projects_root / str(project["root_rel"])
    export = service.export_revision(str(timeline["id"]), "otio")
    timeline_object, _ = _read_otio(export, project_root)

    render = TimelineService(database, workspace).render_episode(str(timeline["id"]))
    assert render["status"] == "VERIFIED"
    rendered = render["probe"]["duration_ms"] / 1000
    assert abs(timeline_object.duration().to_seconds() - rendered) <= 0.06, (
        timeline_object.duration().to_seconds(),
        rendered,
    )


def test_hard_cuts_have_no_transition_object(workspace, database) -> None:
    """The control: a cut-only timeline keeps exactly two clips."""

    projects = ProjectService(database, workspace.projects_root)
    project = projects.create_project(
        code="tm06_cut", title="cut", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=4000, allow_unconfigured_capabilities=True,
    )
    episode = projects.list_episodes(str(projects.list_seasons(str(project["id"]))[0]["id"]))[0]
    media = MediaService(database, workspace)
    first = media.import_file(str(project["id"]), _clip(workspace, "tm06-cut-a.mp4", colour="navy"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    second = media.import_file(str(project["id"]), _clip(workspace, "tm06-cut-b.mp4", colour="maroon"), purpose="SHOT_VIDEO", media_kind="VIDEO")
    timeline = TimelineService(database, workspace).create_timeline_revision(
        str(episode["id"]),
        [
            {"track_type": "VIDEO", "media_version_id": str(first["media_version_id"]), "start_us": 0, "end_us": 2_000_000, "parameters": {"transition_in": "CUT"}},
            {"track_type": "VIDEO", "media_version_id": str(second["media_version_id"]), "start_us": 2_000_000, "end_us": 4_000_000, "parameters": {"transition_in": "CUT"}},
        ],
        {"source": "tm06_cut"},
    )
    service = TimelineExportService(database, workspace)
    project_root = workspace.projects_root / str(project["root_rel"])
    export = service.export_revision(str(timeline["id"]), "otio")
    timeline_object, _ = _read_otio(export, project_root)
    track = timeline_object.tracks[0]
    assert [type(child).__name__ for child in track] == ["Clip", "Clip"]
    assert timeline_object.duration() == otio.opentime.RationalTime(96, 24)


def test_jianying_still_reports_the_transition_as_a_loss(workspace, database) -> None:
    """The writer has no verified transition support, so it must not claim any.

    The old rule made the OTIO writer's newly-claimed capability cover the Jianying
    writer too, and the draft was reported as loss-free.
    """

    service, timeline, project = _dissolve_timeline(workspace, database, "tm06_jy")
    project_root = workspace.projects_root / str(project["root_rel"])
    export = service.export_revision(str(timeline["id"]), "jianying")
    assert "jianying-v" in str(export["rel_path"])
    manifest = json.loads((project_root / str(export["rel_path"]) / "manifest.json").read_text(encoding="utf-8"))
    features = {str(loss["feature"]) for loss in manifest["losses"]}
    assert "TRANSITION_DISSOLVE" in features, manifest["losses"]
    transition_losses = [loss for loss in manifest["losses"] if loss["feature"] == "TRANSITION_DISSOLVE"]
    assert transition_losses[0]["format"] == "jianying"
    assert transition_losses[0]["value"]["duration_seconds"] == 0.5
    assert transition_losses[0]["recoverable_from_manifest"] is True
    assert manifest["best_effort"] is True
    assert manifest["writer_version"] == 4
