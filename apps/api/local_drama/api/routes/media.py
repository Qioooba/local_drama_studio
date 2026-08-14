from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from local_drama.api.schemas.g3 import KeyframeCandidateRequest, MediaImportRequest
from local_drama.application.contact_sheets import ContactSheetExportService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.media import MediaService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(tags=["media"])


def service(request: Request) -> MediaService:
    return MediaService(request.app.state.database, request.app.state.settings)


@router.post("/episodes/{episode_id}/contact-sheet:export", operation_id="exportEpisodeContactSheet")
async def export_episode_contact_sheet(episode_id: str, request: Request) -> dict[str, object]:
    try:
        result = ContactSheetExportService(request.app.state.database, request.app.state.settings).export_episode(episode_id)
        return {"export": result}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/media:import", status_code=201, operation_id="importMedia")
async def import_media(payload: MediaImportRequest, request: Request) -> dict[str, object]:
    try:
        return {
            "media": service(request).import_file(
                payload.project_id,
                payload.source_path,
                purpose=payload.purpose,
                owner_type=payload.owner_type,
                owner_id=payload.owner_id,
                media_kind=payload.media_kind,
                stage=payload.stage,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-versions/{media_version_id}", operation_id="getMediaVersion")
async def get_media_version(media_version_id: str, request: Request) -> dict[str, object]:
    try:
        return {"media_version": service(request).get_version(media_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/media-versions/{media_version_id}:create-keyframe-candidate", status_code=201, operation_id="createKeyframeCandidate")
async def create_keyframe_candidate(
    media_version_id: str, payload: KeyframeCandidateRequest, request: Request
) -> dict[str, object]:
    try:
        return {"media": service(request).create_keyframe_candidate(media_version_id, payload.shot_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-assets/{media_asset_id}", operation_id="getMediaAsset")
async def get_media_asset(media_asset_id: str, request: Request) -> dict[str, object]:
    try:
        return {"media_asset": service(request).get_asset(media_asset_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-assets/{media_asset_id}/versions", operation_id="listMediaAssetVersions")
async def list_media_asset_versions(media_asset_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_asset_versions(media_asset_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/media-assets/{media_asset_id}:clear-selection", operation_id="clearMediaSelection")
async def clear_media_selection(media_asset_id: str, request: Request) -> dict[str, object]:
    try:
        return {"media_asset": service(request).clear_selection(media_asset_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


def _range_headers(request: Request, path: Path) -> tuple[int, int, int] | Response:
    size = path.stat().st_size
    value = request.headers.get("range")
    if not value:
        return 0, size - 1, 200
    if not value.startswith("bytes=") or "," in value:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    raw = value[6:].split("-", 1)
    try:
        start = int(raw[0]) if raw[0] else max(0, size - int(raw[1]))
        end = int(raw[1]) if len(raw) > 1 and raw[1] else size - 1
    except (ValueError, IndexError):
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    if start < 0 or end < start or start >= size:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    return start, min(end, size - 1), 206


def _stream(path: Path, start: int, end: int) -> Iterator[bytes]:
    with path.open("rb") as source:
        source.seek(start)
        remaining = end - start + 1
        while remaining:
            chunk = source.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


async def _content(media_version_id: str, request: Request, head: bool = False) -> Response:
    try:
        item, path = service(request).content_path(media_version_id)
        selected = _range_headers(request, path)
        if isinstance(selected, Response):
            return selected
        start, end, status = selected
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Content-Type": item["mime_type"]}
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{path.stat().st_size}"
        if head:
            return Response(status_code=status, headers=headers)
        return StreamingResponse(_stream(path, start, end), status_code=status, headers=headers, media_type=item["mime_type"])
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-versions/{media_version_id}/content", operation_id="getMediaContent")
async def get_content(media_version_id: str, request: Request) -> Response:
    return await _content(media_version_id, request)


@router.head("/media-versions/{media_version_id}/content", operation_id="headMediaContent")
async def head_content(media_version_id: str, request: Request) -> Response:
    return await _content(media_version_id, request, head=True)


@router.get("/media-versions/{media_version_id}/thumbnail", operation_id="getMediaThumbnail")
async def thumbnail(media_version_id: str, request: Request, size: str = "small", frame: str = "poster") -> FileResponse:
    try:
        path, mime = service(request).thumbnail(media_version_id, size, frame)
        return FileResponse(path, media_type=mime)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-versions/{media_version_id}/filmstrip", operation_id="getMediaFilmstrip")
async def filmstrip(media_version_id: str, request: Request) -> FileResponse:
    try:
        path, mime = service(request).filmstrip(media_version_id)
        return FileResponse(path, media_type=mime)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-versions/{media_version_id}/waveform", operation_id="getMediaWaveform")
async def waveform(media_version_id: str, request: Request) -> FileResponse:
    try:
        path, mime = service(request).waveform(media_version_id)
        return FileResponse(path, media_type=mime)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
