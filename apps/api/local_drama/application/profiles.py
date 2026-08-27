"""Manifest-backed local runtime and capability profile registry."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from local_drama.application.override_schema import default_override_schema
from local_drama.domain.capabilities import normalize_capability
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation_contracts import resolve_camera_plan
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


_EXECUTION_COMPONENT_ROLES = {
    "PRIMARY_MODEL",
    "TEXT_ENCODER",
    "VIDEO_VAE",
    "AUDIO_VAE",
    "LORA",
    "CONTROLNET",
    "UPSCALER",
    "TTS_ENGINE",
    "VOICE_MODEL",
}


def _parse_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if value is None:
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _artifact_role(kind: Any, code: Any) -> str:
    """Map manifest/model-artifact labels to the stable UI execution roles."""

    raw = f"{kind or ''} {code or ''}".upper().replace("-", "_").replace(" ", "_")
    if raw in _EXECUTION_COMPONENT_ROLES:
        return raw
    if "TEXT_ENCODER" in raw or "TEXTENCODER" in raw or "CLIP" in raw:
        return "TEXT_ENCODER"
    if "AUDIO_VAE" in raw or "AUDIOVAE" in raw:
        return "AUDIO_VAE"
    if "VIDEO_VAE" in raw or "VIDEOVAE" in raw or raw.endswith("_VAE"):
        return "VIDEO_VAE"
    if "LORA" in raw:
        return "LORA"
    if "CONTROLNET" in raw:
        return "CONTROLNET"
    if "UPSCAL" in raw:
        return "UPSCALER"
    if "TTS" in raw:
        return "TTS_ENGINE"
    if "VOICE" in raw:
        return "VOICE_MODEL"
    if "UNET" in raw or "DIT" in raw or "CHECKPOINT" in raw or "MODEL" in raw:
        return "PRIMARY_MODEL"
    return str(kind or code or "UNKNOWN").upper()


# Profile contracts are user-editable JSON, but they are not a secret store or
# a second runtime configuration channel.  Keep remote-provider credentials
# and egress controls out of the persisted contract/API surface.  This is a
# recursive check because input slots and parameter schemas may nest arbitrary
# JSON objects and arrays.
_FORBIDDEN_LOCAL_CONFIG_KEY_PARTS = (
    "api_key",
    "apikey",
    "secret_key",
    "secretkey",
    "client_secret",
    "clientsecret",
    "credential",
    "password",
    "authorization",
    "bearer",
    "provider_url",
    "providerurl",
    "remote_url",
    "remoteurl",
    "remote_endpoint",
    "remoteendpoint",
    "endpoint_url",
    "endpointurl",
)


def _validate_local_contract_config(value: Any, *, path: str) -> None:
    """Reject secret/remote configuration fields before profile persistence.

    A profile may still describe a local transport and user-selected model
    references, but API keys, credential values and remote endpoint controls
    are deliberately not supported in the LOCAL_ONLY release.  The error
    reports only the JSON field path; it never echoes the supplied value.
    """

    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            normalized = re.sub(r"[^a-z0-9]+", "", key_text.casefold())
            if any(part in key_text.casefold() or re.sub(r"[^a-z0-9]+", "", part) in normalized for part in _FORBIDDEN_LOCAL_CONFIG_KEY_PARTS):
                raise DomainRuleError(
                    "LOCAL_CONFIG_FIELD_FORBIDDEN",
                    "LOCAL_ONLY Profile 契约不得包含远程凭据或远程 endpoint 字段",
                    {"field_path": f"{path}.{key_text}"},
                )
            _validate_local_contract_config(child, path=f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_local_contract_config(child, path=f"{path}[{index}]")


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
                try:
                    canonical_capability = normalize_capability(str(capability))
                except ValueError as error:
                    raise DomainRuleError(
                        "PROFILE_CAPABILITY_INVALID",
                        "模型 manifest 包含未知或含义不唯一的 capability",
                        {"manifest_capability": str(capability)},
                    ) from error
                route_status = manifest.route_status.get(f"native_{str(capability).lower()}", capability_data.get("status", "UNKNOWN"))
                profile_code = _code(f"h3-native-{capability}")
                profile_id = _stable_id(f"profile:{profile_code}")
                profile_status = "CANDIDATE_BLOCKED" if runtime_status != "AVAILABLE" else "CANDIDATE_UNVERIFIED"
                capability_contract = {
                    "capability": canonical_capability,
                    "manifest_capability_alias": str(capability),
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
                            canonical_capability,
                            _json({
                                "schema_version": "localdrama.execution-profile-bundle.v1",
                                "manifest_sha256": manifest.sha256,
                                "runtime_id": runtime_id,
                                "artifact_ids": artifact_ids,
                                "route_status": route_status,
                                "override_schema": default_override_schema(canonical_capability),
                            }),
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
                        "capability": canonical_capability,
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
                v.output_contract_json, v.resource_policy_json, v.revision,
                v.model_bundle_json, v.parameter_schema_json,
                w.contract_json AS workflow_contract_json
                FROM execution_profiles p JOIN execution_profile_versions v
                ON v.execution_profile_id = p.id
                LEFT JOIN workflow_versions w ON w.id = v.workflow_version_id
                ORDER BY p.code, v.version_no DESC"""
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["capability_contract"] = json.loads(item.pop("capability_json"))
            item["model_bundle"] = _parse_json(item.pop("model_bundle_json"), {})
            parameter_schema = _parse_json(item.pop("parameter_schema_json"), {})
            item["override_schema"] = item["model_bundle"].get("override_schema") if isinstance(item["model_bundle"], dict) else None
            if not isinstance(item["override_schema"], dict):
                item["override_schema"] = parameter_schema.get("override_schema") if isinstance(parameter_schema, dict) else None
            if not isinstance(item["override_schema"], dict):
                item["override_schema"] = default_override_schema(str(item["capability"]))
            workflow_contract = json.loads(str(item.pop("workflow_contract_json") or "{}"))
            item["workflow_tier"] = str(workflow_contract.get("production_tier") or "").strip().upper() or None
            item["dynamic_production_tiers"] = workflow_contract.get("dynamic_production_tiers") is True
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

    def _execution_detail(self, connection: Any, row: Any) -> dict[str, Any]:
        """Build the local, read-only execution view for a Profile version.

        Older manifest syncs only persisted ``artifact_ids``.  Newer bundles
        may carry component roles, but the API normalizes both shapes so the
        UI never has to infer model files from a raw JSON blob.
        """

        bundle = _parse_json(row["model_bundle_json"], {})
        if not isinstance(bundle, dict):
            bundle = {}
        parameter_schema = _parse_json(row["parameter_schema_json"], {})
        if not isinstance(parameter_schema, dict):
            parameter_schema = {}

        runtime_id = str(row["runtime_version_id"] or bundle.get("runtime_id") or "").strip()
        runtime_payload: dict[str, Any] | None = None
        if runtime_id:
            runtime_row = connection.execute(
                """SELECT id, code, title, transport, base_url, executable_ref,
                runtime_version, status, details_json, revision FROM local_runtimes WHERE id=?""",
                (runtime_id,),
            ).fetchone()
            if runtime_row is not None:
                runtime_payload = {
                    "id": str(runtime_row["id"]),
                    "code": str(runtime_row["code"]),
                    "title": str(runtime_row["title"]),
                    "transport": str(runtime_row["transport"]),
                    "base_url": runtime_row["base_url"],
                    "executable_ref": runtime_row["executable_ref"],
                    "version": runtime_row["runtime_version"],
                    "status": str(runtime_row["status"]),
                    "details": _parse_json(runtime_row["details_json"], {}),
                    "revision": int(runtime_row["revision"]),
                }
        if runtime_payload is None and runtime_id:
            runtime_payload = {"id": runtime_id, "status": "UNKNOWN"}

        workflow_payload: dict[str, Any] | None = None
        workflow_id = str(row["workflow_version_id"] or bundle.get("workflow_version_id") or "").strip()
        if workflow_id:
            workflow_row = connection.execute(
                """SELECT wv.id, wv.workflow_id, w.code, w.title, wv.version_no,
                wv.content_hash, wv.status, wv.contract_json, wv.revision
                FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id
                WHERE wv.id=?""",
                (workflow_id,),
            ).fetchone()
            if workflow_row is not None:
                workflow_payload = {
                    "id": str(workflow_row["id"]),
                    "workflow_id": str(workflow_row["workflow_id"]),
                    "code": str(workflow_row["code"]),
                    "title": str(workflow_row["title"]),
                    "version_no": int(workflow_row["version_no"]),
                    "content_hash": str(workflow_row["content_hash"]),
                    "status": str(workflow_row["status"]),
                    "contract": _parse_json(workflow_row["contract_json"], {}),
                    "revision": int(workflow_row["revision"]),
                }
        if workflow_payload is None and workflow_id:
            workflow_payload = {"id": workflow_id, "status": "UNKNOWN"}

        raw_components = bundle.get("components")
        component_specs: list[dict[str, Any]] = []
        if isinstance(raw_components, list):
            component_specs = [dict(item) for item in raw_components if isinstance(item, dict)]
        if not component_specs:
            artifact_ids = bundle.get("artifact_ids")
            if isinstance(artifact_ids, list):
                component_specs = [{"artifact_id": str(item)} for item in artifact_ids if str(item).strip()]

        artifact_ids = [str(item.get("artifact_id")) for item in component_specs if str(item.get("artifact_id") or "").strip()]
        artifact_by_id: dict[str, Any] = {}
        if artifact_ids:
            placeholders = ",".join("?" for _ in artifact_ids)
            artifact_rows = connection.execute(
                f"""SELECT id, runtime_id, code, kind, machine_path_ref, sha256, size_bytes,
                compatibility_json, status, manifest_sha256, revision FROM model_artifacts
                WHERE id IN ({placeholders})""",
                artifact_ids,
            ).fetchall()
            artifact_by_id = {str(item["id"]): item for item in artifact_rows}

        components: list[dict[str, Any]] = []
        for spec in component_specs:
            artifact_id = str(spec.get("artifact_id") or "").strip()
            artifact = artifact_by_id.get(artifact_id)
            if artifact is None:
                components.append(
                    {
                        "artifact_id": artifact_id or None,
                        "role": str(spec.get("role") or "UNKNOWN").upper(),
                        "purpose": spec.get("purpose"),
                        "title": str(spec.get("title") or artifact_id or "缺失模型组件"),
                        "status": "MISSING",
                        "required": bool(spec.get("required", True)),
                    }
                )
                continue
            role = str(spec.get("role") or _artifact_role(artifact["kind"], artifact["code"])).upper()
            compatibility = _parse_json(artifact["compatibility_json"], {})
            components.append(
                {
                    "artifact_id": str(artifact["id"]),
                    "role": role,
                    "purpose": spec.get("purpose"),
                    "title": str(spec.get("title") or artifact["code"]),
                    "code": str(artifact["code"]),
                    "kind": str(artifact["kind"]),
                    "status": str(artifact["status"]),
                    "available": str(artifact["status"]).upper() in {"AVAILABLE", "VERIFIED", "READY", "PUBLISHED"},
                    "required": bool(spec.get("required", True)),
                    "machine_path": str(artifact["machine_path_ref"]),
                    "sha256": artifact["sha256"],
                    "size_bytes": artifact["size_bytes"],
                    "manifest_sha256": artifact["manifest_sha256"],
                    "compatibility": compatibility if isinstance(compatibility, dict) else {},
                    "revision": int(artifact["revision"]),
                }
            )

        remote_model = bundle.get("model") or bundle.get("model_ref")
        if remote_model and not components:
            components.append(
                {
                    "artifact_id": None,
                    "role": "REMOTE_MODEL",
                    "title": str(remote_model),
                    "model": str(remote_model),
                    "status": "CONFIGURED",
                    "required": True,
                }
            )

        provider_connection: dict[str, Any] | None = None
        provider_connection_id = str(bundle.get("provider_connection_id") or "").strip()
        if provider_connection_id:
            try:
                provider_row = connection.execute(
                    "SELECT id, title, provider_kind, protocol, base_url, model, status, revision FROM provider_connections WHERE id=?",
                    (provider_connection_id,),
                ).fetchone()
            except sqlite3.OperationalError:
                provider_row = None
            if provider_row is not None:
                provider_connection = {
                    "id": str(provider_row["id"]),
                    "title": str(provider_row["title"]),
                    "provider_kind": str(provider_row["provider_kind"]),
                    "protocol": str(provider_row["protocol"]),
                    "base_url": str(provider_row["base_url"]),
                    "model": provider_row["model"],
                    "status": str(provider_row["status"]),
                    "revision": int(provider_row["revision"]),
                }

        defaults = bundle.get("defaults")
        if not isinstance(defaults, dict):
            defaults = parameter_schema.get("defaults") if isinstance(parameter_schema.get("defaults"), dict) else {}
        override_schema = bundle.get("override_schema")
        if not isinstance(override_schema, dict):
            override_schema = parameter_schema.get("override_schema")
        if not isinstance(override_schema, dict):
            override_schema = default_override_schema(str(row["capability"] or ""))

        worker_policy = _parse_json(row["worker_policy"], None)
        if worker_policy is None:
            worker_policy = row["worker_policy"]
        execution_fingerprint = self._execution_fingerprint(row)
        model_bundle_fingerprint = hashlib.sha256(_json(bundle).encode("utf-8")).hexdigest()
        workflow_fingerprint = workflow_payload.get("content_hash") if workflow_payload else None
        return {
            "schema_version": "localdrama.profile-execution-detail.v1",
            "runtime": runtime_payload,
            "workflow": workflow_payload,
            "components": components,
            "provider_connection_id": bundle.get("provider_connection_id"),
            "provider_connection": provider_connection,
            "provider": bundle.get("provider"),
            "model": bundle.get("model"),
            "defaults": defaults,
            "override_schema": override_schema,
            "worker_policy": worker_policy,
            "model_bundle": bundle,
            "fingerprints": {
                "execution": execution_fingerprint,
                "model_bundle": f"sha256:{model_bundle_fingerprint}",
                "workflow": f"sha256:{workflow_fingerprint}" if workflow_fingerprint else None,
                "manifest": row["manifest_sha256"],
            },
            "read_only": True,
            "local_only": True,
        }

    def get_version(self, profile_version_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT v.*, p.code, p.title FROM execution_profile_versions v
                JOIN execution_profiles p ON p.id=v.execution_profile_id WHERE v.id=?""",
                (profile_version_id,),
            ).fetchone()
            if row is None:
                raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
            execution = self._execution_detail(connection, row)
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
            "model_bundle": _parse_json(row["model_bundle_json"], {}),
            **payload,
            "capability_contract": json.loads(str(row["capability_json"] or "{}")),
            "contract_hash": self._contract_hash(payload),
            "execution": execution,
            "validation": ({**dict(validation), "checks": json.loads(str(validation["checks_json"]))} if validation else None),
        }

    def resolve_camera_plan(
        self,
        profile_version_id: str,
        *,
        shot_type: str,
        movement: str,
        direction: str,
        intensity: float,
        curve: str,
        prompt_text: str = "",
    ) -> dict[str, Any]:
        profile = self.get_version(profile_version_id)
        if profile["status"] != "PUBLISHED":
            raise DomainRuleError("PROFILE_NOT_PUBLISHED", "只有已发布 Profile 才能裁决正式 CameraPlan")
        parameter_schema = profile["parameter_schema"]
        capabilities = parameter_schema.get("capabilities", {}) if isinstance(parameter_schema, dict) else {}
        camera = capabilities.get("camera", {}) if isinstance(capabilities, dict) else {}
        support = str(camera.get("support", "UNSUPPORTED")) if isinstance(camera, dict) else "UNSUPPORTED"
        if support not in {"NATIVE", "PROMPT_FALLBACK", "UNSUPPORTED"}:
            raise DomainRuleError("PROFILE_CAMERA_CONTRACT_INVALID", "Profile camera capability support 无效")
        fallback = support == "PROMPT_FALLBACK" and camera.get("prompt_fallback") is True
        if support == "PROMPT_FALLBACK" and not fallback:
            raise DomainRuleError("PROFILE_CAMERA_FALLBACK_INVALID", "Camera prompt fallback 必须由 Profile 显式声明")
        plan = resolve_camera_plan(
            native_supported=support == "NATIVE",
            prompt_fallback_supported=fallback,
            shot_type=shot_type,
            movement=movement,
            prompt_text=prompt_text,
            direction=direction,
            intensity=intensity,
            curve=curve,
            profile_version_id=profile_version_id,
        )
        return {
            "camera_plan": plan.to_dict(),
            "submission_allowed": plan.mode != "UNSUPPORTED",
            "support": support,
            "profile": {"id": profile_version_id, "code": profile["code"], "version_no": profile["version_no"]},
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
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
        _validate_local_contract_config(input_contract, path="input_contract")
        _validate_local_contract_config(parameter_schema, path="parameter_schema")
        _validate_local_contract_config(output_contract, path="output_contract")
        _validate_local_contract_config(resource_policy, path="resource_policy")
        now = _utc_now()
        with self.database.transaction() as connection:
            source = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (source_version_id,)).fetchone()
            if source is None:
                raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
            try:
                source_capability = normalize_capability(str(source["capability"]))
            except ValueError as error:
                raise DomainRuleError(
                    "PROFILE_CAPABILITY_INVALID",
                    "源 Profile capability 未知或含义不唯一",
                    {"profile_version_id": source_version_id},
                ) from error
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
                    source_capability,
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

    @staticmethod
    def _compatibility_checks(row: Any) -> list[dict[str, Any]]:
        input_contract = json.loads(str(row["input_contract_json"] or "{}"))
        parameter_schema = json.loads(str(row["parameter_schema_json"] or "{}"))
        output_contract = json.loads(str(row["output_contract_json"] or "{}"))
        resource_policy = json.loads(str(row["resource_policy_json"] or "{}"))
        checks: list[dict[str, Any]] = []
        transport = str(input_contract.get("transport", ""))
        checks.append({"code": "LOCAL_TRANSPORT", "passed": transport in {"LOOPBACK_HTTP", "LOCAL_PROCESS", "LOCAL_CLI"}})
        slots = input_contract.get("input_slots", {})
        slots_ok = isinstance(slots, dict) and all(
            isinstance(spec, dict) and isinstance(spec.get("min", 0), int) and isinstance(spec.get("max", 0), int)
            and 0 <= int(spec["min"]) <= int(spec["max"])
            for spec in slots.values()
        )
        checks.append({"code": "INPUT_SLOT_BOUNDS", "passed": slots_ok})
        seed = parameter_schema.get("seed")
        checks.append({"code": "SEED_DETERMINISM", "passed": isinstance(seed, dict) and str(seed.get("determinism", "")) in {"EXPLICIT", "BEST_EFFORT", "NONDETERMINISTIC", "profile_declared"}})
        checks.append({"code": "OUTPUT_MEDIA_KIND", "passed": str(output_contract.get("media_kind", "")) in {"IMAGE", "VIDEO", "AUDIO", "DOCUMENT"}})
        checks.append({"code": "GPU_CONCURRENCY", "passed": resource_policy.get("gpu_heavy_concurrency") == 1})
        matrix = parameter_schema.get("capabilities")
        matrix_ok = isinstance(matrix, dict)
        for name in ("extend", "V2V", "reference", "motion"):
            item = matrix.get(name) if isinstance(matrix, dict) else None
            support = item.get("support") if isinstance(item, dict) else None
            valid_support = support in {"NATIVE", "PROMPT_FALLBACK", "UNSUPPORTED"}
            required_inputs = item.get("required_inputs", []) if isinstance(item, dict) else []
            inputs_ok = isinstance(required_inputs, list) and all(str(slot) in slots for slot in required_inputs)
            fallback_ok = support != "PROMPT_FALLBACK" or (isinstance(item, dict) and item.get("prompt_fallback") is True)
            checks.append({"code": f"CAPABILITY_{name.upper()}", "passed": bool(matrix_ok and valid_support and inputs_ok and fallback_ok)})
        if str(row["capability"]).startswith("VIDEO_"):
            camera = matrix.get("camera") if isinstance(matrix, dict) else None
            camera_support = camera.get("support") if isinstance(camera, dict) else None
            camera_inputs = camera.get("required_inputs", []) if isinstance(camera, dict) else []
            camera_inputs_ok = isinstance(camera_inputs, list) and all(str(slot) in slots for slot in camera_inputs)
            camera_fallback_ok = camera_support != "PROMPT_FALLBACK" or (
                isinstance(camera, dict) and camera.get("prompt_fallback") is True
            )
            checks.append({
                "code": "CAPABILITY_CAMERA",
                "passed": bool(
                    matrix_ok
                    and camera_support in {"NATIVE", "PROMPT_FALLBACK", "UNSUPPORTED"}
                    and camera_inputs_ok
                    and camera_fallback_ok
                ),
            })
        return checks

    def validate_compatibility(self, profile_version_id: str, actor: str = "local-user") -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
        if row is None:
            raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
        payload = self._contract_payload(row)
        contract_hash = self._contract_hash(payload)
        checks = self._compatibility_checks(row)
        status = "PASS" if all(item["passed"] for item in checks) else "FAIL"
        attestation_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO profile_compatibility_attestations
                (id, profile_version_id, contract_hash, status, checks_json, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (attestation_id, profile_version_id, contract_hash, status, _json(checks), now, actor),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'operator', 'PROFILE_COMPATIBILITY_VALIDATED', 'execution_profile_version', ?, ?, ?)""",
                (actor, profile_version_id, f"Profile capability compatibility {status}", _json({"attestation_id": attestation_id})),
            )
        return {"id": attestation_id, "profile_version_id": profile_version_id, "contract_hash": contract_hash, "status": status, "checks": checks, "runtime_contacted": False, "network_contacted": False}

    def retire_version(self, profile_version_id: str, actor: str = "local-user") -> dict[str, Any]:
        now = _utc_now()
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
            if row is None:
                raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
            if row["status"] != "PUBLISHED":
                raise DomainRuleError("PROFILE_RETIRE_REQUIRES_PUBLISHED", "只有 PUBLISHED ProfileVersion 可退休")
            if connection.execute("SELECT 1 FROM project_profile_bindings WHERE execution_profile_version_id=? AND status='ACTIVE'", (profile_version_id,)).fetchone():
                raise DomainRuleError("PROFILE_VERSION_IN_USE", "项目仍绑定该 ProfileVersion，不能退休")
            if connection.execute("SELECT 1 FROM jobs WHERE execution_profile_version_id=?", (profile_version_id,)).fetchone():
                raise DomainRuleError("PROFILE_VERSION_HAS_JOBS", "已有 Job 快照引用该 ProfileVersion，不能退休")
            connection.execute("UPDATE execution_profile_versions SET status='RETIRED', updated_at=?, revision=revision+1 WHERE id=?", (now, profile_version_id))
            connection.execute(
                """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES (?, 'operator', 'PROFILE_VERSION_RETIRED', 'execution_profile_version', ?, '退休未绑定的重复 ProfileVersion', ?)""",
                (actor, profile_version_id, _json({"prior_status": "PUBLISHED"})),
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
            try:
                candidate_capability = normalize_capability(str(candidate["capability"]))
            except ValueError as error:
                raise DomainRuleError(
                    "PROFILE_CAPABILITY_INVALID",
                    "待发布 Profile capability 未知或含义不唯一",
                    {"profile_version_id": candidate_version_id},
                ) from error
            image_capability = candidate_capability.startswith("IMAGE_")
            if candidate_capability not in {"VIDEO_T2V", "VIDEO_I2V"} and not image_capability:
                raise DomainRuleError(
                    "PROFILE_EVIDENCE_CAPABILITY_MISMATCH",
                    "真实证据发布只支持已验证的 T2V、I2V 或 IMAGE_* capability",
                )
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
                contract_compatibility = connection.execute(
                    """SELECT * FROM profile_compatibility_attestations WHERE profile_version_id=?
                    ORDER BY created_at DESC LIMIT 1""",
                    (candidate_version_id,),
                ).fetchone()
                if (
                    contract_compatibility is None
                    or contract_compatibility["status"] != "PASS"
                    or contract_compatibility["contract_hash"] != self._contract_hash(contract_payload)
                ):
                    raise DomainRuleError(
                        "PROFILE_COMPATIBILITY_REQUIRED",
                        "DRAFT Profile 真实证据发布需要匹配当前 contract hash 的 PASS capability compatibility 证明",
                    )
            else:
                contract_compatibility = None
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
                "VIDEO_T2V": "H3_T2VA_CANDIDATE",
                "VIDEO_I2V": "H3_FL2VA_I2V_CANDIDATE",
            }.get(candidate_capability)
            if expected_workflow_capability is None and image_capability:
                expected_workflow_capability = "SDXL_T2I_CANDIDATE"
            if expected_workflow_capability is None or workflow_capability != expected_workflow_capability:
                raise DomainRuleError(
                    "PROFILE_EVIDENCE_CAPABILITY_MISMATCH",
                    "Workflow capability 与待发布 Profile capability 不一致",
                    {"expected": expected_workflow_capability, "actual": workflow_capability},
                )
            evidence = connection.execute(
                """SELECT mv.id AS media_version_id, mv.integrity_status, mv.source_artifact_id,
                mv.source_job_attempt_id, ma.media_kind, ma.project_id, a.status AS artifact_status,
                ja.state AS attempt_state, j.state AS job_state, j.subject_type, j.subject_id,
                j.input_snapshot_json, j.execution_profile_version_id
                FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                JOIN artifacts a ON a.id=mv.source_artifact_id
                JOIN job_attempts ja ON ja.id=mv.source_job_attempt_id AND ja.id=a.job_attempt_id
                JOIN jobs j ON j.id=ja.job_id WHERE mv.id=?""",
                (media_version_id,),
            ).fetchone()
            if evidence is None:
                raise DomainRuleError("PROFILE_EVIDENCE_LINEAGE_REQUIRED", "Profile 发布需要完整 Artifact/Attempt/Job 媒体谱系")
            snapshot = json.loads(str(evidence["input_snapshot_json"]))
            execution_snapshot = snapshot.get("execution_snapshot", {})
            frozen_profile_id = execution_snapshot.get("profile_version_id") if isinstance(execution_snapshot, dict) else None
            frozen_fingerprint = execution_snapshot.get("profile_execution_fingerprint") if isinstance(execution_snapshot, dict) else None
            required_media_kind = "IMAGE" if image_capability else "VIDEO"
            valid = (
                evidence["integrity_status"] == "VERIFIED"
                and evidence["artifact_status"] == "VERIFIED"
                and evidence["attempt_state"] == "SUCCEEDED"
                and evidence["job_state"] == "SUCCEEDED"
                and evidence["media_kind"] == required_media_kind
                and snapshot.get("workflow_version_id") == workflow_version_id
                and evidence["execution_profile_version_id"] == candidate_version_id
                and frozen_profile_id == candidate_version_id
                and frozen_fingerprint == self._execution_fingerprint(candidate)
            )
            if not valid:
                raise DomainRuleError(
                    "PROFILE_EVIDENCE_INVALID",
                    "媒体或执行谱系不足以发布 ProfileVersion；证据必须由当前候选 Profile 与执行指纹真实产生",
                )
            if candidate_capability == "VIDEO_I2V":
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
                effective_input = json.loads(str(candidate["input_contract_json"] or "{}"))
                effective_input["input_slots"] = workflow_input_slots
                effective_input["transport"] = "LOOPBACK_HTTP"
                effective_payload = {
                    "input_contract": effective_input,
                    "parameter_schema": json.loads(str(candidate["parameter_schema_json"] or "{}")),
                    "output_contract": json.loads(str(candidate["output_contract_json"] or "{}")),
                    "resource_policy": json.loads(str(candidate["resource_policy_json"] or "{}")),
                }
                if self._contract_hash(effective_payload) == self._contract_hash(self._contract_payload(existing)):
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
            workflow_content = json.loads(str(workflow["content_json"] or "{}"))
            required_workflow_nodes = sorted({
                str(node.get("class_type"))
                for node in workflow_content.values()
                if isinstance(node, dict) and node.get("class_type")
            }) if isinstance(workflow_content, dict) else []
            uses_core_h3 = "MiniMaxH3ImageToVideo" in required_workflow_nodes
            manifest_capability.update(
                {
                    "status": "playable_success_verified",
                    "playable_success_verified_in_this_run": True,
                    "blocking_evidence": None,
                    "node_family": "comfy_extras.MiniMaxH3ImageToVideo" if uses_core_h3 else manifest_capability.get("node_family"),
                    "required_nodes": required_workflow_nodes,
                    "workflow": str(workflow["package_rel_path"]),
                    "workflow_sha256": str(workflow["content_hash"]),
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
                    "contract_compatibility_attestation_id": str(contract_compatibility["id"]) if contract_compatibility else None,
                    "manifest_capability": manifest_capability,
                }
            )
            input_contract = json.loads(str(candidate["input_contract_json"] or "{}"))
            declared_slots = input_contract.get("input_slots", {})
            if not isinstance(declared_slots, dict):
                raise DomainRuleError("PROFILE_INPUT_CONTRACT_INVALID", "Profile input_slots 契约无效")
            merged_slots = dict(declared_slots)
            for slot_name, workflow_slot in workflow_input_slots.items():
                if slot_name in merged_slots and merged_slots[slot_name] != workflow_slot:
                    raise DomainRuleError(
                        "PROFILE_WORKFLOW_INPUT_CONFLICT",
                        "Profile 与 workflow 对同一 input slot 的约束不一致",
                        {"slot": slot_name, "profile": merged_slots[slot_name], "workflow": workflow_slot},
                    )
                merged_slots[slot_name] = workflow_slot
            input_contract["input_slots"] = merged_slots
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
                    candidate_capability,
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
