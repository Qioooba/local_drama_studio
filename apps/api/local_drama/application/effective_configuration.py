"""Server-side effective Profile/settings resolution used before generation."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from local_drama.application.override_schema import effective_schema, validate_overrides
from local_drama.application.profiles import ProfileService
from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.domain.capabilities import normalize_capability
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.infrastructure.database.sqlite import Database


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _defaults(profile: dict[str, Any]) -> dict[str, Any]:
    bundle = profile.get("model_bundle") if isinstance(profile.get("model_bundle"), dict) else {}
    from_bundle = bundle.get("defaults") if isinstance(bundle, dict) else None
    if isinstance(from_bundle, dict):
        return dict(from_bundle)
    schema = effective_schema(profile)
    fields = schema.get("fields", {}) if isinstance(schema, dict) else {}
    return {
        str(key): field.get("default")
        for key, field in fields.items()
        if isinstance(field, dict) and "default" in field
    } if isinstance(fields, dict) else {}


class EffectiveConfigurationService:
    def __init__(self, database: Database, manifest_path) -> None:
        self.database = database
        self.manifest_path = manifest_path

    def resolve(
        self,
        *,
        project_id: str,
        episode_id: str | None,
        shot_id: str | None,
        capability_code: str,
        requested_profile_version_id: str | None,
        run_overrides: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            capability = normalize_capability(capability_code)
        except ValueError as error:
            raise DomainRuleError("GENERATION_CAPABILITY_INVALID", "未知的生成能力", {"capability": capability_code}) from error

        with self.database.connect() as connection:
            repository = SqliteGenerationPreferenceRepository(connection)
            resolution = GenerationPreferenceQueryService(repository).resolve(
                project_id=project_id, capability=capability, episode_id=episode_id, shot_id=shot_id,
            )
            selected_id = requested_profile_version_id or resolution.get("profile_version_id")
            profile = repository.profile(str(selected_id)) if selected_id else None

        blocking_errors: list[dict[str, Any]] = []
        if requested_profile_version_id and profile is None:
            blocking_errors.append({"code": "PROFILE_VERSION_NOT_FOUND", "message": "请求的 Profile Version 不存在"})
        if profile is not None:
            if str(profile.get("status")) != "PUBLISHED":
                blocking_errors.append({"code": "PROFILE_NOT_PUBLISHED", "message": "只有已发布 Profile Version 才能提交生成"})
            try:
                profile_capability = normalize_capability(str(profile.get("capability") or ""))
            except ValueError:
                profile_capability = str(profile.get("capability") or "").upper()
            if profile_capability != capability:
                blocking_errors.append({"code": "CAPABILITY_MISMATCH", "message": "请求 Profile 与当前生成能力不匹配"})
        elif not requested_profile_version_id and resolution.get("blocked_reason"):
            blocking_errors.append({"code": str(resolution["blocked_reason"]), "message": "当前继承链没有可执行的 Profile Version"})

        normalized_overrides: dict[str, Any] = {}
        if profile is not None:
            try:
                normalized_overrides = validate_overrides(run_overrides, profile, scope="RUN")
            except DomainRuleError as error:
                blocking_errors.append({"code": error.code, "message": error.message, "details": error.details})
        elif run_overrides:
            blocking_errors.append({"code": "PROFILE_REQUIRED_FOR_OVERRIDES", "message": "没有可执行 Profile，不能解析本次运行覆盖参数"})

        base_settings = resolution.get("effective_settings") if not requested_profile_version_id else _defaults(profile or {})
        if not isinstance(base_settings, dict):
            base_settings = _defaults(profile or {})
        effective_settings = {**base_settings, **normalized_overrides}
        setting_sources = dict(resolution.get("setting_sources") or {}) if not requested_profile_version_id else {key: "PROFILE_DEFAULT" for key in base_settings}
        setting_sources.update({key: "RUN_OVERRIDE" for key in normalized_overrides})

        execution: dict[str, Any] = {}
        if selected_id:
            try:
                execution = ProfileService(self.database, self.manifest_path).get_version(str(selected_id)).get("execution", {})
            except DomainRuleError:
                execution = {}
        components = execution.get("components", []) if isinstance(execution, dict) else []
        runtime = execution.get("runtime") if isinstance(execution, dict) else None
        runtime_status = str(runtime.get("status")) if isinstance(runtime, dict) and runtime.get("status") else "UNKNOWN"
        warnings = list(resolution.get("warnings") or [])
        acceleration = str(effective_settings.get("acceleration") or "OFF").upper()
        if acceleration == "TURBO_LORA":
            lora_components = [item for item in components if isinstance(item, dict) and str(item.get("role") or "").upper() == "LORA"]
            if not lora_components or any(item.get("available") is False or str(item.get("status") or "").upper() in {"MISSING", "CANDIDATE_BLOCKED"} for item in lora_components):
                blocking_errors.append({
                    "code": "H3_LORA_ARTIFACT_UNAVAILABLE",
                    "message": "TURBO_LORA 需要一个已验证且可用的 LORA artifact",
                    "details": {"component_count": len(lora_components)},
                })
        if runtime_status not in {"READY", "AVAILABLE"} and selected_id:
            warnings.append(f"本机 Runtime 当前状态为 {runtime_status}，提交前需要确认运行时可用")

        payload = {
            "profile_version_id": selected_id,
            "capability": capability,
            "effective_settings": effective_settings,
            "components": components,
            "runtime_status": runtime_status,
            "blocking_errors": blocking_errors,
            "execution_fingerprint": execution.get("fingerprints", {}).get("execution") if isinstance(execution.get("fingerprints"), dict) else None,
        }
        fingerprint = hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()
        return {
            "capability": capability,
            "profile_version_id": selected_id,
            "profile": resolution.get("profile") if not requested_profile_version_id else {
                "code": profile.get("code"), "title": profile.get("title"), "version_no": profile.get("version_no"),
                "capability": profile.get("capability"), "status": profile.get("status"), "resources": profile.get("resources", {}),
                "override_schema": effective_schema(profile), "model_bundle": profile.get("model_bundle", {}),
            } if profile else None,
            "effective_settings": effective_settings,
            "setting_sources": setting_sources,
            "components": components,
            "runtime_status": runtime_status,
            "warnings": warnings,
            "blocking_errors": blocking_errors,
            "estimated_resources": (resolution.get("estimated_resources") or {}) if isinstance(resolution, dict) else {},
            "fingerprint": f"sha256:{fingerprint}",
            "valid_until": (datetime.now(UTC) + timedelta(seconds=60)).isoformat(),
            "read_only": True,
            "local_only": True,
        }
