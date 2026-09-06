"""Explicit promotion of discovery evidence into V2 registered candidates."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.model_platform.domain.capabilities import is_canonical_capability


@dataclass(frozen=True, slots=True)
class RegisteredDiscoveryCandidate:
    model_release_id: str
    model_release_code: str
    runtime_model_installation_id: str
    created: bool


class DiscoveryRegistrationService:
    """Turn one present scanner observation into a reviewable V2 candidate.

    Registration is an explicit administrator action.  It creates neither
    validation evidence nor a Profile, and every offering remains NOT_RUN.
    A changed native locator is deliberately rejected until a later immutable
    runtime/version replacement workflow is implemented.
    """

    def __init__(self, database: Database) -> None:
        self.database = database

    def register(self, observation_id: str) -> RegisteredDiscoveryCandidate:
        now = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT observation.id,observation.native_id,observation.kind,observation.observed_json,
                          run.runtime_installation_version_id
                   FROM mp_discovery_observations observation
                   JOIN mp_discovery_runs run ON run.id=observation.discovery_run_id
                   WHERE observation.id=? AND run.status='SUCCEEDED'""",
                (observation_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("MP_DISCOVERY_OBSERVATION_NOT_FOUND", "扫描观察不存在或所属扫描未成功。")
            runtime_version_id = row["runtime_installation_version_id"]
            if runtime_version_id is None:
                raise DomainRuleError("MP_DISCOVERY_RUNTIME_UNBOUND", "扫描观察没有受控 V2 RuntimeVersion，不能登记。")
            observed = _json_object(row["observed_json"])
            if str(observed.get("presence") or "") != "PRESENT":
                raise DomainRuleError("MP_DISCOVERY_CANDIDATE_NOT_PRESENT", "只能登记当前扫描中存在且完整的模型候选。")
            native_locator = str(row["native_id"])
            existing = connection.execute(
                """SELECT id,release_id FROM mp_runtime_model_installations
                   WHERE runtime_installation_version_id=? AND native_locator=?""",
                (runtime_version_id, native_locator),
            ).fetchone()
            if existing is not None:
                release = connection.execute("SELECT code FROM mp_model_releases WHERE id=?", (existing["release_id"],)).fetchone()
                return RegisteredDiscoveryCandidate(
                    str(existing["release_id"]), str(release["code"]), str(existing["id"]), False
                )

            runtime_kind = str(row["kind"])
            raw_metadata = observed.get("metadata")
            metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}
            family_code = _code(f"{runtime_kind.lower()}-{metadata.get('family') or metadata.get('release_code') or native_locator}")
            family = connection.execute("SELECT id FROM mp_model_families WHERE code=?", (family_code,)).fetchone()
            if family is None:
                family_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO mp_model_families (id,code,title,vendor,license_json,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?)""",
                    (family_id, family_code, str(metadata.get("family") or native_locator), None, "{}", now, now),
                )
            else:
                family_id = str(family["id"])
            digest = str(observed.get("digest") or _hash(native_locator))
            release_code = _code(f"{family_code}-{digest[:12]}")
            release = connection.execute("SELECT id,code FROM mp_model_releases WHERE code=?", (release_code,)).fetchone()
            if release is None:
                release_id = str(uuid.uuid4())
                connection.execute(
                    """INSERT INTO mp_model_releases
                    (id,family_id,code,upstream_id,revision,format,quantization,metadata_json,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        release_id,
                        family_id,
                        release_code,
                        native_locator,
                        digest,
                        {"OLLAMA": "OLLAMA_TAG", "LLAMA_CPP_MANAGED": "GGUF"}.get(runtime_kind, "MODEL_LOCK_BUNDLE"),
                        _optional_text(metadata.get("quantization_level")),
                        _json({"discovery_observation_id": observation_id, "runtime_kind": runtime_kind}),
                        now,
                        now,
                    ),
                )
            else:
                release_id = str(release["id"])
                release_code = str(release["code"])
            installation_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO mp_runtime_model_installations
                (id,release_id,runtime_installation_version_id,native_locator,install_state,metadata_json,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (installation_id, release_id, runtime_version_id, native_locator, "DISCOVERED", "{}", now, now),
            )
            for capability in _candidate_capabilities(observed):
                definition = connection.execute("SELECT id FROM mp_capability_definitions WHERE code=?", (capability,)).fetchone()
                if definition is not None:
                    connection.execute(
                        """INSERT OR IGNORE INTO mp_capability_offerings
                        (id,runtime_model_installation_id,capability_definition_id,native_metadata_json,validation_status,created_at,updated_at)
                        VALUES (?,?,?,?,?,?,?)""",
                        (str(uuid.uuid4()), installation_id, definition["id"], "{}", "NOT_RUN", now, now),
                    )
        return RegisteredDiscoveryCandidate(release_id, release_code, installation_id, True)


def _candidate_capabilities(observed: dict[str, Any]) -> tuple[str, ...]:
    values = observed.get("candidate_capabilities")
    if not isinstance(values, list):
        return ()
    return tuple(
        code for item in values
        if isinstance(item, dict)
        for code in [str(item.get("capability") or "").strip().upper()]
        if is_canonical_capability(code)
    )


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError) as error:
        raise DomainRuleError("MP_DISCOVERY_OBSERVATION_INVALID", "扫描观察数据不是合法 JSON。") from error
    if not isinstance(parsed, dict):
        raise DomainRuleError("MP_DISCOVERY_OBSERVATION_INVALID", "扫描观察数据必须是 JSON 对象。")
    return parsed


def _code(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not cleaned:
        return f"candidate-{_hash(value)[:12]}"
    return cleaned[:140]


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _optional_text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()
