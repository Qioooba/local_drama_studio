"""Shot Studio query semantics over a bounded factual read port."""

from __future__ import annotations

from typing import Any

from local_drama.application.ports.shot_studio import ShotStudioReadPort


class ShotStudioQueryService:
    """Build the creator-facing aggregate without knowing the persistence engine."""

    def __init__(self, reader: ShotStudioReadPort) -> None:
        self.reader = reader

    def studio(
        self, episode_id: str, shot_id: str, nav_radius: int = 12
    ) -> dict[str, Any]:
        facts = self.reader.studio_facts(episode_id, shot_id, nav_radius)
        current = facts["current_shot"]
        blockers = current.get("blockers", [])
        blocking_codes = {
            str(item.get("code"))
            for item in blockers
            if isinstance(item, dict) and bool(item.get("blocking", True))
        }
        preferences = current.get("generation_preferences", {})
        resolutions = preferences.get("resolutions", []) if isinstance(preferences, dict) else []
        capability_options = [
            self._capability_option(item)
            for item in resolutions
            if isinstance(item, dict) and item.get("profile_version_id")
        ]
        has_executable_capability = bool(capability_options)
        production_blocked = any(
            str(item.get("code") or "").startswith(("PRODUCTION_", "WORKFLOW_PRODUCTION_", "VIDEO_WORKFLOW_"))
            for item in blockers
            if isinstance(item, dict)
        )
        can_generate = has_executable_capability and not bool(
            blocking_codes
            & {
                "PROFILE_NOT_BOUND",
                "PRODUCTION_PLAN_NOT_BOUND",
                "DIRECTOR_FIELDS_MISSING",
                "SHOT_NOT_PRODUCTION_READY",
            }
        ) and not production_blocked
        current_media = current.get("current_media")
        review_subject_id = (
            current_media.get("media_version_id")
            if isinstance(current_media, dict)
            else None
        )
        return {
            **facts,
            "current_shot": {
                **current,
                "capability_options": capability_options,
            },
            "allowed_actions": {
                "edit_draft": True,
                "mark_ready": True,
                "generate": can_generate,
                "adopt_working_version": True,
                "write_review_decision": False,
            },
            "review_handoff": {
                "subject_type": "MEDIA_VERSION" if review_subject_id else None,
                "subject_id": review_subject_id,
                "route_kind": "REVIEW",
                "write_owner": "REVIEW_WORKSPACE",
            },
            "read_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
            "request_shape": "bounded_shot_studio_v2",
        }

    def continuity(self, shot_id: str) -> dict[str, Any]:
        return {
            **self.reader.continuity_facts(shot_id),
            "read_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
            "request_shape": "shot_continuity_context_v2",
        }

    @staticmethod
    def _capability_option(resolution: dict[str, Any]) -> dict[str, Any]:
        profile = resolution.get("profile")
        profile = profile if isinstance(profile, dict) else {}
        return {
            "id": str(resolution["profile_version_id"]),
            "version_id": str(resolution["profile_version_id"]),
            "code": str(profile.get("code") or resolution.get("capability") or ""),
            "title": str(profile.get("title") or profile.get("code") or resolution.get("capability") or ""),
            "version_no": int(profile["version_no"]) if profile.get("version_no") is not None else None,
            "capability": str(resolution.get("capability") or ""),
            "capability_contract": profile.get("capability_contract", {}),
            "source": str(resolution.get("source") or "DEFAULT"),
            "status": "BLOCKED" if resolution.get("blocked_reason") else "PUBLISHED",
            "blocked_reason": resolution.get("blocked_reason"),
        }
