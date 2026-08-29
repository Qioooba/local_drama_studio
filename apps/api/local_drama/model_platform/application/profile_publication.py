"""Immutable V2 execution Profiles and payload-bound validation publication."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


@dataclass(frozen=True, slots=True)
class ProfileVersionDraft:
    profile_code: str
    profile_title: str
    capability_definition_id: str
    runtime_installation_version_id: str
    parameter_contract_version_id: str
    adapter_binding_contract_version_id: str
    resource_policy_version_id: str
    payload: Mapping[str, Any]
    workflow_version_id: str | None = None


@dataclass(frozen=True, slots=True)
class CreatedProfileVersion:
    profile_id: str
    profile_version_id: str
    version_no: int
    payload_hash: str


@dataclass(frozen=True, slots=True)
class RecordedValidation:
    validation_run_id: str
    status: str


class ProfilePublicationService:
    """Creates immutable candidates and promotes only payload-matched evidence."""

    def __init__(self, database: Database) -> None:
        self.database = database

    def create_candidate(self, draft: ProfileVersionDraft) -> CreatedProfileVersion:
        if not draft.profile_code.strip() or not draft.profile_title.strip():
            raise DomainRuleError("MP_PROFILE_IDENTITY_INVALID", "Profile code 和 title 均为必填项。")
        payload_hash = _hash(draft.payload)
        now = _utc_now()
        with self.database.transaction() as connection:
            _assert_references_exist(connection, draft)
            _assert_profile_runtime_bindings(connection, draft)
            profile = connection.execute("SELECT id FROM mp_execution_profiles WHERE code=?", (draft.profile_code,)).fetchone()
            if profile is None:
                profile_id = str(uuid.uuid4())
                connection.execute(
                    "INSERT INTO mp_execution_profiles (id,code,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                    (profile_id, draft.profile_code, draft.profile_title, now, now),
                )
            else:
                profile_id = str(profile["id"])
                existing_title = connection.execute("SELECT title FROM mp_execution_profiles WHERE id=?", (profile_id,)).fetchone()
                if str(existing_title["title"]) != draft.profile_title:
                    raise DomainRuleError("MP_PROFILE_TITLE_IMMUTABLE", "既有 Profile 不可通过创建新版本修改标题。")
            existing = connection.execute(
                "SELECT id FROM mp_execution_profile_versions WHERE profile_id=? AND payload_hash=?",
                (profile_id, payload_hash),
            ).fetchone()
            if existing is not None:
                raise DomainRuleError(
                    "MP_PROFILE_PAYLOAD_ALREADY_EXISTS",
                    "相同不可变 payload 已存在；请复用该版本或创建有意义的新版本。",
                    {"profile_version_id": str(existing["id"]), "payload_hash": payload_hash},
                )
            version_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version_no), 0) + 1 FROM mp_execution_profile_versions WHERE profile_id=?",
                    (profile_id,),
                ).fetchone()[0]
            )
            profile_version_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO mp_execution_profile_versions
                (id,profile_id,version_no,capability_definition_id,runtime_installation_version_id,parameter_contract_version_id,
                 adapter_binding_contract_version_id,resource_policy_version_id,workflow_version_id,payload_json,payload_hash,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    profile_version_id,
                    profile_id,
                    version_no,
                    draft.capability_definition_id,
                    draft.runtime_installation_version_id,
                    draft.parameter_contract_version_id,
                    draft.adapter_binding_contract_version_id,
                    draft.resource_policy_version_id,
                    draft.workflow_version_id,
                    _json(draft.payload),
                    payload_hash,
                    now,
                    now,
                ),
            )
        return CreatedProfileVersion(profile_id, profile_version_id, version_no, payload_hash)

    def record_validation(
        self,
        profile_version_id: str,
        *,
        validation_kind: str,
        status: str,
        result: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> RecordedValidation:
        now = _utc_now()
        validation_run_id = str(uuid.uuid4())
        with self.database.transaction() as connection:
            profile = connection.execute(
                "SELECT payload_hash,capability_definition_id,runtime_installation_version_id,payload_json "
                "FROM mp_execution_profile_versions WHERE id=?", (profile_version_id,)
            ).fetchone()
            if profile is None:
                raise DomainRuleError("MP_PROFILE_VERSION_NOT_FOUND", "待验证的 V2 ProfileVersion 不存在。")
            expected_hash = str(profile["payload_hash"])
            result_hash = result.get("payload_hash")
            if result_hash != expected_hash:
                raise DomainRuleError(
                    "MP_VALIDATION_PAYLOAD_MISMATCH",
                    "验证证据必须绑定到当前不可变 Profile payload。",
                    {"expected": expected_hash, "actual": result_hash},
                )
            _assert_source_capability_smoke(connection, profile, result)
            connection.execute(
                """INSERT INTO mp_validation_runs
                (id,target_kind,target_id,validation_kind,status,result_json,started_at,finished_at,created_at,updated_at)
                VALUES (?, 'EXECUTION_PROFILE_VERSION', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (validation_run_id, profile_version_id, validation_kind, status, _json(result), now, now, now, now),
            )
            connection.execute(
                """INSERT INTO mp_validation_evidence
                (id,validation_run_id,kind,content_hash,payload_json,artifact_ref,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (str(uuid.uuid4()), validation_run_id, validation_kind, _hash(evidence), _json(evidence), None, now, now),
            )
        return RecordedValidation(validation_run_id, status)

    def publish(self, profile_version_id: str, *, validation_run_id: str, reason: str) -> None:
        now = _utc_now()
        with self.database.transaction() as connection:
            profile = connection.execute(
                "SELECT payload_hash FROM mp_execution_profile_versions WHERE id=?", (profile_version_id,)
            ).fetchone()
            if profile is None:
                raise DomainRuleError("MP_PROFILE_VERSION_NOT_FOUND", "待发布的 V2 ProfileVersion 不存在。")
            validation = connection.execute(
                "SELECT target_id,status,result_json FROM mp_validation_runs WHERE id=? AND target_kind='EXECUTION_PROFILE_VERSION'",
                (validation_run_id,),
            ).fetchone()
            if validation is None or str(validation["target_id"]) != profile_version_id:
                raise DomainRuleError("MP_PUBLICATION_VALIDATION_TARGET_INVALID", "发布验证不属于当前 ProfileVersion。")
            if str(validation["status"]) != "SMOKE_PASSED":
                raise DomainRuleError("MP_PUBLICATION_VALIDATION_NOT_PASSED", "只有能力冒烟通过的验证可用于发布。")
            try:
                validation_result = json.loads(str(validation["result_json"]))
            except (TypeError, ValueError) as error:
                raise DomainRuleError("MP_PUBLICATION_VALIDATION_INVALID", "发布验证结果不是合法 JSON。") from error
            if not isinstance(validation_result, dict) or validation_result.get("payload_hash") != str(profile["payload_hash"]):
                raise DomainRuleError("MP_PUBLICATION_PAYLOAD_MISMATCH", "发布验证与 Profile payload 不匹配。")
            existing = connection.execute(
                "SELECT status FROM mp_profile_publications WHERE execution_profile_version_id=?", (profile_version_id,)
            ).fetchone()
            if existing is not None:
                raise DomainRuleError("MP_PROFILE_ALREADY_PUBLISHED", "一个不可变 ProfileVersion 只能有一个发布记录。")
            connection.execute(
                """INSERT INTO mp_profile_publications
                (id,execution_profile_version_id,status,validation_run_id,published_at,retired_at,reason,created_at,updated_at)
                VALUES (?,?,'PUBLISHED',?,?,NULL,?,?,?)""",
                (str(uuid.uuid4()), profile_version_id, validation_run_id, now, reason, now, now),
            )


def _assert_references_exist(connection: sqlite3.Connection, draft: ProfileVersionDraft) -> None:
    checks = (
        ("mp_capability_definitions", draft.capability_definition_id),
        ("mp_runtime_installation_versions", draft.runtime_installation_version_id),
        ("mp_parameter_contract_versions", draft.parameter_contract_version_id),
        ("mp_adapter_binding_contract_versions", draft.adapter_binding_contract_version_id),
        ("mp_resource_policy_versions", draft.resource_policy_version_id),
    )
    for table, identifier in checks:
        if connection.execute(f"SELECT 1 FROM {table} WHERE id=?", (identifier,)).fetchone() is None:
            raise DomainRuleError("MP_PROFILE_REFERENCE_NOT_FOUND", "Profile 依赖的版本化合同不存在。", {"table": table, "id": identifier})


def _assert_profile_runtime_bindings(connection: sqlite3.Connection, draft: ProfileVersionDraft) -> None:
    """Profiles bind to proven Offerings, never an arbitrary runtime version.

    The payload retains runtime-model installation identifiers because a
    RuntimeVersion can host more than one native model.  Each binding must be
    a declared offering for this capability, already smoke-passed, and its
    concrete installation must be READY.  This keeps a later UI or import
    endpoint from manufacturing a publishable Profile with bare IDs.
    """
    payload = _json_object(draft.payload, "MP_PROFILE_PAYLOAD_INVALID")
    raw_ids = payload.get("runtime_model_installation_ids")
    if not isinstance(raw_ids, list) or not raw_ids or any(not isinstance(item, str) or not item.strip() for item in raw_ids):
        raise DomainRuleError(
            "MP_PROFILE_RUNTIME_BINDING_REQUIRED",
            "Profile payload 必须绑定至少一个已验证的 RuntimeModelInstallation。",
        )
    identifiers = tuple(dict.fromkeys(item.strip() for item in raw_ids))
    placeholders = ",".join("?" for _ in identifiers)
    rows = connection.execute(
        f"""SELECT installation.id FROM mp_runtime_model_installations installation
            JOIN mp_capability_offerings offering ON offering.runtime_model_installation_id=installation.id
            WHERE installation.id IN ({placeholders})
              AND installation.runtime_installation_version_id=?
              AND offering.capability_definition_id=?
              AND offering.validation_status='SMOKE_PASSED'
              AND installation.install_state='READY'""",
        (*identifiers, draft.runtime_installation_version_id, draft.capability_definition_id),
    ).fetchall()
    proven = {str(row["id"]) for row in rows}
    missing = [identifier for identifier in identifiers if identifier not in proven]
    if missing:
        raise DomainRuleError(
            "MP_PROFILE_RUNTIME_BINDING_NOT_READY",
            "Profile 只能绑定同一 RuntimeVersion 上已经冒烟通过并就绪的 Offering。",
            {"runtime_model_installation_ids": missing},
        )


def _assert_source_capability_smoke(connection: sqlite3.Connection, profile: sqlite3.Row, result: Mapping[str, Any]) -> None:
    source_id = result.get("source_capability_validation_run_id")
    if not isinstance(source_id, str) or not source_id.strip():
        raise DomainRuleError(
            "MP_PROFILE_VALIDATION_SOURCE_REQUIRED",
            "Profile 验证必须引用真实通过的 CapabilityOffering smoke 记录。",
        )
    payload = _json_object(profile["payload_json"], "MP_PROFILE_PAYLOAD_INVALID")
    raw_ids = payload.get("runtime_model_installation_ids")
    if not isinstance(raw_ids, list):  # create_candidate already protects this; preserve corruption defense.
        raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Profile payload 缺少 RuntimeModelInstallation 绑定。")
    identifiers = tuple(item.strip() for item in raw_ids if isinstance(item, str) and item.strip())
    if not identifiers:
        raise DomainRuleError("MP_PROFILE_PAYLOAD_INVALID", "Profile payload 缺少 RuntimeModelInstallation 绑定。")
    placeholders = ",".join("?" for _ in identifiers)
    source = connection.execute(
        f"""SELECT run.id FROM mp_validation_runs run
            JOIN mp_capability_offerings offering ON offering.id=run.target_id
            JOIN mp_runtime_model_installations installation ON installation.id=offering.runtime_model_installation_id
            WHERE run.id=? AND run.target_kind='CAPABILITY_OFFERING'
              AND run.validation_kind='CAPABILITY_SMOKE' AND run.status='SMOKE_PASSED'
              AND installation.id IN ({placeholders})
              AND installation.runtime_installation_version_id=?
              AND offering.capability_definition_id=?
              AND EXISTS (SELECT 1 FROM mp_validation_evidence evidence WHERE evidence.validation_run_id=run.id)""",
        (source_id.strip(), *identifiers, profile["runtime_installation_version_id"], profile["capability_definition_id"]),
    ).fetchone()
    if source is None:
        raise DomainRuleError(
            "MP_PROFILE_VALIDATION_SOURCE_INVALID",
            "Profile 验证引用的能力 smoke 不属于当前已验证运行时/能力，或缺少证据。",
        )


def _json_object(value: object, error_code: str) -> Mapping[str, Any]:
    try:
        decoded = json.loads(str(value)) if not isinstance(value, Mapping) else value
    except (TypeError, ValueError) as error:
        raise DomainRuleError(error_code, "Profile payload 不是合法 JSON 对象。") from error
    if not isinstance(decoded, Mapping):
        raise DomainRuleError(error_code, "Profile payload 必须是 JSON 对象。")
    return decoded


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
