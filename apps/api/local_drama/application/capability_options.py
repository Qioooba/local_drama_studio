"""Unified read model for capability selection in creator-facing pages.

Business pages must not reconstruct executability from the global Profile
catalog.  This service combines immutable Profile versions, their frozen
execution route, project preference resolution, and the configured LLM
runtime into one explainable, read-only response.
"""

from __future__ import annotations

from typing import Any, Protocol

from local_drama.domain.capabilities import normalize_capability
from local_drama.domain.errors import DomainRuleError

_READY_RUNTIME_STATES = {"AVAILABLE", "READY", "RUNNING", "PUBLISHED", "CONFIGURED"}
_BLOCKED_RUNTIME_STATES = {"BLOCKED", "BLOCKED_OFFLINE", "FAILED", "DISABLED", "REVOKED", "STOPPED"}


class ProfileCatalogPort(Protocol):
    def list_profiles(self) -> list[dict[str, Any]]: ...

    def get_version(self, profile_version_id: str) -> dict[str, Any]: ...


class PreferenceResolverPort(Protocol):
    def __call__(
        self,
        *,
        project_id: str,
        capability: str,
        episode_id: str | None = None,
        shot_id: str | None = None,
    ) -> dict[str, Any]: ...


def _text(value: Any) -> str:
    return str(value or "").strip()


def _model_identity(profile: dict[str, Any]) -> tuple[str, str]:
    raw_bundle: Any = profile.get("model_bundle")
    bundle: dict[str, Any] = raw_bundle if isinstance(raw_bundle, dict) else {}
    model = _text(bundle.get("model_family") or bundle.get("model") or bundle.get("model_ref"))
    provider = _text(bundle.get("provider") or "LOCAL").upper()
    return model or _text(profile.get("title") or profile.get("code") or "未命名模型"), provider


def _requires_workflow(capability: str) -> bool:
    return capability.startswith("IMAGE_") or capability.startswith("VIDEO_")


class CapabilityOptionService:
    def __init__(
        self,
        profiles: ProfileCatalogPort,
        preference_resolver: PreferenceResolverPort,
        *,
        configured_llm_provider: str,
        configured_llm_base_url: str,
        configured_llm_model: str,
    ) -> None:
        self.profiles = profiles
        self.preference_resolver = preference_resolver
        self.configured_llm_provider = configured_llm_provider.strip().upper()
        self.configured_llm_base_url = configured_llm_base_url.strip()
        self.configured_llm_model = configured_llm_model.strip()

    def list_options(
        self,
        *,
        capability: str,
        project_id: str | None = None,
        episode_id: str | None = None,
        shot_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            canonical = normalize_capability(capability)
        except ValueError as error:
            raise DomainRuleError(
                "GENERATION_CAPABILITY_INVALID",
                "未知的生成能力",
                {"capability": capability},
            ) from error

        matching = [
            item for item in self.profiles.list_profiles()
            if self._canonical_profile_capability(item) == canonical
        ]
        options = [self._option(item, canonical) for item in matching]
        options.sort(
            key=lambda item: (
                not bool(item["selectable"]),
                _text(item["model"]["name"]).casefold(),
                -int(item["profile"]["version_no"]),
            )
        )

        resolution = self._resolution(
            canonical=canonical,
            options=options,
            project_id=project_id,
            episode_id=episode_id,
            shot_id=shot_id,
        )
        configured_runtime = self._configured_runtime(canonical, options)
        return {
            "capability": canonical,
            "scope": {"project_id": project_id, "episode_id": episode_id, "shot_id": shot_id},
            "selection": resolution,
            "options": options,
            "configured_runtime": configured_runtime,
            "summary": {
                "total_count": len(options),
                "selectable_count": sum(bool(item["selectable"]) for item in options),
                "blocked_count": sum(not bool(item["selectable"]) for item in options),
            },
            "repair_href": "/system/capabilities?view=resources",
            "read_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    @staticmethod
    def _canonical_profile_capability(profile: dict[str, Any]) -> str:
        try:
            return normalize_capability(_text(profile.get("capability")))
        except ValueError:
            return _text(profile.get("capability")).upper()

    def _option(self, profile: dict[str, Any], capability: str) -> dict[str, Any]:
        detail = self.profiles.get_version(_text(profile.get("version_id")))
        raw_execution: Any = detail.get("execution")
        execution: dict[str, Any] = raw_execution if isinstance(raw_execution, dict) else {}
        runtime: dict[str, Any] | None = execution.get("runtime") if isinstance(execution.get("runtime"), dict) else None
        workflow: dict[str, Any] | None = execution.get("workflow") if isinstance(execution.get("workflow"), dict) else None
        connection: dict[str, Any] | None = execution.get("provider_connection") if isinstance(execution.get("provider_connection"), dict) else None
        raw_components: Any = execution.get("components")
        components: list[Any] = raw_components if isinstance(raw_components, list) else []
        model_name, provider = _model_identity(detail)
        status = _text(detail.get("status")).upper()
        blockers: list[dict[str, str]] = []
        warnings: list[dict[str, str]] = []

        if status != "PUBLISHED":
            blockers.append({"code": "PROFILE_NOT_PUBLISHED", "message": "该执行配置尚未发布到全局能力目录"})

        if _requires_workflow(capability):
            workflow_status = _text(workflow.get("status") if workflow else "").upper()
            if not workflow:
                blockers.append({"code": "WORKFLOW_REQUIRED", "message": "该生成能力还没有绑定工作流版本"})
            elif workflow_status != "PUBLISHED":
                blockers.append({"code": "WORKFLOW_NOT_PUBLISHED", "message": "绑定的工作流版本尚未发布"})

        unavailable_components = [
            item for item in components
            if isinstance(item, dict)
            and bool(item.get("required", True))
            and (_text(item.get("status")).upper() == "MISSING" or item.get("available") is False)
        ]
        if unavailable_components:
            blockers.append({
                "code": "MODEL_COMPONENT_UNAVAILABLE",
                "message": f"缺少或不可用的必需模型组件：{len(unavailable_components)} 个",
            })

        if connection and _text(connection.get("status")).upper() != "ACTIVE":
            blockers.append({"code": "PROVIDER_CONNECTION_INACTIVE", "message": "绑定的模型服务连接未启用"})

        runtime_status = _text(runtime.get("status") if runtime else "UNKNOWN").upper() or "UNKNOWN"
        if runtime_status in _BLOCKED_RUNTIME_STATES:
            blockers.append({"code": "RUNTIME_UNAVAILABLE", "message": f"执行运行时当前状态为 {runtime_status}"})
        elif runtime_status not in _READY_RUNTIME_STATES:
            warnings.append({"code": "RUNTIME_STATUS_UNCONFIRMED", "message": f"运行时状态为 {runtime_status}，提交时仍会再次预检"})

        selectable = not blockers
        return {
            "profile_version_id": _text(detail.get("id")),
            "profile": {
                "id": _text(detail.get("execution_profile_id")),
                "code": _text(detail.get("code")),
                "title": _text(detail.get("title")),
                "version_no": int(detail.get("version_no") or 1),
                "status": status,
            },
            "model": {"name": model_name, "provider": provider},
            "runtime": {
                "id": _text(runtime.get("id")) if runtime else None,
                "title": _text(runtime.get("title")) if runtime else None,
                "status": runtime_status,
                "transport": _text(runtime.get("transport")) if runtime else None,
            },
            "workflow": {
                "id": _text(workflow.get("id")) if workflow else None,
                "title": _text(workflow.get("title")) if workflow else None,
                "status": _text(workflow.get("status")).upper() if workflow else None,
            },
            "selectable": selectable,
            "availability": "READY" if selectable and not warnings else "ATTENTION" if selectable else "BLOCKED",
            "blockers": blockers,
            "warnings": warnings,
            "execution_fingerprint": (
                execution.get("fingerprints", {}).get("execution")
                if isinstance(execution.get("fingerprints"), dict)
                else None
            ),
        }

    def _resolution(
        self,
        *,
        canonical: str,
        options: list[dict[str, Any]],
        project_id: str | None,
        episode_id: str | None,
        shot_id: str | None,
    ) -> dict[str, Any]:
        if project_id:
            resolved = self.preference_resolver(
                project_id=project_id,
                capability=canonical,
                episode_id=episode_id,
                shot_id=shot_id,
            )
            profile_version_id = _text(resolved.get("profile_version_id")) or None
            source = _text(resolved.get("source")) or "AUTO"
            inherited_blocker = _text(resolved.get("blocked_reason")) or None
        else:
            first = next((item for item in options if item["selectable"]), None)
            profile_version_id = _text(first.get("profile_version_id")) if first else None
            source = "GLOBAL_AUTO"
            inherited_blocker = None if first else "NO_COMPATIBLE_PROFILE"

        selected = next((item for item in options if item["profile_version_id"] == profile_version_id), None)
        blockers = list(selected.get("blockers", [])) if selected else []
        if inherited_blocker and not blockers:
            blockers.append({"code": inherited_blocker, "message": "当前继承链没有可执行的已发布配置"})
        if profile_version_id and selected is None:
            blockers.append({"code": "PROFILE_UNAVAILABLE", "message": "当前偏好指向的执行配置已不在能力目录中"})
        return {
            "mode": "AUTO",
            "source": source,
            "profile_version_id": profile_version_id,
            "ready": bool(selected and selected["selectable"] and not inherited_blocker),
            "option": selected,
            "blockers": blockers,
        }

    def _configured_runtime(self, capability: str, options: list[dict[str, Any]]) -> dict[str, Any] | None:
        if not capability.startswith("LLM_") or not self.configured_llm_model:
            return None
        matching = next(
            (
                item for item in options
                if _text(item["model"]["name"]).casefold() == self.configured_llm_model.casefold()
                and _text(item["model"]["provider"]).upper() == self.configured_llm_provider
            ),
            None,
        )
        if matching and matching["profile"]["status"] == "PUBLISHED":
            publication_status = "PUBLISHED"
        elif matching:
            publication_status = "CANDIDATE"
        else:
            publication_status = "NOT_PUBLISHED"
        return {
            "provider": self.configured_llm_provider,
            "base_url": self.configured_llm_base_url,
            "model": self.configured_llm_model,
            "publication_status": publication_status,
            "matching_profile_version_id": matching["profile_version_id"] if matching else None,
            "message": (
                "当前默认运行模型已经发布为这项能力"
                if publication_status == "PUBLISHED"
                else "当前默认运行模型已配置，但尚未发布为这项能力"
            ),
        }
