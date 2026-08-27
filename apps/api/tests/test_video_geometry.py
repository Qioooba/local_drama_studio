from __future__ import annotations

import pytest

from local_drama.domain.errors import DomainRuleError
from local_drama.domain.video_geometry import h3_frame_count, h3_render_duration_ms, h3_resolution


def test_h3_geometry_uses_one_canonical_frame_grid() -> None:
    assert h3_frame_count(4.0) == 107
    assert h3_render_duration_ms(107) == 4458


def test_h3_geometry_resolves_verified_canvases() -> None:
    assert h3_resolution("9:16") == (480, 832)
    assert h3_resolution("16:9") == (864, 480)


def test_h3_geometry_rejects_unknown_aspect_ratio() -> None:
    with pytest.raises(DomainRuleError, match="分辨率"):
        h3_resolution("1:1")
