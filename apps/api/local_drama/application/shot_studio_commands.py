from __future__ import annotations

import json
from typing import Any, cast

from local_drama.application.ports.shot_studio_commands import ShotStudioCommandPort
from local_drama.domain.director_intent import (
    normalize_director_intent_v3,
    validate_director_intent_v3_payload,
)
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation_contracts import CameraPlan, resolve_camera_plan
from local_drama.domain.policies import validate_shot_ready


class ShotStudioCommandService:
    """Owns creator mutations that are valid inside Shot Studio.

    Formal human decisions are deliberately absent; those remain Review-owned.
    """

    def __init__(self, writer: ShotStudioCommandPort) -> None:
        self.writer = writer

    def save_draft(
        self,
        shot_id: str,
        fields: dict[str, object],
        *,
        freeze: bool = False,
        expected_revision_no: int | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        normalized = normalize_director_intent_v3(fields)
        validate_director_intent_v3_payload(normalized)
        self._validate_camera_plan(normalized, require_executable=False)
        return self.writer.save_draft(
            shot_id,
            normalized,
            freeze=freeze,
            expected_revision_no=expected_revision_no,
            actor=actor,
        )

    def mark_ready(
        self,
        shot_id: str,
        *,
        draft: dict[str, object] | None = None,
        freeze: bool = False,
        expected_revision_no: int | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        normalized: dict[str, Any] | None = None
        if draft is not None:
            normalized = normalize_director_intent_v3(draft)
            validate_director_intent_v3_payload(normalized)
        else:
            current = self.writer.current_draft(shot_id)
            normalized = dict(current["fields"])
        validate_shot_ready(normalized)
        self._validate_camera_plan(normalized, require_executable=True)
        return self.writer.mark_ready(
            shot_id,
            fields=normalized if draft is not None else None,
            freeze=freeze,
            expected_revision_no=expected_revision_no,
            actor=actor,
        )

    def adopt_working_version(self, media_version_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        return self.writer.adopt_working_version(media_version_id, actor=actor)

    def save_draft_revision(
        self,
        shot_id: str,
        fields: dict[str, object],
        freeze: bool = False,
        expected_revision_no: int | None = None,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        result = self.save_draft(
            shot_id,
            fields,
            freeze=freeze,
            expected_revision_no=expected_revision_no,
            actor=actor,
        )
        return cast(dict[str, Any], result["shot_revision"])

    def mark_ready_shot(self, shot_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        result = self.mark_ready(shot_id, actor=actor)
        return cast(dict[str, Any], result["shot"])

    def _validate_camera_plan(self, fields: dict[str, Any], *, require_executable: bool) -> None:
        camera_payload = fields.get("camera_plan")
        if camera_payload is None:
            if require_executable:
                raise DomainRuleError("CAMERA_PLAN_REQUIRED", "镜头就绪前必须完成运镜能力裁决")
            return
        camera_plan = CameraPlan.from_payload(camera_payload)
        if camera_plan.mode == "UNSUPPORTED" and not require_executable:
            return
        profile_version_id = camera_plan.profile_version_id
        if not profile_version_id:
            raise DomainRuleError("CAMERA_PROFILE_REQUIRED", "可执行 CameraPlan 必须绑定 Published ProfileVersion")
        profile = self.writer.camera_profile(profile_version_id)
        if profile is None:
            raise DomainRuleError(
                "PROFILE_VERSION_NOT_FOUND",
                "CameraPlan 绑定的 ProfileVersion 不存在",
                {"profile_version_id": profile_version_id},
            )
        if str(profile["status"]) != "PUBLISHED":
            raise DomainRuleError(
                "PROFILE_NOT_PUBLISHED",
                "只有已发布 Profile 才能保存可执行 CameraPlan",
                {"profile_version_id": profile_version_id},
            )
        try:
            schema = json.loads(str(profile.get("parameter_schema_json") or "{}"))
        except json.JSONDecodeError as error:
            raise DomainRuleError("PROFILE_CAMERA_CONTRACT_INVALID", "Profile parameter schema 不是有效 JSON") from error
        capabilities = schema.get("capabilities", {}) if isinstance(schema, dict) else {}
        if "camera" not in capabilities:
            contract = {"support": "PROMPT_FALLBACK", "prompt_fallback": True}
        else:
            contract = capabilities.get("camera", {}) if isinstance(capabilities, dict) else {}
        support = str(contract.get("support", "UNSUPPORTED")) if isinstance(contract, dict) else "UNSUPPORTED"
        if support not in {"NATIVE", "PROMPT_FALLBACK", "UNSUPPORTED"}:
            raise DomainRuleError("PROFILE_CAMERA_CONTRACT_INVALID", "Profile camera capability support 无效")
        fallback = support == "PROMPT_FALLBACK" and contract.get("prompt_fallback") is True
        if support == "PROMPT_FALLBACK" and not fallback:
            raise DomainRuleError("PROFILE_CAMERA_FALLBACK_INVALID", "Camera prompt fallback 必须由 Profile 显式声明")
        resolved = resolve_camera_plan(
            native_supported=support == "NATIVE",
            prompt_fallback_supported=fallback,
            shot_type=camera_plan.shot_type,
            movement=camera_plan.movement,
            prompt_text=camera_plan.prompt_text,
            direction=camera_plan.direction,
            intensity=camera_plan.intensity,
            curve=camera_plan.curve,
            profile_version_id=profile_version_id,
        )
        if resolved.mode == "UNSUPPORTED":
            raise DomainRuleError(
                "CAMERA_PLAN_UNSUPPORTED",
                "当前 Published Profile 不支持该结构化运镜，不能保存可执行 revision",
                {"profile_version_id": profile_version_id, "movement": camera_plan.movement},
            )
        if resolved.to_dict() != camera_plan.to_dict():
            raise DomainRuleError(
                "CAMERA_PLAN_RESOLUTION_STALE",
                "CameraPlan 与当前 Published Profile capability contract 不一致，请重新裁决",
                {"profile_version_id": profile_version_id},
            )
