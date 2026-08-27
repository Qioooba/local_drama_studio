from __future__ import annotations

from collections.abc import Iterator
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import Literal
from urllib.parse import unquote

from fastapi import APIRouter, Request, Response
from fastapi.responses import FileResponse, StreamingResponse

from local_drama.api.schemas.g3 import KeyframeCandidateRequest, MediaImportRequest, MediaIntegrityRepairRequest
from local_drama.api.schemas.motion_controls import MotionControlRequest
from local_drama.api.uploading import receive_bounded_upload
from local_drama.application.contact_sheets import ContactSheetExportService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.media import AUDIO_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, MediaService
from local_drama.application.motion_controls import MotionControlService
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


@router.get("/episodes/{episode_id}/contact-sheet:download", operation_id="downloadEpisodeContactSheet")
async def download_episode_contact_sheet(episode_id: str, rel_path: str, request: Request) -> FileResponse:
    try:
        path = ContactSheetExportService(request.app.state.database, request.app.state.settings).download_archive(episode_id, rel_path)
        return FileResponse(path, media_type="application/zip", filename=path.name)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/media:import", status_code=201, operation_id="importMedia")
async def import_media(payload: MediaImportRequest, request: Request) -> dict[str, object]:
    try:
        media_service = service(request)
        if payload.media_kind == "IMAGE":
            source = Path(payload.source_path)
            if source.suffix.lower() not in IMAGE_EXTENSIONS:
                raise DomainRuleError("MEDIA_UPLOAD_TYPE_INVALID", "资产参考导入仅支持图片文件")
            if source.is_file() and source.stat().st_size > 25 * 1024 * 1024:
                raise DomainRuleError("MEDIA_UPLOAD_TOO_LARGE", "导入图片不能超过 25 MB")
            media_service.validate_image_upload(source)
        return {
            "media": media_service.import_file(
                payload.project_id,
                payload.source_path,
                purpose=payload.purpose,
                owner_type=payload.owner_type,
                owner_id=payload.owner_id,
                media_kind=payload.media_kind,
                stage=payload.stage,
                schedule_derivatives=True,
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/projects/{project_id}/media-catalogue", operation_id="listProjectMediaCatalogue")
async def list_project_media_catalogue(
    project_id: str,
    request: Request,
    q: str = "",
    media_kind: str | None = None,
    limit: int = 40,
) -> dict[str, object]:
    try:
        return {"items": service(request).catalogue(project_id, query=q, media_kind=media_kind, limit=limit)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/media:upload", status_code=201, operation_id="uploadProjectMedia")
async def upload_project_media(project_id: str, request: Request) -> dict[str, object]:
    """Register one bounded browser upload for images, videos, or audio without accepting a client path."""
    filename = unquote(request.headers.get("x-file-name", "upload.bin"))
    safe_filename = Path(filename).name[:180] or "upload.bin"
    suffix = Path(safe_filename).suffix.lower()
    
    settings = request.app.state.settings
    if suffix in IMAGE_EXTENSIONS:
        media_kind = "IMAGE"
        purpose = "ASSET_REFERENCE"
        maximum_bytes = settings.uploads.image_mb * 1024 * 1024
    elif suffix in VIDEO_EXTENSIONS:
        media_kind = "VIDEO"
        purpose = "VIDEO_REFERENCE"
        maximum_bytes = settings.uploads.video_mb * 1024 * 1024
    elif suffix in AUDIO_EXTENSIONS:
        media_kind = "AUDIO"
        purpose = "AUDIO_REFERENCE"
        maximum_bytes = settings.uploads.audio_mb * 1024 * 1024
    else:
        raise api_error_from_domain(DomainRuleError("MEDIA_UPLOAD_TYPE_INVALID", "媒体上传仅支持常见图片、视频或音频文件"))

    try:
        async with receive_bounded_upload(
            request,
            work_group="picker-uploads",
            allowed_suffixes=frozenset(IMAGE_EXTENSIONS | VIDEO_EXTENSIONS | AUDIO_EXTENSIONS),
            maximum_bytes=maximum_bytes,
            default_filename="upload.bin",
            error_prefix="MEDIA_UPLOAD",
        ) as (temporary, _safe_filename, _received_bytes):
            media_service = service(request)
            if media_kind == "IMAGE":
                media_service.validate_image_upload(temporary)
            imported = media_service.import_file(
                project_id,
                temporary,
                purpose=purpose,
                owner_type="PROJECT",
                owner_id=project_id,
                media_kind=media_kind,
                stage="IMPORTED",
                schedule_derivatives=True,
            )
            return {"media": imported}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-versions/{media_version_id}", operation_id="getMediaVersion")
async def get_media_version(media_version_id: str, request: Request) -> dict[str, object]:
    try:
        return {"media_version": service(request).get_version(media_version_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/media-versions/{media_version_id}:repair-integrity", operation_id="repairMediaIntegrity")
async def repair_media_integrity(
    media_version_id: str, payload: MediaIntegrityRepairRequest, request: Request,
) -> dict[str, object]:
    try:
        return {"media_version": service(request).repair_content_integrity(
            media_version_id, expected_revision=payload.expected_revision,
        )}
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


@router.post("/media-versions/{source_media_version_id}/motion-masks", status_code=201, operation_id="createMotionControl")
async def create_motion_control(
    source_media_version_id: str, payload: MotionControlRequest, request: Request
) -> dict[str, object]:
    """Persist a motion brush/mask/vector/keyframe without modifying source media."""
    try:
        return {
            "motion_control": MotionControlService(request.app.state.database, request.app.state.settings).create(
                source_media_version_id,
                payload.model_dump(mode="json"),
            )
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-versions/{source_media_version_id}/motion-masks", operation_id="listMotionControls")
async def list_motion_controls(source_media_version_id: str, request: Request) -> dict[str, object]:
    try:
        return {
            "items": MotionControlService(request.app.state.database, request.app.state.settings).list_for_source(
                source_media_version_id
            )
        }
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


def _if_range_matches(value: str, *, etag: str | None, path: Path) -> bool:
    """Return whether an If-Range validator still identifies this file.

    Media versions expose a strong SHA-256 ETag.  A date validator is also
    accepted for browser clients that do not retain the ETag; HTTP dates have
    one-second precision, so compare truncated mtime values.
    """
    candidate = value.strip()
    if not candidate:
        return False
    if candidate.startswith("W/") or candidate.startswith('"'):
        return etag is not None and candidate == etag
    try:
        parsed = parsedate_to_datetime(candidate)
    except (TypeError, ValueError, OverflowError):
        return False
    if parsed.tzinfo is None:
        return False
    return int(path.stat().st_mtime) <= int(parsed.timestamp())


def _range_headers(request: Request, path: Path, *, etag: str | None = None) -> tuple[int, int, int] | Response:
    stat = path.stat()
    size = stat.st_size
    value = request.headers.get("range")
    if not value:
        return 0, size - 1, 200
    if_range = request.headers.get("if-range")
    if if_range and not _if_range_matches(if_range, etag=etag, path=path):
        # RFC 9110: a failed If-Range validator causes the Range to be
        # ignored, yielding the complete representation (200), not 416.
        return 0, size - 1, 200
    if not value.startswith("bytes=") or "," in value:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    raw = value[6:].split("-", 1)
    if len(raw) != 2:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
    try:
        if not raw[0]:
            # Suffix-byte-range-spec: bytes=-N means the final N bytes, not
            # bytes from offset zero through N.
            suffix_length = int(raw[1])
            if suffix_length <= 0:
                raise ValueError("invalid suffix range")
            start = max(0, size - suffix_length)
            end = size - 1
        else:
            start = int(raw[0])
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
        # MIME is an independent safety net for callers that imported a real
        # image with an incorrect semantic media_kind.  An image must never
        # fall through to the raw range endpoint just because its label was
        # misclassified; the only visual read surface is the derived cache.
        if str(item["media_kind"]) == "IMAGE" or str(item["mime_type"]).lower().startswith("image/"):
            raise DomainRuleError(
                "IMAGE_CONTENT_REQUIRES_THUMBNAIL",
                "图片读取必须使用派生缩略图接口，不直接读取原图",
                {"thumbnail_path": f"/api/v1/media-versions/{media_version_id}/thumbnail?size=small&frame=poster"},
                suggested_action="改用 /thumbnail?size=small&frame=poster",
            )
        return _range_response(request, path, str(item["mime_type"]), etag=f'"{item["sha256"]}"', head=head)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


def _range_response(request: Request, path: Path, mime_type: str, *, etag: str, head: bool = False) -> Response:
        selected = _range_headers(request, path, etag=etag)
        if isinstance(selected, Response):
            return selected
        start, end, status = selected
        stat = path.stat()
        headers = {
            "Accept-Ranges": "bytes",
            "Content-Length": str(end - start + 1),
            "Content-Type": mime_type,
            "ETag": etag,
            "Last-Modified": formatdate(stat.st_mtime, usegmt=True),
        }
        if status == 206:
            headers["Content-Range"] = f"bytes {start}-{end}/{stat.st_size}"
        if head:
            return Response(status_code=status, headers=headers)
        return StreamingResponse(_stream(path, start, end), status_code=status, headers=headers, media_type=mime_type)


@router.get("/media-versions/{media_version_id}/content", operation_id="getMediaContent")
async def get_content(media_version_id: str, request: Request) -> Response:
    return await _content(media_version_id, request)


@router.head("/media-versions/{media_version_id}/content", operation_id="headMediaContent")
async def head_content(media_version_id: str, request: Request) -> Response:
    return await _content(media_version_id, request, head=True)


async def _proxy(media_version_id: str, request: Request, head: bool = False) -> Response:
    try:
        path, mime = service(request).proxy(media_version_id, materialize=False)
        return _range_response(request, path, mime, etag=f'"proxy-{path.stem}"', head=head)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-versions/{media_version_id}/proxy", operation_id="getMediaProxy")
async def get_proxy(media_version_id: str, request: Request) -> Response:
    return await _proxy(media_version_id, request)


@router.head("/media-versions/{media_version_id}/proxy", operation_id="headMediaProxy")
async def head_proxy(media_version_id: str, request: Request) -> Response:
    return await _proxy(media_version_id, request, head=True)


@router.get("/media-versions/{media_version_id}/thumbnail", operation_id="getMediaThumbnail")
async def thumbnail(media_version_id: str, request: Request, size: str = "small", frame: str = "poster") -> FileResponse:
    try:
        path, mime = service(request).thumbnail(media_version_id, size, frame, materialize=False)
        return FileResponse(path, media_type=mime)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-versions/{media_version_id}/filmstrip", operation_id="getMediaFilmstrip")
async def filmstrip(media_version_id: str, request: Request) -> FileResponse:
    try:
        path, mime = service(request).filmstrip(media_version_id, materialize=False)
        return FileResponse(path, media_type=mime)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/media-versions/{media_version_id}/waveform", operation_id="getMediaWaveform")
async def waveform(media_version_id: str, request: Request) -> FileResponse:
    try:
        path, mime = service(request).waveform(media_version_id, materialize=False)
        return FileResponse(path, media_type=mime)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/media-versions/{media_version_id}/derivatives:submit", status_code=202, operation_id="submitMediaDerivative")
async def submit_media_derivative(
    media_version_id: str,
    request: Request,
    kind: Literal["THUMBNAIL", "FILMSTRIP", "WAVEFORM", "PROXY"],
    size: str = "small",
    frame: str = "poster",
) -> dict[str, object]:
    try:
        return {"job": service(request).submit_derivative(media_version_id, kind, size=size, frame=frame)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/projects/{project_id}/media-derivatives:backfill", status_code=202, operation_id="backfillProjectMediaDerivatives")
async def backfill_project_media_derivatives(
    project_id: str, request: Request, cursor: int = 0, limit: int = 50,
) -> dict[str, object]:
    try:
        return {"backfill": service(request).backfill_project_derivatives(project_id, cursor=cursor, limit=limit)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
