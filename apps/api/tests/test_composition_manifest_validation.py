"""The composition manifest validator, and the render step that must call it.

``validate_manifest`` existed but nothing called it: the explainer render went
straight from ``build_manifest`` to FFmpeg, so a manifest whose audio reached past
the end of the film was silently truncated by the mixer instead of being refused
(design §4.2 names manifest → validation → atomic render; matrix R08 requires
out-of-range audio to block).

These tests pin the validator's own behaviour.  The render step's call into it is
guarded separately, because building a full render handler fixture would test the
fixture more than the wiring.
"""

from __future__ import annotations

from pathlib import Path

from local_drama.application.composition.manifest import (
    AspectRatio,
    ManifestChunkSpec,
    Ratio,
    build_manifest,
    manifest_clip,
)
from local_drama.application.composition.validation import (
    CODE_AUDIO_OUTSIDE_FILM,
    CODE_UNDECLARED_SILENCE,
    validate_manifest,
)

FPS_25 = Ratio(25, 1)
RATE = 48_000
SHA_A = "a" * 64


def _manifest(*, total_frames: int, clips: list[object], declared: list[dict] | None = None) -> object:
    meta = {
        "declared_silence": declared or [],
    }
    return build_manifest(
        edition_id="ed-1",
        composition_revision_id="cr-1",
        aspect_ratio=AspectRatio("16:9"),
        fps=FPS_25,
        total_frames=total_frames,
        audio_sample_rate_hz=RATE,
        clips=clips,
        chunks=[ManifestChunkSpec(chunk_no=0, start_frame=0, end_frame_exclusive=total_frames)],
        meta=meta,
        validate_structure=False,
    )


def _media(media_version_id: str) -> dict[str, dict[str, object]]:
    return {
        media_version_id: {
            "sha256": SHA_A,
            "duration_ms": 5_000,
            "audio_sample_rate_hz": RATE,
            "integrity_status": "VERIFIED",
        }
    }


def _voice(*, end_frame: int, sample_end: int, sample_start: int = 0, clip_id: str = "n-0") -> object:
    return manifest_clip(
        clip_id=clip_id,
        track="NARRATION",
        item_kind="AUDIO_CLIP",
        start_frame=0,
        end_frame_exclusive=end_frame,
        fps=FPS_25,
        sample_rate_hz=RATE,
        media_version_id="mv-a",
        media_sha256=SHA_A,
        source_in_us=0,
        source_out_us=2_000_000,
        sample_start=sample_start,
        sample_end_exclusive=sample_end,
        narration_segment_id="seg-1",
    )


def _codes(report: object) -> list[str]:
    return [finding.code for finding in report.blockers]


def test_audio_placed_past_the_end_of_the_film_is_a_blocker() -> None:
    """R08: an out-of-range placement must be refused, not silently truncated."""

    # A 2 s film (96 000 samples) whose narration claims to run to 2.5 s.
    manifest = _manifest(total_frames=50, clips=[_voice(end_frame=75, sample_end=120_000)])
    report = validate_manifest(manifest, media_lookup=_media("mv-a"))
    assert CODE_AUDIO_OUTSIDE_FILM in _codes(report)
    details = [
        finding.details
        for finding in report.blockers
        if finding.code == CODE_AUDIO_OUTSIDE_FILM
    ]
    assert any(item.get("end_frame_exclusive") == 75 for item in details)
    assert any(item.get("sample_end_exclusive") == 120_000 for item in details)


def test_audio_inside_the_film_has_no_out_of_range_finding() -> None:
    """The check must not fire on a consistent manifest."""

    manifest = _manifest(total_frames=50, clips=[_voice(end_frame=50, sample_end=96_000)])
    report = validate_manifest(manifest, media_lookup=_media("mv-a"))
    assert CODE_AUDIO_OUTSIDE_FILM not in _codes(report)


def test_an_effect_declared_past_the_end_is_blocked() -> None:
    """A bed entry outside the film is the case that used to vanish silently."""

    effect = manifest_clip(
        clip_id="s-0",
        track="SFX",
        item_kind="AUDIO_CLIP",
        start_frame=25,
        end_frame_exclusive=75,
        fps=FPS_25,
        sample_rate_hz=RATE,
        media_version_id="mv-a",
        media_sha256=SHA_A,
        source_in_us=0,
        source_out_us=200_000,
        sample_start=48_000,
        sample_end_exclusive=57_600,
    )
    manifest = _manifest(
        total_frames=50,
        clips=[
            _voice(end_frame=50, sample_end=96_000),
            effect,
        ],
        declared=[{"start_frame": 50, "end_frame_exclusive": 50, "reason": "tail"}],
    )
    report = validate_manifest(manifest, media_lookup=_media("mv-a"))
    assert CODE_AUDIO_OUTSIDE_FILM in _codes(report)


def test_undeclared_silence_on_a_bed_track_is_still_a_blocker() -> None:
    """Existing rule kept honest: audio silence must be declared."""

    bed = manifest_clip(
        clip_id="b-0",
        track="BGM",
        item_kind="AUDIO_CLIP",
        start_frame=0,
        end_frame_exclusive=25,
        fps=FPS_25,
        sample_rate_hz=RATE,
        media_version_id="mv-a",
        media_sha256=SHA_A,
        source_in_us=0,
        source_out_us=1_000_000,
        sample_start=0,
        sample_end_exclusive=48_000,
    )
    manifest = _manifest(total_frames=50, clips=[_voice(end_frame=50, sample_end=96_000), bed])
    report = validate_manifest(manifest, media_lookup=_media("mv-a"))
    assert CODE_UNDECLARED_SILENCE in _codes(report)


def test_a_declared_bed_gap_is_accepted() -> None:
    bed = manifest_clip(
        clip_id="b-0",
        track="BGM",
        item_kind="AUDIO_CLIP",
        start_frame=0,
        end_frame_exclusive=25,
        fps=FPS_25,
        sample_rate_hz=RATE,
        media_version_id="mv-a",
        media_sha256=SHA_A,
        source_in_us=0,
        source_out_us=1_000_000,
        sample_start=0,
        sample_end_exclusive=48_000,
    )
    manifest = _manifest(
        total_frames=50,
        clips=[_voice(end_frame=50, sample_end=96_000), bed],
        declared=[{"start_frame": 25, "end_frame_exclusive": 50, "reason": "音乐在结尾淡出"}],
    )
    report = validate_manifest(manifest, media_lookup=_media("mv-a"))
    assert CODE_UNDECLARED_SILENCE not in _codes(report)
    assert CODE_AUDIO_OUTSIDE_FILM not in _codes(report)


def test_the_render_step_actually_calls_the_validator() -> None:
    """A wiring guard, not behavioural proof.

    The module held a full validator that no production path invoked; this pins the
    call so it cannot be dropped again while the render step keeps rendering.
    """

    source = (
        Path(__file__).resolve().parents[1]
        / "local_drama"
        / "application"
        / "explainers"
        / "production_pipeline.py"
    ).read_text(encoding="utf-8")
    assert "from local_drama.application.composition.validation import validate_manifest" in source
    assert "validation = validate_manifest(manifest, media_lookup=media_lookup)" in source
    assert "MANIFEST_VALIDATION_BLOCKED" in source
