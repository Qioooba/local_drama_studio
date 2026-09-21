from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.api.schemas.video_upscale import UpscaleModelOptions, UpscalePipelineOptions
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.domain.errors import DomainRuleError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _deep_merge(base: dict[str, Any], patch: dict[str, object]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(dict(merged[key]), value)
        else:
            merged[key] = value
    return merged


def _merge_model_options(base: dict[str, Any], patch: dict[str, object]) -> dict[str, Any]:
    merged = _deep_merge(base, patch)
    if "tile_size" in patch and "tile_fallback_sizes" not in patch:
        merged.pop("tile_fallback_sizes", None)
    return merged


def _decode_preset(row: Any) -> dict[str, Any]:
    item = dict(row)
    item["builtin"] = bool(item["builtin"])
    item["pipeline_options"] = json.loads(str(item.pop("pipeline_options_json")))
    item["model_options"] = json.loads(str(item.pop("model_options_json")))
    item["available"] = bool(item["profile_version_id"] and item["publication_status"] == "PUBLISHED")
    item["unavailable_reason"] = None if item["available"] else "UPSCALE_PROFILE_NOT_CONFIGURED"
    return item


class VideoUpscalePresetService:
    def __init__(self, database: DatabaseUnitOfWork) -> None:
        self.database = database

    def _require_project(self, connection: Any, project_id: str) -> None:
        if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})

    def list_presets(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if project_id is not None:
                self._require_project(connection, project_id)
            rows = connection.execute(
                """SELECT p.id,p.project_id,p.code,p.title,p.builtin,p.status,p.current_version_id,
                pv.id AS version_id,pv.version_no,pv.profile_version_id,pv.pipeline_options_json,
                pv.model_options_json,pv.content_hash,pub.status AS publication_status
                FROM video_upscale_presets p
                JOIN video_upscale_preset_versions pv ON pv.id=p.current_version_id
                LEFT JOIN mp_profile_publications pub ON pub.execution_profile_version_id=pv.profile_version_id
                WHERE p.status='ACTIVE' AND (p.project_id IS NULL OR p.project_id=?)
                ORDER BY p.builtin DESC,p.title,p.id""",
                (project_id or "",),
            ).fetchall()
        return [_decode_preset(row) for row in rows]

    def get_settings(self, project_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            self._require_project(connection, project_id)
            row = connection.execute(
                """SELECT settings.*,preset.id AS preset_id,preset.title AS preset_title
                FROM project_upscale_settings settings
                JOIN video_upscale_preset_versions version ON version.id=settings.preset_version_id
                JOIN video_upscale_presets preset ON preset.id=version.preset_id
                WHERE settings.project_id=?""",
                (project_id,),
            ).fetchone()
            if row is None:
                default = connection.execute(
                    """SELECT p.id AS preset_id,p.title AS preset_title,p.current_version_id AS preset_version_id
                    FROM video_upscale_presets p WHERE p.project_id IS NULL AND p.code='ANIME_1080_STANDARD'"""
                ).fetchone()
        if row is None:
            if default is None:
                raise DomainRuleError("UPSCALE_DEFAULT_PRESET_MISSING", "内置超分预设缺失")
            return {
                "project_id": project_id,
                "preset_id": str(default["preset_id"]),
                "preset_title": str(default["preset_title"]),
                "preset_version_id": str(default["preset_version_id"]),
                "overrides": {"pipeline": {}, "model": {}},
                "revision": 0,
                "inherited": True,
            }
        item = dict(row)
        item["overrides"] = json.loads(str(item.pop("overrides_json")))
        item["inherited"] = False
        return item

    def update_settings(
        self,
        project_id: str,
        *,
        preset_version_id: str,
        pipeline_overrides: dict[str, object],
        model_overrides: dict[str, object],
        expected_revision: int,
        actor: str,
    ) -> dict[str, Any]:
        overrides = {"pipeline": pipeline_overrides, "model": model_overrides}
        now = _now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            preset = connection.execute(
                """SELECT pv.id,pv.pipeline_options_json,pv.model_options_json
                FROM video_upscale_preset_versions pv JOIN video_upscale_presets p ON p.id=pv.preset_id
                WHERE pv.id=? AND p.status='ACTIVE' AND (p.project_id IS NULL OR p.project_id=?)""",
                (preset_version_id, project_id),
            ).fetchone()
            if preset is None:
                raise DomainRuleError("UPSCALE_PRESET_VERSION_NOT_FOUND", "超分预设版本不存在或不属于当前项目")
            base_pipeline = json.loads(str(preset["pipeline_options_json"]))
            base_model = json.loads(str(preset["model_options_json"]))
            try:
                UpscalePipelineOptions.model_validate(_deep_merge(base_pipeline, pipeline_overrides))
                UpscaleModelOptions.model_validate(_merge_model_options(base_model, model_overrides))
            except ValueError as error:
                raise DomainRuleError(
                    "UPSCALE_PARAMETER_UNSUPPORTED",
                    "项目默认覆盖不符合当前超分参数合同",
                    {"validation_error": str(error)},
                ) from error
            current = connection.execute(
                "SELECT revision FROM project_upscale_settings WHERE project_id=?", (project_id,)
            ).fetchone()
            current_revision = int(current["revision"]) if current else 0
            if current_revision != expected_revision:
                raise DomainRuleError(
                    "REVISION_CONFLICT",
                    "项目超分默认设置已被其他操作更新",
                    {"expected_revision": expected_revision, "actual_revision": current_revision},
                )
            if current is None:
                connection.execute(
                    """INSERT INTO project_upscale_settings
                    (project_id,preset_version_id,overrides_json,created_at,updated_at,created_by,revision,schema_version)
                    VALUES (?,?,?,?,?,?,1,'video-upscale.v1')""",
                    (project_id, preset_version_id, json.dumps(overrides, ensure_ascii=False, sort_keys=True), now, now, actor),
                )
            else:
                connection.execute(
                    """UPDATE project_upscale_settings SET preset_version_id=?,overrides_json=?,updated_at=?,
                    created_by=?,revision=revision+1 WHERE project_id=? AND revision=?""",
                    (preset_version_id, json.dumps(overrides, ensure_ascii=False, sort_keys=True), now, actor, project_id, expected_revision),
                )
        return self.get_settings(project_id)

    def create_preset(
        self,
        project_id: str,
        *,
        code: str,
        title: str,
        profile_version_id: str | None,
        pipeline_options: dict[str, Any],
        model_options: dict[str, Any],
        actor: str = "local-user",
    ) -> dict[str, Any]:
        pipeline = UpscalePipelineOptions.model_validate(pipeline_options).model_dump(mode="json")
        model = UpscaleModelOptions.model_validate(model_options).model_dump(mode="json")
        content_hash = _hash({"pipeline": pipeline, "model": model, "profile_version_id": profile_version_id})
        preset_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            self._require_project(connection, project_id)
            if profile_version_id:
                profile = connection.execute(
                    """SELECT 1 FROM mp_profile_publications publication
                    JOIN mp_execution_profile_versions version ON version.id=publication.execution_profile_version_id
                    JOIN mp_capability_definitions capability ON capability.id=version.capability_definition_id
                    WHERE version.id=? AND publication.status='PUBLISHED' AND capability.code='UPSCALE_VIDEO'""",
                    (profile_version_id,),
                ).fetchone()
                if profile is None:
                    raise DomainRuleError("UPSCALE_PROFILE_NOT_PUBLISHED", "用户预设只能引用已发布的视频超分 Profile")
            duplicate = connection.execute(
                "SELECT id FROM video_upscale_presets WHERE project_id=? AND code=?",
                (project_id, code),
            ).fetchone()
            if duplicate is not None:
                raise DomainRuleError("UPSCALE_PRESET_CODE_CONFLICT", "当前项目已存在相同代码的超分预设")
            connection.execute(
                """INSERT INTO video_upscale_presets
                (id,project_id,code,title,builtin,current_version_id,status,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,0,?,'ACTIVE',?,?,?,1,'video-upscale.v1')""",
                (preset_id, project_id, code, title, version_id, now, now, actor),
            )
            connection.execute(
                """INSERT INTO video_upscale_preset_versions
                (id,preset_id,version_no,profile_version_id,pipeline_options_json,model_options_json,content_hash,
                 parent_version_id,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,1,?,?,?,?,NULL,?,?,?,1,'video-upscale.v1')""",
                (version_id, preset_id, profile_version_id, _json(pipeline), _json(model), content_hash, now, now, actor),
            )
        return next(item for item in self.list_presets(project_id) if item["id"] == preset_id)

    def create_preset_version(
        self,
        preset_id: str,
        *,
        title: str | None,
        profile_version_id: str | None,
        pipeline_options: dict[str, Any],
        model_options: dict[str, Any],
        expected_current_version_id: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        pipeline = UpscalePipelineOptions.model_validate(pipeline_options).model_dump(mode="json")
        model = UpscaleModelOptions.model_validate(model_options).model_dump(mode="json")
        content_hash = _hash({"pipeline": pipeline, "model": model, "profile_version_id": profile_version_id})
        version_id = str(uuid.uuid4())
        now = _now()
        project_id: str
        with self.database.transaction() as connection:
            preset = connection.execute("SELECT * FROM video_upscale_presets WHERE id=?", (preset_id,)).fetchone()
            if preset is None:
                raise DomainRuleError("UPSCALE_PRESET_NOT_FOUND", "超分预设不存在")
            if int(preset["builtin"]):
                raise DomainRuleError("UPSCALE_BUILTIN_PRESET_IMMUTABLE", "内置预设不可修改；请另存为项目预设")
            project_id = str(preset["project_id"])
            if str(preset["current_version_id"]) != expected_current_version_id:
                raise DomainRuleError("REVISION_CONFLICT", "超分预设已有新版本，请刷新后重试")
            if profile_version_id:
                profile = connection.execute(
                    """SELECT 1 FROM mp_profile_publications publication
                    JOIN mp_execution_profile_versions version ON version.id=publication.execution_profile_version_id
                    JOIN mp_capability_definitions capability ON capability.id=version.capability_definition_id
                    WHERE version.id=? AND publication.status='PUBLISHED' AND capability.code='UPSCALE_VIDEO'""",
                    (profile_version_id,),
                ).fetchone()
                if profile is None:
                    raise DomainRuleError("UPSCALE_PROFILE_NOT_PUBLISHED", "用户预设只能引用已发布的视频超分 Profile")
            version_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version_no),0)+1 FROM video_upscale_preset_versions WHERE preset_id=?",
                    (preset_id,),
                ).fetchone()[0]
            )
            duplicate = connection.execute(
                "SELECT id FROM video_upscale_preset_versions WHERE preset_id=? AND content_hash=?",
                (preset_id, content_hash),
            ).fetchone()
            if duplicate is not None:
                raise DomainRuleError("UPSCALE_PRESET_VERSION_DUPLICATE", "相同配置的预设版本已经存在")
            connection.execute(
                """INSERT INTO video_upscale_preset_versions
                (id,preset_id,version_no,profile_version_id,pipeline_options_json,model_options_json,content_hash,
                 parent_version_id,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,1,'video-upscale.v1')""",
                (
                    version_id,
                    preset_id,
                    version_no,
                    profile_version_id,
                    _json(pipeline),
                    _json(model),
                    content_hash,
                    expected_current_version_id,
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                """UPDATE video_upscale_presets SET current_version_id=?,title=COALESCE(?,title),updated_at=?,
                revision=revision+1 WHERE id=? AND current_version_id=?""",
                (version_id, title, now, preset_id, expected_current_version_id),
            )
        return next(item for item in self.list_presets(project_id) if item["id"] == preset_id)
