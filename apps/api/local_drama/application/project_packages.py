from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

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
    path = PurePosixPath(name)
    if not name or "\\" in name or path.is_absolute() or ".." in path.parts or any(not part for part in path.parts):
        raise DomainRuleError("PROJECT_PACKAGE_PATH_INVALID", "项目包包含不安全路径", {"path": name})
    return path


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


class ProjectPackageService:
    def __init__(self, database: Database, projects_root: Path) -> None:
        self.database = database
        self.projects_root = projects_root.resolve()

    def _project(self, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        return dict(row)

    def _root(self, project: dict[str, Any]) -> Path:
        root = (self.projects_root / str(project["root_rel"])).resolve()
        if root.parent != self.projects_root or not root.is_dir() or _is_reparse(root):
            raise DomainRuleError("PROJECT_ROOT_INVALID", "项目根目录缺失或越界")
        return root

    def _state(self, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            project = connection.execute("SELECT id,code,title,template_version,aspect_ratio,fps_num,fps_den,timezone FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            seasons = [dict(row) for row in connection.execute("SELECT id,number,display_order,code,title FROM seasons WHERE project_id=? ORDER BY display_order", (project_id,))]
            episodes = [dict(row) for row in connection.execute("""SELECT e.id,e.season_id,e.number,e.display_order,e.code,e.title,e.target_duration_ms
                FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE s.project_id=? ORDER BY s.display_order,e.display_order""", (project_id,))]
            scenes = [dict(row) for row in connection.execute("SELECT id,code,title,location,time_of_day FROM scenes WHERE project_id=? ORDER BY code", (project_id,))]
            shots = [dict(row) for row in connection.execute("""SELECT sh.id,sh.episode_id,sh.code,sh.order_key,sh.target_duration_ms,sh.shot_type,sr.fields_json
                FROM shots sh JOIN episodes e ON e.id=sh.episode_id JOIN seasons s ON s.id=e.season_id
                LEFT JOIN shot_revisions sr ON sr.id=sh.current_revision_id WHERE s.project_id=? ORDER BY e.display_order,CAST(sh.order_key AS REAL),sh.code""", (project_id,))]
            plan = connection.execute("""SELECT pp.code,pp.title,ppv.version_no,ppv.plan_json,ppv.status FROM project_plan_bindings ppb
                JOIN production_plan_versions ppv ON ppv.id=ppb.production_plan_version_id JOIN production_plans pp ON pp.id=ppv.production_plan_id WHERE ppb.project_id=?""", (project_id,)).fetchone()
            profiles = [dict(row) for row in connection.execute("""SELECT ppb.capability,ppb.status AS binding_status,ppb.execution_profile_version_id,epv.status AS profile_status
                FROM project_profile_bindings ppb JOIN execution_profile_versions epv ON epv.id=ppb.execution_profile_version_id WHERE ppb.project_id=? ORDER BY ppb.capability""", (project_id,))]
            targets = [dict(row) for row in connection.execute("""SELECT dt.code,dt.title,dt.transport,dt.status,dtv.version_no,dtv.target_spec_json,dtv.status AS version_status
                FROM delivery_targets dt JOIN delivery_target_versions dtv ON dtv.delivery_target_id=dt.id WHERE dt.project_id=? ORDER BY dt.code,dtv.version_no""", (project_id,))]
        for shot in shots:
            shot["fields"] = json.loads(str(shot.pop("fields_json") or "{}"))
        return {"schema_version": STATE_SCHEMA, "project": dict(project), "seasons": seasons, "episodes": episodes, "scenes": scenes,
                "shots": shots, "production_plan": dict(plan) if plan else None, "profile_bindings": profiles, "delivery_targets": targets,
                "excluded_domains": ["jobs", "attempts", "reviews", "audit_events", "outbox", "cache", "work"]}

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
                os.replace(partial, final)
                reused = False
        except Exception:
            partial.unlink(missing_ok=True)
            raise
        return {"status": "EXPORTED", "project_id": project_id, "rel_path": final.relative_to(root).as_posix(), "byte_size": final.stat().st_size,
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
                for name, item in expected.items():
                    size, digest = _zip_digest(archive, name)
                    if size != int(item["byte_size"]) or digest != item["sha256"]:
                        raise DomainRuleError("PROJECT_PACKAGE_HASH_MISMATCH", "项目包文件 hash/size 不匹配", {"path": name})
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
        candidate = (root / rel_path).resolve()
        if allowed not in candidate.parents or not candidate.is_file() or _is_reparse(candidate):
            raise DomainRuleError("PROJECT_PACKAGE_PATH_NOT_ALLOWED", "只能预检项目已导出的注册项目包")
        return self.inspect_path(candidate)
