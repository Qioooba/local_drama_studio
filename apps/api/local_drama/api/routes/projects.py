from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from fastapi import APIRouter, Header, Request

from local_drama.api.schemas.projects import (
    EpisodeSceneRangeRequest,
    ProjectCreateRequest,
    ProjectPackageDryRunRequest,
    ProjectTemplateCopyRequest,
    ProjectUpdateRequest,
    SceneCreateRequest,
    ShotCreateRequest,
    ShotRevisionRequest,
    StoryboardBatchCommitRequest,
    StoryboardBatchPlanRequest,
)
from local_drama.application.configuration import ConfigurationService
from local_drama.application.errors import api_error_from_domain
from local_drama.application.project_packages import ProjectPackageService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError

router = APIRouter(prefix="/projects", tags=["projects"])


def service(request: Request) -> ProjectService:
    settings = request.app.state.settings
    return ProjectService(request.app.state.database, settings.projects_root)


def package_service(request: Request) -> ProjectPackageService:
    settings = request.app.state.settings
    return ProjectPackageService(request.app.state.database, settings.projects_root, settings.data_root, settings=settings)


@router.get("", operation_id="listProjects")
async def list_projects(request: Request, limit: int = 50, search: str | None = None, status: str | None = None) -> dict[str, object]:
    try:
        return {"items": service(request).list_projects(limit, search=search, status=status), "page": {"next_cursor": None, "has_more": False}}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("", operation_id="createProject", status_code=201)
async def create_project(
    request: Request,
    payload: ProjectCreateRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, object]:
    del idempotency_key  # G5 will make idempotency persistence universal; project codes are unique in G2.
    try:
        result = service(request).create_project(
            code=payload.code,
            title=payload.title,
            episode_count=payload.episode_count,
            aspect_ratio=payload.aspect_ratio,
            fps_num=payload.fps.numerator if payload.fps else None,
            fps_den=payload.fps.denominator if payload.fps else None,
            target_duration_ms=payload.target_duration_ms,
            allow_unconfigured_capabilities=payload.allow_unconfigured_capabilities,
            season_count=payload.season_count,
            width=payload.width,
            height=payload.height,
            primary_language=payload.primary_language,
            subtitle_mode=payload.subtitle_mode,
            subtitle_language=payload.subtitle_language,
            production_plan=payload.production_plan.model_dump() if payload.production_plan else None,
            profile_bindings=[item.model_dump() for item in payload.profile_bindings],
            delivery_target=payload.delivery_target.model_dump() if payload.delivery_target else None,
            request_id=getattr(request.state, "request_id", None),
        )
        return {"project": result, "blockers": ConfigurationService(request.app.state.database).blockers(str(result["id"]))}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post(":plan", operation_id="planProjectCreation")
async def plan_project_creation(payload: ProjectCreateRequest, request: Request) -> dict[str, object]:
    try:
        return {"plan": service(request).plan_project_creation(
            code=payload.code,
            title=payload.title,
            episode_count=payload.episode_count,
            aspect_ratio=payload.aspect_ratio,
            fps_num=payload.fps.numerator if payload.fps else None,
            fps_den=payload.fps.denominator if payload.fps else None,
            target_duration_ms=payload.target_duration_ms,
            allow_unconfigured_capabilities=payload.allow_unconfigured_capabilities,
            season_count=payload.season_count,
            width=payload.width,
            height=payload.height,
            primary_language=payload.primary_language,
            subtitle_mode=payload.subtitle_mode,
            subtitle_language=payload.subtitle_language,
            production_plan=payload.production_plan.model_dump() if payload.production_plan else None,
            profile_bindings=[item.model_dump() for item in payload.profile_bindings],
            delivery_target=payload.delivery_target.model_dump() if payload.delivery_target else None,
        )}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/{project_id}", operation_id="getProject")
async def get_project(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"project": service(request).get_project(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.patch("/{project_id}", operation_id="updateProject")
async def update_project(project_id: str, payload: ProjectUpdateRequest, request: Request) -> dict[str, object]:
    try:
        return {"project": service(request).update_project_title(project_id, payload.title, payload.expected_revision)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{project_id}:activate", operation_id="activateProject")
async def activate_project(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"project": service(request).transition_project(project_id, "ACTIVE")}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{project_id}:pause", operation_id="pauseProject")
async def pause_project(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"project": service(request).transition_project(project_id, "PAUSED")}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{project_id}:archive", operation_id="archiveProject")
async def archive_project(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"project": service(request).transition_project(project_id, "ARCHIVED")}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{project_id}:copy-template", operation_id="copyProjectTemplate", status_code=201)
async def copy_project_template(project_id: str, payload: ProjectTemplateCopyRequest, request: Request) -> dict[str, object]:
    try:
        return service(request).copy_as_template(project_id, code=payload.code, title=payload.title)
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{project_id}/packages:export", operation_id="exportProjectPackage")
async def export_project_package(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"package": package_service(request).export(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{project_id}/packages:dry-run", operation_id="dryRunProjectPackage")
async def dry_run_project_package(project_id: str, payload: ProjectPackageDryRunRequest, request: Request) -> dict[str, object]:
    try:
        return {"dry_run": package_service(request).dry_run(project_id, payload.rel_path)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{project_id}:restore", operation_id="restoreProject")
async def restore_project(project_id: str, request: Request) -> dict[str, object]:
    try:
        return {"project": service(request).transition_project(project_id, "ACTIVE")}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/{project_id}/health", operation_id="projectHealth")
async def project_health(project_id: str, request: Request) -> dict[str, object]:
    try:
        project = service(request).get_project(project_id)
        root = (request.app.state.settings.projects_root / project["root_rel"]).resolve()
        projects_root = request.app.state.settings.projects_root.resolve()
        if not root.is_relative_to(projects_root):
            raise DomainRuleError("PATH_ESCAPE", "项目根目录越界")
        referenced: set[str] = set()
        missing: list[str] = []
        size_mismatch: list[str] = []
        hash_mismatch: list[str] = []
        with request.app.state.database.connect() as connection:
            media_rows = connection.execute(
                "SELECT mv.rel_path, mv.byte_size, mv.sha256 FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE ma.project_id=?",
                (project_id,),
            ).fetchall()
        for row in media_rows:
            rel = Path(str(row["rel_path"])).as_posix()
            referenced.add(rel)
            path = (root / rel).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
                missing.append(rel)
            elif path.stat().st_size != int(row["byte_size"]):
                size_mismatch.append(rel)
            else:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
                if digest != str(row["sha256"]):
                    hash_mismatch.append(rel)
        orphan_files: list[str] = []
        if root.is_dir():
            for path in root.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                rel = path.relative_to(root).as_posix()
                if rel not in referenced and not rel.startswith("exports/"):
                    orphan_files.append(rel)
        integrity = request.app.state.database.integrity_check()
        disk = shutil.disk_usage(root if root.exists() else projects_root)
        blockers = ([] if root.exists() else ["PROJECT_ROOT_MISSING"]) + ([] if integrity == "ok" else ["DATABASE_INTEGRITY_FAILED"]) + (["MEDIA_MISSING"] if missing else []) + (["MEDIA_SIZE_MISMATCH"] if size_mismatch else []) + (["MEDIA_HASH_MISMATCH"] if hash_mismatch else [])
        return {
            "project_id": project_id,
            "status": "HEALTHY" if not blockers else "BLOCKED",
            "root_exists": root.exists(),
            "database_integrity": integrity,
            "media": {"referenced_count": len(referenced), "missing": missing[:100], "size_mismatch": size_mismatch[:100], "hash_mismatch": hash_mismatch[:100]},
            "orphan_files": orphan_files[:100],
            "orphan_count": len(orphan_files),
            "disk": {"free_bytes": disk.free, "total_bytes": disk.total},
            "blockers": blockers,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/{project_id}/seasons", operation_id="listSeasons")
async def list_seasons(project_id: str, request: Request) -> dict[str, object]:
    return {"items": service(request).list_seasons(project_id)}


@router.get("/{project_id}/scenes", operation_id="listProjectScenes")
async def list_project_scenes(project_id: str, request: Request) -> dict[str, object]:
    return {"items": service(request).list_scenes(project_id)}


@router.post("/{project_id}/scenes", operation_id="createProjectScene", status_code=201)
async def create_project_scene(project_id: str, payload: SceneCreateRequest, request: Request) -> dict[str, object]:
    try:
        return {"scene": service(request).create_scene(project_id, payload.code, payload.title, payload.location, payload.time_of_day)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/seasons/{season_id}/episodes", operation_id="listEpisodes")
async def list_episodes(season_id: str, request: Request) -> dict[str, object]:
    return {"items": service(request).list_episodes(season_id)}


@router.get("/episodes/{episode_id}", operation_id="getEpisode")
async def get_episode(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"episode": service(request).get_episode(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/episodes/{episode_id}/scene-ranges", operation_id="listEpisodeSceneRanges")
async def list_episode_scene_ranges(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"items": service(request).list_episode_scene_ranges(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/scene-ranges", operation_id="bindEpisodeSceneRange", status_code=201)
async def bind_episode_scene_range(episode_id: str, payload: EpisodeSceneRangeRequest, request: Request) -> dict[str, object]:
    try:
        return {"range": service(request).bind_episode_scene_range(episode_id, payload.scene_id, payload.ordinal, payload.source_start, payload.source_end, payload.source_label)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}:reorder", operation_id="reorderEpisode")
async def reorder_episode(episode_id: str, display_order: int, request: Request) -> dict[str, object]:
    try:
        return {"episode": service(request).reorder_episode(episode_id, display_order)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/episodes/{episode_id}/shots", operation_id="listShots")
async def list_shots(episode_id: str, request: Request) -> dict[str, object]:
    return {"items": service(request).list_shots(episode_id)}


@router.get("/episodes/{episode_id}/storyboard", operation_id="getStoryboardWorkspace")
async def get_storyboard_workspace(episode_id: str, request: Request) -> dict[str, object]:
    try:
        return {"storyboard": service(request).get_storyboard_workspace(episode_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/storyboard:plan", operation_id="planStoryboardBatch")
async def plan_storyboard_batch(episode_id: str, payload: StoryboardBatchPlanRequest, request: Request) -> dict[str, object]:
    try:
        return {"plan": service(request).plan_storyboard_batch(episode_id, payload.model_dump())}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/episodes/{episode_id}/storyboard:commit", operation_id="commitStoryboardBatch")
async def commit_storyboard_batch(episode_id: str, payload: StoryboardBatchCommitRequest, request: Request) -> dict[str, object]:
    try:
        data = payload.model_dump()
        expected_plan_hash = str(data.pop("expected_plan_hash"))
        return {"result": service(request).commit_storyboard_batch(episode_id, data, expected_plan_hash)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.get("/shots/{shot_id}", operation_id="getShot")
async def get_shot(shot_id: str, request: Request) -> dict[str, object]:
    try:
        return {"shot": service(request).get_shot(shot_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{project_id}/episodes/{episode_id}/shots", operation_id="createShot", status_code=201)
async def create_shot(project_id: str, episode_id: str, payload: ShotCreateRequest, request: Request) -> dict[str, object]:
    del project_id
    try:
        return {"shot": service(request).create_shot(episode_id, payload.code, payload.target_duration_ms, payload.shot_type)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/{project_id}/media-thumbnails:rebuild", operation_id="rebuildProjectThumbnails")
async def rebuild_project_thumbnails(project_id: str, request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    try:
        return {"rebuild": ProjectPackageService(request.app.state.database, settings.projects_root, settings.data_root, settings=settings).rebuild_project_thumbnails(project_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/shots/{shot_id}/revisions", operation_id="createShotRevision", status_code=201)
async def create_shot_revision(shot_id: str, payload: ShotRevisionRequest, request: Request) -> dict[str, object]:
    try:
        return {"shot_revision": service(request).create_shot_revision(shot_id, payload.fields, payload.freeze)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error


@router.post("/shots/{shot_id}:mark-production-ready", operation_id="markShotProductionReady")
async def mark_production_ready(shot_id: str, request: Request) -> dict[str, object]:
    try:
        return {"shot": service(request).mark_shot_production_ready(shot_id)}
    except DomainRuleError as error:
        raise api_error_from_domain(error) from error
