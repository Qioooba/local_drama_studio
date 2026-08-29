from __future__ import annotations

import hashlib
import json
import shutil
import stat
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any, cast
from urllib.parse import quote

from local_drama.application.commands.director_recipes import canonical_recipe, recipe_hash, validate_recipe
from local_drama.application.local_artifacts import local_artifact_reference
from local_drama.application.media import MediaService
from local_drama.config import Settings
from local_drama.domain.capabilities import normalize_capability
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import validate_project_code
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.filesystem.atomic import replace_path
from local_drama.infrastructure.filesystem.path_policy import canonical_relative_path, controlled_path, safe_filename
from local_drama.infrastructure.filesystem.template import TEMPLATE_DIRECTORIES, TEMPLATE_VERSION

PACKAGE_SCHEMA = "localdrama.project-package.v2"
STATE_SCHEMA = "localdrama.project-state.v2"
MAX_ENTRIES = 100_000
MAX_EXPANDED_BYTES = 2 * 1024**4
MAX_COMPRESSION_RATIO = 250


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    try:
        return PurePosixPath(canonical_relative_path(name, code="PROJECT_PACKAGE_PATH_INVALID"))
    except DomainRuleError as error:
        raise DomainRuleError("PROJECT_PACKAGE_PATH_INVALID", "项目包包含不安全路径", {"path": name}) from error


def _writestr(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    archive.writestr(info, content)


def _zip_digest(archive: zipfile.ZipFile, name: str) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with archive.open(name) as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _is_reparse(path: Path) -> bool:
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class ProjectPackageService:
    def __init__(self, database: Database, projects_root: Path, data_root: Path | None = None, settings: Settings | None = None) -> None:
        self.database = database
        self.projects_root = projects_root.resolve()
        resolved_data_root = (data_root or projects_root.parent / "data").resolve()
        self.staging_root = resolved_data_root / "imports" / "project-packages"
        self.settings = settings or Settings(
            data_root=resolved_data_root,
            projects_root=self.projects_root,
            cache_root=self.projects_root.parent / "cache",
            work_root=self.projects_root.parent / "work",
            logs_root=self.projects_root.parent / "logs",
            backups_root=self.projects_root.parent / "backups",
        )

    def list_inbox_packages(self) -> list[dict[str, Any]]:
        """List importable packages without accepting an arbitrary client path."""
        inbox = self.staging_root / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        items: list[dict[str, Any]] = []
        for package in sorted(inbox.iterdir(), key=lambda item: item.name.casefold()):
            if not package.is_file() or package.suffix.lower() != ".ldspkg" or _is_reparse(package):
                continue
            stat_result = package.stat()
            items.append({
                "name": package.name,
                "byte_size": stat_result.st_size,
                "modified_at": datetime.fromtimestamp(stat_result.st_mtime, UTC).isoformat().replace("+00:00", "Z"),
            })
        return items

    def import_browser_upload(self, source: Path, original_name: str) -> dict[str, Any]:
        safe_name = safe_filename(original_name, default="project.ldspkg")
        if not safe_name or Path(safe_name).suffix.lower() != ".ldspkg":
            raise DomainRuleError("PROJECT_PACKAGE_UPLOAD_TYPE_INVALID", "项目包必须是 .ldspkg 文件")
        self.inspect_path(source)
        digest = _sha256(source)
        inbox = (self.staging_root / "inbox").resolve()
        inbox.mkdir(parents=True, exist_ok=True)
        destination = controlled_path(inbox, safe_name)
        if destination.exists() and _sha256(destination) != digest:
            destination = inbox / f"{Path(safe_name).stem}-{digest[:12]}.ldspkg"
        if not destination.exists():
            partial = inbox / f".partial-{uuid.uuid4().hex}.ldspkg"
            try:
                shutil.copyfile(source, partial)
                replace_path(partial, destination)
            finally:
                partial.unlink(missing_ok=True)
        stat_result = destination.stat()
        return {"name": destination.name, "byte_size": stat_result.st_size, "sha256": digest,
                "modified_at": datetime.fromtimestamp(stat_result.st_mtime, UTC).isoformat().replace("+00:00", "Z")}

    def _rebuild_thumbnails(self, media_version_ids: list[str]) -> dict[str, Any]:
        """Materialize small derived thumbnails for imported IMAGE/VIDEO versions."""
        created: list[str] = []
        failed: list[dict[str, str]] = []
        media_service = MediaService(self.database, self.settings)
        for media_version_id in media_version_ids:
            try:
                media_service.thumbnail(media_version_id, "small", "poster")
            except DomainRuleError as error:
                failed.append({"media_version_id": media_version_id, "code": error.code})
            except (OSError, RuntimeError) as error:
                failed.append({"media_version_id": media_version_id, "code": type(error).__name__})
            else:
                created.append(media_version_id)
        return {"requested": len(media_version_ids), "created": len(created), "failed": len(failed), "created_media_version_ids": created, "failures": failed}

    def rebuild_project_thumbnails(self, project_id: str, actor: str = "local-user") -> dict[str, Any]:
        """Retry derived poster thumbnails without touching source media or project state."""
        self._project(project_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT mv.id,mv.mime_type,ma.media_kind FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                WHERE ma.project_id=? AND ma.media_kind IN ('IMAGE','VIDEO') ORDER BY mv.created_at,mv.id""",
                (project_id,),
            ).fetchall()
        eligible: list[str] = []
        exclusions: list[dict[str, str]] = []
        for row in rows:
            kind = str(row["media_kind"]).upper()
            mime = str(row["mime_type"] or "").lower()
            if (kind == "IMAGE" and mime.startswith("image/")) or (kind == "VIDEO" and mime.startswith("video/")):
                eligible.append(str(row["id"]))
            else:
                exclusions.append({"media_version_id": str(row["id"]), "code": "MEDIA_KIND_MIME_MISMATCH"})
        result = {**self._rebuild_thumbnails(eligible), "skipped": len(exclusions), "exclusions": exclusions}
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'MEDIA_THUMBNAILS_REBUILT', 'project', ?, ?, ?)""",
                (actor, project_id, "重建项目媒体缩略图", json.dumps(result, ensure_ascii=False, sort_keys=True)),
            )
        return {"project_id": project_id, **result, "pending": max(0, int(result["requested"]) - int(result["created"]) - int(result["failed"])),
                "runtime_contacted": False, "network_contacted": False, "mutated": bool(result["created"])}

    def _project(self, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        return dict(row)

    def _root(self, project: dict[str, Any]) -> Path:
        root = controlled_path(
            self.projects_root,
            str(project["root_rel"]),
            must_exist=True,
            code="PROJECT_ROOT_INVALID",
        )
        if root.parent != self.projects_root or not root.is_dir() or _is_reparse(root):
            raise DomainRuleError("PROJECT_ROOT_INVALID", "项目根目录缺失或越界")
        return root

    def _state(self, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            project = connection.execute("""SELECT id,code,title,template_version,aspect_ratio,fps_num,fps_den,timezone,
                width,height,primary_language,subtitle_mode,subtitle_language FROM projects WHERE id=?""", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            seasons = [dict(row) for row in connection.execute("SELECT id,number,display_order,code,title FROM seasons WHERE project_id=? ORDER BY display_order", (project_id,))]
            episodes = [dict(row) for row in connection.execute("""SELECT e.id,e.season_id,e.number,e.display_order,e.code,e.title,e.target_duration_ms
                FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? ORDER BY s.display_order,e.display_order""", (project_id,))]
            scenes = [dict(row) for row in connection.execute("SELECT id,code,title,location,time_of_day FROM scenes WHERE project_id=? ORDER BY code", (project_id,))]
            shots = [dict(row) for row in connection.execute("""SELECT sh.id,sh.episode_id,sh.scene_id,sh.code,sh.order_key,sh.target_duration_ms,sh.shot_type,sr.fields_json
                FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id
                LEFT JOIN shot_revisions sr ON sr.id=sh.current_revision_id WHERE s.project_id=? ORDER BY e.display_order,CAST(sh.order_key AS REAL),sh.code""", (project_id,))]
            shot_groups = [dict(row) for row in connection.execute("""SELECT g.id,g.episode_id,g.scene_id,g.kind,g.code,g.title,g.order_key,
                g.metadata_json,g.status,g.created_at,g.updated_at,g.created_by,g.revision,g.schema_version
                FROM shot_groups g JOIN episodes e ON e.id=g.episode_id JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? ORDER BY e.display_order,g.order_key,g.id""", (project_id,))]
            shot_group_members = [dict(row) for row in connection.execute("""SELECT m.group_id,m.shot_id,m.order_key,m.created_at,m.created_by
                FROM shot_group_members m JOIN shot_groups g ON g.id=m.group_id JOIN episodes e ON e.id=g.episode_id
                JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? ORDER BY m.group_id,m.order_key,m.shot_id""", (project_id,))]
            plan = connection.execute("""SELECT pp.code,pp.title,ppv.version_no,ppv.plan_json,ppv.status FROM project_plan_bindings ppb
                JOIN production_plan_versions ppv ON ppv.id=ppb.production_plan_version_id JOIN production_plans pp ON pp.id=ppv.production_plan_id WHERE ppb.project_id=?""", (project_id,)).fetchone()
            profiles = [dict(row) for row in connection.execute("""SELECT ppb.capability,ppb.status AS binding_status,ppb.execution_profile_version_id,epv.status AS profile_status
                FROM project_profile_bindings ppb JOIN execution_profile_versions epv ON epv.id=ppb.execution_profile_version_id WHERE ppb.project_id=? ORDER BY ppb.capability""", (project_id,))]
            targets = [dict(row) for row in connection.execute("""SELECT dt.code,dt.title,dt.transport,dt.status,dtv.version_no,dtv.target_spec_json,dtv.status AS version_status
                FROM delivery_targets dt JOIN delivery_target_versions dtv ON dtv.delivery_target_id=dt.id WHERE dt.project_id=? ORDER BY dt.code,dtv.version_no""", (project_id,))]
            media_assets = [dict(row) for row in connection.execute("""SELECT id,owner_type,owner_id,purpose,media_kind,version_counter,metadata_json
                FROM media_assets WHERE project_id=? ORDER BY created_at,id""", (project_id,))]
            media_versions = [dict(row) for row in connection.execute("""SELECT mv.id,mv.media_asset_id,mv.version_no,mv.take_no,mv.stage,mv.rel_path,mv.mime_type,
                mv.byte_size,mv.sha256,mv.duration_ms,mv.fps_num,mv.fps_den,mv.parent_version_id,mv.integrity_status,mv.source_name,
                mv.import_source,mv.probe_json FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                WHERE ma.project_id=? ORDER BY ma.created_at,mv.version_no,mv.id""", (project_id,))]
            story_assets = [dict(row) for row in connection.execute("""SELECT id,kind,code,name,description,canonical_media_version_id,
                extra_json,status,created_at,updated_at,created_by,revision,schema_version
                FROM story_assets WHERE project_id=? ORDER BY kind,code,id""", (project_id,))]
            asset_proposals = [dict(row) for row in connection.execute("""SELECT id,breakdown_draft_id,proposal_key,kind,name,
                evidence_json,suggested_asset_id,resolved_asset_id,status,decision_note,created_at,updated_at,created_by,
                revision,schema_version FROM story_asset_proposals WHERE project_id=? ORDER BY created_at,id""", (project_id,))]
            asset_states = [dict(row) for row in connection.execute("""SELECT id,story_asset_id,code,label,state_kind,description,state_json,
                status,created_at,updated_at,created_by,revision,schema_version
                FROM story_asset_states WHERE project_id=? ORDER BY story_asset_id,code,id""", (project_id,))]
            asset_references = [dict(row) for row in connection.execute("""SELECT id,story_asset_id,asset_state_id,media_version_id,
                reference_kind,label,priority,is_locked,yaw_deg,pitch_deg,metadata_json,status,created_at,updated_at,created_by,revision,schema_version
                FROM story_asset_references WHERE project_id=? ORDER BY story_asset_id,priority,created_at,id""", (project_id,))]
            episode_asset_state_bindings = [dict(row) for row in connection.execute("""SELECT b.id,b.episode_id,b.story_asset_id,b.asset_state_id,
                b.created_at,b.created_by,b.revision,b.schema_version FROM episode_asset_state_bindings b
                JOIN episodes e ON e.id=b.episode_id JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? ORDER BY b.episode_id,b.story_asset_id,b.id""", (project_id,))]
            shot_asset_bindings = [dict(row) for row in connection.execute("""SELECT b.id,b.shot_id,b.asset_id,b.asset_state_id,b.role_in_shot,
                b.created_at,b.created_by,b.revision,b.schema_version FROM shot_asset_bindings b
                JOIN shots sh ON sh.id=b.shot_id JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id
                WHERE s.project_id=? ORDER BY b.shot_id,b.asset_id,b.role_in_shot,b.id""", (project_id,))]
            generation_preference_sets = [dict(row) for row in connection.execute("""SELECT id,owner_type,owner_id,capability,current_version_id,
                status,created_at,updated_at,created_by,revision,schema_version FROM generation_preference_sets
                WHERE project_id=? ORDER BY capability,owner_type,owner_id,id""", (project_id,))]
            generation_preference_versions = [dict(row) for row in connection.execute("""SELECT v.id,v.preference_set_id,v.version_no,
                v.execution_profile_version_id,v.resolution_mode,v.settings_json,v.reason,v.is_frozen,v.created_at,v.created_by,v.schema_version
                FROM generation_preference_versions v JOIN generation_preference_sets s ON s.id=v.preference_set_id
                WHERE s.project_id=? ORDER BY v.preference_set_id,v.version_no,v.id""", (project_id,))]
            qc_policy_sets = [dict(row) for row in connection.execute("""SELECT id,owner_type,owner_id,stage,current_version_id,status,
                created_at,updated_at,created_by,revision,schema_version FROM generation_qc_policy_sets
                WHERE project_id=? ORDER BY stage,owner_type,owner_id,id""", (project_id,))]
            qc_policy_versions = [dict(row) for row in connection.execute("""SELECT v.id,v.policy_set_id,v.version_no,v.policy_json,
                v.max_auto_rerolls,v.auto_reroll_categories_json,v.is_frozen,v.reason,v.created_at,v.created_by,v.schema_version
                FROM generation_qc_policy_versions v JOIN generation_qc_policy_sets s ON s.id=v.policy_set_id
                WHERE s.project_id=? ORDER BY v.policy_set_id,v.version_no,v.id""", (project_id,))]
            director_recipes = [dict(row) for row in connection.execute("""SELECT id,code,title,status,created_at,updated_at,
                created_by,revision,schema_version FROM director_recipes WHERE project_id=? ORDER BY code,id""", (project_id,))]
            director_recipe_versions = [dict(row) for row in connection.execute("""SELECT v.id,v.recipe_id,v.version_no,v.recipe_json,
                v.recipe_hash,v.reason,v.is_frozen,v.created_at,v.created_by,v.schema_version
                FROM director_recipe_versions v JOIN director_recipes r ON r.id=v.recipe_id
                WHERE r.project_id=? ORDER BY v.recipe_id,v.version_no,v.id""", (project_id,))]
            director_recipe_binding_row = connection.execute("""SELECT recipe_version_id,reason,created_at,updated_at,created_by,
                revision,schema_version FROM project_director_recipe_bindings WHERE project_id=?""", (project_id,)).fetchone()
        for shot in shots:
            shot["fields"] = json.loads(str(shot.pop("fields_json") or "{}"))
        for asset in media_assets:
            asset["metadata"] = json.loads(str(asset.pop("metadata_json") or "{}"))
        for version in media_versions:
            version["probe"] = json.loads(str(version.pop("probe_json") or "{}"))
        for asset in story_assets:
            asset["extra"] = json.loads(str(asset.pop("extra_json") or "{}"))
        for proposal in asset_proposals:
            proposal["evidence"] = json.loads(str(proposal.pop("evidence_json") or "{}"))
        for state in asset_states:
            state["state"] = json.loads(str(state.pop("state_json") or "{}"))
        for reference in asset_references:
            reference["metadata"] = json.loads(str(reference.pop("metadata_json") or "{}"))
        for version in generation_preference_versions:
            version["settings"] = json.loads(str(version.pop("settings_json") or "{}"))
        for group in shot_groups:
            group["metadata"] = json.loads(str(group.pop("metadata_json") or "{}"))
        for version in qc_policy_versions:
            version["policy"] = json.loads(str(version.pop("policy_json") or "{}"))
            version["auto_reroll_categories"] = json.loads(str(version.pop("auto_reroll_categories_json") or "[]"))
        for version in director_recipe_versions:
            version["recipe"] = json.loads(str(version.pop("recipe_json") or "{}"))
        return {"schema_version": STATE_SCHEMA, "project": dict(project), "seasons": seasons, "episodes": episodes, "scenes": scenes,
                "shots": shots, "shot_groups": shot_groups, "shot_group_members": shot_group_members,
                "production_plan": dict(plan) if plan else None, "profile_bindings": profiles, "delivery_targets": targets,
                "media_assets": media_assets, "media_versions": media_versions, "media_selection_state_excluded": True,
                "story_assets": story_assets, "story_asset_proposals": asset_proposals,
                "story_asset_states": asset_states, "story_asset_references": asset_references,
                "episode_asset_state_bindings": episode_asset_state_bindings, "shot_asset_bindings": shot_asset_bindings,
                "generation_preference_sets": generation_preference_sets,
                "generation_preference_versions": generation_preference_versions,
                "generation_qc_policy_sets": qc_policy_sets, "generation_qc_policy_versions": qc_policy_versions,
                "director_recipes": director_recipes, "director_recipe_versions": director_recipe_versions,
                "project_director_recipe_binding": dict(director_recipe_binding_row) if director_recipe_binding_row else None,
                "excluded_domains": ["jobs", "attempts", "reviews", "selections", "variant_qc_links", "audit_events", "outbox", "cache", "work"]}

    def _source_files(self, root: Path) -> list[Path]:
        files: list[Path] = []
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if relative.parts[:2] == ("exports", "project-packages") or any(part.startswith(".partial-") for part in relative.parts):
                continue
            if _is_reparse(path):
                raise DomainRuleError("PROJECT_PACKAGE_REPARSE_POINT", "项目包导出拒绝 symlink/junction", {"path": relative.as_posix()})
            if path.is_file():
                resolved = path.resolve()
                if root not in resolved.parents:
                    raise DomainRuleError("PROJECT_PACKAGE_PATH_ESCAPE", "项目文件解析后越界", {"path": relative.as_posix()})
                files.append(path)
        return sorted(files, key=lambda item: item.relative_to(root).as_posix())

    def export(self, project_id: str) -> dict[str, Any]:
        project = self._project(project_id)
        root = self._root(project)
        state_bytes = _json_bytes(self._state(project_id))
        state_sha = hashlib.sha256(state_bytes).hexdigest()
        output_dir = root / "exports" / "project-packages"
        output_dir.mkdir(parents=True, exist_ok=True)
        final = output_dir / f"{project['code']}-{state_sha[:12]}.ldspkg"
        partial = output_dir / f".partial-{uuid.uuid4().hex}.ldspkg"
        entries: list[dict[str, Any]] = [{"path": "project-state.json", "byte_size": len(state_bytes), "sha256": state_sha}]
        try:
            with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
                _writestr(archive, "project-state.json", state_bytes)
                for source in self._source_files(root):
                    relative = source.relative_to(root).as_posix()
                    before_size, before_sha = source.stat().st_size, _sha256(source)
                    archive.write(source, f"payload/{relative}")
                    if source.stat().st_size != before_size or _sha256(source) != before_sha:
                        raise DomainRuleError("PROJECT_PACKAGE_SOURCE_CHANGED", "导出期间源文件发生变化", {"path": relative})
                    entries.append({"path": f"payload/{relative}", "byte_size": before_size, "sha256": before_sha})
                manifest = {"schema_version": PACKAGE_SCHEMA, "project_id": project_id, "project_code": project["code"], "state_sha256": state_sha,
                            "entry_count": len(entries), "expanded_bytes": sum(int(item["byte_size"]) for item in entries), "entries": entries}
                _writestr(archive, "package-manifest.json", _json_bytes(manifest))
            verified = self.inspect_path(partial)
            if verified["status"] not in {"READY_REBIND_EXISTING", "IDENTITY_CONFLICT"}:
                raise DomainRuleError("PROJECT_PACKAGE_VERIFY_FAILED", "项目包导出后校验失败")
            if final.exists():
                if _sha256(final) != _sha256(partial):
                    raise DomainRuleError("PROJECT_PACKAGE_OUTPUT_CONFLICT", "同名项目包内容不一致")
                partial.unlink()
                reused = True
            else:
                replace_path(partial, final)
                reused = False
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        rel_path = final.relative_to(root).as_posix()
        artifact = local_artifact_reference(
            root=root,
            path=final,
            scope="PROJECT",
            kind="FILE",
            display_name=final.name,
            download_url=f"/api/v1/projects/{quote(project_id, safe='')}/packages:download?rel_path={quote(rel_path, safe='')}",
            download_filename=final.name,
            error_code="PROJECT_PACKAGE_OUTPUT_INVALID",
        )
        return {"status": "EXPORTED", "project_id": project_id, "artifact": artifact, "rel_path": rel_path, "byte_size": final.stat().st_size,
                "sha256": _sha256(final), "entry_count": len(entries), "expanded_bytes": sum(int(item["byte_size"]) for item in entries), "reused": reused,
                "database_mutated": False, "runtime_contacted": False, "network_contacted": False}

    def inspect_path(self, package: Path) -> dict[str, Any]:
        try:
            with zipfile.ZipFile(package) as archive:
                infos = archive.infolist()
                if len(infos) > MAX_ENTRIES:
                    raise DomainRuleError("PROJECT_PACKAGE_TOO_MANY_ENTRIES", "项目包文件数超过上限")
                names = [info.filename for info in infos]
                if len(names) != len(set(names)):
                    raise DomainRuleError("PROJECT_PACKAGE_DUPLICATE_PATH", "项目包包含重复路径")
                for name in names:
                    _safe_member(name)
                if any(stat.S_ISLNK(info.external_attr >> 16) for info in infos):
                    raise DomainRuleError("PROJECT_PACKAGE_REPARSE_POINT", "项目包不接受 symlink 条目")
                if "package-manifest.json" not in names or "project-state.json" not in names:
                    raise DomainRuleError("PROJECT_PACKAGE_REQUIRED_FILE_MISSING", "项目包缺少 manifest 或 state")
                expanded = sum(info.file_size for info in infos)
                compressed = sum(max(info.compress_size, 1) for info in infos)
                if expanded > MAX_EXPANDED_BYTES or expanded / compressed > MAX_COMPRESSION_RATIO:
                    raise DomainRuleError("PROJECT_PACKAGE_EXPANSION_UNSAFE", "项目包展开大小或压缩比不安全")
                manifest = json.loads(archive.read("package-manifest.json"))
                state = json.loads(archive.read("project-state.json"))
                if manifest.get("schema_version") != PACKAGE_SCHEMA or state.get("schema_version") != STATE_SCHEMA:
                    raise DomainRuleError("PROJECT_PACKAGE_SCHEMA_UNSUPPORTED", "项目包 schema 不受支持")
                expected = {str(item["path"]): item for item in manifest.get("entries", [])}
                if set(expected) != set(names) - {"package-manifest.json"}:
                    raise DomainRuleError("PROJECT_PACKAGE_MANIFEST_MISMATCH", "manifest 文件清单与压缩包不一致")
                if int(manifest.get("entry_count", -1)) != len(expected) or int(manifest.get("expanded_bytes", -1)) != sum(
                    int(item["byte_size"]) for item in expected.values()
                ):
                    raise DomainRuleError("PROJECT_PACKAGE_MANIFEST_MISMATCH", "manifest 汇总计数与文件清单不一致")
                for name, item in expected.items():
                    size, digest = _zip_digest(archive, name)
                    if size != int(item["byte_size"]) or digest != item["sha256"]:
                        raise DomainRuleError("PROJECT_PACKAGE_HASH_MISMATCH", "项目包文件 hash/size 不匹配", {"path": name})
                media_assets = state.get("media_assets", [])
                media_versions = state.get("media_versions", [])
                if not isinstance(media_assets, list) or not isinstance(media_versions, list):
                    raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包媒体状态格式无效")
                asset_ids = {str(item.get("id")) for item in media_assets}
                version_ids = {str(item.get("id")) for item in media_versions}
                if len(asset_ids) != len(media_assets) or len(version_ids) != len(media_versions):
                    raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包包含重复媒体 ID")
                for version in media_versions:
                    if str(version.get("media_asset_id")) not in asset_ids:
                        raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "媒体版本引用了未知资产")
                    parent = version.get("parent_version_id")
                    if parent is not None and str(parent) not in version_ids:
                        raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "媒体版本引用了未知 parent")
                    media_path = f"payload/{_safe_member(str(version.get('rel_path') or '')).as_posix()}"
                    entry = expected.get(media_path)
                    if entry is None or int(entry["byte_size"]) != int(version.get("byte_size", -1)) or entry["sha256"] != version.get("sha256"):
                        raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "媒体版本与 manifest 文件不一致", {"path": media_path})
        except (OSError, zipfile.BadZipFile, KeyError, ValueError, json.JSONDecodeError) as error:
            raise DomainRuleError("PROJECT_PACKAGE_INVALID", "项目包无法安全读取") from error
        project_id, project_code = str(manifest["project_id"]), str(manifest["project_code"])
        with self.database.connect() as connection:
            id_match = connection.execute("SELECT id,code FROM projects WHERE id=?", (project_id,)).fetchone()
            code_match = connection.execute("SELECT id,code FROM projects WHERE code=?", (project_code,)).fetchone()
        conflict = id_match is not None or code_match is not None
        free = shutil.disk_usage(self.projects_root).free
        blockers = (["PROJECT_IDENTITY_CONFLICT"] if conflict else []) + (["INSUFFICIENT_DISK_SPACE"] if free < expanded else [])
        status = "READY_REBIND_EXISTING" if id_match and str(id_match["code"]) == project_code and free >= expanded else ("IDENTITY_CONFLICT" if conflict else ("READY_IMPORT" if not blockers else "BLOCKED"))
        return {"status": status, "project_id": project_id, "project_code": project_code, "entry_count": int(manifest["entry_count"]), "expanded_bytes": expanded,
                "free_bytes": free, "blockers": blockers, "conflict_options": ["REBIND_EXISTING", "IMPORT_AS_COPY_REWRITE_IDENTITY"] if conflict else [],
                "would_import": False, "mutated": False, "runtime_contacted": False, "network_contacted": False}

    def dry_run(self, project_id: str, rel_path: str) -> dict[str, Any]:
        project = self._project(project_id)
        root = self._root(project)
        allowed = (root / "exports" / "project-packages").resolve()
        candidate = controlled_path(
            root,
            rel_path,
            must_exist=True,
            require_file=True,
            code="PROJECT_PACKAGE_PATH_NOT_ALLOWED",
        )
        if allowed not in candidate.parents or not candidate.is_file() or _is_reparse(candidate):
            raise DomainRuleError("PROJECT_PACKAGE_PATH_NOT_ALLOWED", "只能预检项目已导出的注册项目包")
        return self.inspect_path(candidate)

    def export_download_path(self, project_id: str, rel_path: str) -> Path:
        project = self._project(project_id)
        root = self._root(project)
        allowed = (root / "exports" / "project-packages").resolve()
        candidate = controlled_path(
            root,
            rel_path,
            must_exist=True,
            require_file=True,
            code="PROJECT_PACKAGE_PATH_NOT_ALLOWED",
        )
        if candidate.parent != allowed or candidate.suffix.lower() != ".ldspkg" or not candidate.is_file() or _is_reparse(candidate):
            raise DomainRuleError("PROJECT_PACKAGE_PATH_NOT_ALLOWED", "只能下载项目已导出的注册项目包")
        self.inspect_path(candidate)
        return candidate

    def stage_from_inbox(self, inbox_name: str) -> dict[str, Any]:
        if safe_filename(inbox_name, default="") != inbox_name or not inbox_name.lower().endswith(".ldspkg"):
            raise DomainRuleError("PROJECT_PACKAGE_INBOX_NAME_INVALID", "inbox 只接受单个 .ldspkg 文件名")
        inbox = (self.staging_root / "inbox").resolve()
        staged = (self.staging_root / "staged").resolve()
        inbox.mkdir(parents=True, exist_ok=True)
        staged.mkdir(parents=True, exist_ok=True)
        source = (inbox / inbox_name).resolve()
        if source.parent != inbox or not source.is_file() or _is_reparse(source):
            raise DomainRuleError("PROJECT_PACKAGE_INBOX_FILE_NOT_FOUND", "inbox 项目包不存在或不是普通文件")
        before_size, before_sha = source.stat().st_size, _sha256(source)
        destination = staged / f"{before_sha}.ldspkg"
        partial = staged / f".partial-{uuid.uuid4().hex}.ldspkg"
        try:
            if destination.exists():
                if _sha256(destination) != before_sha or destination.stat().st_size != before_size:
                    raise DomainRuleError("PROJECT_PACKAGE_STAGING_CONFLICT", "staging 中同 hash 文件内容异常")
                reused = True
            else:
                shutil.copyfile(source, partial)
                if _sha256(partial) != before_sha or partial.stat().st_size != before_size or _sha256(source) != before_sha:
                    raise DomainRuleError("PROJECT_PACKAGE_STAGE_COPY_MISMATCH", "staging 复制前后 hash/size 不一致")
                self.inspect_path(partial)
                replace_path(partial, destination)
                reused = False
            dry_run = self.inspect_path(destination)
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return {"status": "STAGED", "stage_token": before_sha, "source_name": inbox_name, "byte_size": before_size, "sha256": before_sha,
                "reused": reused, "source_retained": True, "dry_run": dry_run, "database_mutated": False, "runtime_contacted": False, "network_contacted": False}

    def dry_run_staged(self, stage_token: str) -> dict[str, Any]:
        if len(stage_token) != 64 or any(character not in "0123456789abcdef" for character in stage_token):
            raise DomainRuleError("PROJECT_PACKAGE_STAGE_TOKEN_INVALID", "stage token 必须是小写 SHA-256")
        package = (self.staging_root / "staged" / f"{stage_token}.ldspkg").resolve()
        allowed = (self.staging_root / "staged").resolve()
        if package.parent != allowed or not package.is_file() or _is_reparse(package) or _sha256(package) != stage_token:
            raise DomainRuleError("PROJECT_PACKAGE_STAGE_NOT_FOUND", "staged 项目包不存在或完整性失败")
        return self.inspect_path(package)

    def _staged_package(self, stage_token: str) -> Path:
        self.dry_run_staged(stage_token)
        return (self.staging_root / "staged" / f"{stage_token}.ldspkg").resolve()

    def _read_state(self, package: Path) -> dict[str, Any]:
        self.inspect_path(package)
        with zipfile.ZipFile(package) as archive:
            state = cast(dict[str, Any], json.loads(archive.read("project-state.json")))
        required_lists = ("seasons", "episodes", "scenes", "shots", "profile_bindings", "delivery_targets")
        if not isinstance(state.get("project"), dict) or any(not isinstance(state.get(key), list) for key in required_lists):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包结构状态不完整")
        project = state["project"]
        if not all(project.get(key) for key in ("id", "code", "title")):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包项目身份不完整")
        season_ids = {str(item.get("id")) for item in state["seasons"]}
        episode_ids = {str(item.get("id")) for item in state["episodes"]}
        if len(season_ids) != len(state["seasons"]) or len(episode_ids) != len(state["episodes"]):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包包含重复季或集 ID")
        if any(str(item.get("season_id")) not in season_ids for item in state["episodes"]):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包分集引用了未知季")
        if any(str(item.get("episode_id")) not in episode_ids for item in state["shots"]):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包镜头引用了未知分集")
        state.setdefault("media_assets", [])
        state.setdefault("media_versions", [])
        optional_lists = (
            "shot_groups",
            "shot_group_members",
            "story_assets",
            "story_asset_proposals",
            "story_asset_states",
            "story_asset_references",
            "episode_asset_state_bindings",
            "shot_asset_bindings",
            "generation_preference_sets",
            "generation_preference_versions",
            "generation_qc_policy_sets",
            "generation_qc_policy_versions",
            "director_recipes",
            "director_recipe_versions",
        )
        for key in optional_lists:
            state.setdefault(key, [])
        if not isinstance(state["media_assets"], list) or not isinstance(state["media_versions"], list):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包媒体状态格式无效")
        if any(not isinstance(state[key], list) for key in optional_lists):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包扩展状态格式无效")
        if any(not isinstance(item, dict) for key in optional_lists for item in state[key]):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包扩展状态条目无效")
        if state.get("project_director_recipe_binding") is not None and not isinstance(state["project_director_recipe_binding"], dict):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包导演配方绑定格式无效")
        media_asset_ids = {str(item.get("id")) for item in state["media_assets"]}
        media_version_ids = {str(item.get("id")) for item in state["media_versions"]}
        if len(media_asset_ids) != len(state["media_assets"]) or len(media_version_ids) != len(state["media_versions"]):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目包包含重复媒体 ID")
        manifest_paths = {str(item.get("path")) for item in self._manifest_entries(package)}
        for version in state["media_versions"]:
            if str(version.get("media_asset_id")) not in media_asset_ids:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "媒体版本引用了未知资产")
            parent = version.get("parent_version_id")
            if parent is not None and str(parent) not in media_version_ids:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "媒体版本引用了未知 parent")
            relative = _safe_member(str(version.get("rel_path") or ""))
            if f"payload/{relative.as_posix()}" not in manifest_paths:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "媒体版本文件未登记在 manifest", {"rel_path": str(relative)})

        def unique_ids(key: str, label: str) -> set[str]:
            values = {str(item.get("id")) for item in state[key]}
            if "None" in values or len(values) != len(state[key]):
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", f"项目包包含重复或缺失的{label} ID")
            return values

        story_asset_ids = unique_ids("story_assets", "故事资产")
        proposal_ids = unique_ids("story_asset_proposals", "资产建议")
        state_ids = unique_ids("story_asset_states", "资产状态")
        reference_ids = unique_ids("story_asset_references", "资产参考")
        episode_binding_ids = unique_ids("episode_asset_state_bindings", "分集资产状态绑定")
        shot_binding_ids = unique_ids("shot_asset_bindings", "镜头资产绑定")
        preference_set_ids = unique_ids("generation_preference_sets", "生成偏好集")
        preference_version_ids = unique_ids("generation_preference_versions", "生成偏好版本")
        shot_group_ids = unique_ids("shot_groups", "镜头分组")
        qc_policy_set_ids = unique_ids("generation_qc_policy_sets", "QC policy 集")
        qc_policy_version_ids = unique_ids("generation_qc_policy_versions", "QC policy 版本")
        director_recipe_ids = unique_ids("director_recipes", "导演配方")
        director_recipe_version_ids = unique_ids("director_recipe_versions", "导演配方版本")
        # Keep the variables explicit: duplicate detection above is part of the
        # frozen package contract even when a given ID is not referenced below.
        _ = proposal_ids, reference_ids, episode_binding_ids, shot_binding_ids, preference_version_ids, qc_policy_version_ids, director_recipe_version_ids
        scene_ids = {str(item.get("id")) for item in state["scenes"]}
        shot_ids = {str(item.get("id")) for item in state["shots"]}
        for shot in state["shots"]:
            if shot.get("scene_id") is not None and str(shot["scene_id"]) not in scene_ids:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "镜头 scene_id 引用了未知场景")
        group_episode: dict[str, str] = {}
        for group in state["shot_groups"]:
            group_id, episode_id = str(group["id"]), str(group.get("episode_id"))
            if episode_id not in episode_ids or (group.get("scene_id") is not None and str(group["scene_id"]) not in scene_ids):
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "镜头分组引用未知分集或场景")
            group_episode[group_id] = episode_id
        seen_members: set[tuple[str, str]] = set()
        shot_episode = {str(item["id"]): str(item.get("episode_id")) for item in state["shots"]}
        for member in state["shot_group_members"]:
            pair = (str(member.get("group_id")), str(member.get("shot_id")))
            if pair in seen_members or pair[0] not in shot_group_ids or pair[1] not in shot_ids or group_episode[pair[0]] != shot_episode[pair[1]]:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "镜头分组成员重复、越界或跨分集")
            seen_members.add(pair)
        state_asset = {str(item["id"]): str(item.get("story_asset_id")) for item in state["story_asset_states"]}
        if any(asset_id not in story_asset_ids for asset_id in state_asset.values()):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "资产状态引用了未知故事资产")
        for asset in state["story_assets"]:
            canonical = asset.get("canonical_media_version_id")
            if canonical is not None and str(canonical) not in media_version_ids:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "故事资产 canonical 引用了未知媒体版本")
        allowed_proposal_statuses = {"PENDING", "ACCEPTED_NEW", "ACCEPTED_MERGE", "REJECTED"}
        for proposal in state["story_asset_proposals"]:
            suggested_id, resolved_id = proposal.get("suggested_asset_id"), proposal.get("resolved_asset_id")
            if suggested_id is not None and str(suggested_id) not in story_asset_ids:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "资产建议引用了未知 suggested 资产")
            if resolved_id is not None and str(resolved_id) not in story_asset_ids:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "资产建议引用了未知 resolved 资产")
            if str(proposal.get("status")) not in allowed_proposal_statuses or not isinstance(proposal.get("evidence", {}), dict):
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "资产建议状态或 evidence 无效")
        for reference in state["story_asset_references"]:
            asset_id = str(reference.get("story_asset_id"))
            state_id = reference.get("asset_state_id")
            if asset_id not in story_asset_ids or str(reference.get("media_version_id")) not in media_version_ids:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "资产参考引用了未知故事资产或媒体版本")
            if state_id is not None and (str(state_id) not in state_ids or state_asset[str(state_id)] != asset_id):
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "资产参考状态与故事资产不一致")
        for binding in state["episode_asset_state_bindings"]:
            asset_id, asset_state_id = str(binding.get("story_asset_id")), str(binding.get("asset_state_id"))
            if str(binding.get("episode_id")) not in episode_ids or asset_id not in story_asset_ids:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "分集资产状态绑定引用未知对象")
            if asset_state_id not in state_ids or state_asset[asset_state_id] != asset_id:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "分集资产状态绑定不一致")
        for binding in state["shot_asset_bindings"]:
            asset_id, asset_state_id = str(binding.get("asset_id")), binding.get("asset_state_id")
            if str(binding.get("shot_id")) not in {str(item.get("id")) for item in state["shots"]} or asset_id not in story_asset_ids:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "镜头资产绑定引用未知对象")
            if asset_state_id is not None and (str(asset_state_id) not in state_ids or state_asset[str(asset_state_id)] != asset_id):
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "镜头资产状态绑定不一致")
        version_set = {str(item.get("preference_set_id")) for item in state["generation_preference_versions"]}
        if any(set_id not in preference_set_ids for set_id in version_set):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "生成偏好版本引用未知偏好集")
        versions_by_set = {
            set_id: {str(item["id"]) for item in state["generation_preference_versions"] if str(item.get("preference_set_id")) == set_id}
            for set_id in preference_set_ids
        }
        source_project_id = str(project["id"])
        valid_owners = {"PROJECT": {source_project_id}, "EPISODE": episode_ids, "SHOT": shot_ids}
        for preference in state["generation_preference_sets"]:
            set_id = str(preference["id"])
            owner_type, owner_id = str(preference.get("owner_type")), str(preference.get("owner_id"))
            if owner_type not in valid_owners or owner_id not in valid_owners[owner_type]:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "生成偏好 owner 不属于包内项目")
            current = preference.get("current_version_id")
            if current is not None and str(current) not in versions_by_set[set_id]:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "生成偏好 current version 不属于对应偏好集")
        qc_versions_by_set = {
            set_id: {str(item["id"]) for item in state["generation_qc_policy_versions"] if str(item.get("policy_set_id")) == set_id}
            for set_id in qc_policy_set_ids
        }
        if any(str(item.get("policy_set_id")) not in qc_policy_set_ids for item in state["generation_qc_policy_versions"]):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "QC policy 版本引用未知 policy 集")
        for policy_set in state["generation_qc_policy_sets"]:
            set_id = str(policy_set["id"])
            owner_type, owner_id = str(policy_set.get("owner_type")), str(policy_set.get("owner_id"))
            if owner_type not in valid_owners or owner_id not in valid_owners[owner_type]:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "QC policy owner 不属于包内项目")
            current = policy_set.get("current_version_id")
            if current is not None and str(current) not in qc_versions_by_set[set_id]:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "QC policy current version 不属于对应 policy 集")
        recipe_versions_by_recipe = {
            recipe_id: {str(item["id"]) for item in state["director_recipe_versions"] if str(item.get("recipe_id")) == recipe_id}
            for recipe_id in director_recipe_ids
        }
        if any(str(item.get("recipe_id")) not in director_recipe_ids for item in state["director_recipe_versions"]):
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "导演配方版本引用未知配方")
        for version in state["director_recipe_versions"]:
            raw_recipe = version.get("recipe")
            if not isinstance(raw_recipe, dict):
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "导演配方版本 recipe 格式无效")
            recipe = validate_recipe(raw_recipe)
            if str(version.get("recipe_hash") or "") != recipe_hash(recipe):
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "导演配方版本 hash 与声明内容不一致")
            policy_ref = recipe["qc_policy_ref"].get("policy_version_id")
            if policy_ref is not None and str(policy_ref) not in qc_policy_version_ids:
                raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "导演配方引用未知 QC policy 版本")
        binding = state.get("project_director_recipe_binding")
        all_recipe_version_ids = set().union(*recipe_versions_by_recipe.values()) if recipe_versions_by_recipe else set()
        if binding is not None and str(binding.get("recipe_version_id")) not in all_recipe_version_ids:
            raise DomainRuleError("PROJECT_PACKAGE_STATE_INVALID", "项目导演配方绑定引用未知版本")
        return state

    def _manifest_entries(self, package: Path) -> list[dict[str, Any]]:
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read("package-manifest.json"))
        return list(manifest.get("entries", []))

    def _extract_payload(self, package: Path, temporary_root: Path) -> None:
        temporary_root.mkdir(parents=True, exist_ok=False)
        for relative in TEMPLATE_DIRECTORIES:
            (temporary_root / relative).mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(package) as archive:
            for info in archive.infolist():
                if not info.filename.startswith("payload/"):
                    continue
                payload_relative = _safe_member(info.filename).relative_to("payload")
                if not payload_relative.parts:
                    continue
                destination = (temporary_root / Path(*payload_relative.parts)).resolve()
                if temporary_root.resolve() not in destination.parents:
                    raise DomainRuleError("PROJECT_PACKAGE_PATH_INVALID", "项目包 payload 展开越界")
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, destination.open("xb") as target:
                    shutil.copyfileobj(source, target, length=1024 * 1024)

    def import_as_copy(
        self,
        stage_token: str,
        *,
        code: str,
        title: str,
        actor: str = "local-user",
        request_id: str | None = None,
        simulate_failure: bool = False,
    ) -> dict[str, Any]:
        validate_project_code(code)
        if not title or len(title) > 200:
            raise DomainRuleError("INVALID_PROJECT_TITLE", "项目标题必须是 1—200 个字符")
        package = self._staged_package(stage_token)
        state = self._read_state(package)
        source_project = state["project"]
        identity_mode = "IMPORT_AS_COPY_REWRITE_IDENTITY"
        operation_key = hashlib.sha256(f"{stage_token}\0{identity_mode}\0{code}".encode()).hexdigest()
        now = _utc_now()
        with self.database.transaction() as connection:
            receipt = connection.execute("SELECT * FROM project_package_imports WHERE operation_key=?", (operation_key,)).fetchone()
            if receipt is not None and receipt["status"] == "COMPLETED":
                result = cast(dict[str, Any], json.loads(str(receipt["result_json"])))
                target = connection.execute("SELECT id,code FROM projects WHERE id=?", (receipt["target_project_id"],)).fetchone()
                if target is None or str(target["code"]) != code:
                    raise DomainRuleError("PROJECT_PACKAGE_RECEIPT_INCONSISTENT", "项目包 receipt 与项目记录不一致")
                result["reused"] = True
                return result
            if receipt is not None and receipt["status"] == "PREPARING":
                updated_at = datetime.fromisoformat(str(receipt["updated_at"]).replace("Z", "+00:00"))
                if datetime.now(UTC) - updated_at < timedelta(minutes=15):
                    raise DomainRuleError("PROJECT_PACKAGE_IMPORT_IN_PROGRESS", "相同项目包导入仍在进行，请稍后重试")
            if receipt is None:
                project_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO project_package_imports (operation_key,stage_token,identity_mode,source_project_id,
                    target_project_id,target_code,status,result_json,created_at,updated_at,created_by)
                    VALUES (?,?,?,?,?,?,'PREPARING','{}',?,?,?)""",
                    (operation_key, stage_token, identity_mode, source_project["id"], project_id, code, now, now, actor),
                )
                prior_status = None
            else:
                project_id = str(receipt["target_project_id"])
                prior_status = str(receipt["status"])
                connection.execute(
                    "UPDATE project_package_imports SET status='PREPARING',last_error_code=NULL,updated_at=? WHERE operation_key=?",
                    (now, operation_key),
                )
        def fail_receipt(error_code: str) -> None:
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE project_package_imports SET status='FAILED',last_error_code=?,updated_at=? WHERE operation_key=?",
                    (error_code, _utc_now(), operation_key),
                )
        final_root = (self.projects_root / code).resolve()
        if final_root.parent != self.projects_root:
            fail_receipt("PROJECT_PACKAGE_IMPORT_TARGET_INVALID")
            raise DomainRuleError("PROJECT_PACKAGE_IMPORT_TARGET_INVALID", "项目导入目标目录越界")
        with self.database.connect() as connection:
            existing_project = connection.execute("SELECT id FROM projects WHERE code=?", (code,)).fetchone()
            if existing_project is not None:
                fail_receipt("PROJECT_CODE_EXISTS")
                raise DomainRuleError("PROJECT_CODE_EXISTS", "项目 code 已存在", {"code": code})
        if final_root.exists():
            marker = final_root / "project.json"
            marker_data: dict[str, Any] = {}
            try:
                marker_data = json.loads(marker.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            owned_recovery = (prior_status in {"FAILED", "PREPARING"} and marker_data.get("project_id") == project_id
                              and marker_data.get("imported_from_package_sha256") == stage_token)
            if not owned_recovery:
                fail_receipt("PROJECT_ROOT_EXISTS")
                raise DomainRuleError("PROJECT_ROOT_EXISTS", "目标项目目录已存在，未覆盖", {"code": code})
            shutil.rmtree(final_root)
        temporary_root = self.projects_root / f".{code}.import-{uuid.uuid4().hex}"
        promoted = False
        counts = {"seasons": 0, "episodes": 0, "scenes": 0, "shots": 0, "profiles": 0, "profiles_skipped": 0,
                  "delivery_targets": 0, "media_assets": 0, "media_versions": 0, "thumbnails_pending": 0, "thumbnails_created": 0,
                  "thumbnails_failed": 0, "payload_files": 0, "story_assets": 0, "story_asset_proposals": 0,
                  "story_asset_states": 0,
                  "story_asset_references": 0, "episode_asset_state_bindings": 0, "shot_asset_bindings": 0,
                  "generation_preference_sets": 0, "generation_preference_versions": 0,
                  "generation_preference_sets_skipped_missing_profiles": 0,
                  "shot_groups": 0, "shot_group_members": 0,
                  "generation_qc_policy_sets": 0, "generation_qc_policy_versions": 0,
                  "director_recipes": 0, "director_recipe_versions": 0,
                  "project_director_recipe_bindings": 0}
        thumbnail_media_version_ids: list[str] = []
        try:
            self._extract_payload(package, temporary_root)
            counts["payload_files"] = sum(1 for item in temporary_root.rglob("*") if item.is_file())
            now = _utc_now()
            project_json = {"schema_version": "localdrama.project.v2", "project_id": project_id, "project_code": code,
                            "title": title, "created_at": now, "template_version": TEMPLATE_VERSION,
                            "initial_season": "SEASON_001", "legacy_refs": [], "imported_from_package_sha256": stage_token}
            (temporary_root / "project.json").write_text(json.dumps(project_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if _sha256(package) != stage_token:
                raise DomainRuleError("PROJECT_PACKAGE_STAGE_NOT_FOUND", "提交前 staged 项目包完整性失败")
            replace_path(temporary_root, final_root)
            promoted = True
            season_map = {str(item["id"]): str(uuid.uuid4()) for item in state["seasons"]}
            episode_map = {str(item["id"]): str(uuid.uuid4()) for item in state["episodes"]}
            scene_map = {str(item["id"]): str(uuid.uuid4()) for item in state["scenes"]}
            shot_map = {str(item["id"]): str(uuid.uuid4()) for item in state["shots"]}
            shot_group_map = {str(item["id"]): str(uuid.uuid4()) for item in state["shot_groups"]}
            media_asset_map = {str(item["id"]): str(uuid.uuid4()) for item in state["media_assets"]}
            media_version_map = {str(item["id"]): str(uuid.uuid4()) for item in state["media_versions"]}
            story_asset_map = {str(item["id"]): str(uuid.uuid4()) for item in state["story_assets"]}
            asset_proposal_map = {str(item["id"]): str(uuid.uuid4()) for item in state["story_asset_proposals"]}
            asset_state_map = {str(item["id"]): str(uuid.uuid4()) for item in state["story_asset_states"]}
            preference_set_map = {str(item["id"]): str(uuid.uuid4()) for item in state["generation_preference_sets"]}
            preference_version_map = {str(item["id"]): str(uuid.uuid4()) for item in state["generation_preference_versions"]}
            qc_policy_set_map = {str(item["id"]): str(uuid.uuid4()) for item in state["generation_qc_policy_sets"]}
            qc_policy_version_map = {str(item["id"]): str(uuid.uuid4()) for item in state["generation_qc_policy_versions"]}
            director_recipe_map = {str(item["id"]): str(uuid.uuid4()) for item in state["director_recipes"]}
            director_recipe_version_map = {str(item["id"]): str(uuid.uuid4()) for item in state["director_recipe_versions"]}
            media_kind_by_asset = {str(item["id"]): str(item["media_kind"]) for item in state["media_assets"]}
            with self.database.transaction() as connection:
                if connection.execute("SELECT 1 FROM projects WHERE code=?", (code,)).fetchone():
                    raise DomainRuleError("PROJECT_CODE_EXISTS", "项目 code 已存在", {"code": code})
                connection.execute(
                    """INSERT INTO projects (id,code,title,status,template_version,root_rel,aspect_ratio,fps_num,fps_den,timezone,
                    width,height,primary_language,subtitle_mode,subtitle_language,created_at,updated_at,created_by)
                    VALUES (?,?,?,'DRAFT',?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (project_id, code, title, TEMPLATE_VERSION, code, source_project.get("aspect_ratio"), source_project.get("fps_num"),
                     source_project.get("fps_den"), source_project.get("timezone") or "Asia/Shanghai", source_project.get("width"),
                     source_project.get("height"), source_project.get("primary_language"), source_project.get("subtitle_mode"),
                     source_project.get("subtitle_language"), now, now, actor),
                )
                for season in state["seasons"]:
                    connection.execute(
                        "INSERT INTO seasons (id,project_id,number,code,title,display_order,created_at,updated_at,created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                        (season_map[str(season["id"])], project_id, season["number"], season["code"], season["title"],
                         season["display_order"], now, now, actor),
                    )
                    counts["seasons"] += 1
                for episode in state["episodes"]:
                    connection.execute(
                        """INSERT INTO episodes (id,season_id,number,display_order,code,title,narrative_status,production_status,
                        target_duration_ms,source_range_json,created_at,updated_at,created_by)
                        VALUES (?,?,?,?,?,?,'OUTLINE','NOT_STARTED',?,'{}',?,?,?)""",
                        (episode_map[str(episode["id"])], season_map[str(episode["season_id"])], episode["number"],
                         episode["display_order"], episode["code"], episode["title"], episode["target_duration_ms"], now, now, actor),
                    )
                    counts["episodes"] += 1
                for scene in state["scenes"]:
                    connection.execute(
                        "INSERT INTO scenes (id,project_id,code,title,location,time_of_day,created_at,updated_at,created_by) VALUES (?,?,?,?,?,?,?,?,?)",
                        (scene_map[str(scene["id"])], project_id, scene["code"], scene["title"], scene.get("location"), scene.get("time_of_day"), now, now, actor),
                    )
                    counts["scenes"] += 1
                for shot in state["shots"]:
                    shot_id, revision_id = shot_map[str(shot["id"])], str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO shots (id,episode_id,scene_id,code,order_key,target_duration_ms,shot_type,status,current_revision_id,
                        created_at,updated_at,created_by) VALUES (?,?,?,?,?,?,?,'DRAFT',?,?,?,?)""",
                        (shot_id, episode_map[str(shot["episode_id"])], scene_map[str(shot["scene_id"])] if shot.get("scene_id") is not None else None,
                         shot["code"], shot["order_key"], shot["target_duration_ms"], shot.get("shot_type") or "OTHER", revision_id, now, now, actor),
                    )
                    connection.execute(
                        "INSERT INTO shot_revisions (id,shot_id,revision_no,fields_json,is_frozen,created_at,updated_at,created_by) VALUES (?,?,1,?,0,?,?,?)",
                        (revision_id, shot_id, json.dumps(shot.get("fields") or {}, ensure_ascii=False), now, now, actor),
                    )
                    counts["shots"] += 1
                for group in state["shot_groups"]:
                    connection.execute(
                        """INSERT INTO shot_groups
                        (id,episode_id,scene_id,kind,code,title,order_key,metadata_json,status,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (shot_group_map[str(group["id"])], episode_map[str(group["episode_id"])],
                         scene_map[str(group["scene_id"])] if group.get("scene_id") is not None else None,
                         group["kind"], group["code"], group.get("title") or "", group["order_key"],
                         json.dumps(group.get("metadata") or {}, ensure_ascii=False, sort_keys=True), group.get("status") or "ACTIVE",
                         group.get("created_at") or now, group.get("updated_at") or now, group.get("created_by") or actor,
                         int(group.get("revision") or 1), group.get("schema_version") or "v1"),
                    )
                    counts["shot_groups"] += 1
                for member in state["shot_group_members"]:
                    connection.execute(
                        "INSERT INTO shot_group_members (group_id,shot_id,order_key,created_at,created_by) VALUES (?,?,?,?,?)",
                        (shot_group_map[str(member["group_id"])], shot_map[str(member["shot_id"])], member["order_key"],
                         member.get("created_at") or now, member.get("created_by") or actor),
                    )
                    counts["shot_group_members"] += 1
                for asset in state["media_assets"]:
                    owner_type = str(asset["owner_type"])
                    source_owner_id = str(asset["owner_id"])
                    owner_id: str | None
                    if owner_type == "PROJECT":
                        owner_id = project_id
                    elif owner_type == "EPISODE":
                        owner_id = episode_map.get(source_owner_id)
                    elif owner_type == "SHOT":
                        owner_id = shot_map.get(source_owner_id)
                    else:
                        owner_id = None
                    if owner_id is None:
                        raise DomainRuleError("PROJECT_PACKAGE_MEDIA_OWNER_UNSUPPORTED", "媒体 owner 无法安全重写",
                                              {"owner_type": owner_type, "owner_id": source_owner_id})
                    connection.execute(
                        """INSERT INTO media_assets (id,project_id,owner_type,owner_id,purpose,media_kind,selected_version_id,
                        approved_version_id,version_counter,metadata_json,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,NULL,NULL,?,?,?, ?,?,1,'v2')""",
                        (media_asset_map[str(asset["id"])], project_id, owner_type, owner_id, asset["purpose"], asset["media_kind"],
                         asset["version_counter"], json.dumps(asset.get("metadata") or {}, ensure_ascii=False), now, now, actor),
                    )
                    counts["media_assets"] += 1
                for version in state["media_versions"]:
                    relative = _safe_member(str(version["rel_path"]))
                    media_path = (final_root / Path(*relative.parts)).resolve()
                    if final_root not in media_path.parents or not media_path.is_file() or _is_reparse(media_path):
                        raise DomainRuleError("PROJECT_PACKAGE_MEDIA_FILE_MISSING", "导入媒体文件缺失或越界", {"rel_path": str(relative)})
                    if media_path.stat().st_size != int(version["byte_size"]) or _sha256(media_path) != str(version["sha256"]):
                        raise DomainRuleError("PROJECT_PACKAGE_MEDIA_HASH_MISMATCH", "导入媒体文件 hash/size 不匹配", {"rel_path": str(relative)})
                    parent = version.get("parent_version_id")
                    connection.execute(
                        """INSERT INTO media_versions (id,media_asset_id,version_no,take_no,stage,rel_path,mime_type,byte_size,
                        sha256,duration_ms,fps_num,fps_den,parent_version_id,source_job_attempt_id,integrity_status,source_name,
                        import_source,probe_json,source_artifact_id,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,NULL,?,?,?,1,'v2')""",
                        (media_version_map[str(version["id"])], media_asset_map[str(version["media_asset_id"])], version["version_no"],
                         version.get("take_no") or 1, version["stage"], relative.as_posix(), version["mime_type"], version["byte_size"],
                         version["sha256"], version.get("duration_ms"), version.get("fps_num"), version.get("fps_den"),
                         media_version_map.get(str(parent)) if parent else None, "VERIFIED", version.get("source_name"),
                         "PROJECT_PACKAGE", json.dumps(version.get("probe") or {}, ensure_ascii=False), now, now, actor),
                    )
                    counts["media_versions"] += 1
                    if media_kind_by_asset[str(version["media_asset_id"])] in {"IMAGE", "VIDEO"}:
                        thumbnail_media_version_ids.append(media_version_map[str(version["id"])])
                        counts["thumbnails_pending"] += 1
                for asset in state["story_assets"]:
                    canonical = asset.get("canonical_media_version_id")
                    connection.execute(
                        """INSERT INTO story_assets
                        (id,project_id,kind,code,name,description,canonical_media_version_id,extra_json,status,
                        created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (story_asset_map[str(asset["id"])], project_id, asset["kind"], asset["code"], asset["name"],
                         asset.get("description") or "", media_version_map[str(canonical)] if canonical is not None else None,
                         json.dumps(asset.get("extra") or {}, ensure_ascii=False, sort_keys=True), asset.get("status") or "ACTIVE",
                         asset.get("created_at") or now, asset.get("updated_at") or now, asset.get("created_by") or actor,
                         int(asset.get("revision") or 1), asset.get("schema_version") or "v2"),
                    )
                    counts["story_assets"] += 1
                for proposal in state["story_asset_proposals"]:
                    source_draft_id = proposal.get("breakdown_draft_id")
                    evidence = dict(proposal.get("evidence") or {})
                    if source_draft_id is not None:
                        evidence.setdefault("source_breakdown_draft_id", source_draft_id)
                    suggested_id, resolved_id = proposal.get("suggested_asset_id"), proposal.get("resolved_asset_id")
                    connection.execute(
                        """INSERT INTO story_asset_proposals
                        (id,project_id,breakdown_draft_id,proposal_key,kind,name,evidence_json,suggested_asset_id,
                         resolved_asset_id,status,decision_note,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,NULL,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (asset_proposal_map[str(proposal["id"])], project_id, proposal["proposal_key"], proposal["kind"],
                         proposal["name"], json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                         story_asset_map[str(suggested_id)] if suggested_id is not None else None,
                         story_asset_map[str(resolved_id)] if resolved_id is not None else None,
                         proposal.get("status") or "PENDING", proposal.get("decision_note") or "",
                         proposal.get("created_at") or now, proposal.get("updated_at") or now,
                         proposal.get("created_by") or actor, int(proposal.get("revision") or 1),
                         proposal.get("schema_version") or "v2"),
                    )
                    counts["story_asset_proposals"] += 1
                for asset_state in state["story_asset_states"]:
                    connection.execute(
                        """INSERT INTO story_asset_states
                        (id,project_id,story_asset_id,code,label,state_kind,description,state_json,status,
                        created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (asset_state_map[str(asset_state["id"])], project_id,
                         story_asset_map[str(asset_state["story_asset_id"])], asset_state["code"], asset_state["label"],
                         asset_state["state_kind"], asset_state.get("description") or "",
                         json.dumps(asset_state.get("state") or {}, ensure_ascii=False, sort_keys=True),
                         asset_state.get("status") or "ACTIVE", asset_state.get("created_at") or now,
                         asset_state.get("updated_at") or now, asset_state.get("created_by") or actor,
                         int(asset_state.get("revision") or 1), asset_state.get("schema_version") or "v1"),
                    )
                    counts["story_asset_states"] += 1
                for reference in state["story_asset_references"]:
                    source_state_id = reference.get("asset_state_id")
                    connection.execute(
                        """INSERT INTO story_asset_references
                        (id,project_id,story_asset_id,asset_state_id,media_version_id,reference_kind,label,priority,is_locked,
                        yaw_deg,pitch_deg,metadata_json,status,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), project_id, story_asset_map[str(reference["story_asset_id"])],
                         asset_state_map[str(source_state_id)] if source_state_id is not None else None,
                         media_version_map[str(reference["media_version_id"])], reference["reference_kind"],
                         reference.get("label") or "", int(reference.get("priority", 100)),
                         1 if reference.get("is_locked") else 0, reference.get("yaw_deg"), reference.get("pitch_deg"),
                         json.dumps(reference.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
                         reference.get("status") or "ACTIVE", reference.get("created_at") or now,
                         reference.get("updated_at") or now, reference.get("created_by") or actor,
                         int(reference.get("revision") or 1), reference.get("schema_version") or "v1"),
                    )
                    counts["story_asset_references"] += 1
                for binding in state["episode_asset_state_bindings"]:
                    connection.execute(
                        """INSERT INTO episode_asset_state_bindings
                        (id,episode_id,story_asset_id,asset_state_id,created_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), episode_map[str(binding["episode_id"])],
                         story_asset_map[str(binding["story_asset_id"])], asset_state_map[str(binding["asset_state_id"])],
                         binding.get("created_at") or now, binding.get("created_by") or actor,
                         int(binding.get("revision") or 1), binding.get("schema_version") or "v1"),
                    )
                    counts["episode_asset_state_bindings"] += 1
                for binding in state["shot_asset_bindings"]:
                    source_state_id = binding.get("asset_state_id")
                    connection.execute(
                        """INSERT INTO shot_asset_bindings
                        (id,shot_id,asset_id,asset_state_id,role_in_shot,created_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), shot_map[str(binding["shot_id"])], story_asset_map[str(binding["asset_id"])],
                         asset_state_map[str(source_state_id)] if source_state_id is not None else None,
                         binding.get("role_in_shot") or "main", binding.get("created_at") or now,
                         binding.get("created_by") or actor, int(binding.get("revision") or 1),
                         binding.get("schema_version") or "v2"),
                    )
                    counts["shot_asset_bindings"] += 1
                preference_versions_by_set: dict[str, list[dict[str, Any]]] = {}
                for preference_version in state["generation_preference_versions"]:
                    preference_versions_by_set.setdefault(str(preference_version["preference_set_id"]), []).append(preference_version)
                for preference in state["generation_preference_sets"]:
                    source_set_id = str(preference["id"])
                    source_versions = preference_versions_by_set.get(source_set_id, [])
                    required_profiles = {
                        str(item["execution_profile_version_id"])
                        for item in source_versions if item.get("execution_profile_version_id") is not None
                    }
                    missing_profiles = [profile_id for profile_id in required_profiles if connection.execute(
                        "SELECT 1 FROM execution_profile_versions WHERE id=?", (profile_id,)
                    ).fetchone() is None]
                    if missing_profiles:
                        # A preference version is immutable evidence.  Dropping
                        # only its profile or inventing a replacement would
                        # falsify history, so skip the entire set atomically.
                        counts["generation_preference_sets_skipped_missing_profiles"] += 1
                        continue
                    owner_type, source_owner_id = str(preference["owner_type"]), str(preference["owner_id"])
                    owner_id = project_id if owner_type == "PROJECT" else (
                        episode_map[source_owner_id] if owner_type == "EPISODE" else shot_map[source_owner_id]
                    )
                    target_set_id = preference_set_map[source_set_id]
                    connection.execute(
                        """INSERT INTO generation_preference_sets
                        (id,project_id,owner_type,owner_id,capability,current_version_id,status,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,NULL,?,?,?,?,?,?)""",
                        (target_set_id, project_id, owner_type, owner_id, preference["capability"],
                         preference.get("status") or "ACTIVE", preference.get("created_at") or now,
                         preference.get("updated_at") or now, preference.get("created_by") or actor,
                         int(preference.get("revision") or 1), preference.get("schema_version") or "v1"),
                    )
                    for preference_version in source_versions:
                        source_version_id = str(preference_version["id"])
                        connection.execute(
                            """INSERT INTO generation_preference_versions
                            (id,preference_set_id,version_no,execution_profile_version_id,resolution_mode,settings_json,
                            reason,is_frozen,created_at,created_by,schema_version)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            (preference_version_map[source_version_id], target_set_id,
                             int(preference_version["version_no"]), preference_version.get("execution_profile_version_id"),
                             preference_version["resolution_mode"],
                             json.dumps(preference_version.get("settings") or {}, ensure_ascii=False, sort_keys=True),
                             preference_version.get("reason") or "", 1 if preference_version.get("is_frozen", True) else 0,
                             preference_version.get("created_at") or now, preference_version.get("created_by") or actor,
                             preference_version.get("schema_version") or "v1"),
                        )
                        counts["generation_preference_versions"] += 1
                    source_current_id = preference.get("current_version_id")
                    if source_current_id is not None:
                        connection.execute(
                            "UPDATE generation_preference_sets SET current_version_id=? WHERE id=?",
                            (preference_version_map[str(source_current_id)], target_set_id),
                        )
                    counts["generation_preference_sets"] += 1
                qc_versions_by_set: dict[str, list[dict[str, Any]]] = {}
                for policy_version in state["generation_qc_policy_versions"]:
                    qc_versions_by_set.setdefault(str(policy_version["policy_set_id"]), []).append(policy_version)
                for policy_set in state["generation_qc_policy_sets"]:
                    source_set_id = str(policy_set["id"])
                    owner_type, source_owner_id = str(policy_set["owner_type"]), str(policy_set["owner_id"])
                    owner_id = project_id if owner_type == "PROJECT" else (
                        episode_map[source_owner_id] if owner_type == "EPISODE" else shot_map[source_owner_id]
                    )
                    target_set_id = qc_policy_set_map[source_set_id]
                    connection.execute(
                        """INSERT INTO generation_qc_policy_sets
                        (id,project_id,owner_type,owner_id,stage,current_version_id,status,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,NULL,?,?,?,?,?,?)""",
                        (target_set_id, project_id, owner_type, owner_id, policy_set["stage"], policy_set.get("status") or "ACTIVE",
                         policy_set.get("created_at") or now, policy_set.get("updated_at") or now,
                         policy_set.get("created_by") or actor, int(policy_set.get("revision") or 1), policy_set.get("schema_version") or "v1"),
                    )
                    for policy_version in qc_versions_by_set.get(source_set_id, []):
                        source_version_id = str(policy_version["id"])
                        connection.execute(
                            """INSERT INTO generation_qc_policy_versions
                            (id,policy_set_id,version_no,policy_json,max_auto_rerolls,auto_reroll_categories_json,
                            is_frozen,reason,created_at,created_by,schema_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                            (qc_policy_version_map[source_version_id], target_set_id, int(policy_version["version_no"]),
                             json.dumps(policy_version.get("policy") or {}, ensure_ascii=False, sort_keys=True),
                             int(policy_version.get("max_auto_rerolls") or 0),
                             json.dumps(policy_version.get("auto_reroll_categories") or [], ensure_ascii=False, sort_keys=True),
                             1 if policy_version.get("is_frozen", True) else 0, policy_version.get("reason") or "",
                             policy_version.get("created_at") or now, policy_version.get("created_by") or actor,
                             policy_version.get("schema_version") or "v1"),
                        )
                        counts["generation_qc_policy_versions"] += 1
                    source_current_id = policy_set.get("current_version_id")
                    if source_current_id is not None:
                        connection.execute(
                            "UPDATE generation_qc_policy_sets SET current_version_id=? WHERE id=?",
                            (qc_policy_version_map[str(source_current_id)], target_set_id),
                        )
                    counts["generation_qc_policy_sets"] += 1
                recipe_versions_by_recipe: dict[str, list[dict[str, Any]]] = {}
                for recipe_version in state["director_recipe_versions"]:
                    recipe_versions_by_recipe.setdefault(str(recipe_version["recipe_id"]), []).append(recipe_version)
                for recipe in state["director_recipes"]:
                    source_recipe_id = str(recipe["id"])
                    target_recipe_id = director_recipe_map[source_recipe_id]
                    connection.execute(
                        """INSERT INTO director_recipes
                        (id,project_id,code,title,status,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?,?,?)""",
                        (target_recipe_id, project_id, recipe["code"], recipe["title"], recipe.get("status") or "ACTIVE",
                         recipe.get("created_at") or now, recipe.get("updated_at") or now, recipe.get("created_by") or actor,
                         int(recipe.get("revision") or 1), recipe.get("schema_version") or "v1"),
                    )
                    for recipe_version in recipe_versions_by_recipe.get(source_recipe_id, []):
                        source_version_id = str(recipe_version["id"])
                        rewritten_recipe = json.loads(json.dumps(recipe_version.get("recipe") or {}))
                        qc_ref = rewritten_recipe.get("qc_policy_ref")
                        if isinstance(qc_ref, dict) and qc_ref.get("policy_version_id") is not None:
                            qc_ref["policy_version_id"] = qc_policy_version_map[str(qc_ref["policy_version_id"])]
                        canonical = canonical_recipe(rewritten_recipe)
                        rewritten_hash = recipe_hash(rewritten_recipe)
                        connection.execute(
                            """INSERT INTO director_recipe_versions
                            (id,recipe_id,version_no,recipe_json,recipe_hash,reason,is_frozen,created_at,created_by,schema_version)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (director_recipe_version_map[source_version_id], target_recipe_id, int(recipe_version["version_no"]),
                             canonical, rewritten_hash, recipe_version.get("reason") or "",
                             1 if recipe_version.get("is_frozen", True) else 0, recipe_version.get("created_at") or now,
                             recipe_version.get("created_by") or actor, recipe_version.get("schema_version") or "v1"),
                        )
                        counts["director_recipe_versions"] += 1
                    counts["director_recipes"] += 1
                recipe_binding = state.get("project_director_recipe_binding")
                if isinstance(recipe_binding, dict):
                    connection.execute(
                        """INSERT INTO project_director_recipe_bindings
                        (project_id,recipe_version_id,reason,created_at,updated_at,created_by,revision,schema_version)
                        VALUES (?,?,?,?,?,?,?,?)""",
                        (project_id, director_recipe_version_map[str(recipe_binding["recipe_version_id"])],
                         recipe_binding.get("reason") or "", recipe_binding.get("created_at") or now,
                         recipe_binding.get("updated_at") or now, recipe_binding.get("created_by") or actor,
                         int(recipe_binding.get("revision") or 1), recipe_binding.get("schema_version") or "v1"),
                    )
                    counts["project_director_recipe_bindings"] += 1
                plan = state.get("production_plan")
                if isinstance(plan, dict):
                    plan_id, version_id = str(uuid.uuid4()), str(uuid.uuid4())
                    plan_code = f"{code[:70]}_import_{project_id[:8]}"
                    connection.execute("INSERT INTO production_plans (id,code,title,created_at,updated_at,created_by) VALUES (?,?,?,?,?,?)",
                                       (plan_id, plan_code, plan.get("title") or "Imported plan", now, now, actor))
                    connection.execute(
                        "INSERT INTO production_plan_versions (id,production_plan_id,version_no,plan_json,status,created_at,updated_at,created_by) VALUES (?,?,1,?,'ACTIVE',?,?,?)",
                        (version_id, plan_id, plan.get("plan_json") or "{}", now, now, actor),
                    )
                    connection.execute("INSERT INTO project_plan_bindings (id,project_id,production_plan_version_id,created_at,updated_at,created_by) VALUES (?,?,?,?,?,?)",
                                       (str(uuid.uuid4()), project_id, version_id, now, now, actor))
                    connection.execute("UPDATE projects SET production_plan_version_id=? WHERE id=?", (version_id, project_id))
                for profile in state["profile_bindings"]:
                    profile_version_id = str(profile.get("execution_profile_version_id") or "")
                    exists = connection.execute(
                        "SELECT capability FROM execution_profile_versions WHERE id=? AND status='PUBLISHED'", (profile_version_id,)
                    ).fetchone()
                    if not exists:
                        counts["profiles_skipped"] += 1
                        continue
                    try:
                        package_capability = normalize_capability(str(profile.get("capability") or ""))
                        profile_capability = normalize_capability(str(exists["capability"]))
                    except ValueError as error:
                        raise DomainRuleError(
                            "PROFILE_CAPABILITY_INVALID",
                            "项目包 Profile 绑定 capability 未知或含义不唯一",
                            {"profile_version_id": profile_version_id},
                        ) from error
                    if package_capability != profile_capability:
                        raise DomainRuleError(
                            "PROFILE_CAPABILITY_MISMATCH",
                            "项目包绑定 capability 与 Profile 版本不一致",
                            {"profile_version_id": profile_version_id},
                        )
                    connection.execute(
                        """INSERT INTO project_profile_bindings (id,project_id,capability,execution_profile_version_id,status,
                        created_at,updated_at,created_by) VALUES (?,?,?,?,'ACTIVE',?,?,?)""",
                        (str(uuid.uuid4()), project_id, package_capability, profile_version_id, now, now, actor),
                    )
                    counts["profiles"] += 1
                for target in state["delivery_targets"]:
                    target_id, target_version_id = str(uuid.uuid4()), str(uuid.uuid4())
                    spec = target.get("target_spec_json") or "{}"
                    connection.execute(
                        """INSERT INTO delivery_targets (id,project_id,code,title,transport,target_spec_json,status,created_at,updated_at,created_by)
                        VALUES (?,?,?,?,?,?,'ACTIVE',?,?,?)""",
                        (target_id, project_id, target["code"], target["title"], target.get("transport") or "LOCAL_FILESYSTEM", spec, now, now, actor),
                    )
                    connection.execute(
                        """INSERT INTO delivery_target_versions (id,delivery_target_id,version_no,target_spec_json,status,created_at,updated_at,created_by)
                        VALUES (?,?,1,?,'ACTIVE',?,?,?)""",
                        (target_version_id, target_id, spec, now, now, actor),
                    )
                    counts["delivery_targets"] += 1
                metadata = {"stage_token": stage_token, "source_project_id": source_project["id"], "identity_mode": "IMPORT_AS_COPY_REWRITE_IDENTITY",
                            "counts": counts, "excluded_domains": state.get("excluded_domains", [])}
                connection.execute(
                    """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,request_id,summary,metadata_redacted_json)
                    VALUES (?,'producer','PROJECT_PACKAGE_IMPORTED','project',?,?,?,?)""",
                    (actor, project_id, request_id, f"从项目包导入 {code}", json.dumps(metadata, ensure_ascii=False, sort_keys=True)),
                )
                connection.execute(
                    "INSERT INTO outbox_events (type,project_id,subject_type,subject_id,payload_json) VALUES ('project.changed',?,'project',?,?)",
                    (project_id, project_id, json.dumps({"status": "DRAFT", "revision": 1}, sort_keys=True)),
                )
                if simulate_failure:
                    raise RuntimeError("simulated project package import failure")
                result = {"status": "IMPORTED", "identity_mode": identity_mode, "project_id": project_id,
                          "project_code": code, "source_project_id": source_project["id"], "stage_token": stage_token,
                          "counts": counts, "staged_package_retained": True, "runtime_contacted": False,
                          "network_contacted": False, "reused": False}
                connection.execute(
                    """UPDATE project_package_imports SET status='COMPLETED',result_json=?,last_error_code=NULL,updated_at=?
                    WHERE operation_key=?""",
                    (json.dumps(result, ensure_ascii=False, sort_keys=True), _utc_now(), operation_key),
                )
            thumbnail_result = self._rebuild_thumbnails(thumbnail_media_version_ids)
            counts["thumbnails_created"] = int(thumbnail_result["created"])
            counts["thumbnails_failed"] = int(thumbnail_result["failed"])
            counts["thumbnails_pending"] = max(0, len(thumbnail_media_version_ids) - counts["thumbnails_created"] - counts["thumbnails_failed"])
            result["counts"] = counts
            result["thumbnail_rebuild"] = thumbnail_result
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE project_package_imports SET result_json=?,updated_at=? WHERE operation_key=?",
                    (json.dumps(result, ensure_ascii=False, sort_keys=True), _utc_now(), operation_key),
                )
        except Exception as error:
            if temporary_root.exists():
                shutil.rmtree(temporary_root, ignore_errors=True)
            if promoted and final_root.exists():
                shutil.rmtree(final_root, ignore_errors=True)
            error_code = error.code if isinstance(error, DomainRuleError) else type(error).__name__
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE project_package_imports SET status='FAILED',last_error_code=?,updated_at=? WHERE operation_key=?",
                    (error_code, _utc_now(), operation_key),
                )
            raise
        return result

    def rebind_existing(
        self,
        stage_token: str,
        *,
        actor: str = "local-user",
        request_id: str | None = None,
        simulate_failure: bool = False,
    ) -> dict[str, Any]:
        package = self._staged_package(stage_token)
        state = self._read_state(package)
        package_project = state["project"]
        project_id, project_code = str(package_project["id"]), str(package_project["code"])
        with self.database.connect() as connection:
            row = connection.execute("SELECT id,code,title,root_rel FROM projects WHERE id=?", (project_id,)).fetchone()
            code_row = connection.execute("SELECT id FROM projects WHERE code=?", (project_code,)).fetchone()
        if row is None or str(row["code"]) != project_code or code_row is None or str(code_row["id"]) != project_id:
            raise DomainRuleError("PROJECT_PACKAGE_REBIND_IDENTITY_MISMATCH", "rebind 要求本地项目 ID 与 code 同时匹配")
        final_root = controlled_path(
            self.projects_root,
            str(row["root_rel"]),
            code="PROJECT_ROOT_INVALID",
        )
        if final_root.parent != self.projects_root:
            raise DomainRuleError("PROJECT_ROOT_INVALID", "项目根目录配置越界")
        if final_root.exists():
            raise DomainRuleError("PROJECT_PACKAGE_REBIND_ROOT_EXISTS", "项目目录仍存在；rebind 不允许覆盖现有目录")
        temporary_root = self.projects_root / f".{project_code}.rebind-{uuid.uuid4().hex}"
        promoted = False
        media_kind_by_asset = {str(asset["id"]): str(asset["media_kind"]) for asset in state.get("media_assets", [])}
        thumbnail_media_version_ids = [
            str(version["id"])
            for version in state.get("media_versions", [])
            if media_kind_by_asset.get(str(version.get("media_asset_id"))) in {"IMAGE", "VIDEO"}
        ]
        try:
            self._extract_payload(package, temporary_root)
            now = _utc_now()
            project_json = {"schema_version": "localdrama.project.v2", "project_id": project_id, "project_code": project_code,
                            "title": row["title"], "created_at": now, "template_version": TEMPLATE_VERSION,
                            "initial_season": "SEASON_001", "legacy_refs": [], "rebound_from_package_sha256": stage_token}
            (temporary_root / "project.json").write_text(json.dumps(project_json, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            if _sha256(package) != stage_token:
                raise DomainRuleError("PROJECT_PACKAGE_STAGE_NOT_FOUND", "提交前 staged 项目包完整性失败")
            replace_path(temporary_root, final_root)
            promoted = True
            with self.database.transaction() as connection:
                current = connection.execute("SELECT id,code FROM projects WHERE id=?", (project_id,)).fetchone()
                if current is None or str(current["code"]) != project_code:
                    raise DomainRuleError("PROJECT_PACKAGE_REBIND_IDENTITY_MISMATCH", "提交期间项目身份发生变化")
                metadata = {"stage_token": stage_token, "identity_mode": "REBIND_EXISTING", "database_structure_changed": False}
                connection.execute(
                    """INSERT INTO audit_events (actor,role_context,action,subject_type,subject_id,request_id,summary,metadata_redacted_json)
                    VALUES (?,'producer','PROJECT_PACKAGE_REBOUND','project',?,?,?,?)""",
                    (actor, project_id, request_id, f"从项目包恢复目录 {project_code}", json.dumps(metadata, sort_keys=True)),
                )
                if simulate_failure:
                    raise RuntimeError("simulated project package rebind failure")
        except Exception:
            if temporary_root.exists():
                shutil.rmtree(temporary_root, ignore_errors=True)
            if promoted and final_root.exists():
                shutil.rmtree(final_root, ignore_errors=True)
            raise
        thumbnail_result = self._rebuild_thumbnails(thumbnail_media_version_ids)
        return {"status": "REBOUND", "identity_mode": "REBIND_EXISTING", "project_id": project_id,
                "project_code": project_code, "stage_token": stage_token, "database_structure_changed": False,
                "staged_package_retained": True, "thumbnail_rebuild": thumbnail_result,
                "runtime_contacted": False, "network_contacted": False}
