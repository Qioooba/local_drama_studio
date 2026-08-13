"""Manifest-backed local runtime and capability profile registry."""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.manifest import ManifestSnapshot, load_manifest


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _stable_id(value: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-drama:{value}"))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _code(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:120]


class ProfileService:
    def __init__(self, database: Database, manifest_path: Path) -> None:
        self.database = database
        self.manifest_path = manifest_path

    def load(self) -> ManifestSnapshot:
        return load_manifest(self.manifest_path)

    def sync_manifest(self, actor: str = "system") -> dict[str, Any]:
        manifest = self.load()
        runtime_data = manifest.runtime
        comfy_api = dict(runtime_data.get("comfyui_api", {}))
        base_url = str(comfy_api.get("base_url", ""))
        if not base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise DomainRuleError("LOCAL_RUNTIME_REQUIRED", "本地 Runtime 只允许 loopback HTTP")
        runtime_id = _stable_id("runtime:comfyui-h3")
        now = _utc_now()
        runtime_status = "AVAILABLE" if comfy_api.get("port_8188_listening") else "BLOCKED_OFFLINE"
        current_state = dict(manifest.data.get("authoritative_current_state", {}))
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO local_runtimes
                (id, code, title, transport, base_url, executable_ref, runtime_version, status, details_json,
                 created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, 'comfyui-h3-local', 'ComfyUI H3 本机候选 Runtime', 'LOOPBACK_HTTP', ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')
                ON CONFLICT(code) DO UPDATE SET base_url=excluded.base_url,
                executable_ref=excluded.executable_ref, runtime_version=excluded.runtime_version,
                status=excluded.status, details_json=excluded.details_json, updated_at=excluded.updated_at,
                revision=local_runtimes.revision+1""",
                (
                    runtime_id,
                    base_url,
                    runtime_data.get("comfyui_root"),
                    runtime_data.get("comfyui_version"),
                    runtime_status,
                    _json({"manifest_sha256": manifest.sha256, "comfyui": comfy_api}),
                    now,
                    now,
                    actor,
                ),
            )

            partitions = manifest.data.get("models", {}).get("partitions", {})
            artifact_ids: list[str] = []
            for partition_name, partition in partitions.items():
                if not isinstance(partition, dict):
                    continue
                for component_name, component in partition.items():
                    if not isinstance(component, dict) or not component.get("path"):
                        continue
                    artifact_base_code = _code(f"h3-{partition_name}-{component_name}")
                    artifact_code = f"{artifact_base_code}-{manifest.sha256[:12]}"
                    artifact_id = _stable_id(f"artifact:{artifact_base_code}:{manifest.sha256}")
                    artifact_ids.append(artifact_id)
                    connection.execute(
                        """INSERT INTO model_artifacts
                        (id, runtime_id, code, kind, machine_path_ref, sha256, size_bytes, license_note,
                         compatibility_json, status, manifest_sha256, created_at, updated_at, created_by, revision, schema_version)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'CANDIDATE', ?, ?, ?, ?, 1, 'v2')
                        ON CONFLICT(code) DO NOTHING""",
                        (
                            artifact_id,
                            runtime_id,
                            artifact_code,
                            component.get("role", component_name),
                            component["path"],
                            component.get("full_sha256") if isinstance(component.get("full_sha256"), str) and len(component["full_sha256"]) == 64 else None,
                            component.get("bytes"),
                            "manifest-provided; verify license before formal publish",
                            _json({"partition": partition_name, "component": component_name}),
                            manifest.sha256,
                            now,
                            now,
                            actor,
                        ),
                    )

            profiles: list[dict[str, Any]] = []
            capabilities = manifest.capabilities
            for capability, capability_data in capabilities.items():
                if not isinstance(capability_data, dict):
                    continue
                route_status = manifest.route_status.get(f"native_{str(capability).lower()}", capability_data.get("status", "UNKNOWN"))
                profile_code = _code(f"h3-native-{capability}")
                profile_id = _stable_id(f"profile:{profile_code}")
                profile_status = "CANDIDATE_BLOCKED" if runtime_status != "AVAILABLE" else "CANDIDATE_UNVERIFIED"
                capability_contract = {
                    "capability": capability,
                    "route_status": route_status,
                    "manifest_capability": capability_data,
                    "published": False,
                    "activation_required": True,
                }
                connection.execute(
                    """INSERT INTO execution_profiles (id, code, title, created_at, updated_at, created_by, revision, schema_version)
                    VALUES (?, ?, ?, ?, ?, ?, 1, 'v2')
                    ON CONFLICT(code) DO UPDATE SET title=excluded.title, updated_at=excluded.updated_at,
                    revision=execution_profiles.revision+1""",
                    (profile_id, profile_code, f"H3 {capability} 本机候选 Profile", now, now, actor),
                )
                existing_version = connection.execute(
                    """SELECT * FROM execution_profile_versions
                    WHERE execution_profile_id=? AND manifest_sha256=? ORDER BY version_no DESC LIMIT 1""",
                    (profile_id, manifest.sha256),
                ).fetchone()
                if existing_version is None:
                    version_no = int(
                        connection.execute(
                            "SELECT COALESCE(MAX(version_no), 0) + 1 AS next_no FROM execution_profile_versions WHERE execution_profile_id=?",
                            (profile_id,),
                        ).fetchone()["next_no"]
                    )
                    version_id = _stable_id(f"profile-version:{profile_code}:{manifest.sha256}")
                    connection.execute(
                        """INSERT INTO execution_profile_versions
                        (id, execution_profile_id, version_no, capability, model_bundle_json, input_contract_json,
                         parameter_schema_json, status, manifest_sha256, capability_json, worker_policy,
                         created_at, updated_at, created_by, revision, schema_version)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                        (
                            version_id,
                            profile_id,
                            version_no,
                            capability,
                            _json({"manifest_sha256": manifest.sha256, "artifact_ids": artifact_ids, "route_status": route_status}),
                            _json({"required_inputs": capability_data.get("required_nodes", []), "transport": "LOOPBACK_HTTP"}),
                            _json({"seed": {"required": True, "determinism": "profile_declared"}}),
                            profile_status,
                            manifest.sha256,
                            _json(capability_contract),
                            manifest.worker_policy,
                            now,
                            now,
                            actor,
                        ),
                    )
                else:
                    version_id = str(existing_version["id"])
                    version_no = int(existing_version["version_no"])
                    profile_status = str(existing_version["status"])
                profiles.append(
                    {
                        "id": profile_id,
                        "version_id": version_id,
                        "version_no": version_no,
                        "code": profile_code,
                        "capability": capability,
                        "status": profile_status,
                        "route_status": route_status,
                        "published": profile_status == "PUBLISHED",
                    }
                )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'MANIFEST_SYNCED', 'manifest', ?, ?, ?)",
                (actor, manifest.sha256, "同步只读本机 manifest 候选", _json({"manifest_sha256": manifest.sha256, "profile_count": len(profiles)})),
            )
        return {
            "manifest": manifest.as_public_dict(),
            "runtime": {"id": runtime_id, "status": runtime_status, "base_url": base_url},
            "profiles": profiles,
            "artifact_count": len(artifact_ids),
            "current_state": current_state,
        }

    def list_profiles(self) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT p.id, p.code, p.title, v.id AS version_id, v.version_no, v.capability,
                v.status, v.manifest_sha256, v.capability_json, v.worker_policy,
                v.output_contract_json, v.resource_policy_json, v.revision
                FROM execution_profiles p JOIN execution_profile_versions v
                ON v.execution_profile_id = p.id ORDER BY p.code, v.version_no DESC"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["capability_contract"] = json.loads(item.pop("capability_json"))
            result.append(item)
        return result

    @staticmethod
    def _contract_payload(row: Any) -> dict[str, Any]:
        return {
            "input_contract": json.loads(str(row["input_contract_json"] or "{}")),
            "parameter_schema": json.loads(str(row["parameter_schema_json"] or "{}")),
            "output_contract": json.loads(str(row["output_contract_json"] or "{}")),
            "resource_policy": json.loads(str(row["resource_policy_json"] or "{}")),
        }

    @staticmethod
    def _contract_hash(payload: dict[str, Any]) -> str:
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()

    @staticmethod
    def _execution_fingerprint(row: Any) -> str:
        payload = {
            "capability": row["capability"],
            "runtime_version_id": row["runtime_version_id"],
            "workflow_version_id": row["workflow_version_id"],
            "model_bundle": json.loads(str(row["model_bundle_json"] or "{}")),
            "input_contract": json.loads(str(row["input_contract_json"] or "{}")),
            "parameter_schema": json.loads(str(row["parameter_schema_json"] or "{}")),
            "output_contract": json.loads(str(row["output_contract_json"] or "{}")),
            "resource_policy": json.loads(str(row["resource_policy_json"] or "{}")),
            "worker_policy": row["worker_policy"],
        }
        return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()

    def get_version(self, profile_version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT v.*, p.code, p.title FROM execution_profile_versions v
                JOIN execution_profiles p ON p.id=v.execution_profile_id WHERE v.id=?""",
                (profile_version_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
            validation = connection.execute(
                """SELECT id, contract_hash, status, checks_json, created_at FROM profile_validation_attestations
                WHERE profile_version_id=? ORDER BY created_at DESC LIMIT 1""",
                (profile_version_id,),
            ).fetchone()
        payload = self._contract_payload(row)
        return {
            "id": str(row["id"]),
            "execution_profile_id": str(row["execution_profile_id"]),
            "code": str(row["code"]),
            "title": str(row["title"]),
            "version_no": int(row["version_no"]),
            "capability": str(row["capability"]),
            "status": str(row["status"]),
            "revision": int(row["revision"]),
            **payload,
            "contract_hash": self._contract_hash(payload),
            "validation": ({**dict(validation), "checks": json.loads(str(validation["checks_json"]))} if validation else None),
        }

    def derive_contract_version(
        self,
        source_version_id: str,
        expected_source_revision: int,
        input_contract: dict[str, Any],
        parameter_schema: dict[str, Any],
        output_contract: dict[str, Any],
        resource_policy: dict[str, Any],
        actor: str = "local-user",
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            source = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (source_version_id,)).fetchone()
            if source is None:
                raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
            if int(source["revision"]) != expected_source_revision:
                raise DomainRuleError(
                    "PROFILE_VERSION_STALE",
                    "Profile 编辑基于旧 revision",
                    {"expected": expected_source_revision, "actual": int(source["revision"])},
                )
            next_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version_no),0)+1 AS n FROM execution_profile_versions WHERE execution_profile_id=?",
                    (source["execution_profile_id"],),
                ).fetchone()["n"]
            )
            version_id = str(uuid.uuid4())
            connection.execute(
                """INSERT INTO execution_profile_versions
                (id, execution_profile_id, version_no, capability, runtime_version_id, workflow_version_id,
                model_bundle_json, input_contract_json, parameter_schema_json, output_contract_json,
                resource_policy_json, status, manifest_sha256, capability_json, worker_policy,
                created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                (
                    version_id,
                    source["execution_profile_id"],
                    next_no,
                    source["capability"],
                    source["runtime_version_id"],
                    source["workflow_version_id"],
                    source["model_bundle_json"],
                    _json(input_contract),
                    _json(parameter_schema),
                    _json(output_contract),
                    _json(resource_policy),
                    source["manifest_sha256"],
                    source["capability_json"],
                    source["worker_policy"],
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'operator', 'PROFILE_CONTRACT_VERSION_DERIVED', 'execution_profile_version', ?, ?, ?)""",
                (actor, version_id, "派生不可变 Profile 契约候选", _json({"source_version_id": source_version_id})),
            )
        return self.get_version(version_id)

    def validate_contract_version(self, profile_version_id: str, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
        if row["status"] != "DRAFT":
            raise DomainRuleError("PROFILE_VALIDATION_REQUIRES_DRAFT", "只有 DRAFT ProfileVersion 可生成契约验证证明")
        payload = self._contract_payload(row)
        checks: list[dict[str, Any]] = []
        required = {
            "input_contract": "输入契约",
            "parameter_schema": "参数 Schema",
            "output_contract": "输出契约",
            "resource_policy": "资源策略",
        }
        for key, label in required.items():
            value = payload[key]
            checks.append({"code": key.upper(), "passed": isinstance(value, dict) and bool(value), "label": label})
        transport = str(payload["input_contract"].get("transport", ""))
        checks.append({"code": "LOCAL_TRANSPORT", "passed": transport in {"LOOPBACK_HTTP", "LOCAL_PROCESS", "LOCAL_CLI"}})
        seed = payload["parameter_schema"].get("seed")
        checks.append({"code": "SEED_DETERMINISM", "passed": isinstance(seed, dict) and bool(seed.get("determinism"))})
        checks.append({"code": "OUTPUT_MEDIA_KIND", "passed": bool(payload["output_contract"].get("media_kind"))})
        checks.append({"code": "GPU_CONCURRENCY", "passed": payload["resource_policy"].get("gpu_heavy_concurrency") == 1})
        failed = [item["code"] for item in checks if not item["passed"]]
        status = "PASS" if not failed else "FAIL"
        attestation_id = str(uuid.uuid4())
        contract_hash = self._contract_hash(payload)
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO profile_validation_attestations
                (id, profile_version_id, contract_hash, status, checks_json, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (attestation_id, profile_version_id, contract_hash, status, _json(checks), now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'operator', 'PROFILE_CONTRACT_VALIDATED', 'execution_profile_version', ?, ?, ?)""",
                (actor, profile_version_id, f"Profile 本地契约验证 {status}", _json({"attestation_id": attestation_id, "failed": failed})),
            )
        return {
            "id": attestation_id,
            "profile_version_id": profile_version_id,
            "contract_hash": contract_hash,
            "status": status,
            "checks": checks,
            "runtime_contacted": False,
            "network_contacted": False,
        }

    def publish_validated_contract(self, profile_version_id: str, actor: str = "local-user") -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            draft = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
            if draft is None:
                raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
            if draft["status"] != "DRAFT":
                raise DomainRuleError("PROFILE_PUBLISH_REQUIRES_DRAFT", "只有 DRAFT ProfileVersion 可由契约发布")
            validation = connection.execute(
                """SELECT * FROM profile_validation_attestations WHERE profile_version_id=?
                ORDER BY created_at DESC LIMIT 1""",
                (profile_version_id,),
            ).fetchone()
            payload = self._contract_payload(draft)
            contract_hash = self._contract_hash(payload)
            if validation is None or validation["status"] != "PASS" or validation["contract_hash"] != contract_hash:
                raise DomainRuleError("PROFILE_VALIDATION_REQUIRED", "发布需要与当前契约 hash 匹配的 PASS 验证证明")
            prior = connection.execute(
                """SELECT * FROM execution_profile_versions WHERE execution_profile_id=? AND status='PUBLISHED'
                AND version_no<? ORDER BY version_no DESC LIMIT 1""",
                (draft["execution_profile_id"], draft["version_no"]),
            ).fetchone()
            if prior is None or self._execution_fingerprint(prior) != self._execution_fingerprint(draft):
                raise DomainRuleError(
                    "PROFILE_REAL_EVIDENCE_REQUIRED",
                    "执行指纹已变化；必须通过真实成功媒体证据发布，契约校验不能冒充模型实跑",
                )
            connection.execute(
                "UPDATE execution_profile_versions SET status='PUBLISHED', updated_at=?, revision=revision+1 WHERE id=?",
                (now, profile_version_id),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'operator', 'PROFILE_CONTRACT_VERSION_PUBLISHED', 'execution_profile_version', ?, ?, ?)""",
                (actor, profile_version_id, "发布执行指纹未变化的已验证 Profile 契约版本", _json({"validation_id": validation["id"]})),
            )
        return self.get_version(profile_version_id)

    def get_manifest(self) -> dict[str, Any]:
        return self.load().as_public_dict()

    def publish_from_evidence(
        self,
        candidate_version_id: str,
        media_version_id: str,
        workflow_version_id: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            candidate = connection.execute(
                "SELECT * FROM execution_profile_versions WHERE id=?", (candidate_version_id,)
            ).fetchone()
            if candidate is None:
                raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
            if candidate["capability"] not in {"T2V", "I2V"}:
                raise DomainRuleError("PROFILE_EVIDENCE_CAPABILITY_MISMATCH", "真实证据发布只支持已验证的 T2V 或 I2V capability")
            contract_validation = None
            if candidate["status"] == "DRAFT":
                contract_payload = self._contract_payload(candidate)
                contract_validation = connection.execute(
                    """SELECT * FROM profile_validation_attestations WHERE profile_version_id=?
                    ORDER BY created_at DESC LIMIT 1""",
                    (candidate_version_id,),
                ).fetchone()
                if (
                    contract_validation is None
                    or contract_validation["status"] != "PASS"
                    or contract_validation["contract_hash"] != self._contract_hash(contract_payload)
                ):
                    raise DomainRuleError(
                        "PROFILE_VALIDATION_REQUIRED",
                        "DRAFT Profile 真实证据发布需要匹配当前 contract hash 的 PASS 验证证明",
                    )
            workflow = connection.execute(
                "SELECT * FROM workflow_versions WHERE id=?", (workflow_version_id,)
            ).fetchone()
            if workflow is None or workflow["status"] != "PUBLISHED":
                raise DomainRuleError("WORKFLOW_NOT_PUBLISHED", "Profile 发布证据必须引用 PUBLISHED workflow")
            workflow_contract = json.loads(str(workflow["contract_json"]))
            workflow_capability = str(workflow_contract.get("capability", ""))
            workflow_input_slots = workflow_contract.get("input_slots", {})
            if not isinstance(workflow_input_slots, dict):
                raise DomainRuleError("WORKFLOW_INPUT_CONTRACT_INVALID", "Workflow input_slots 契约无效")
            expected_workflow_capability = {
                "T2V": "H3_T2VA_CANDIDATE",
                "I2V": "H3_FL2VA_I2V_CANDIDATE",
            }[str(candidate["capability"])]
            if workflow_capability != expected_workflow_capability:
                raise DomainRuleError(
                    "PROFILE_EVIDENCE_CAPABILITY_MISMATCH",
                    "Workflow capability 与待发布 Profile capability 不一致",
                    {"expected": expected_workflow_capability, "actual": workflow_capability},
                )
            evidence = connection.execute(
                """SELECT mv.id AS media_version_id, mv.integrity_status, mv.source_artifact_id,
                mv.source_job_attempt_id, ma.media_kind, ma.project_id, a.status AS artifact_status,
                ja.state AS attempt_state, j.state AS job_state, j.subject_type, j.subject_id,
                j.input_snapshot_json
                FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                JOIN artifacts a ON a.id=mv.source_artifact_id
                JOIN job_attempts ja ON ja.id=mv.source_job_attempt_id AND ja.id=a.job_attempt_id
                JOIN jobs j ON j.id=ja.job_id WHERE mv.id=?""",
                (media_version_id,),
            ).fetchone()
            if evidence is None:
                raise DomainRuleError("PROFILE_EVIDENCE_LINEAGE_REQUIRED", "Profile 发布需要完整 Artifact/Attempt/Job 媒体谱系")
            snapshot = json.loads(str(evidence["input_snapshot_json"]))
            valid = (
                evidence["integrity_status"] == "VERIFIED"
                and evidence["artifact_status"] == "VERIFIED"
                and evidence["attempt_state"] == "SUCCEEDED"
                and evidence["job_state"] == "SUCCEEDED"
                and evidence["media_kind"] == "VIDEO"
                and snapshot.get("workflow_version_id") == workflow_version_id
            )
            if not valid:
                raise DomainRuleError("PROFILE_EVIDENCE_INVALID", "媒体或执行谱系不足以发布 ProfileVersion")
            if candidate["capability"] == "I2V":
                first_frames = [
                    str(item.get("media_version_id"))
                    for item in snapshot.get("media_bindings", [])
                    if isinstance(item, dict) and item.get("role") == "FIRST_FRAME" and item.get("media_version_id")
                ]
                if len(first_frames) != 1:
                    raise DomainRuleError(
                        "PROFILE_EVIDENCE_APPROVED_FIRST_FRAME_REQUIRED",
                        "I2V Profile 发布证据必须绑定且仅绑定一个 FIRST_FRAME",
                    )
                approved = connection.execute(
                    """SELECT mv.id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                    WHERE mv.id=? AND ma.project_id=? AND ma.media_kind='IMAGE' AND ma.purpose='KEYFRAME'
                    AND ma.approved_version_id=mv.id AND mv.stage='KEYFRAME' AND mv.integrity_status='VERIFIED'""",
                    (first_frames[0], evidence["project_id"]),
                ).fetchone()
                if approved is None:
                    raise DomainRuleError(
                        "PROFILE_EVIDENCE_APPROVED_FIRST_FRAME_REQUIRED",
                        "I2V Profile 发布证据的 FIRST_FRAME 必须是同项目已批准关键帧",
                    )
            existing = connection.execute(
                """SELECT * FROM execution_profile_versions WHERE execution_profile_id=?
                AND workflow_version_id=? AND status='PUBLISHED' ORDER BY version_no DESC LIMIT 1""",
                (candidate["execution_profile_id"], workflow_version_id),
            ).fetchone()
            if existing is not None:
                existing_input_contract = json.loads(str(existing["input_contract_json"] or "{}"))
                if existing_input_contract.get("input_slots") == workflow_input_slots:
                    return dict(existing)
            next_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(version_no),0)+1 AS n FROM execution_profile_versions WHERE execution_profile_id=?",
                    (candidate["execution_profile_id"],),
                ).fetchone()["n"]
            )
            version_id = str(uuid.uuid4())
            contract = json.loads(str(candidate["capability_json"]))
            manifest_capability = dict(contract.get("manifest_capability", {}))
            manifest_capability.update(
                {
                    "status": "playable_success_verified",
                    "playable_success_verified_in_this_run": True,
                    "blocking_evidence": None,
                }
            )
            contract.update(
                {
                    "published": True,
                    "playable_success_verified_in_this_run": True,
                    "evidence_media_version_id": media_version_id,
                    "evidence_artifact_id": evidence["source_artifact_id"],
                    "evidence_job_attempt_id": evidence["source_job_attempt_id"],
                    "workflow_version_id": workflow_version_id,
                    "contract_validation_attestation_id": str(contract_validation["id"]) if contract_validation else None,
                    "manifest_capability": manifest_capability,
                }
            )
            input_contract = json.loads(str(candidate["input_contract_json"] or "{}"))
            input_contract["input_slots"] = workflow_input_slots
            input_contract["transport"] = "LOOPBACK_HTTP"
            connection.execute(
                """INSERT INTO execution_profile_versions
                (id, execution_profile_id, version_no, capability, runtime_version_id, workflow_version_id,
                model_bundle_json, input_contract_json, parameter_schema_json, output_contract_json,
                resource_policy_json, status, manifest_sha256,
                capability_json, worker_policy, created_at, updated_at, created_by, revision, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PUBLISHED', ?, ?, ?, ?, ?, ?, 1, 'v2')""",
                (
                    version_id,
                    candidate["execution_profile_id"],
                    next_no,
                    candidate["capability"],
                    candidate["runtime_version_id"],
                    workflow_version_id,
                    candidate["model_bundle_json"],
                    _json(input_contract),
                    candidate["parameter_schema_json"],
                    candidate["output_contract_json"],
                    candidate["resource_policy_json"],
                    candidate["manifest_sha256"],
                    _json(contract),
                    candidate["worker_policy"],
                    now,
                    now,
                    actor,
                ),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'operator', 'PROFILE_VERSION_PUBLISHED_FROM_EVIDENCE', 'execution_profile_version', ?, ?, ?)""",
                (
                    actor,
                    version_id,
                    "使用真实成功媒体谱系发布 ProfileVersion",
                    _json({"candidate_version_id": candidate_version_id, "media_version_id": media_version_id, "workflow_version_id": workflow_version_id}),
                ),
            )
        with self.database.connect() as connection:
            published = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (version_id,)).fetchone()
        return dict(published)
