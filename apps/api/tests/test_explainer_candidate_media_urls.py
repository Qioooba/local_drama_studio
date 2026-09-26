"""A candidate card must point at a media URL the product will actually serve.

The media endpoint refuses to serve an image original on purpose
(``IMAGE_CONTENT_REQUIRES_THUMBNAIL``) and answers 409.  Every image candidate
therefore rendered "缩略图待生成" on the storyboard page even though its media
version was registered and readable through the derived cache — measured on a
real generated candidate: ``status=READY``, registered 1920×1088 PNG, and
``/content`` returning 409.  These tests pin the readable URL for each media kind.
"""

from __future__ import annotations

from typing import Any, Mapping

from local_drama.api.routes.explainers import _candidate_view

MEDIA_VERSION_ID = "mv-1"
ASSET_ID = "ma-1"


class _Repo:
    """Minimal repository stand-in: the view only needs two ``find`` lookups."""

    def __init__(self, media_kind: str) -> None:
        self.media_kind = media_kind

    def find(self, table: str, row_id: str) -> Mapping[str, Any] | None:
        if row_id != MEDIA_VERSION_ID and row_id != ASSET_ID:
            return None
        if table == "media_versions":
            return {"id": MEDIA_VERSION_ID, "media_asset_id": ASSET_ID, "duration_ms": 4200}
        if table == "media_assets":
            return {"id": ASSET_ID, "media_kind": self.media_kind}
        return None


def _view(media_kind: str) -> dict[str, Any]:
    return _candidate_view(
        _Repo(media_kind),  # type: ignore[arg-type]
        {"id": "c-1", "status": "READY", "media_version_id": MEDIA_VERSION_ID, "variant_no": 1},
    )


def test_an_image_candidate_uses_the_derived_thumbnail_not_the_refused_original() -> None:
    view = _view("IMAGE")
    assert view["thumbnail_url"] == f"/api/v1/media-versions/{MEDIA_VERSION_ID}/thumbnail?size=small&frame=poster"
    assert view["preview_url"] == f"/api/v1/media-versions/{MEDIA_VERSION_ID}/thumbnail?size=medium&frame=poster"
    assert "/content" not in str(view["thumbnail_url"])
    assert "/content" not in str(view["preview_url"])
    # The original stays available for callers that need the exact bytes.
    assert view["content_url"] == f"/api/v1/media-versions/{MEDIA_VERSION_ID}/content"


def test_a_video_candidate_keeps_stream_playback_and_a_poster_thumbnail() -> None:
    view = _view("VIDEO")
    assert view["thumbnail_url"] == f"/api/v1/media-versions/{MEDIA_VERSION_ID}/thumbnail?size=small&frame=poster"
    assert view["playback_url"] == f"/api/v1/media-versions/{MEDIA_VERSION_ID}/proxy"
    assert view["preview_url"] == f"/api/v1/media-versions/{MEDIA_VERSION_ID}/proxy"
    assert view["duration_ms"] == 4200


def test_a_candidate_without_media_registers_no_urls() -> None:
    view = _candidate_view(
        _Repo("IMAGE"),  # type: ignore[arg-type]
        {"id": "c-2", "status": "PENDING", "media_version_id": None, "variant_no": 1},
    )
    assert view["thumbnail_url"] is None
    assert view["preview_url"] is None
    assert view["playback_url"] is None
