"""Application service for Character Identity Packs (PR-CUR-007)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.character_identity_packs import (
    IMMUTABLE_PACK_VERSION_STATUSES,
    REQUIRED_THREE_VIEW_SLOTS,
    PackVersionStatus,
    SlotKind,
)
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(val: Any) -> str:
    return json.dumps(val, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(val: Any) -> str:
    import hashlib

    return hashlib.sha256(_json(val).encode("utf-8")).hexdigest()


def _slot_kind(value: str) -> str:
    cleaned = value.strip().upper()
    try:
        return SlotKind(cleaned).value
    except ValueError as error:
        raise DomainRuleError(
            "IDENTITY_PACK_SLOT_KIND_INVALID",
            "身份包槽位类型不受支持",
            {"slot_kind": cleaned, "allowed": [item.value for item in SlotKind]},
        ) from error


class CharacterIdentityPackService:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _slot_rows(connection: sqlite3.Connection, pack_version_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            """SELECT s.*,mv.sha256,mv.byte_size,mv.integrity_status,mv.version_no AS media_version_no,
            ma.media_kind,ma.project_id AS media_project_id,
            waa.id AS current_authorization_id,waa.authorization_status,
            waa.license_status,waa.sha256 AS authorization_sha256,
            waa.byte_size AS authorization_byte_size,waa.revision AS authorization_revision
            FROM character_identity_pack_slots s
            JOIN media_versions mv ON mv.id=s.media_version_id
            JOIN media_assets ma ON ma.id=mv.media_asset_id
            LEFT JOIN workspace_asset_authorizations waa ON waa.id=s.authorization_id
            WHERE s.pack_version_id=? ORDER BY s.slot_kind,s.id""",
            (pack_version_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _validate_slot_row(version: sqlite3.Row | dict[str, Any], slot: dict[str, Any]) -> dict[str, Any]:
        if str(slot["media_project_id"]) != str(version["project_id"]):
            raise DomainRuleError("MEDIA_PROJECT_MISMATCH", "身份包参考图必须属于身份包所在项目")
        if str(slot["media_kind"]).upper() != "IMAGE":
            raise DomainRuleError(
                "IDENTITY_PACK_MEDIA_KIND_INVALID",
                "身份包参考槽只能绑定 IMAGE MediaVersion",
                {"slot_kind": slot["slot_kind"], "media_kind": slot["media_kind"]},
            )
        if str(slot["integrity_status"]).upper() != "VERIFIED":
            raise DomainRuleError(
                "IDENTITY_PACK_MEDIA_NOT_VERIFIED",
                "身份包参考图完整性必须为 VERIFIED",
                {"slot_kind": slot["slot_kind"], "integrity_status": slot["integrity_status"]},
            )
        authorization_id = slot.get("current_authorization_id")
        if not authorization_id:
            raise DomainRuleError(
                "IDENTITY_PACK_MEDIA_AUTHORIZATION_REQUIRED",
                "身份包参考图必须先完成项目工作区授权",
                {"slot_kind": slot["slot_kind"], "media_version_id": slot["media_version_id"]},
                suggested_action="在媒体选择器中执行“授权并绑定”，或前往项目授权页完成授权",
            )
        if (
            str(slot.get("authorization_status")) != "AUTHORIZED"
            or str(slot.get("license_status")) != "LOCAL_PROJECT_AUTHORIZED"
            or str(slot.get("authorization_sha256")) != str(slot.get("sha256"))
            or int(slot.get("authorization_byte_size") or -1) != int(slot.get("byte_size") or -2)
        ):
            raise DomainRuleError(
                "IDENTITY_PACK_MEDIA_AUTHORIZATION_INVALID",
                "参考图的项目授权已撤回或内容快照不一致",
                {"slot_kind": slot["slot_kind"], "authorization_id": authorization_id},
            )
        return {
            "slot_kind": str(slot["slot_kind"]),
            "media_version_id": str(slot["media_version_id"]),
            "media_version_no": int(slot["media_version_no"]),
            "sha256": str(slot["sha256"]),
            "byte_size": int(slot["byte_size"]),
            "authorization": {
                "id": str(authorization_id),
                "revision": int(slot["authorization_revision"]),
                "license_status": str(slot["license_status"]),
            },
        }

    @classmethod
    def _validated_content(
        cls,
        connection: sqlite3.Connection,
        version: sqlite3.Row | dict[str, Any],
        *,
        require_three_view: bool,
    ) -> tuple[dict[str, Any], str]:
        slots = cls._slot_rows(connection, str(version["id"]))
        kinds = {str(row["slot_kind"]).upper() for row in slots}
        missing = sorted(set(REQUIRED_THREE_VIEW_SLOTS) - kinds)
        if require_three_view and missing:
            raise DomainRuleError(
                "PACK_REQUIRED_SLOTS_MISSING",
                "正式批准前必须补齐 FRONT / LEFT / RIGHT 三个必需视角",
                {"missing_slots": missing, "required_slots": list(REQUIRED_THREE_VIEW_SLOTS)},
                suggested_action="从项目媒体选择对应图片，或先生成缺失三视图",
            )
        normalized = [cls._validate_slot_row(version, slot) for slot in slots]
        required_media = [
            item["media_version_id"]
            for item in normalized
            if item["slot_kind"] in REQUIRED_THREE_VIEW_SLOTS
        ]
        required_hashes = [
            item["sha256"]
            for item in normalized
            if item["slot_kind"] in REQUIRED_THREE_VIEW_SLOTS
        ]
        if require_three_view and (
            len(required_media) != len(set(required_media))
            or len(required_hashes) != len(set(required_hashes))
        ):
            raise DomainRuleError(
                "PACK_REQUIRED_MEDIA_DUPLICATED",
                "FRONT / LEFT / RIGHT 必须绑定三个内容不同的不可变图片版本",
            )
        content = {
            "schema_version": "localdrama.identity-pack-content.v1",
            "pack_id": str(version["pack_id"]),
            "version_id": str(version["id"]),
            "version_no": int(version["version_no"]),
            "project_id": str(version["project_id"]),
            "story_asset_id": str(version["story_asset_id"]),
            "asset_state_id": str(version["asset_state_id"]) if version["asset_state_id"] else None,
            "slots": normalized,
        }
        return content, _digest(content)

    @staticmethod
    def _mark_variants_stale_for_version(
        connection: sqlite3.Connection,
        pack_version_id: str,
        *,
        reason: str,
        now: str,
    ) -> list[str]:
        rows = connection.execute(
            """SELECT id,identity_pack_snapshot_json FROM generation_variants
            WHERE is_stale=0 AND identity_pack_snapshot_json<>'{}'"""
        ).fetchall()
        impacted: list[str] = []
        for row in rows:
            try:
                snapshot = json.loads(str(row["identity_pack_snapshot_json"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                continue
            packs = snapshot.get("packs", []) if isinstance(snapshot, dict) else []
            if any(
                isinstance(item, dict) and str(item.get("pack_version_id") or "") == pack_version_id
                for item in packs
            ):
                impacted.append(str(row["id"]))
        if impacted:
            placeholders = ",".join("?" for _ in impacted)
            connection.execute(
                f"""UPDATE generation_variants SET is_stale=1,stale_reason=?,updated_at=?,revision=revision+1
                WHERE id IN ({placeholders})""",
                (reason, now, *impacted),
            )
        return impacted

    @classmethod
    def generation_snapshot_for_intent(
        cls,
        connection: sqlite3.Connection,
        intent: sqlite3.Row | dict[str, Any],
    ) -> dict[str, Any] | None:
        """Freeze every explicitly bound character pack for a SHOT intent.

        A character binding without a pack remains a visible upstream
        readiness gap and is not silently converted into a synthetic pack.
        Once a pack id is present, however, generation fails closed unless the
        exact version is current, approved, complete and media-valid.
        """
        if str(intent["owner_type"]).upper() != "SHOT" or not intent["owner_id"]:
            return None
        bindings = connection.execute(
            """SELECT b.shot_id,b.asset_id AS binding_story_asset_id,
            b.asset_state_id AS binding_asset_state_id,b.role_in_shot,
            b.identity_pack_version_id,
            v.id AS version_id,v.pack_id,v.project_id AS pack_project_id,
            v.story_asset_id AS pack_story_asset_id,v.asset_state_id AS pack_asset_state_id,
            v.version_no,v.status AS version_status,v.content_hash,
            p.code AS pack_code,p.name AS pack_name,
            p.status AS pack_status,p.current_version_id
            FROM shot_asset_bindings b
            JOIN story_assets a ON a.id=b.asset_id AND a.kind='CHARACTER'
            LEFT JOIN character_identity_pack_versions v ON v.id=b.identity_pack_version_id
            LEFT JOIN character_identity_packs p ON p.id=v.pack_id
            WHERE b.shot_id=? AND b.identity_pack_version_id IS NOT NULL
            ORDER BY b.role_in_shot,a.code,b.asset_id""",
            (str(intent["owner_id"]),),
        ).fetchall()
        if not bindings:
            return None
        packs: list[dict[str, Any]] = []
        for binding in bindings:
            if not binding["version_id"]:
                raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_FOUND", "镜头绑定的身份包版本不存在")
            if str(binding["pack_project_id"]) != str(intent["project_id"]):
                raise DomainRuleError("IDENTITY_PACK_SHOT_PROJECT_MISMATCH", "镜头身份包与生成任务不属于同一项目")
            if str(binding["binding_story_asset_id"]) != str(binding["pack_story_asset_id"]):
                raise DomainRuleError("ASSET_PACK_MISMATCH", "镜头角色与身份包角色不一致")
            if str(binding["pack_status"]) != "ACTIVE":
                raise DomainRuleError("IDENTITY_PACK_NOT_ACTIVE", "镜头绑定的身份包已归档或废弃")
            if str(binding["current_version_id"] or "") != str(binding["version_id"]):
                raise DomainRuleError(
                    "IDENTITY_PACK_BINDING_STALE",
                    "镜头绑定的身份包已有新批准版本，请先确认并重新绑定",
                    {
                        "story_asset_id": binding["binding_story_asset_id"],
                        "bound_version_id": binding["version_id"],
                        "current_version_id": binding["current_version_id"],
                    },
                )
            if str(binding["version_status"]) != PackVersionStatus.APPROVED.value:
                raise DomainRuleError(
                    "APPROVED_IDENTITY_PACK_REQUIRED",
                    "生成前必须将镜头绑定到当前人工批准的身份包版本",
                    {
                        "story_asset_id": binding["binding_story_asset_id"],
                        "pack_version_id": binding["version_id"],
                    },
                )
            version = {
                "id": binding["version_id"],
                "pack_id": binding["pack_id"],
                "project_id": binding["pack_project_id"],
                "story_asset_id": binding["pack_story_asset_id"],
                "asset_state_id": binding["pack_asset_state_id"],
                "version_no": binding["version_no"],
            }
            content, content_hash = cls._validated_content(
                connection,
                version,
                require_three_view=True,
            )
            packs.append(
                {
                    "story_asset_id": str(binding["binding_story_asset_id"]),
                    "asset_state_id": (
                        str(binding["binding_asset_state_id"])
                        if binding["binding_asset_state_id"]
                        else None
                    ),
                    "pack_asset_state_id": (
                        str(binding["pack_asset_state_id"])
                        if binding["pack_asset_state_id"]
                        else None
                    ),
                    "role_in_shot": str(binding["role_in_shot"]),
                    "pack_id": str(binding["pack_id"]),
                    "pack_code": str(binding["pack_code"]),
                    "pack_name": str(binding["pack_name"]),
                    "pack_version_id": str(binding["version_id"]),
                    "pack_version_no": int(binding["version_no"]),
                    "content_hash": content_hash,
                    "slots": content["slots"],
                }
            )
        snapshot = {
            "schema_version": "localdrama.identity-pack-snapshot.v1",
            "shot_id": str(intent["owner_id"]),
            "packs": packs,
        }
        snapshot["snapshot_hash"] = _digest(snapshot)
        return snapshot

    def list_packs(self, story_asset_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT p.*, a.name AS asset_name, a.code AS asset_code,
                v.version_no AS current_version_no, v.status AS current_version_status,
                v.slots_json AS current_slots_json
                FROM character_identity_packs p
                JOIN story_assets a ON a.id = p.story_asset_id
                LEFT JOIN character_identity_pack_versions v ON v.id = p.current_version_id
                WHERE p.story_asset_id = ?
                ORDER BY p.created_at ASC""",
                (story_asset_id,),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["current_slots"] = json.loads(item.pop("current_slots_json") or "{}")
            result.append(item)
        return result

    def get_pack(self, pack_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            pack_row = connection.execute(
                """SELECT p.*, a.name AS asset_name, a.code AS asset_code, a.project_id
                FROM character_identity_packs p
                JOIN story_assets a ON a.id = p.story_asset_id
                WHERE p.id = ?""",
                (pack_id,),
            ).fetchone()
            if pack_row is None:
                raise DomainRuleError("IDENTITY_PACK_NOT_FOUND", "角色身份包不存在")
            versions = connection.execute(
                """SELECT * FROM character_identity_pack_versions
                WHERE pack_id = ? ORDER BY version_no DESC""",
                (pack_id,),
            ).fetchall()
        pack = dict(pack_row)
        pack["versions"] = []
        for v in versions:
            v_dict = dict(v)
            v_dict["slots"] = json.loads(v_dict.get("slots_json") or "{}")
            v_dict["approval_metadata"] = json.loads(v_dict.get("approval_metadata_json") or "{}")
            pack["versions"].append(v_dict)
        return pack

    def get_version(self, pack_version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT v.*, p.name AS pack_name, p.code AS pack_code, a.name AS asset_name
                FROM character_identity_pack_versions v
                JOIN character_identity_packs p ON p.id = v.pack_id
                JOIN story_assets a ON a.id = v.story_asset_id
                WHERE v.id = ?""",
                (pack_version_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_FOUND", "身份包版本不存在")
            slots = self._slot_rows(connection, pack_version_id)
        result = dict(row)
        result["slots"] = slots
        result["slots_map"] = json.loads(result.get("slots_json") or "{}")
        result["approval_metadata"] = json.loads(result.get("approval_metadata_json") or "{}")
        present = {str(slot["slot_kind"]).upper() for slot in slots}
        result["missing_required_slots"] = sorted(set(REQUIRED_THREE_VIEW_SLOTS) - present)
        approval_blockers: list[dict[str, Any]] = []
        for slot in slots:
            try:
                self._validate_slot_row(row, slot)
            except DomainRuleError as error:
                approval_blockers.append(
                    {
                        "code": error.code,
                        "slot_kind": str(slot["slot_kind"]),
                        "message": error.message,
                    }
                )
        required_slots = [
            slot for slot in slots if str(slot["slot_kind"]).upper() in REQUIRED_THREE_VIEW_SLOTS
        ]
        required_media_ids = {str(slot["media_version_id"]) for slot in required_slots}
        required_content_hashes = {str(slot["sha256"]) for slot in required_slots}
        if len(required_slots) == len(REQUIRED_THREE_VIEW_SLOTS) and (
            len(required_media_ids) != len(REQUIRED_THREE_VIEW_SLOTS)
            or len(required_content_hashes) != len(REQUIRED_THREE_VIEW_SLOTS)
        ):
            approval_blockers.append(
                {
                    "code": "PACK_REQUIRED_MEDIA_DUPLICATED",
                    "slot_kind": None,
                    "message": "FRONT / LEFT / RIGHT 必须绑定三个内容不同的不可变图片版本",
                }
            )
        result["approval_blockers"] = approval_blockers
        result["approval_ready"] = (
            str(result["status"]) in {PackVersionStatus.DRAFT.value, PackVersionStatus.READY_FOR_REVIEW.value}
            and not result["missing_required_slots"]
            and not approval_blockers
        )
        return result

    def create_pack(
        self,
        project_id: str,
        story_asset_id: str,
        code: str,
        name: str,
        description: str = "",
        asset_state_id: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        clean_code = code.strip().upper()
        if not clean_code or not name.strip():
            raise DomainRuleError("IDENTITY_PACK_INPUT_INVALID", "身份包代码与名称不能为空")
        now = _now()
        pack_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())

        with self.database.transaction() as connection:
            asset = connection.execute(
                "SELECT id, project_id, kind FROM story_assets WHERE id = ?",
                (story_asset_id,),
            ).fetchone()
            if asset is None:
                raise DomainRuleError("STORY_ASSET_NOT_FOUND", "角色资产不存在")
            if asset["project_id"] != project_id:
                raise DomainRuleError("ASSET_PROJECT_MISMATCH", "资产不属于当前项目")
            if str(asset["kind"]).upper() != "CHARACTER":
                raise DomainRuleError("ASSET_KIND_MISMATCH", "只有角色资产可以创建身份包")

            if asset_state_id:
                state = connection.execute(
                    "SELECT id, story_asset_id FROM story_asset_states WHERE id = ?",
                    (asset_state_id,),
                ).fetchone()
                if state is None or state["story_asset_id"] != story_asset_id:
                    raise DomainRuleError("ASSET_STATE_MISMATCH", "所选状态不属于该角色资产")

            try:
                connection.execute(
                    """INSERT INTO character_identity_packs
                    (id, project_id, story_asset_id, asset_state_id, code, name, description,
                     status, current_version_id, created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', NULL, ?, ?, ?, 1, 'v1')""",
                    (pack_id, project_id, story_asset_id, asset_state_id, clean_code, name.strip(), description.strip(), now, now, actor),
                )
            except sqlite3.IntegrityError as err:
                raise DomainRuleError("IDENTITY_PACK_DUPLICATE_CODE", f"角色已存在代码为 {clean_code} 的身份包") from err

            connection.execute(
                """INSERT INTO character_identity_pack_versions
                (id, pack_id, project_id, story_asset_id, asset_state_id, version_no,
                 status, slots_json, generator_job_id, approval_metadata_json,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, 1, 'DRAFT', '{}', NULL, '{}', ?, ?, ?, 1, 'v1')""",
                (version_id, pack_id, project_id, story_asset_id, asset_state_id, now, now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'CHARACTER_IDENTITY_PACK_CREATED', 'character_identity_pack', ?, ?, ?)""",
                (actor, pack_id, f"创建角色身份包 {clean_code}", _json({"name": name, "version_id": version_id})),
            )

        return self.get_pack(pack_id)

    def create_version_draft(
        self,
        pack_id: str,
        from_version_id: str | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        now = _now()
        version_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            pack = connection.execute(
                "SELECT * FROM character_identity_packs WHERE id = ?", (pack_id,)
            ).fetchone()
            if pack is None:
                raise DomainRuleError("IDENTITY_PACK_NOT_FOUND", "角色身份包不存在")
            if pack["status"] == "RETIRED":
                raise DomainRuleError("IDENTITY_PACK_RETIRED", "已废弃的身份包不能创建新版本")

            max_version_row = connection.execute(
                "SELECT MAX(version_no) AS max_v FROM character_identity_pack_versions WHERE pack_id = ?",
                (pack_id,),
            ).fetchone()
            next_version_no = (max_version_row["max_v"] or 0) + 1

            initial_slots: dict[str, str] = {}
            if from_version_id:
                source_version = connection.execute(
                    "SELECT * FROM character_identity_pack_versions WHERE id = ? AND pack_id = ?",
                    (from_version_id, pack_id),
                ).fetchone()
                if source_version is None:
                    raise DomainRuleError(
                        "IDENTITY_PACK_SOURCE_VERSION_NOT_FOUND",
                        "新草稿的来源版本不存在或不属于当前身份包",
                    )
                initial_slots = json.loads(source_version["slots_json"] or "{}")

            connection.execute(
                """INSERT INTO character_identity_pack_versions
                (id, pack_id, project_id, story_asset_id, asset_state_id, version_no,
                 status, slots_json, generator_job_id, approval_metadata_json,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?, NULL, '{}', ?, ?, ?, 1, 'v1')""",
                (
                    version_id,
                    pack_id,
                    pack["project_id"],
                    pack["story_asset_id"],
                    pack["asset_state_id"],
                    next_version_no,
                    _json(initial_slots),
                    now,
                    now,
                    actor,
                ),
            )

            # Copy slots rows if derived from existing version
            if initial_slots:
                source_slots = connection.execute(
                    "SELECT * FROM character_identity_pack_slots WHERE pack_version_id = ?",
                    (from_version_id,),
                ).fetchall()
                for slot in source_slots:
                    slot_id = str(uuid.uuid4())
                    connection.execute(
                        """INSERT INTO character_identity_pack_slots
                        (id, pack_version_id, slot_kind, media_version_id, is_primary,
                         generation_profile_version_id, authorization_id, created_at, created_by, schema_version)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'v1')""",
                        (
                            slot_id,
                            version_id,
                            slot["slot_kind"],
                            slot["media_version_id"],
                            slot["is_primary"],
                            slot["generation_profile_version_id"],
                            slot["authorization_id"],
                            now,
                            actor,
                        ),
                    )

        return self.get_version(version_id)

    def set_version_slot(
        self,
        pack_version_id: str,
        slot_kind: str,
        media_version_id: str,
        generation_profile_version_id: str | None = None,
        is_primary: bool = True,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        clean_kind = _slot_kind(slot_kind)
        now = _now()
        slot_id = str(uuid.uuid4())

        with self.database.transaction() as connection:
            version = connection.execute(
                "SELECT * FROM character_identity_pack_versions WHERE id = ?",
                (pack_version_id,),
            ).fetchone()
            if version is None:
                raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_FOUND", "身份包版本不存在")
            if str(version["status"]) in IMMUTABLE_PACK_VERSION_STATUSES:
                raise DomainRuleError("APPROVED_PACK_IMMUTABLE", "已批准、已替代或已废弃的身份包版本不可修改，请创建新版本草稿")
            if str(version["status"]) not in {PackVersionStatus.DRAFT.value, PackVersionStatus.READY_FOR_REVIEW.value}:
                raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_EDITABLE", "当前身份包版本状态不可编辑")

            media = connection.execute(
                """SELECT mv.id AS media_version_id,mv.version_no AS media_version_no,
                mv.sha256,mv.byte_size,mv.integrity_status,
                ma.project_id AS media_project_id,ma.media_kind,
                waa.id AS current_authorization_id,waa.authorization_status,
                waa.license_status,waa.sha256 AS authorization_sha256,
                waa.byte_size AS authorization_byte_size,waa.revision AS authorization_revision
                FROM media_versions mv
                JOIN media_assets ma ON ma.id = mv.media_asset_id
                LEFT JOIN workspace_asset_authorizations waa
                  ON waa.project_id=ma.project_id AND waa.media_version_id=mv.id
                WHERE mv.id = ?""",
                (media_version_id,),
            ).fetchone()
            if media is None:
                raise DomainRuleError("MEDIA_VERSION_NOT_FOUND", "媒体版本不存在")
            if media["media_project_id"] != version["project_id"]:
                raise DomainRuleError("MEDIA_PROJECT_MISMATCH", "媒体版本不属于当前项目")
            media_for_validation = {
                **dict(media),
                "slot_kind": clean_kind,
            }
            self._validate_slot_row(version, media_for_validation)

            if generation_profile_version_id:
                profile = connection.execute(
                    "SELECT status,capability FROM execution_profile_versions WHERE id=?",
                    (generation_profile_version_id,),
                ).fetchone()
                if profile is None or str(profile["status"]) != "PUBLISHED":
                    raise DomainRuleError("IDENTITY_PACK_PROFILE_NOT_PUBLISHED", "槽位来源 Profile 必须是已发布的不可变版本")

            # Upsert slot row
            connection.execute(
                """INSERT INTO character_identity_pack_slots
                (id, pack_version_id, slot_kind, media_version_id, is_primary,
                 generation_profile_version_id, authorization_id, created_at, created_by, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'v1')
                ON CONFLICT(pack_version_id, slot_kind) DO UPDATE SET
                  media_version_id = excluded.media_version_id,
                  is_primary = excluded.is_primary,
                  generation_profile_version_id = excluded.generation_profile_version_id,
                  authorization_id = excluded.authorization_id""",
                (
                    slot_id,
                    pack_version_id,
                    clean_kind,
                    media_version_id,
                    1 if is_primary else 0,
                    generation_profile_version_id,
                    media["current_authorization_id"],
                    now,
                    actor,
                ),
            )

            # Update version slots_json
            current_slots = json.loads(version["slots_json"] or "{}")
            current_slots[clean_kind] = media_version_id
            connection.execute(
                """UPDATE character_identity_pack_versions
                SET slots_json = ?, content_hash=NULL, updated_at = ?, revision = revision + 1
                WHERE id = ?""",
                (_json(current_slots), now, pack_version_id),
            )

        return self.get_version(pack_version_id)

    def remove_version_slot(
        self,
        pack_version_id: str,
        slot_kind: str,
    ) -> dict[str, Any]:
        clean_kind = _slot_kind(slot_kind)
        now = _now()
        with self.database.transaction() as connection:
            version = connection.execute(
                "SELECT * FROM character_identity_pack_versions WHERE id = ?",
                (pack_version_id,),
            ).fetchone()
            if version is None:
                raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_FOUND", "身份包版本不存在")
            if str(version["status"]) in IMMUTABLE_PACK_VERSION_STATUSES:
                raise DomainRuleError("APPROVED_PACK_IMMUTABLE", "已批准、已替代或已废弃的身份包版本不可修改")
            if str(version["status"]) not in {PackVersionStatus.DRAFT.value, PackVersionStatus.READY_FOR_REVIEW.value}:
                raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_EDITABLE", "当前身份包版本状态不可编辑")

            connection.execute(
                "DELETE FROM character_identity_pack_slots WHERE pack_version_id = ? AND slot_kind = ?",
                (pack_version_id, clean_kind),
            )

            current_slots = json.loads(version["slots_json"] or "{}")
            current_slots.pop(clean_kind, None)
            connection.execute(
                """UPDATE character_identity_pack_versions
                SET slots_json = ?, content_hash=NULL, updated_at = ?, revision = revision + 1
                WHERE id = ?""",
                (_json(current_slots), now, pack_version_id),
            )
        return self.get_version(pack_version_id)

    def approve_pack_version(
        self,
        pack_version_id: str,
        comment: str = "",
        actor: str = "local-user",
    ) -> dict[str, Any]:
        approval_comment = comment.strip()
        if not approval_comment:
            raise DomainRuleError("PACK_APPROVAL_COMMENT_REQUIRED", "人工批准必须填写审核说明")
        now = _now()
        with self.database.transaction() as connection:
            version = connection.execute(
                "SELECT * FROM character_identity_pack_versions WHERE id = ?",
                (pack_version_id,),
            ).fetchone()
            if version is None:
                raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_FOUND", "身份包版本不存在")
            if version["status"] == "APPROVED":
                return self.get_version(pack_version_id)
            if str(version["status"]) not in {
                PackVersionStatus.DRAFT.value,
                PackVersionStatus.READY_FOR_REVIEW.value,
            }:
                raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_APPROVABLE", "当前身份包版本状态不能批准，请新建草稿")

            pack = connection.execute(
                "SELECT status FROM character_identity_packs WHERE id=?",
                (version["pack_id"],),
            ).fetchone()
            if pack is None or str(pack["status"]) != "ACTIVE":
                raise DomainRuleError("IDENTITY_PACK_NOT_ACTIVE", "只有启用中的身份包可以批准新版本")

            content, content_hash = self._validated_content(
                connection,
                version,
                require_three_view=True,
            )
            slots = content["slots"]
            for slot in slots:
                authorization = slot.get("authorization")
                authorization_id = authorization.get("id") if isinstance(authorization, dict) else None
                connection.execute(
                    """UPDATE character_identity_pack_slots SET authorization_id=?
                    WHERE pack_version_id=? AND slot_kind=?""",
                    (authorization_id, pack_version_id, slot["slot_kind"]),
                )
            approval_metadata = {
                "approved_at": now,
                "approved_by": actor,
                "comment": approval_comment,
                "slots_count": len(slots),
                "has_three_view": True,
                "required_slots": list(REQUIRED_THREE_VIEW_SLOTS),
                "content_hash": content_hash,
            }

            # 1. Supersede any currently approved versions of this pack
            superseded_ids = [
                str(row["id"])
                for row in connection.execute(
                    """SELECT id FROM character_identity_pack_versions
                    WHERE pack_id=? AND status='APPROVED' AND id<>?""",
                    (version["pack_id"], pack_version_id),
                ).fetchall()
            ]
            connection.execute(
                """UPDATE character_identity_pack_versions
                SET status = 'SUPERSEDED', updated_at = ?, revision = revision + 1
                WHERE pack_id = ? AND status = 'APPROVED' AND id<>?""",
                (now, version["pack_id"], pack_version_id),
            )

            # 2. Mark this version as APPROVED
            connection.execute(
                """UPDATE character_identity_pack_versions
                SET status = 'APPROVED', approval_metadata_json = ?, content_hash=?,
                retired_at=NULL,retired_by=NULL,retired_reason=NULL,
                updated_at = ?, revision = revision + 1
                WHERE id = ?""",
                (_json(approval_metadata), content_hash, now, pack_version_id),
            )

            # 3. Point pack current_version_id to this approved version
            connection.execute(
                """UPDATE character_identity_packs
                SET current_version_id = ?, updated_at = ?, revision = revision + 1
                WHERE id = ?""",
                (pack_version_id, now, version["pack_id"]),
            )

            stale_variant_ids: list[str] = []
            for superseded_id in superseded_ids:
                stale_variant_ids.extend(
                    self._mark_variants_stale_for_version(
                        connection,
                        superseded_id,
                        reason=f"identity_pack_superseded:{superseded_id}:{pack_version_id}",
                        now=now,
                    )
                )

            # 4. Audit event
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'producer', 'CHARACTER_IDENTITY_PACK_APPROVED', 'character_identity_pack_version', ?, ?, ?)""",
                (
                    actor,
                    pack_version_id,
                    f"批准角色身份包版本 v{version['version_no']}",
                    _json(
                        {
                            **approval_metadata,
                            "superseded_version_ids": superseded_ids,
                            "stale_variant_ids": sorted(set(stale_variant_ids)),
                        }
                    ),
                ),
            )

        return self.get_version(pack_version_id)

    def bind_shot_identity_pack(
        self,
        shot_id: str,
        story_asset_id: str,
        pack_version_id: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        now = _now()
        with self.database.transaction() as connection:
            shot = connection.execute(
                """SELECT sh.id,sh.episode_id,se.project_id
                FROM shots sh JOIN episodes e ON e.id=sh.episode_id
                JOIN seasons se ON se.id=e.season_id WHERE sh.id=?""",
                (shot_id,),
            ).fetchone()
            if shot is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在")

            version = connection.execute(
                """SELECT v.*,p.status AS pack_status,p.current_version_id
                FROM character_identity_pack_versions v
                JOIN character_identity_packs p ON p.id=v.pack_id WHERE v.id=?""",
                (pack_version_id,),
            ).fetchone()
            if version is None:
                raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_FOUND", "身份包版本不存在")
            if version["story_asset_id"] != story_asset_id:
                raise DomainRuleError("ASSET_PACK_MISMATCH", "身份包版本与目标角色资产不一致")
            if str(version["project_id"]) != str(shot["project_id"]):
                raise DomainRuleError("IDENTITY_PACK_SHOT_PROJECT_MISMATCH", "身份包版本与镜头不属于同一项目")
            if str(version["pack_status"]) != "ACTIVE":
                raise DomainRuleError("IDENTITY_PACK_NOT_ACTIVE", "已归档或废弃的身份包不能绑定到镜头")
            if str(version["status"]) != PackVersionStatus.APPROVED.value:
                raise DomainRuleError("APPROVED_IDENTITY_PACK_REQUIRED", "镜头只能绑定已经人工批准的身份包版本")
            if str(version["current_version_id"] or "") != pack_version_id:
                raise DomainRuleError("CURRENT_APPROVED_IDENTITY_PACK_REQUIRED", "镜头只能绑定当前已批准版本")
            self._validated_content(connection, version, require_three_view=True)

            binding_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO shot_asset_bindings
                (id, shot_id, asset_id, asset_state_id, identity_pack_version_id, role_in_shot,
                 created_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, 'main', ?, ?, 1, 'v2')
                ON CONFLICT(shot_id, asset_id, role_in_shot) DO UPDATE SET
                  identity_pack_version_id = excluded.identity_pack_version_id,
                  asset_state_id = COALESCE(excluded.asset_state_id, shot_asset_bindings.asset_state_id),
                  revision = shot_asset_bindings.revision + 1""",
                (binding_id, shot_id, story_asset_id, version["asset_state_id"], pack_version_id, now, actor),
            )

            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'producer','SHOT_IDENTITY_PACK_BOUND','shot',?,?,?)""",
                (
                    actor,
                    shot_id,
                    "镜头绑定已人工批准的角色身份包版本",
                    _json(
                        {
                            "story_asset_id": story_asset_id,
                            "pack_version_id": pack_version_id,
                            "version_no": int(version["version_no"]),
                            "content_hash": version["content_hash"],
                        }
                    ),
                ),
            )

        return {"shot_id": shot_id, "story_asset_id": story_asset_id, "identity_pack_version_id": pack_version_id}

    def get_shot_character_packs(self, shot_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT b.shot_id, b.asset_id AS story_asset_id, b.asset_state_id, b.identity_pack_version_id, b.role_in_shot,
                a.name AS character_name, a.code AS character_code,
                v.version_no AS bound_version_no, v.status AS bound_version_status,
                v.slots_json AS bound_slots_json,
                p.id AS pack_id, p.name AS pack_name, p.code AS pack_code,
                p.current_version_id AS latest_approved_version_id
                FROM shot_asset_bindings b
                JOIN story_assets a ON a.id = b.asset_id AND a.kind = 'CHARACTER'
                LEFT JOIN character_identity_pack_versions v ON v.id = b.identity_pack_version_id
                LEFT JOIN character_identity_packs p ON p.id = v.pack_id
                WHERE b.shot_id = ?""",
                (shot_id,),
            ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["bound_slots"] = json.loads(d.pop("bound_slots_json") or "{}") if d.get("bound_slots_json") else {}
            # Stale check: if a newer approved version exists for the pack
            d["is_stale"] = bool(
                d["identity_pack_version_id"]
                and (
                    d["bound_version_status"] != PackVersionStatus.APPROVED.value
                    or not d["latest_approved_version_id"]
                    or d["identity_pack_version_id"] != d["latest_approved_version_id"]
                )
            )
            d["stale_reason"] = (
                "BOUND_PACK_VERSION_NOT_APPROVED"
                if d["identity_pack_version_id"] and d["bound_version_status"] != PackVersionStatus.APPROVED.value
                else "NEWER_APPROVED_PACK_VERSION_AVAILABLE"
                if d["is_stale"] and d["latest_approved_version_id"]
                else "NO_CURRENT_APPROVED_PACK_VERSION"
                if d["is_stale"]
                else None
            )
            result.append(d)
        return result

    def compare_versions(self, base_version_id: str, target_version_id: str) -> dict[str, Any]:
        base = self.get_version(base_version_id)
        target = self.get_version(target_version_id)
        if str(base["pack_id"]) != str(target["pack_id"]):
            raise DomainRuleError("IDENTITY_PACK_COMPARE_SCOPE_INVALID", "只能比较同一身份包内的两个版本")
        base_slots = {str(item["slot_kind"]): item for item in base["slots"]}
        target_slots = {str(item["slot_kind"]): item for item in target["slots"]}
        added = sorted(set(target_slots) - set(base_slots))
        removed = sorted(set(base_slots) - set(target_slots))
        changed = sorted(
            kind
            for kind in set(base_slots) & set(target_slots)
            if str(base_slots[kind]["media_version_id"]) != str(target_slots[kind]["media_version_id"])
        )
        unchanged = sorted(set(base_slots) & set(target_slots) - set(changed))
        return {
            "pack_id": str(base["pack_id"]),
            "base": {
                "id": base_version_id,
                "version_no": int(base["version_no"]),
                "status": str(base["status"]),
                "content_hash": base.get("content_hash"),
            },
            "target": {
                "id": target_version_id,
                "version_no": int(target["version_no"]),
                "status": str(target["status"]),
                "content_hash": target.get("content_hash"),
            },
            "slots": {
                "added": added,
                "removed": removed,
                "changed": [
                    {
                        "slot_kind": kind,
                        "before_media_version_id": str(base_slots[kind]["media_version_id"]),
                        "after_media_version_id": str(target_slots[kind]["media_version_id"]),
                    }
                    for kind in changed
                ],
                "unchanged": unchanged,
            },
            "has_changes": bool(added or removed or changed),
        }

    def version_impact(self, pack_version_id: str) -> dict[str, Any]:
        version = self.get_version(pack_version_id)
        with self.database.connect() as connection:
            shot_rows = connection.execute(
                """SELECT b.shot_id,b.asset_id AS story_asset_id,b.role_in_shot,
                sh.code AS shot_code,e.id AS episode_id,e.code AS episode_code
                FROM shot_asset_bindings b JOIN shots sh ON sh.id=b.shot_id
                JOIN episodes e ON e.id=sh.episode_id
                WHERE b.identity_pack_version_id=? ORDER BY e.code,sh.code,b.asset_id""",
                (pack_version_id,),
            ).fetchall()
            variant_rows = connection.execute(
                """SELECT gv.id,gv.status,gv.is_stale,gv.stale_reason,gv.identity_pack_snapshot_json,
                gi.owner_type,gi.owner_id,j.id AS job_id,j.state AS job_state
                FROM generation_variants gv JOIN generation_intents gi ON gi.id=gv.intent_id
                LEFT JOIN jobs j ON j.subject_type='GENERATION_VARIANT' AND j.subject_id=gv.id
                WHERE gv.identity_pack_snapshot_json<>'{}' ORDER BY gv.created_at,gv.id"""
            ).fetchall()
        variants: list[dict[str, Any]] = []
        for row in variant_rows:
            try:
                snapshot = json.loads(str(row["identity_pack_snapshot_json"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                continue
            packs = snapshot.get("packs", []) if isinstance(snapshot, dict) else []
            if any(
                isinstance(item, dict) and str(item.get("pack_version_id") or "") == pack_version_id
                for item in packs
            ):
                item = dict(row)
                item.pop("identity_pack_snapshot_json", None)
                variants.append(item)
        return {
            "pack_version_id": pack_version_id,
            "pack_id": str(version["pack_id"]),
            "version_no": int(version["version_no"]),
            "status": str(version["status"]),
            "is_current": bool(
                self.get_pack(str(version["pack_id"])).get("current_version_id") == pack_version_id
            ),
            "shots": [dict(row) for row in shot_rows],
            "generation_variants": variants,
            "summary": {
                "shot_binding_count": len(shot_rows),
                "generation_variant_count": len(variants),
                "running_job_count": sum(
                    str(row.get("job_state") or "") in {"QUEUED", "CLAIMED", "RUNNING", "WAITING"}
                    for row in variants
                ),
            },
        }

    def retire_pack_version(
        self,
        pack_version_id: str,
        reason: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        clean_reason = reason.strip()
        if not clean_reason:
            raise DomainRuleError("IDENTITY_PACK_RETIRE_REASON_REQUIRED", "废弃身份包版本必须填写原因")
        now = _now()
        with self.database.transaction() as connection:
            version = connection.execute(
                "SELECT * FROM character_identity_pack_versions WHERE id=?",
                (pack_version_id,),
            ).fetchone()
            if version is None:
                raise DomainRuleError("IDENTITY_PACK_VERSION_NOT_FOUND", "身份包版本不存在")
            if str(version["status"]) == PackVersionStatus.RETIRED.value:
                return self.get_version(pack_version_id)
            connection.execute(
                """UPDATE character_identity_pack_versions
                SET status='RETIRED',retired_at=?,retired_by=?,retired_reason=?,
                updated_at=?,revision=revision+1 WHERE id=?""",
                (now, actor, clean_reason, now, pack_version_id),
            )
            connection.execute(
                """UPDATE character_identity_packs SET current_version_id=NULL,
                updated_at=?,revision=revision+1 WHERE id=? AND current_version_id=?""",
                (now, version["pack_id"], pack_version_id),
            )
            stale_variant_ids = self._mark_variants_stale_for_version(
                connection,
                pack_version_id,
                reason=f"identity_pack_retired:{pack_version_id}",
                now=now,
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'producer','CHARACTER_IDENTITY_PACK_VERSION_RETIRED',
                'character_identity_pack_version',?,?,?)""",
                (
                    actor,
                    pack_version_id,
                    f"废弃角色身份包版本 v{version['version_no']}",
                    _json({"reason": clean_reason, "stale_variant_ids": stale_variant_ids}),
                ),
            )
        return self.get_version(pack_version_id)
