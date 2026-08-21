"""Generation capability resolver with shot → episode → project inheritance and canonical validation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from local_drama.application.ports.generation_preferences import GenerationPreferenceRepository
from local_drama.application.queries.generation_estimates import _dimensions, _gpu_class
from local_drama.domain.capabilities import normalize_capability
from local_drama.domain.errors import DomainRuleError


def compute_resolution_fingerprint(
    capability: str,
    profile_id: str | None,
    version_no: int | None,
    source: str,
    settings: dict[str, Any] | None = None,
) -> str:
    payload = {
        "capability": capability,
        "profile_id": profile_id,
        "version_no": version_no,
        "source": source,
        "settings": settings or {},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class GenerationPreferenceQueryService:
    def __init__(self, repository: GenerationPreferenceRepository) -> None:
        self.repository = repository

    def list_current(self, project_id: str) -> list[dict[str, Any]]:
        if not self.repository.project_exists(project_id):
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        return self.repository.list_current(project_id)

    def resolve(
        self, *, project_id: str, capability: str,
        episode_id: str | None = None, shot_id: str | None = None,
        requirements: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            canonical_cap = normalize_capability(capability)
        except ValueError as err:
            raise DomainRuleError("GENERATION_CAPABILITY_INVALID", f"未知的生成能力: {capability}", {"capability": capability}) from err

        if not self.repository.project_exists(project_id):
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        if shot_id and self.repository.owner_project_id("SHOT", shot_id) != project_id:
            raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在或不属于当前项目", {"shot_id": shot_id})
        if episode_id and self.repository.owner_project_id("EPISODE", episode_id) != project_id:
            raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在或不属于当前项目", {"episode_id": episode_id})

        if shot_id and not episode_id:
            episode_id = self.repository.shot_episode_id(shot_id)

        candidates = []
        if shot_id:
            candidates.append(("SHOT", shot_id))
        if episode_id:
            candidates.append(("EPISODE", episode_id))
        candidates.append(("PROJECT", project_id))

        for source, owner_id in candidates:
            preference = self.repository.current_preference(project_id, source, owner_id, canonical_cap)
            if preference is None:
                continue

            if preference["resolution_mode"] == "EXPLICIT":
                profile = self.repository.profile(str(preference["execution_profile_version_id"]))
                if profile is None or str(profile["status"]) != "PUBLISHED":
                    return self._blocked(canonical_cap, source, preference, "PROFILE_UNAVAILABLE")

                try:
                    profile_cap = normalize_capability(str(profile["capability"]))
                except ValueError:
                    profile_cap = str(profile["capability"]).upper()

                if profile_cap != canonical_cap:
                    return self._blocked(canonical_cap, source, preference, "CAPABILITY_MISMATCH")

                # Check explicit requirements against profile
                if requirements and not self._satisfies_requirements(profile, requirements):
                    return self._blocked(canonical_cap, source, preference, "REQUIREMENTS_UNSATISFIED")

                return self._resolution(canonical_cap, source, preference, profile, native=True)

            automatic = self.repository.auto_profile(canonical_cap)
            if automatic is None or (requirements and not self._satisfies_requirements(automatic, requirements)):
                return self._blocked(canonical_cap, source, preference, "NO_COMPATIBLE_PROFILE")
            return self._resolution(canonical_cap, source, preference, automatic, native=True)

        automatic = self.repository.auto_profile(canonical_cap)
        if automatic is None or (requirements and not self._satisfies_requirements(automatic, requirements)):
            fingerprint = compute_resolution_fingerprint(canonical_cap, None, None, "AUTO")
            return {
                "capability": canonical_cap, "profile_version_id": None, "source": "AUTO",
                "native_support": False, "fallback_support": False, "warnings": [],
                "estimated_resources": {}, "blocked_reason": "NO_COMPATIBLE_PROFILE", "preference": None,
                "profile": None, "recommendation": None,
                "resolution_fingerprint": fingerprint,
            }
        return self._resolution(canonical_cap, "AUTO", None, automatic, native=True)

    def _satisfies_requirements(self, profile: dict[str, Any], requirements: dict[str, Any]) -> bool:
        if not requirements:
            return True
        resources = profile.get("resources", {})
        # If requirements demand specific frame inputs
        if requirements.get("requires_first_last_frame"):
            try:
                cap = normalize_capability(str(profile.get("capability", "")))
            except ValueError:
                return False
            if cap != "VIDEO_FIRST_LAST_FRAME":
                return False
        if requirements.get("requires_motion_control"):
            try:
                cap = normalize_capability(str(profile.get("capability", "")))
            except ValueError:
                return False
            if cap != "VIDEO_MOTION_CONTROL":
                return False
        # If minimum VRAM requirement is specified
        if "min_vram_gb" in requirements:
            profile_vram = resources.get("vram_gb") or resources.get("estimated_vram_gb")
            if profile_vram is not None and profile_vram < requirements["min_vram_gb"]:
                return False
        return True

    def _resolution(self, capability: str, source: str, preference: dict[str, Any] | None, profile: dict[str, Any], *, native: bool) -> dict[str, Any]:
        automatic = preference is None or preference.get("resolution_mode") == "AUTO"
        profile_id = str(profile["id"])
        version_no = int(profile.get("version_no", 1))
        settings = preference.get("settings") if preference else None
        fingerprint = compute_resolution_fingerprint(capability, profile_id, version_no, source, settings)
        return {
            "capability": capability, "profile_version_id": profile_id, "source": source,
            "native_support": native, "fallback_support": False, "warnings": [],
            "estimated_resources": profile.get("resources", {}), "blocked_reason": None,
            "preference": preference,
            "profile": self._profile_fact(profile),
            "recommendation": self._recommendation(profile, capability=capability, automatic=automatic),
            "resolution_fingerprint": fingerprint,
        }

    @staticmethod
    def _profile_fact(profile: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": str(profile["code"]), "title": str(profile["title"]),
            "version_no": int(profile["version_no"]), "capability": str(profile["capability"]),
            "status": str(profile["status"]), "resources": profile.get("resources", {}),
        }

    def _recommendation(self, profile: dict[str, Any], *, capability: str, automatic: bool) -> dict[str, Any]:
        history = self.repository.recent_terminal_attempts(str(profile["id"]), limit=100)
        items = history.get("items", [])
        latest_dimensions: dict[str, Any] | None = None
        latest_gpu: str | None = None
        for row in items:
            dimensions = _dimensions(row.get("input_snapshot_json"))
            gpu_class = _gpu_class(row)
            spatial_complete = dimensions["width"] is not None and dimensions["height"] is not None
            steps_complete = dimensions["steps"] is not None
            temporal_complete = not capability.startswith("VIDEO_") or (
                dimensions["duration_seconds"] is not None or dimensions["frame_count"] is not None
            )
            if spatial_complete and steps_complete and temporal_complete and gpu_class is not None:
                latest_dimensions, latest_gpu = dimensions, gpu_class
                break

        cohort: list[dict[str, Any]] = []
        if latest_dimensions is not None:
            cohort = [
                row for row in items
                if _dimensions(row.get("input_snapshot_json")) == latest_dimensions and _gpu_class(row) == latest_gpu
            ]
        terminal_count = len(cohort)
        succeeded_count = sum(str(row.get("state")) == "SUCCEEDED" for row in cohort)
        enough = terminal_count >= 3
        if not history.get("schema_available", False):
            unknown_reason = "SCHEMA_UNAVAILABLE"
        elif latest_dimensions is None:
            unknown_reason = "NO_COMPARABLE_DIMENSION_HISTORY"
        elif not enough:
            unknown_reason = "INSUFFICIENT_SAME_DIMENSION_SAMPLES"
        else:
            unknown_reason = None
        return {
            "selection_reason": "AUTO_NEWEST_PUBLISHED_EXACT_CAPABILITY" if automatic else "EXPLICIT_PUBLISHED_VERSION",
            "facts": {
                "capability_exact_match": str(profile["capability"]).upper() == capability,
                "published": str(profile["status"]) == "PUBLISHED",
                "native_support": True,
                "resources": profile.get("resources", {}),
            },
            "local_success_rate": {
                "status": "AVAILABLE" if enough else "UNKNOWN",
                "reason": unknown_reason,
                "value": round(succeeded_count / terminal_count, 4) if enough else None,
                "successful_sample_count": succeeded_count,
                "terminal_sample_count": terminal_count,
                "minimum_sample_count": 3,
                "dimensions": {**(latest_dimensions or {}), "gpu_class": latest_gpu} if latest_dimensions else None,
                "evidence": {
                    "source": "LOCAL_TERMINAL_JOB_ATTEMPTS", "terminal_states": ["SUCCEEDED", "FAILED"],
                    "most_recent_first": True, "candidate_limit": 100,
                    "candidate_count": len(items), "gpu_hardware_model_known": False,
                },
            },
        }

    @staticmethod
    def _blocked(capability: str, source: str, preference: dict[str, Any], reason: str) -> dict[str, Any]:
        fingerprint = compute_resolution_fingerprint(capability, None, None, source, preference.get("settings") if preference else None)
        return {
            "capability": capability, "profile_version_id": None, "source": source,
            "native_support": False, "fallback_support": False,
            "warnings": ["已选择的配置当前不可执行；未静默回退到其他能力"],
            "estimated_resources": {}, "blocked_reason": reason, "preference": preference,
            "profile": None, "recommendation": None,
            "resolution_fingerprint": fingerprint,
        }
