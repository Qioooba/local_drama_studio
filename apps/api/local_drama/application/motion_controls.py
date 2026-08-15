"""Immutable local motion-mask/vector/keyframe control media.

The control canvas never edits a source MediaVersion.  Raster masks and
keyframes reference an already registered immutable MediaVersion; vector
brush/keyframe payloads are canonicalized into a new local JSON MediaVersion.
All capability decisions are made against the frozen published profile.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.application.media import MediaService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


_CAPABILITY_KEYS: dict[str, tuple[str, ...]] = {
    "MOTION_BRUSH": ("motion_mask", "motion"),
    "INPAINT": ("inpaint", "local_edit"),
    "OUTPAINT": ("outpaint", "local_edit"),
}
_CONTROL_KINDS = {"MOTION_MASK", "VECTOR", "KEYFRAME"}
_OPERATIONS = set(_CAPABILITY_KEYS)


class MotionControlService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.media = MediaService(database, settings)

    def _profile_capability(self, profile_version_id: str, operation: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT id, revision, status, capability, parameter_schema_json
                FROM execution_profile_versions WHERE id=?""",
                (profile_version_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "运动控制必须绑定已存在的 ProfileVersion")
        if str(row["status"]) != "PUBLISHED":
            raise DomainRuleError("PROFILE_NOT_PUBLISHED", "运动控制只能绑定已发布 ProfileVersion")
        try:
            schema = json.loads(str(row["parameter_schema_json"] or "{}"))
        except json.JSONDecodeError as error:
            raise DomainRuleError("PROFILE_CAPABILITY_CONTRACT_INVALID", "Profile capability contract 不是有效 JSON") from error
        capabilities = schema.get("capabilities", {}) if isinstance(schema, dict) else {}
        if not isinstance(capabilities, dict):
            raise DomainRuleError("PROFILE_CAPABILITY_CONTRACT_INVALID", "Profile capabilities 必须是对象")
        selected_key: str | None = None
        selected: dict[str, Any] | None = None
        for key in _CAPABILITY_KEYS[operation]:
            candidate = capabilities.get(key)
            if isinstance(candidate, dict) and candidate.get("enabled") is True:
                selected_key = key
                selected = candidate
                break
        if selected is None:
            raise DomainRuleError(
                "PROFILE_CONTROL_UNSUPPORTED",
                f"当前已发布 Profile 未声明 {operation} 能力",
                {
                    "profile_version_id": profile_version_id,
                    "required_capabilities": list(_CAPABILITY_KEYS[operation]),
                    "declared_capabilities": sorted(str(key) for key in capabilities),
                },
                suggested_action="发布声明该能力槽位且 enabled=true 的 ProfileVersion",
            )
        return {
            "id": str(row["id"]),
            "revision": int(row["revision"]),
            "capability": str(row["capability"]),
            "operation": operation,
            "capability_key": selected_key,
            "contract": selected,
        }

    def _media_in_project(self, media_version_id: str, project_id: str, *, label: str) -> dict[str, Any]:
        try:
            item = self.media.get_version(media_version_id)
        except DomainRuleError as error:
            raise DomainRuleError("CONTROL_MEDIA_NOT_FOUND", f"{label} MediaVersion 不存在", {"media_version_id": media_version_id}) from error
        if str(item["project_id"]) != project_id:
            raise DomainRuleError("CONTROL_MEDIA_PROJECT_MISMATCH", f"{label} 必须属于源媒体所在项目")
        if str(item["integrity_status"]) != "VERIFIED":
            raise DomainRuleError("CONTROL_MEDIA_UNVERIFIED", f"{label} 必须是 VERIFIED MediaVersion")
        self.media.verify_content_integrity(media_version_id)
        return item

    def _create_vector_media(self, source: dict[str, Any], payload: dict[str, Any], actor: str) -> dict[str, Any]:
        """Register a canonical vector/keyframe JSON file without retaining a source path."""
        work_dir = (self.settings.work_root / "motion-controls").resolve()
        if not work_dir.is_relative_to(self.settings.work_root.resolve()):
            raise DomainRuleError("PATH_ESCAPE", "运动控制 staging 目录越界")
        work_dir.mkdir(parents=True, exist_ok=True)
        temp_path = work_dir / f"{uuid.uuid4().hex}.control.json"
        document = {
            "schema_version": "motion-control-v1",
            "source_media_version_id": str(source["id"]),
            "control_kind": str(payload["control_kind"]),
            "operation": str(payload["operation"]),
            "subject_role": str(payload["subject_role"]),
            "coordinate_space": str(payload.get("coordinate_space") or "NORMALIZED"),
            "vector_path": payload.get("vector_path") or [],
            "keyframes": payload.get("keyframes") or [],
            "note": str(payload.get("note") or ""),
        }
        temp_path.write_text(_json(document), encoding="utf-8")
        try:
            return self.media.import_file(
                str(source["project_id"]),
                temp_path,
                purpose="MOTION_CONTROL",
                owner_type="MEDIA_VERSION",
                owner_id=str(source["id"]),
                media_kind="OTHER",
                stage="MOTION_CONTROL",
                actor=actor,
            )
        finally:
            temp_path.unlink(missing_ok=True)

    def create(self, source_media_version_id: str, payload: dict[str, Any], actor: str = "local-user") -> dict[str, Any]:
        control_kind = str(payload.get("control_kind") or "").upper()
        operation = str(payload.get("operation") or "").upper()
        subject_role = str(payload.get("subject_role") or "").strip()
        profile_version_id = str(payload.get("profile_version_id") or "").strip()
        if control_kind not in _CONTROL_KINDS:
            raise DomainRuleError("CONTROL_KIND_INVALID", "运动控制类型无效")
        if operation not in _OPERATIONS:
            raise DomainRuleError("CONTROL_OPERATION_INVALID", "运动控制 operation 无效")
        if not subject_role:
            raise DomainRuleError("CONTROL_SUBJECT_REQUIRED", "运动控制必须填写 subject_role")
        source = self._media_in_project(source_media_version_id, str(self.media.get_version(source_media_version_id)["project_id"]), label="源媒体")
        if str(source["media_kind"]) not in {"IMAGE", "VIDEO"}:
            raise DomainRuleError("CONTROL_SOURCE_KIND_INVALID", "运动控制源媒体必须是 IMAGE 或 VIDEO")
        capability = self._profile_capability(profile_version_id, operation)
        vector_path = payload.get("vector_path") or []
        keyframes = payload.get("keyframes") or []
        if not isinstance(vector_path, list) or not isinstance(keyframes, list):
            raise DomainRuleError("CONTROL_PAYLOAD_INVALID", "vector_path/keyframes 必须是数组")
        coordinate_space = str(payload.get("coordinate_space") or "NORMALIZED").upper()
        if coordinate_space not in {"NORMALIZED", "PIXELS"}:
            raise DomainRuleError("CONTROL_COORDINATE_SPACE_INVALID", "运动控制坐标系必须是 NORMALIZED 或 PIXELS")
        points = [*vector_path, *keyframes]
        for index, point in enumerate(points):
            if not isinstance(point, dict):
                raise DomainRuleError("CONTROL_PAYLOAD_INVALID", f"运动控制点[{index}] 必须是对象")
            for axis in ("x", "y"):
                value = point.get(axis)
                if value is None:
                    continue
                try:
                    numeric = float(value)
                except (TypeError, ValueError) as error:
                    raise DomainRuleError("CONTROL_COORDINATE_INVALID", f"运动控制点[{index}] 坐标必须是数字") from error
                if coordinate_space == "NORMALIZED" and not 0 <= numeric <= 1:
                    raise DomainRuleError("CONTROL_COORDINATE_INVALID", "NORMALIZED 坐标必须位于 0..1")
        mask_id = str(payload.get("mask_media_version_id") or "").strip() or None
        keyframe_id = str(payload.get("keyframe_media_version_id") or "").strip() or None
        if mask_id and control_kind != "MOTION_MASK":
            raise DomainRuleError("MASK_ROLE_INVALID", "mask_media_version_id 只允许用于 MOTION_MASK 控制")
        if keyframe_id and control_kind != "KEYFRAME":
            raise DomainRuleError("KEYFRAME_ROLE_INVALID", "keyframe_media_version_id 只允许用于 KEYFRAME 控制")
        if control_kind == "KEYFRAME":
            if not keyframe_id:
                raise DomainRuleError("KEYFRAME_MEDIA_REQUIRED", "KEYFRAME 控制必须绑定 keyframe_media_version_id")
            control_media = self._media_in_project(keyframe_id, str(source["project_id"]), label="关键帧")
            if str(control_media["media_kind"]) != "IMAGE":
                raise DomainRuleError("KEYFRAME_MEDIA_INVALID", "关键帧控制必须绑定 IMAGE MediaVersion")
        elif control_kind == "MOTION_MASK" and mask_id:
            control_media = self._media_in_project(mask_id, str(source["project_id"]), label="mask")
            if str(control_media["media_kind"]) != "IMAGE":
                raise DomainRuleError("MASK_MEDIA_INVALID", "mask 控制必须绑定 IMAGE MediaVersion")
        elif control_kind == "MOTION_MASK" and not vector_path:
            raise DomainRuleError("MASK_MEDIA_REQUIRED", "MOTION_MASK 必须绑定 mask MediaVersion 或提供运动笔刷路径")
        elif control_kind == "VECTOR" and not vector_path and not keyframes:
            raise DomainRuleError("VECTOR_PAYLOAD_REQUIRED", "VECTOR 控制必须提供 vector_path 或 keyframes")
        else:
            control_media = self._create_vector_media(source, payload, actor)
        control_media_id = str(control_media.get("media_version_id") or control_media.get("id") or "")
        if not control_media_id:
            raise DomainRuleError("CONTROL_MEDIA_INVALID", "控制媒体注册结果缺少 MediaVersion ID")
        if control_media_id == str(source["id"]):
            raise DomainRuleError("CONTROL_SOURCE_REUSE_FORBIDDEN", "控制媒体不能覆盖或复用源 MediaVersion")
        normalized = {
            "schema_version": "motion-control-v1",
            "source_media_version_id": str(source["id"]),
            "control_media_version_id": control_media_id,
            "control_kind": control_kind,
            "operation": operation,
            "subject_role": subject_role,
            "profile_version_id": profile_version_id,
            "capability": capability,
            "mask_media_version_id": mask_id,
            "keyframe_media_version_id": keyframe_id,
            "vector_path": vector_path,
            "keyframes": keyframes,
            "coordinate_space": coordinate_space,
            "note": str(payload.get("note") or ""),
        }
        control_hash = _hash(normalized)
        with self.database.connect() as connection:
            duplicate = connection.execute(
                "SELECT id FROM motion_controls WHERE source_media_version_id=? AND control_hash=?",
                (str(source["id"]), control_hash),
            ).fetchone()
        if duplicate is not None:
            return {"duplicate": True, **self.get(str(duplicate["id"]))}
        control_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO motion_controls
                (id, source_media_version_id, control_media_version_id, profile_version_id,
                 control_kind, operation, subject_role, control_payload_json, control_hash,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v1')""",
                (
                    control_id,
                    str(source["id"]),
                    control_media_id,
                    profile_version_id,
                    control_kind,
                    operation,
                    subject_role,
                    _json(normalized),
                    control_hash,
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'MOTION_CONTROL_CREATED', 'motion_control', ?, ?, ?)""",
                (actor, control_id, "创建不可变运动区域/局部编辑控制", _json({"source_media_version_id": source["id"], "control_media_version_id": control_media_id, "operation": operation, "profile_version_id": profile_version_id})),
            )
        return {"duplicate": False, **self.get(control_id)}

    def get(self, control_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM motion_controls WHERE id=?", (control_id,)).fetchone()
        if row is None:
            raise DomainRuleError("MOTION_CONTROL_NOT_FOUND", "运动控制不存在", {"control_id": control_id})
        item = dict(row)
        item["control_payload"] = json.loads(item.pop("control_payload_json"))
        item["source_media"] = self.media.get_version(str(item["source_media_version_id"]))
        item["control_media"] = self.media.get_version(str(item["control_media_version_id"]))
        return item

    def list_for_source(self, source_media_version_id: str) -> list[dict[str, Any]]:
        self.media.get_version(source_media_version_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM motion_controls WHERE source_media_version_id=? ORDER BY created_at, id",
                (source_media_version_id,),
            ).fetchall()
        return [self.get(str(row["id"])) for row in rows]
