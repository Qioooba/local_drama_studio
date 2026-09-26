"""The render canvas must follow the edition, capped by the real sources.

Measured defect this pins: the composition manifest was built without an explicit
size, so it fell back to ``AspectRatio.pixels`` — a module-level 854x480 proxy that
ignores both the edition and the configured generation height.  A 1080p edition
whose card clips are really 1920x1080 therefore delivered an 854x480 film, and the
documented "a later super-resolution step lifts it" path cannot run on a machine
without the ncnn model (see the upscale audit), so the detail was simply lost.
"""

from __future__ import annotations

from local_drama.application.explainers.production_pipeline import _render_canvas


def _canvas(width: int, height: int, *sources: int | None) -> tuple[int, int]:
    return _render_canvas(
        edition_width=width,
        edition_height=height,
        aspect_ratio="16:9" if width >= height else "9:16",
        source_heights=sources,
    )


def test_edition_canvas_is_used_when_the_sources_can_fill_it() -> None:
    assert _canvas(1920, 1080, 1080, 1080) == (1920, 1080)


def test_a_480p_run_keeps_its_proxy_canvas() -> None:
    """No regression for genuinely proxy-sized sources: never upscale in the renderer."""

    assert _canvas(854, 480, 480, 480) == (854, 480)
    # Even when the edition claims more than the sources can fill, the canvas is
    # reduced to the platform's own proxy geometry instead of inventing detail.
    assert _canvas(1920, 1080, 480) == (854, 480)


def test_the_canvas_keeps_the_edition_aspect_ratio_and_even_dimensions() -> None:
    width, height = _canvas(1920, 1080, 720)
    assert (width, height) == (1280, 720)
    assert width % 2 == 0 and height % 2 == 0
    assert _canvas(480, 854, 854) == (480, 854)


def test_an_unknown_source_size_still_renders_at_the_edition_canvas() -> None:
    assert _canvas(1920, 1080) == (1920, 1080)
    assert _canvas(1920, 1080, 0, None) == (1920, 1080)
