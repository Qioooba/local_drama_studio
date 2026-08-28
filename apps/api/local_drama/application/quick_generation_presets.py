"""Reusable, user-owned parameter presets for standalone generation."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.application.override_schema import effective_schema, validate_overrides
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.application.ports.quick_generation import ProfileReader
from local_drama.domain.capabilities import normalize_capability
from local_drama.domain.errors import DomainRuleError


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class QuickGenerationPresetService:
    def __init__(self, database: DatabaseUnitOfWork, profiles: ProfileReader) -> None:
        self.database = database
        self.profiles = profiles

    @staticmethod
    def _response(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["parameters"] = json.loads(str(item.pop("parameters_json") or "{}"))
        item["favorite"] = bool(item["favorite"])
        return item

    def _validated(self, capability: str, profile_version_id: str, parameters: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        try:
            canonical = normalize_capability(capability)
        except ValueError as error:
            raise DomainRuleError("QUICK_PRESET_CAPABILITY_INVALID", "常用参数的模型能力无效") from error
        profile = self.profiles.get_version(profile_version_id)
        if profile["status"] != "PUBLISHED" or str(profile["capability"]) != canonical:
            raise DomainRuleError("QUICK_PRESET_MODEL_MISMATCH", "常用参数必须绑定同能力的已发布模型")
        raw_execution = profile.get("execution")
        execution: dict[str, Any] = raw_execution if isinstance(raw_execution, dict) else {}
        validation_profile = {**profile, "override_schema": effective_schema({"capability": canonical, "override_schema": execution.get("override_schema")})}
        return canonical, validate_overrides(parameters, validation_profile, scope="RUN")

    def list(self, capability: str | None = None) -> dict[str, Any]:
        params: list[Any] = []
        where = ""
        if capability:
            try:
                canonical = normalize_capability(capability)
            except ValueError as error:
                raise DomainRuleError("QUICK_PRESET_CAPABILITY_INVALID", "常用参数的模型能力无效") from error
            where = "WHERE q.capability=?"
            params.append(canonical)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"""SELECT q.*,p.title AS model_title,v.version_no AS model_version_no,v.status AS model_status
                FROM quick_generation_presets q
                JOIN execution_profile_versions v ON v.id=q.execution_profile_version_id
                JOIN execution_profiles p ON p.id=v.execution_profile_id
                {where}
                ORDER BY q.favorite DESC,q.updated_at DESC,q.name""",
                params,
            ).fetchall()
        return {"items": [self._response(row) for row in rows]}

    def create(
        self,
        *,
        name: str,
        capability: str,
        execution_profile_version_id: str,
        parameters: dict[str, Any],
        favorite: bool = True,
    ) -> dict[str, Any]:
        normalized_name = name.strip()
        if not 1 <= len(normalized_name) <= 120:
            raise DomainRuleError("QUICK_PRESET_NAME_INVALID", "常用参数名称必须是 1—120 个字符")
        canonical, normalized = self._validated(capability, execution_profile_version_id, parameters)
        preset_id, now = str(uuid.uuid4()), _now()
        try:
            with self.database.transaction() as connection:
                connection.execute(
                    """INSERT INTO quick_generation_presets
                    (id,name,capability,execution_profile_version_id,parameters_json,favorite,created_at,updated_at,created_by)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                    (preset_id, normalized_name, canonical, execution_profile_version_id, _json(normalized), int(favorite), now, now, "local-user"),
                )
        except sqlite3.IntegrityError as error:
            raise DomainRuleError("QUICK_PRESET_NAME_EXISTS", "这个模型能力下已经有同名常用参数") from error
        return {"preset": self.get(preset_id)}

    def get(self, preset_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT q.*,p.title AS model_title,v.version_no AS model_version_no,v.status AS model_status
                FROM quick_generation_presets q
                JOIN execution_profile_versions v ON v.id=q.execution_profile_version_id
                JOIN execution_profiles p ON p.id=v.execution_profile_id WHERE q.id=?""",
                (preset_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("QUICK_PRESET_NOT_FOUND", "常用参数不存在")
        return self._response(row)

    def update(
        self,
        preset_id: str,
        *,
        name: str,
        execution_profile_version_id: str,
        parameters: dict[str, Any],
        favorite: bool,
        expected_revision: int,
    ) -> dict[str, Any]:
        current = self.get(preset_id)
        normalized_name = name.strip()
        if not 1 <= len(normalized_name) <= 120:
            raise DomainRuleError("QUICK_PRESET_NAME_INVALID", "常用参数名称必须是 1—120 个字符")
        _, normalized = self._validated(str(current["capability"]), execution_profile_version_id, parameters)
        try:
            with self.database.transaction() as connection:
                cursor = connection.execute(
                    """UPDATE quick_generation_presets SET name=?,execution_profile_version_id=?,parameters_json=?,favorite=?,
                    updated_at=?,revision=revision+1 WHERE id=? AND revision=?""",
                    (normalized_name, execution_profile_version_id, _json(normalized), int(favorite), _now(), preset_id, expected_revision),
                )
                if cursor.rowcount != 1:
                    raise DomainRuleError("QUICK_PRESET_REVISION_CONFLICT", "常用参数已被修改，请刷新后重试")
        except sqlite3.IntegrityError as error:
            raise DomainRuleError("QUICK_PRESET_NAME_EXISTS", "这个模型能力下已经有同名常用参数") from error
        return {"preset": self.get(preset_id)}

    def delete(self, preset_id: str) -> dict[str, Any]:
        with self.database.transaction() as connection:
            cursor = connection.execute("DELETE FROM quick_generation_presets WHERE id=?", (preset_id,))
            if cursor.rowcount != 1:
                raise DomainRuleError("QUICK_PRESET_NOT_FOUND", "常用参数不存在")
        return {"deleted": True, "preset_id": preset_id}
