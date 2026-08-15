from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.generation_contracts import CameraPlan, MotionMask, PerformanceBinding, TimedDirection, resolve_camera_plan
from local_drama.domain.policies import VariantInput
from local_drama.infrastructure.database.sqlite import Database

from .jobs import JobService
from .media import MediaService


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class GenerationService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.media = MediaService(database, settings)

    # These roles are deliberately semantic.  A local Profile may bind them to
    # any workflow node, but a Variant can never silently turn a driving input
    # into an arbitrary ``images[]``/``audio`` parameter.
    _DRIVING_ROLE_CAPABILITIES: dict[str, tuple[str, ...]] = {
        "DRIVING_VIDEO": ("driving_video", "performance", "character_driving"),
        "POSE_SEQUENCE": ("pose", "pose_driving", "performance"),
        "POSE_REFERENCE": ("pose", "pose_driving", "performance"),
        "AUDIO_GUIDE": ("audio_guide", "lip_sync", "performance"),
        "FACE_REFERENCE": ("face_reference", "face", "performance"),
        "CHARACTER_REFERENCE": ("character_reference", "reference", "performance"),
        "CHARACTER_DRIVING": ("character_driving", "performance"),
    }

    @staticmethod
    def _input_slots(input_contract: object) -> dict[str, Any]:
        """Return the frozen semantic input-slot contract.

        The blueprint has used both the current ``{"input_slots": {...}}``
        envelope and the older direct-slot form in fixtures.  Supporting both
        here keeps old local Profiles readable while still validating every
        slot instead of dropping an unknown shape.
        """
        if not isinstance(input_contract, dict):
            raise DomainRuleError("PROFILE_INPUT_CONTRACT_INVALID", "Profile input contract 必须是对象")
        slots = input_contract.get("input_slots")
        if slots is None:
            slots = {
                str(key): value
                for key, value in input_contract.items()
                if str(key) not in {"transport", "local_only", "requires_explicit_validation"}
            }
        if not isinstance(slots, dict):
            raise DomainRuleError("PROFILE_INPUT_CONTRACT_INVALID", "Profile input_slots 契约无效")
        for role, spec in slots.items():
            if not isinstance(role, str) or not role.strip() or not isinstance(spec, dict):
                raise DomainRuleError("PROFILE_INPUT_CONTRACT_INVALID", "Profile input slot 必须是带约束的对象", {"role": str(role)})
            minimum = spec.get("min", 0)
            maximum = spec.get("max", minimum if minimum else 1)
            if not isinstance(minimum, int) or not isinstance(maximum, int) or minimum < 0 or maximum < minimum:
                raise DomainRuleError(
                    "PROFILE_INPUT_CONTRACT_INVALID",
                    "Profile input slot min/max 无效",
                    {"role": role, "min": minimum, "max": maximum},
                )
        return {str(role): value for role, value in slots.items()}

    @staticmethod
    def _slot_media_kinds(spec: dict[str, Any]) -> set[str]:
        raw = spec.get("media_kinds", spec.get("allowed_media_kinds", spec.get("media_kind")))
        if raw is None:
            raw = spec.get("media")
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return set()
        kinds: set[str] = set()
        for item in raw:
            token = str(item).upper()
            if token.startswith("IMAGE/"):
                token = "IMAGE"
            elif token.startswith("VIDEO/"):
                token = "VIDEO"
            elif token.startswith("AUDIO/"):
                token = "AUDIO"
            if token in {"IMAGE", "VIDEO", "AUDIO", "DOCUMENT", "OTHER"}:
                kinds.add(token)
        return kinds

    @staticmethod
    def _validate_slot_weight(role: str, spec: dict[str, Any], weight: float | None) -> None:
        weight_rule = spec.get("weight")
        supports_weight = bool(
            spec.get("supports_weight")
            or spec.get("weight_required")
            or spec.get("weights_required")
            or isinstance(weight_rule, dict)
            or "weight_min" in spec
            or "weight_max" in spec
        )
        required = bool(spec.get("weight_required") or spec.get("weights_required"))
        minimum = 0.0
        maximum = 1.0
        if isinstance(weight_rule, dict):
            required = required or bool(weight_rule.get("required"))
            minimum = float(weight_rule.get("min", minimum))
            maximum = float(weight_rule.get("max", maximum))
        elif weight_rule is not None and not isinstance(weight_rule, bool):
            raise DomainRuleError("PROFILE_INPUT_CONTRACT_INVALID", "Profile weight 约束必须是对象", {"role": role})
        if "weight_min" in spec:
            minimum = float(spec["weight_min"])
        if "weight_max" in spec:
            maximum = float(spec["weight_max"])
        if minimum < 0 or maximum > 1 or minimum > maximum:
            raise DomainRuleError("PROFILE_INPUT_CONTRACT_INVALID", "Profile weight 范围无效", {"role": role})
        if weight is None:
            if required:
                raise DomainRuleError("PROFILE_INPUT_WEIGHT_REQUIRED", "该 Profile input slot 要求每个参考输入显式 weight", {"role": role})
            return
        if not supports_weight:
            raise DomainRuleError("PROFILE_INPUT_WEIGHT_UNSUPPORTED", "当前 Profile input slot 未声明 weight，不能提交权重", {"role": role})
        if not minimum <= weight <= maximum:
            raise DomainRuleError(
                "PROFILE_INPUT_WEIGHT_INVALID",
                "Variant 输入 weight 超出 Profile input contract 范围",
                {"role": role, "weight": weight, "min": minimum, "max": maximum},
            )

    @classmethod
    def _validate_driving_capability(cls, role: str, capabilities: dict[str, Any]) -> None:
        names = cls._DRIVING_ROLE_CAPABILITIES.get(role)
        if not names:
            return
        matched = None
        for name in names:
            candidate = capabilities.get(name)
            if isinstance(candidate, dict):
                matched = candidate
                break
        if not isinstance(matched, dict):
            raise DomainRuleError(
                "PROFILE_DRIVING_UNSUPPORTED",
                f"当前 Profile 未声明 {role} 的本地 driving 能力",
                {"role": role, "required_capabilities": list(names)},
            )
        if matched.get("enabled") is False or str(matched.get("support", "NATIVE")).upper() == "UNSUPPORTED":
            raise DomainRuleError(
                "PROFILE_DRIVING_UNSUPPORTED",
                f"当前 Profile 不支持 {role} driving",
                {"role": role, "capability": next(name for name in names if capabilities.get(name) is matched)},
            )

    def create_intent(self, project_id: str, owner_type: str, owner_id: str, purpose: str, creative_goal: str) -> dict[str, Any]:
        intent_id = str(uuid.uuid4())
        now = _now()
        with self.database.transaction() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id = ?", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            connection.execute(
                """INSERT INTO generation_intents (id, project_id, owner_type, owner_id, purpose, creative_goal, status,
                created_at, updated_at, created_by) VALUES (?, ?, ?, ?, ?, ?, 'DRAFT', ?, ?, 'local-user')""",
                (intent_id, project_id, owner_type, owner_id, purpose, creative_goal, now, now),
            )
        return self.get_intent(intent_id)

    def get_intent(self, intent_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM generation_intents WHERE id = ?", (intent_id,)).fetchone()
        if row is None:
            raise DomainRuleError("GENERATION_INTENT_NOT_FOUND", "GenerationIntent 不存在", {"intent_id": intent_id})
        return dict(row)

    def list_intents(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if project_id:
                rows = connection.execute(
                    "SELECT * FROM generation_intents WHERE project_id=? ORDER BY created_at, id", (project_id,)
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM generation_intents ORDER BY created_at, id").fetchall()
        return [dict(row) for row in rows]

    def list_variants(self, intent_id: str) -> list[dict[str, Any]]:
        self.get_intent(intent_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM generation_variants WHERE intent_id=? ORDER BY variant_no", (intent_id,)
            ).fetchall()
        return [self.get_variant(str(row["id"])) for row in rows]

    def _plan_context(self, intent_id: str, plan: VariantPlan) -> tuple[set[str], set[str], dict[str, Any]]:
        with self.database.connect() as connection:
            intent = connection.execute("SELECT * FROM generation_intents WHERE id=?", (intent_id,)).fetchone()
            if intent is None:
                raise DomainRuleError("GENERATION_INTENT_NOT_FOUND", "GenerationIntent 不存在", {"intent_id": intent_id})
            profile = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (plan.profile_version_id,)).fetchone()
            if profile is None:
                raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
            if profile["status"] != "PUBLISHED":
                raise DomainRuleError("PROFILE_NOT_PUBLISHED", "只有已发布 Profile 才能创建 GenerationVariant")
            if not profile["workflow_version_id"]:
                raise DomainRuleError("PROFILE_WORKFLOW_REQUIRED", "生成 Profile 必须冻结一个已发布 WorkflowVersion")
            workflow = connection.execute(
                "SELECT status, content_hash, content_json, node_bindings_json, revision FROM workflow_versions WHERE id=?",
                (profile["workflow_version_id"],),
            ).fetchone()
            if workflow is None:
                raise DomainRuleError("WORKFLOW_VERSION_NOT_FOUND", "Profile 引用的 WorkflowVersion 不存在")
            if workflow["status"] != "PUBLISHED":
                raise DomainRuleError("WORKFLOW_NOT_PUBLISHED", "生成 Profile 不能引用未发布 WorkflowVersion")
            workflow_bindings = json.loads(workflow["node_bindings_json"] or "{}")
            if not isinstance(workflow_bindings, dict):
                raise DomainRuleError("WORKFLOW_BINDING_INVALID", "Workflow semantic binding 契约无效")
            workflow_content = json.loads(workflow["content_json"] or "{}")
            if not isinstance(workflow_content, dict):
                raise DomainRuleError("WORKFLOW_BINDING_INVALID", "Workflow content 契约无效")
            # Keep the exact execution authority in the immutable plan/job
            # snapshot.  A profile id alone is not enough for replay: the
            # local model bundle, runtime and workflow bytes must be
            # auditable even if a newer profile is later published.
            try:
                model_bundle = json.loads(profile["model_bundle_json"] or "{}")
            except (TypeError, json.JSONDecodeError) as error:
                raise DomainRuleError("PROFILE_MODEL_BUNDLE_INVALID", "Profile model bundle 快照无效") from error
            if not isinstance(model_bundle, dict):
                raise DomainRuleError("PROFILE_MODEL_BUNDLE_INVALID", "Profile model bundle 必须是对象")
            if plan.seed_policy == "EXPLICIT" and "SEED" in workflow_bindings:
                semantic_seed = plan.parameter_set.get("SEED")
                if semantic_seed != plan.explicit_seed:
                    raise DomainRuleError(
                        "VARIANT_SEED_SNAPSHOT_MISMATCH",
                        "Variant 显式 seed 必须与实际 Workflow SEED 语义输入一致",
                        {"explicit_seed": plan.explicit_seed, "semantic_seed": semantic_seed},
                    )
            input_contract = json.loads(profile["input_contract_json"] or "{}")
            parameter_schema = json.loads(profile["parameter_schema_json"] or "{}")
            input_slots = self._input_slots(input_contract)
            seed_contract = parameter_schema.get("seed", {}) if isinstance(parameter_schema, dict) else {}
            if not isinstance(seed_contract, dict):
                raise DomainRuleError("PROFILE_SEED_CONTRACT_INVALID", "Profile seed 契约无效")
            capabilities = parameter_schema.get("capabilities", {}) if isinstance(parameter_schema, dict) else {}
            if not isinstance(capabilities, dict):
                raise DomainRuleError("PROFILE_CAPABILITY_CONTRACT_INVALID", "Profile capabilities 契约无效")
            controls = (
                ("timed_directions", TimedDirection, "timed_direction"),
                ("performance_bindings", PerformanceBinding, "performance_binding"),
                ("motion_masks", MotionMask, "motion_mask"),
            )
            for field_name, contract_type, capability_name in controls:
                values = plan.parameter_set.get(field_name, [])
                if values in (None, []):
                    continue
                if not isinstance(values, list):
                    raise DomainRuleError("GENERATION_CONTROL_INVALID", f"{field_name} 必须是数组")
                aliases = {
                    "timed_direction": ("timed_direction", "timed", "camera_timed"),
                    "performance_binding": ("performance_binding", "performance", "character_driving"),
                    "motion_mask": ("motion_mask", "motion", "motion_control"),
                }.get(capability_name, (capability_name,))
                contract: dict[str, Any] = {}
                for alias in aliases:
                    candidate_contract = capabilities.get(alias)
                    if isinstance(candidate_contract, dict):
                        contract = candidate_contract
                        break
                if not isinstance(contract, dict) or contract.get("enabled") is not True:
                    raise DomainRuleError("PROFILE_CONTROL_UNSUPPORTED", f"当前 Profile 未声明 {capability_name} 能力")
                for index, value in enumerate(values):
                    if not isinstance(value, dict):
                        raise DomainRuleError("GENERATION_CONTROL_INVALID", f"{field_name}[{index}] 必须是对象")
                    try:
                        normalized_value = dict(value)
                        # Keep the wire contract tolerant of the common
                        # ``kind``/``type`` aliases while preserving a strict
                        # semantic snapshot in the frozen Variant.
                        if contract_type is PerformanceBinding:
                            if "binding_type" not in normalized_value:
                                for alias in ("binding_kind", "kind", "type"):
                                    if alias in normalized_value:
                                        normalized_value["binding_type"] = normalized_value.pop(alias)
                                        break
                        item = contract_type(**normalized_value)
                        item.validate()
                    except (TypeError, ValueError, DomainRuleError) as error:
                        if isinstance(error, DomainRuleError):
                            raise
                        raise DomainRuleError("GENERATION_CONTROL_INVALID", f"{field_name}[{index}] 结构无效") from error
            camera_payload = plan.parameter_set.get("camera_plan")
            if camera_payload is not None:
                camera_plan = CameraPlan.from_payload(camera_payload)
                if camera_plan.profile_version_id != plan.profile_version_id:
                    raise DomainRuleError(
                        "CAMERA_PLAN_PROFILE_MISMATCH",
                        "CameraPlan 必须由当前 GenerationVariant 的同一 ProfileVersion 裁决",
                        {"camera_profile_version_id": camera_plan.profile_version_id, "variant_profile_version_id": plan.profile_version_id},
                    )
                camera_contract = capabilities.get("camera", {}) if isinstance(capabilities, dict) else {}
                support = str(camera_contract.get("support", "UNSUPPORTED")) if isinstance(camera_contract, dict) else "UNSUPPORTED"
                if support not in {"NATIVE", "PROMPT_FALLBACK", "UNSUPPORTED"}:
                    raise DomainRuleError("PROFILE_CAMERA_CONTRACT_INVALID", "Profile camera capability support 无效")
                fallback = support == "PROMPT_FALLBACK" and camera_contract.get("prompt_fallback") is True
                if support == "PROMPT_FALLBACK" and not fallback:
                    raise DomainRuleError("PROFILE_CAMERA_FALLBACK_INVALID", "Camera prompt fallback 必须由 Profile 显式声明")
                resolved_camera = resolve_camera_plan(
                    native_supported=support == "NATIVE",
                    prompt_fallback_supported=fallback,
                    shot_type=camera_plan.shot_type,
                    movement=camera_plan.movement,
                    prompt_text=camera_plan.prompt_text,
                    direction=camera_plan.direction,
                    intensity=camera_plan.intensity,
                    curve=camera_plan.curve,
                    profile_version_id=plan.profile_version_id,
                )
                if resolved_camera.to_dict() != camera_plan.to_dict():
                    raise DomainRuleError("CAMERA_PLAN_RESOLUTION_STALE", "CameraPlan 与当前 Profile capability contract 不一致，请重新裁决")
                if resolved_camera.mode == "UNSUPPORTED":
                    raise DomainRuleError("CAMERA_PLAN_UNSUPPORTED", "当前 Profile 不支持该结构化运镜，禁止提交 GenerationVariant")
            seed_support = str(seed_contract.get("support") or ("REQUIRED" if seed_contract.get("required") else "OPTIONAL"))
            determinism = str(seed_contract.get("determinism") or "UNDECLARED")
            if plan.seed_policy == "PROVIDER_RANDOM" and seed_support not in {"NONE", "OPTIONAL"}:
                raise DomainRuleError(
                    "PROFILE_PROVIDER_RANDOM_UNSUPPORTED",
                    "当前 Profile 要求显式 seed，不能执行 Provider random 重提",
                    {"profile_version_id": plan.profile_version_id, "seed_support": seed_support},
                    suggested_action="切换到 seed support 为 NONE/OPTIONAL 的 Published Profile，或使用新 seed 重抽",
                )
            allowed_roles = {str(role) for role in input_slots}
            if plan.bindings and not allowed_roles:
                raise DomainRuleError("PROFILE_INPUT_CONTRACT_REQUIRED", "Profile 必须声明语义 input_slots 才能接收媒体输入")
            unsupported_roles = sorted({binding.role for binding in plan.bindings} - allowed_roles)
            if unsupported_roles:
                raise DomainRuleError(
                    "PROFILE_CAPABILITY_UNSUPPORTED",
                    "当前已发布 Profile 不支持请求的语义输入槽",
                    {
                        "profile_version_id": plan.profile_version_id,
                        "unsupported_roles": unsupported_roles,
                        "supported_roles": sorted(allowed_roles),
                        "required_capability": "FIRST_LAST_KEYFRAMES" if "END_FRAME" in unsupported_roles else "SEMANTIC_INPUT_SLOTS",
                    },
                    suggested_action="配置并发布声明所需 input_slots 与 Workflow bindings 的 ExecutionProfileVersion，然后重新预检",
                )
            prompt_revision = None
            if plan.prompt_revision_id:
                prompt_revision = connection.execute(
                    """SELECT pr.*, p.project_id FROM prompt_revisions pr
                    JOIN prompts p ON p.id=pr.prompt_id WHERE pr.id=?""",
                    (plan.prompt_revision_id,),
                ).fetchone()
                if prompt_revision is None:
                    raise DomainRuleError("PROMPT_REVISION_NOT_FOUND", "Variant 必须引用已冻结 PromptRevision")
                if str(prompt_revision["project_id"]) != str(intent["project_id"]):
                    raise DomainRuleError("PROMPT_REVISION_PROJECT_MISMATCH", "PromptRevision 必须属于 GenerationIntent 所在项目")
            counts: dict[str, int] = {}
            media_dependencies: list[dict[str, Any]] = []
            approval_dependencies: dict[tuple[str, int], str] = {}
            visual_dimensions: dict[tuple[str, int], tuple[int, int]] = {}
            bindings_by_role: dict[str, list[VariantInput]] = {}
            for binding in plan.bindings:
                bindings_by_role.setdefault(binding.role, []).append(binding)
                slot = input_slots.get(binding.role)
                if not isinstance(slot, dict):
                    # This is normally caught by unsupported_roles above, but
                    # retaining a slot-level error makes malformed contracts
                    # diagnosable when the same role is mutated concurrently.
                    raise DomainRuleError("PROFILE_INPUT_ROLE_UNSUPPORTED", "Profile 未声明该语义输入槽", {"role": binding.role})
                self._validate_driving_capability(binding.role, capabilities)
                self._validate_slot_weight(binding.role, slot, binding.weight)
            for role, role_bindings in bindings_by_role.items():
                ordinals = [item.ordinal for item in role_bindings]
                if len(set(ordinals)) != len(ordinals) or sorted(ordinals) != list(range(len(ordinals))):
                    raise DomainRuleError(
                        "PROFILE_INPUT_ORDER_INVALID",
                        "同一 Profile input slot 的 ordinal 必须从 0 连续递增，不能跳号或重复",
                        {"role": role, "ordinals": ordinals},
                    )
                slot = input_slots[role]
                allowed_ordinals = slot.get("allowed_ordinals")
                if allowed_ordinals is not None:
                    if not isinstance(allowed_ordinals, list) or any(not isinstance(item, int) for item in allowed_ordinals):
                        raise DomainRuleError("PROFILE_INPUT_CONTRACT_INVALID", "Profile allowed_ordinals 契约无效", {"role": role})
                    if any(item.ordinal not in allowed_ordinals for item in role_bindings):
                        raise DomainRuleError(
                            "PROFILE_INPUT_ORDER_INVALID",
                            "Variant 输入顺序不符合 Profile allowed_ordinals",
                            {"role": role, "allowed_ordinals": allowed_ordinals, "ordinals": ordinals},
                        )
            performance_values = plan.parameter_set.get("performance_bindings", [])
            if performance_values:
                if not isinstance(performance_values, list):
                    raise DomainRuleError("GENERATION_CONTROL_INVALID", "performance_bindings 必须是数组")
                driving_roles = set(self._DRIVING_ROLE_CAPABILITIES)
                if not driving_roles.intersection(bindings_by_role):
                    raise DomainRuleError(
                        "PERFORMANCE_MEDIA_REQUIRED",
                        "PerformanceBinding 必须绑定项目内 driving video、pose、audio 或角色参考 MediaVersion",
                        {"required_roles": sorted(driving_roles)},
                    )
                source_aliases = {
                    "DRIVING_VIDEO": {"DRIVING_VIDEO"},
                    "POSE": {"POSE_SEQUENCE", "POSE_REFERENCE"},
                    "ACTION": {"DRIVING_VIDEO", "POSE_SEQUENCE", "POSE_REFERENCE"},
                    "LIP_SYNC": {"AUDIO_GUIDE", "FACE_REFERENCE"},
                    "FACE_DRIVING": {"FACE_REFERENCE", "CHARACTER_REFERENCE"},
                    "AUDIO_GUIDE": {"AUDIO_GUIDE"},
                    "CHARACTER_DRIVING": {"CHARACTER_REFERENCE", "DRIVING_VIDEO"},
                }
                for index, value in enumerate(performance_values):
                    if not isinstance(value, dict):
                        continue
                    source_role: object = value.get("source_role")
                    if source_role:
                        binding_type: object = value.get("binding_type", value.get("binding_kind", value.get("kind", value.get("type", "CHARACTER_DRIVING"))))
                        allowed_source_roles = source_aliases.get(str(binding_type), driving_roles)
                        if str(source_role) not in allowed_source_roles or str(source_role) not in bindings_by_role:
                            raise DomainRuleError(
                                "PERFORMANCE_SOURCE_ROLE_INVALID",
                                "PerformanceBinding source_role 必须指向同一 Variant 的兼容 driving input role",
                                {"index": index, "source_role": source_role, "allowed_roles": sorted(allowed_source_roles)},
                            )
            for binding in plan.bindings:
                counts[binding.role] = counts.get(binding.role, 0) + 1
                media = connection.execute(
                    """SELECT mv.sha256, mv.integrity_status, mv.probe_json, ma.media_kind, ma.project_id FROM media_versions mv
                    JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE mv.id=?""",
                    (binding.media_version_id,),
                ).fetchone()
                if media is None:
                    raise DomainRuleError("MEDIA_VERSION_NOT_FOUND", "Variant 输入必须引用已注册 MediaVersion")
                if str(media["project_id"]) != str(intent["project_id"]):
                    raise DomainRuleError("VARIANT_INPUT_PROJECT_MISMATCH", "Variant 输入必须属于 GenerationIntent 所在项目")
                if media["integrity_status"] != "VERIFIED":
                    raise DomainRuleError("SOURCE_INTEGRITY_FAILED", "Variant 输入完整性未通过")
                slot = input_slots[binding.role]
                allowed_media_kinds = self._slot_media_kinds(slot)
                if allowed_media_kinds and str(media["media_kind"]).upper() not in allowed_media_kinds:
                    raise DomainRuleError(
                        "PROFILE_INPUT_MEDIA_KIND_INVALID",
                        "Variant 输入媒体类型不符合 Profile input contract",
                        {"role": binding.role, "media_kind": media["media_kind"], "allowed_media_kinds": sorted(allowed_media_kinds)},
                    )
                verified_media = self.media.verify_content_integrity(binding.media_version_id)
                stale_anchor = connection.execute(
                    "SELECT id, stale_reason FROM frame_anchors WHERE extracted_media_version_id=? AND is_stale=1 ORDER BY id LIMIT 1",
                    (binding.media_version_id,),
                ).fetchone()
                if stale_anchor is not None:
                    raise DomainRuleError(
                        "FRAME_ANCHOR_STALE_INPUT",
                        "Variant 不能引用已失效 FrameAnchor 的 extracted media",
                        {"frame_anchor_id": stale_anchor["id"], "stale_reason": stale_anchor["stale_reason"]},
                    )
                if binding.role in {"FIRST_FRAME", "END_FRAME", "MIDDLE_KEYFRAME"}:
                    if media["media_kind"] != "IMAGE":
                        raise DomainRuleError("FRAME_INPUT_MEDIA_KIND_INVALID", "首尾/关键帧语义槽只能绑定 IMAGE MediaVersion")
                    probe = json.loads(media["probe_json"] or "{}")
                    streams = probe.get("streams", []) if isinstance(probe, dict) else []
                    visual = next(
                        (stream for stream in streams if isinstance(stream, dict) and stream.get("width") and stream.get("height")),
                        None,
                    )
                    if visual is None:
                        raise DomainRuleError(
                            "FRAME_DIMENSIONS_REQUIRED", "首尾/关键帧必须先完成可验证的宽高 probe", {"role": binding.role}
                        )
                    visual_dimensions[(binding.role, binding.ordinal)] = (int(visual["width"]), int(visual["height"]))
                if str(intent["purpose"]) in {"I2V_PROXY", "I2V_FORMAL"} and binding.role == "FIRST_FRAME":
                    approval = connection.execute(
                        """SELECT rd.id FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                        JOIN review_decisions rd ON rd.subject_type='MEDIA_VERSION' AND rd.subject_id=mv.id
                        WHERE mv.id=? AND ma.project_id=? AND ma.owner_type='SHOT' AND ma.owner_id=?
                        AND ma.purpose='KEYFRAME' AND ma.media_kind='IMAGE' AND ma.approved_version_id=mv.id
                        AND mv.stage='KEYFRAME' AND mv.integrity_status='VERIFIED'
                        AND rd.decision='APPROVED' AND rd.is_stale=0
                        ORDER BY rd.created_at DESC LIMIT 1""",
                        (binding.media_version_id, intent["project_id"], intent["owner_id"]),
                    ).fetchone()
                    if approval is None:
                        raise DomainRuleError(
                            "APPROVED_KEYFRAME_REQUIRED",
                            "正式 I2V 代理或正式视频只能绑定当前镜头已批准关键帧",
                            {"media_version_id": binding.media_version_id, "shot_id": intent["owner_id"]},
                        )
                    approval_dependencies[(binding.role, binding.ordinal)] = str(approval["id"])
                media_dependencies.append(
                    {
                        "id": binding.media_version_id,
                        "sha256": verified_media["sha256"],
                        "integrity_status": "VERIFIED",
                        "source_approval_id": approval_dependencies.get((binding.role, binding.ordinal)),
                    }
                )
            first_dimensions = visual_dimensions.get(("FIRST_FRAME", 0))
            end_dimensions = visual_dimensions.get(("END_FRAME", 0))
            if first_dimensions and end_dimensions:
                first_width, first_height = first_dimensions
                end_width, end_height = end_dimensions
                if first_width * end_height != end_width * first_height:
                    raise DomainRuleError(
                        "FRAME_ASPECT_RATIO_MISMATCH",
                        "FIRST_FRAME 与 END_FRAME 画幅不兼容；当前 Profile 未声明显式 crop/pad 转换",
                        {"first": {"width": first_width, "height": first_height}, "end": {"width": end_width, "height": end_height}},
                    )
            for role, slot in input_slots.items():
                if not isinstance(slot, dict):
                    raise DomainRuleError("PROFILE_INPUT_CONTRACT_INVALID", "Profile input slot 契约无效", {"role": role})
                count = counts.get(str(role), 0)
                minimum = int(slot.get("min", 0))
                maximum = int(slot.get("max", minimum if minimum else 1))
                if count < minimum or count > maximum:
                    raise DomainRuleError(
                        "PROFILE_INPUT_CARDINALITY_INVALID",
                        "Variant 输入数量不符合 Profile 槽位约束",
                        {"role": role, "count": count, "min": minimum, "max": maximum},
                    )
            for role in counts:
                workflow_binding = workflow_bindings.get(role)
                if not isinstance(workflow_binding, dict) or not workflow_binding.get("node_id") or not workflow_binding.get("input"):
                    raise DomainRuleError(
                        "WORKFLOW_SLOT_UNSUPPORTED", "已发布 Workflow 未声明该语义输入槽", {"role": role}
                    )
                node = workflow_content.get(str(workflow_binding["node_id"]))
                if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                    raise DomainRuleError(
                        "WORKFLOW_BINDING_INVALID", "Workflow semantic binding 指向不存在或无 inputs 的节点", {"role": role}
                    )
            ancestors: set[str] = set()
            parent = None
            current = plan.parent_variant_id
            while current:
                if current in ancestors:
                    raise DomainRuleError("VARIANT_LINEAGE_CYCLE", "GenerationVariant 谱系不能形成环")
                ancestors.add(current)
                row = connection.execute("SELECT * FROM generation_variants WHERE id=?", (current,)).fetchone()
                if row is None:
                    raise DomainRuleError("VARIANT_PARENT_NOT_FOUND", "父 GenerationVariant 不存在")
                if str(row["intent_id"]) != intent_id:
                    raise DomainRuleError("VARIANT_PARENT_INTENT_MISMATCH", "父 GenerationVariant 必须属于同一 GenerationIntent")
                if int(row["is_stale"]):
                    raise DomainRuleError(
                        "VARIANT_PARENT_STALE",
                        "已失效 GenerationVariant 不能继续派生新生产计划",
                        {"variant_id": row["id"], "stale_reason": row["stale_reason"]},
                    )
                if parent is None:
                    parent = row
                current = str(row["parent_variant_id"]) if row["parent_variant_id"] else None
            plan.validate(variant_id="__planned_variant__", ancestors=ancestors, allowed_roles=allowed_roles)
            parent_bindings = []
            if parent is not None:
                parent_bindings = connection.execute(
                    "SELECT role, media_version_id, ordinal, weight FROM variant_input_bindings WHERE variant_id=? ORDER BY role, ordinal, id",
                    (parent["id"],),
                ).fetchall()
            if plan.variant_type == "EXACT_REPLAY" and parent is not None:
                replay_snapshot = {
                    "prompt_revision_id": plan.prompt_revision_id,
                    "profile_version_id": plan.profile_version_id,
                    "parameter_set": plan.parameter_set,
                    "seed_policy": plan.seed_policy,
                    "explicit_seed": plan.explicit_seed,
                    "provider_random_nonce": plan.provider_random_nonce,
                    "bindings": sorted((binding.role, binding.media_version_id, binding.ordinal, binding.weight) for binding in plan.bindings),
                }
                parent_snapshot: dict[str, object] = {
                    "prompt_revision_id": parent["prompt_revision_id"],
                    "profile_version_id": parent["capability_profile_version_id"],
                    "parameter_set": json.loads(parent["parameter_set_json"]),
                    "seed_policy": parent["seed_policy"],
                    "explicit_seed": parent["explicit_seed"],
                    "provider_random_nonce": parent["provider_random_nonce"],
                    "bindings": sorted((row["role"], row["media_version_id"], row["ordinal"], row["weight"]) for row in parent_bindings),
                }
                if replay_snapshot != parent_snapshot:
                    raise DomainRuleError("EXACT_REPLAY_SNAPSHOT_MISMATCH", "Exact replay 必须保持 Prompt、Profile、参数、seed 和输入完全一致")
            if plan.variant_type == "PROMPT_BRANCH":
                if parent is None or prompt_revision is None or not parent["prompt_revision_id"]:
                    raise DomainRuleError("PROMPT_BRANCH_PARENT_REQUIRED", "Prompt branch 必须引用带冻结 PromptRevision 的父 Variant")
                if str(prompt_revision["parent_revision_id"] or "") != str(parent["prompt_revision_id"]):
                    raise DomainRuleError("PROMPT_BRANCH_REVISION_INVALID", "Prompt branch 必须引用父 PromptRevision 的直接子 revision")
                parent_execution = {
                    "profile_version_id": parent["capability_profile_version_id"],
                    "parameter_set": json.loads(parent["parameter_set_json"]),
                    "seed_policy": parent["seed_policy"],
                    "explicit_seed": parent["explicit_seed"],
                    "provider_random_nonce": parent["provider_random_nonce"],
                    "bindings": sorted((row["role"], row["media_version_id"], row["ordinal"], row["weight"]) for row in parent_bindings),
                }
                branch_execution = {
                    "profile_version_id": plan.profile_version_id,
                    "parameter_set": plan.parameter_set,
                    "seed_policy": plan.seed_policy,
                    "explicit_seed": plan.explicit_seed,
                    "provider_random_nonce": plan.provider_random_nonce,
                    "bindings": sorted((item.role, item.media_version_id, item.ordinal, item.weight) for item in plan.bindings),
                }
                if branch_execution != parent_execution:
                    raise DomainRuleError("PROMPT_BRANCH_SCOPE_INVALID", "Prompt branch 只能改变 PromptRevision")
            if plan.variant_type == "SOURCE_IMAGE_BRANCH":
                if parent is None:
                    raise DomainRuleError("SOURCE_BRANCH_PARENT_REQUIRED", "Source branch 必须引用父 Variant")
                parent_by_key = {(str(row["role"]), int(row["ordinal"])): str(row["media_version_id"]) for row in parent_bindings}
                branch_by_key = {(item.role, item.ordinal): item.media_version_id for item in plan.bindings}
                changed = [key for key in sorted(set(parent_by_key) | set(branch_by_key)) if parent_by_key.get(key) != branch_by_key.get(key)]
                if changed != [("FIRST_FRAME", 0)]:
                    raise DomainRuleError("SOURCE_BRANCH_SCOPE_INVALID", "Source branch 必须且只能替换 FIRST_FRAME ordinal 0")
                if (
                    plan.prompt_revision_id != parent["prompt_revision_id"]
                    or plan.profile_version_id != parent["capability_profile_version_id"]
                    or plan.parameter_set != json.loads(parent["parameter_set_json"])
                    or plan.seed_policy != parent["seed_policy"]
                    or plan.explicit_seed != parent["explicit_seed"]
                    or plan.provider_random_nonce != parent["provider_random_nonce"]
                ):
                    raise DomainRuleError("SOURCE_BRANCH_SCOPE_INVALID", "Source branch 只能替换首帧，不能改变其他执行字段")
            if plan.variant_type == "RESAMPLE_NEW_SEED":
                if parent is None:
                    raise DomainRuleError("RESAMPLE_PARENT_REQUIRED", "换 seed 重抽必须引用父 Variant")
                parent_parameters = json.loads(parent["parameter_set_json"])
                branch_parameters = dict(plan.parameter_set)
                parent_parameters.pop("SEED", None)
                branch_parameters.pop("SEED", None)
                parent_snapshot = {
                    "prompt_revision_id": parent["prompt_revision_id"],
                    "profile_version_id": parent["capability_profile_version_id"],
                    "parameter_set_without_seed": parent_parameters,
                    "seed_policy": parent["seed_policy"],
                    "provider_random_nonce": parent["provider_random_nonce"],
                    "bindings": sorted((row["role"], row["media_version_id"], row["ordinal"], row["weight"]) for row in parent_bindings),
                }
                branch_snapshot: dict[str, object] = {
                    "prompt_revision_id": plan.prompt_revision_id,
                    "profile_version_id": plan.profile_version_id,
                    "parameter_set_without_seed": branch_parameters,
                    "seed_policy": plan.seed_policy,
                    "provider_random_nonce": plan.provider_random_nonce,
                    "bindings": sorted((item.role, item.media_version_id, item.ordinal, item.weight) for item in plan.bindings),
                }
                if parent_snapshot != branch_snapshot:
                    raise DomainRuleError("RESAMPLE_SCOPE_INVALID", "换 seed 重抽必须且只能改变 explicit seed")
                if parent["seed_policy"] != "EXPLICIT":
                    raise DomainRuleError("RESAMPLE_SEED_UNSUPPORTED", "父 Variant 未冻结显式 seed，不能声称只改变 seed")
                if plan.explicit_seed == parent["explicit_seed"]:
                    raise DomainRuleError("RESAMPLE_SEED_UNCHANGED", "换 seed 重抽必须使用不同 seed")
            if plan.variant_type == "PROFILE_BRANCH":
                if parent is None:
                    raise DomainRuleError("PROFILE_BRANCH_PARENT_REQUIRED", "Profile branch 必须引用父 Variant")
                parent_snapshot = {
                    "prompt_revision_id": parent["prompt_revision_id"],
                    "parameter_set": json.loads(parent["parameter_set_json"]),
                    "seed_policy": parent["seed_policy"],
                    "explicit_seed": parent["explicit_seed"],
                    "provider_random_nonce": parent["provider_random_nonce"],
                    "bindings": sorted((row["role"], row["media_version_id"], row["ordinal"], row["weight"]) for row in parent_bindings),
                }
                branch_snapshot = {
                    "prompt_revision_id": plan.prompt_revision_id,
                    "parameter_set": plan.parameter_set,
                    "seed_policy": plan.seed_policy,
                    "explicit_seed": plan.explicit_seed,
                    "provider_random_nonce": plan.provider_random_nonce,
                    "bindings": sorted((item.role, item.media_version_id, item.ordinal, item.weight) for item in plan.bindings),
                }
                if parent_snapshot != branch_snapshot:
                    raise DomainRuleError("PROFILE_BRANCH_SCOPE_INVALID", "Profile branch 必须且只能改变 ProfileVersion")
                if plan.profile_version_id == parent["capability_profile_version_id"]:
                    raise DomainRuleError("PROFILE_BRANCH_UNCHANGED", "Profile branch 必须使用不同的 ProfileVersion")
            if plan.variant_type == "RESUBMIT_PROVIDER_RANDOM":
                if parent is None:
                    raise DomainRuleError("PROVIDER_RANDOM_PARENT_REQUIRED", "Provider random 重提必须引用父 Variant")
                parent_snapshot = {
                    "prompt_revision_id": parent["prompt_revision_id"],
                    "profile_version_id": parent["capability_profile_version_id"],
                    "parameter_set": json.loads(parent["parameter_set_json"]),
                    "bindings": sorted((row["role"], row["media_version_id"], row["ordinal"], row["weight"]) for row in parent_bindings),
                }
                branch_snapshot = {
                    "prompt_revision_id": plan.prompt_revision_id,
                    "profile_version_id": plan.profile_version_id,
                    "parameter_set": plan.parameter_set,
                    "bindings": sorted((item.role, item.media_version_id, item.ordinal, item.weight) for item in plan.bindings),
                }
                if branch_snapshot != parent_snapshot:
                    raise DomainRuleError("PROVIDER_RANDOM_SCOPE_INVALID", "Provider random 重提只能改变 seed policy 与随机 nonce")
                if plan.provider_random_nonce == parent["provider_random_nonce"]:
                    raise DomainRuleError("PROVIDER_RANDOM_NONCE_UNCHANGED", "Provider random 重提必须冻结新的随机 nonce")
        dependencies = {
            "intent_id": intent_id,
            "intent_updated_at": intent["updated_at"],
            "profile_version_id": plan.profile_version_id,
            "profile_revision": profile["revision"],
            "profile_status": profile["status"],
            "seed_support": seed_support,
            "determinism": determinism,
            "workflow_version_id": profile["workflow_version_id"],
            "workflow_content_hash": workflow["content_hash"],
            "workflow_revision": workflow["revision"],
            "model_bundle": model_bundle,
            "model_bundle_hash": _digest(model_bundle),
            "runtime_version_id": profile["runtime_version_id"],
            "profile_manifest_sha256": profile["manifest_sha256"],
            "profile_execution_snapshot_hash": _digest(
                {
                    "profile_version_id": plan.profile_version_id,
                    "profile_revision": profile["revision"],
                    "runtime_version_id": profile["runtime_version_id"],
                    "workflow_version_id": profile["workflow_version_id"],
                    "workflow_content_hash": workflow["content_hash"],
                    "model_bundle": model_bundle,
                    "manifest_sha256": profile["manifest_sha256"],
                }
            ),
            "parent_recipe_hash": parent["recipe_hash"] if parent is not None else None,
            "prompt_revision_hash": prompt_revision["content_hash"] if prompt_revision is not None else None,
            "media": sorted(media_dependencies, key=lambda item: str(item["id"])),
            "approvals": [
                {"role": role, "ordinal": ordinal, "source_approval_id": approval_id}
                for (role, ordinal), approval_id in sorted(approval_dependencies.items())
            ],
        }
        return ancestors, allowed_roles, dependencies

    @staticmethod
    def _binding_dict(binding: VariantInput) -> dict[str, Any]:
        result: dict[str, Any] = {
            "role": binding.role,
            "media_version_id": binding.media_version_id,
            "ordinal": binding.ordinal,
        }
        if binding.weight is not None:
            result["weight"] = binding.weight
        return result

    @staticmethod
    def _recipe(plan: VariantPlan) -> dict[str, Any]:
        return {
            "variant_type": plan.variant_type,
            "parent_variant_id": plan.parent_variant_id,
            "branch_reason": plan.branch_reason,
            "prompt_revision_id": plan.prompt_revision_id,
            "profile_version_id": plan.profile_version_id,
            "parameter_set": plan.parameter_set,
            "seed_policy": plan.seed_policy,
            "explicit_seed": plan.explicit_seed,
            "provider_random_nonce": plan.provider_random_nonce,
            "bindings": [GenerationService._binding_dict(binding) for binding in plan.bindings],
        }

    @staticmethod
    def _execution_recipe(plan: VariantPlan) -> dict[str, Any]:
        return {
            "prompt_revision_id": plan.prompt_revision_id,
            "profile_version_id": plan.profile_version_id,
            "parameter_set": plan.parameter_set,
            "seed_policy": plan.seed_policy,
            "explicit_seed": plan.explicit_seed,
            "provider_random_nonce": plan.provider_random_nonce,
            "bindings": [GenerationService._binding_dict(binding) for binding in plan.bindings],
        }

    def preflight_variant(self, intent_id: str, plan: VariantPlan) -> dict[str, Any]:
        _ancestors, _allowed_roles, dependencies = self._plan_context(intent_id, plan)
        recipe = self._recipe(plan)
        execution_recipe = self._execution_recipe(plan)
        evidence_recipe = {"execution": execution_recipe, "approvals": dependencies.get("approvals", [])}
        return {
            "intent_id": intent_id,
            "status": "READY",
            "plan_hash": _digest({"intent_id": intent_id, "recipe": recipe, "dependencies": dependencies}),
            "recipe_hash": _digest(evidence_recipe),
            "dependencies": dependencies,
            "would_persist_variant": False,
            "would_create_job": False,
            "reproducibility": {
                "level": "NON_REPRODUCIBLE" if plan.seed_policy == "PROVIDER_RANDOM" else dependencies["determinism"],
                "determinism": dependencies["determinism"],
                "seed_support": dependencies["seed_support"],
                "claim": "不保证重复结果" if plan.seed_policy == "PROVIDER_RANDOM" else "遵循 Profile determinism 声明",
            },
        }

    def derive_variant_plan(
        self,
        parent_variant_id: str,
        operation: str,
        *,
        explicit_seed: int | None = None,
        prompt_revision_id: str | None = None,
        first_frame_media_version_id: str | None = None,
        profile_version_id: str | None = None,
        branch_reason: str,
    ) -> dict[str, Any]:
        parent = self.get_variant(parent_variant_id)
        if int(parent["is_stale"]):
            raise DomainRuleError(
                "VARIANT_PARENT_STALE",
                "已失效 GenerationVariant 不能继续派生新生产计划",
                {"variant_id": parent_variant_id, "stale_reason": parent["stale_reason"]},
            )
        if operation not in {"RESAMPLE_NEW_SEED", "RESUBMIT_PROVIDER_RANDOM", "EXACT_REPLAY", "PROMPT_BRANCH", "SOURCE_IMAGE_BRANCH", "PROFILE_BRANCH"}:
            raise DomainRuleError("VARIANT_DERIVATION_UNSUPPORTED", "派生操作不受支持")
        parent_seed = parent.get("explicit_seed")
        target_seed: int | None
        if operation == "RESAMPLE_NEW_SEED":
            if explicit_seed is None:
                raise DomainRuleError("SEED_REQUIRED", "换 seed 重抽必须显式提供新 seed")
            if parent["seed_policy"] != "EXPLICIT":
                raise DomainRuleError("RESAMPLE_SEED_UNSUPPORTED", "父 Variant 未冻结显式 seed，不能声称只改变 seed")
            if explicit_seed == parent_seed:
                raise DomainRuleError("RESAMPLE_SEED_UNCHANGED", "换 seed 重抽必须使用不同 seed")
            target_seed = explicit_seed
        elif operation == "RESUBMIT_PROVIDER_RANDOM":
            if explicit_seed is not None:
                raise DomainRuleError("VARIANT_DERIVATION_SCOPE_INVALID", "Provider random 重提不能携带 explicit seed")
            target_seed = None
        elif operation == "EXACT_REPLAY":
            if explicit_seed is not None and explicit_seed != parent_seed:
                raise DomainRuleError("EXACT_REPLAY_SNAPSHOT_MISMATCH", "Exact replay 不能覆盖父 Variant 的 seed")
            target_seed = int(parent_seed) if parent_seed is not None else None
        else:
            if explicit_seed is not None:
                raise DomainRuleError("VARIANT_DERIVATION_SCOPE_INVALID", "分支操作不能覆盖 seed")
            target_seed = int(parent_seed) if parent_seed is not None else None
        parent_bindings = tuple(
            VariantInput(str(item["role"]), str(item["media_version_id"]), int(item["ordinal"]), float(item["weight"]) if item.get("weight") is not None else None)
            for item in parent["bindings"]
        )
        target_prompt_revision_id = str(parent["prompt_revision_id"]) if parent.get("prompt_revision_id") else None
        parent_profile_version_id = str(parent["capability_profile_version_id"])
        target_profile_version_id = parent_profile_version_id
        bindings = parent_bindings
        if operation == "PROMPT_BRANCH":
            if not prompt_revision_id:
                raise DomainRuleError("PROMPT_BRANCH_REVISION_REQUIRED", "Prompt branch 必须提供新的冻结 PromptRevision")
            if first_frame_media_version_id is not None:
                raise DomainRuleError("VARIANT_DERIVATION_SCOPE_INVALID", "Prompt branch 不能覆盖首帧")
            target_prompt_revision_id = prompt_revision_id
        elif operation == "SOURCE_IMAGE_BRANCH":
            if not first_frame_media_version_id:
                raise DomainRuleError("SOURCE_BRANCH_MEDIA_REQUIRED", "Source branch 必须提供新的 FIRST_FRAME MediaVersion")
            if prompt_revision_id is not None:
                raise DomainRuleError("VARIANT_DERIVATION_SCOPE_INVALID", "Source branch 不能覆盖 PromptRevision")
            if not any(item.role == "FIRST_FRAME" and item.ordinal == 0 for item in parent_bindings):
                raise DomainRuleError("SOURCE_BRANCH_FIRST_FRAME_REQUIRED", "父 Variant 没有 FIRST_FRAME ordinal 0")
            bindings = tuple(
                VariantInput(item.role, first_frame_media_version_id, item.ordinal, item.weight)
                if item.role == "FIRST_FRAME" and item.ordinal == 0
                else item
                for item in parent_bindings
            )
        elif operation == "PROFILE_BRANCH":
            if not profile_version_id:
                raise DomainRuleError("PROFILE_BRANCH_VERSION_REQUIRED", "Profile branch 必须提供目标 Published ProfileVersion")
            if prompt_revision_id is not None or first_frame_media_version_id is not None:
                raise DomainRuleError("VARIANT_DERIVATION_SCOPE_INVALID", "Profile branch 只能改变 ProfileVersion")
            if profile_version_id == parent_profile_version_id:
                raise DomainRuleError("PROFILE_BRANCH_UNCHANGED", "Profile branch 必须使用不同的 ProfileVersion")
            target_profile_version_id = profile_version_id
        elif prompt_revision_id is not None or first_frame_media_version_id is not None:
            raise DomainRuleError("VARIANT_DERIVATION_SCOPE_INVALID", "Replay/Resample 不能覆盖 Prompt 或首帧")
        if operation != "PROFILE_BRANCH" and profile_version_id is not None:
            raise DomainRuleError("VARIANT_DERIVATION_SCOPE_INVALID", "只有 Profile branch 可以覆盖 ProfileVersion")
        parameter_set = json.loads(str(parent["parameter_set_json"]))
        if operation in {"RESAMPLE_NEW_SEED", "EXACT_REPLAY"} and "SEED" in parameter_set:
            parameter_set["SEED"] = target_seed
        plan = VariantPlan(
            variant_type=operation,
            parent_variant_id=parent_variant_id,
            branch_reason=branch_reason,
            prompt_revision_id=target_prompt_revision_id,
            profile_version_id=target_profile_version_id,
            parameter_set=parameter_set,
            seed_policy="PROVIDER_RANDOM" if operation == "RESUBMIT_PROVIDER_RANDOM" else str(parent["seed_policy"]),
            explicit_seed=target_seed,
            bindings=bindings,
            provider_random_nonce=str(uuid.uuid4()) if operation == "RESUBMIT_PROVIDER_RANDOM" else parent.get("provider_random_nonce"),
        )
        preflight = self.preflight_variant(str(parent["intent_id"]), plan)
        if operation == "EXACT_REPLAY":
            # A submitted parent carries the authority snapshot in its Job.
            # Refuse to silently replay against changed local model/workflow
            # bytes; callers must publish a new Profile and make an explicit
            # creative branch instead.
            with self.database.connect() as connection:
                job_snapshot = connection.execute(
                    "SELECT input_snapshot_json FROM jobs WHERE subject_type='GENERATION_VARIANT' AND subject_id=? ORDER BY created_at, id LIMIT 1",
                    (parent_variant_id,),
                ).fetchone()
            if job_snapshot is not None:
                try:
                    snapshot = json.loads(str(job_snapshot["input_snapshot_json"] or "{}"))
                    frozen_hash = snapshot.get("execution_snapshot", {}).get("snapshot_hash")
                except (TypeError, json.JSONDecodeError):
                    frozen_hash = None
                current_hash = preflight["dependencies"].get("profile_execution_snapshot_hash")
                if frozen_hash and frozen_hash != current_hash:
                    raise DomainRuleError(
                        "EXACT_REPLAY_EXECUTION_SNAPSHOT_MISMATCH",
                        "Exact replay 的 workflow/model 快照已变化，不能复用旧 Variant 的执行权威",
                        {"frozen_snapshot_hash": frozen_hash, "current_snapshot_hash": current_hash},
                        suggested_action="重新发布匹配的本地 Profile/Workflow，或选择新的 Profile branch",
                    )
        draft = self._recipe(plan)
        draft["intent_id"] = str(parent["intent_id"])
        changed_fields = {
            "RESAMPLE_NEW_SEED": ["explicit_seed"] + (["parameter_set.SEED"] if "SEED" in parameter_set else []),
            "RESUBMIT_PROVIDER_RANDOM": ["seed_policy", "explicit_seed", "provider_random_nonce"],
            "EXACT_REPLAY": [],
            "PROMPT_BRANCH": ["prompt_revision_id"],
            "SOURCE_IMAGE_BRANCH": ["bindings.FIRST_FRAME[0]"],
            "PROFILE_BRANCH": ["profile_version_id"],
        }[operation]
        before: dict[str, Any] = {"explicit_seed": parent_seed}
        after: dict[str, Any] = {"explicit_seed": target_seed}
        if operation == "RESUBMIT_PROVIDER_RANDOM":
            before = {
                "seed_policy": parent["seed_policy"],
                "explicit_seed": parent_seed,
                "provider_random_nonce": parent.get("provider_random_nonce"),
            }
            after = {
                "seed_policy": plan.seed_policy,
                "explicit_seed": target_seed,
                "provider_random_nonce": plan.provider_random_nonce,
            }
        if operation == "PROMPT_BRANCH":
            before = {"prompt_revision_id": parent.get("prompt_revision_id")}
            after = {"prompt_revision_id": target_prompt_revision_id}
        elif operation == "SOURCE_IMAGE_BRANCH":
            before = {
                "first_frame_media_version_id": next(
                    item.media_version_id for item in parent_bindings if item.role == "FIRST_FRAME" and item.ordinal == 0
                )
            }
            after = {"first_frame_media_version_id": first_frame_media_version_id}
        elif operation == "PROFILE_BRANCH":
            before = {"profile_version_id": parent_profile_version_id}
            after = {"profile_version_id": target_profile_version_id}
        preserved = [
            "prompt_revision_id",
            "profile_version_id",
            "parameter_set",
            "seed_policy",
            "explicit_seed",
            "bindings",
            "provider_random_nonce",
        ]
        if operation == "RESAMPLE_NEW_SEED":
            preserved.remove("explicit_seed")
        elif operation == "RESUBMIT_PROVIDER_RANDOM":
            preserved.remove("seed_policy")
            preserved.remove("explicit_seed")
            preserved.remove("provider_random_nonce")
        elif operation == "PROMPT_BRANCH":
            preserved.remove("prompt_revision_id")
        elif operation == "SOURCE_IMAGE_BRANCH":
            preserved.remove("bindings")
        elif operation == "PROFILE_BRANCH":
            preserved.remove("profile_version_id")
        return {
            **preflight,
            "operation": operation,
            "parent_variant_id": parent_variant_id,
            "draft": draft,
            "diff": {
                "changed_fields": changed_fields,
                "preserved_fields": preserved,
                "before": before,
                "after": after,
            },
        }

    def derive_seed_batch(self, parent_variant_id: str, seeds: list[int], branch_reason: str) -> dict[str, Any]:
        if not seeds or len(seeds) > 24:
            raise DomainRuleError("RESAMPLE_BATCH_SIZE_INVALID", "seed batch 必须包含 1—24 个 seed")
        if len(set(seeds)) != len(seeds):
            raise DomainRuleError("RESAMPLE_SEED_DUPLICATE", "seed batch 不能包含重复 seed")
        plans = [
            self.derive_variant_plan(
                parent_variant_id,
                "RESAMPLE_NEW_SEED",
                explicit_seed=seed,
                branch_reason=branch_reason,
            )
            for seed in seeds
        ]
        return {
            "parent_variant_id": parent_variant_id,
            "count": len(plans),
            "plans": plans,
            "would_persist_variants": False,
            "would_create_jobs": False,
        }

    def create_confirmed_variant(self, intent_id: str, plan: VariantPlan, plan_hash: str) -> dict[str, Any]:
        preflight = self.preflight_variant(intent_id, plan)
        if not hmac.compare_digest(str(preflight["plan_hash"]), plan_hash):
            raise DomainRuleError(
                "VARIANT_PLAN_STALE", "Variant 计划已变化，请重新执行 preflight", {"current_plan_hash": preflight["plan_hash"]}
            )
        return self.create_variant(intent_id, plan)

    def submit_confirmed_variant(
        self, intent_id: str, plan: VariantPlan, plan_hash: str, idempotency_key: str
    ) -> dict[str, Any]:
        preflight = self.preflight_variant(intent_id, plan)
        if not hmac.compare_digest(str(preflight["plan_hash"]), plan_hash):
            raise DomainRuleError("VARIANT_PLAN_STALE", "Variant 计划已变化，请重新执行 preflight")
        variant_id = str(uuid.uuid4())
        ancestors, allowed_roles, dependencies = self._plan_context(intent_id, plan)
        plan.validate(variant_id=variant_id, ancestors=ancestors, allowed_roles=allowed_roles)
        now = _now()
        recipe = self._execution_recipe(plan)
        workflow_version_id = str(dependencies["workflow_version_id"])
        semantic_inputs = dict(plan.parameter_set)
        approval_by_slot = {
            (str(item["role"]), int(item["ordinal"])): str(item["source_approval_id"])
            for item in dependencies.get("approvals", [])
        }
        media_bindings_snapshot = [
            {**self._binding_dict(binding), "source_approval_id": approval_by_slot.get((binding.role, binding.ordinal))}
            for binding in plan.bindings
        ]
        recipe_hash = _digest({"execution": recipe, "approvals": dependencies.get("approvals", [])})
        with self.database.transaction() as connection:
            intent = connection.execute("SELECT * FROM generation_intents WHERE id=?", (intent_id,)).fetchone()
            if intent is None:
                raise DomainRuleError("GENERATION_INTENT_NOT_FOUND", "GenerationIntent 不存在")
            next_no = int(
                connection.execute(
                    "SELECT COALESCE(MAX(variant_no),0)+1 AS n FROM generation_variants WHERE intent_id=?", (intent_id,)
                ).fetchone()["n"]
            )
            connection.execute(
                """INSERT INTO generation_variants
                (id, intent_id, variant_no, variant_type, parent_variant_id, branch_reason, prompt_revision_id,
                capability_profile_version_id, parameter_set_json, seed_policy, explicit_seed, provider_random_nonce,
                input_fingerprint, recipe_hash, status, created_at, updated_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, 'local-user')""",
                (
                    variant_id, intent_id, next_no, plan.variant_type, plan.parent_variant_id, plan.branch_reason,
                    plan.prompt_revision_id, plan.profile_version_id, _canonical(plan.parameter_set), plan.seed_policy,
                    plan.explicit_seed, plan.provider_random_nonce, _digest(media_bindings_snapshot),
                    recipe_hash, now, now,
                ),
            )
            for binding in plan.bindings:
                connection.execute(
                    """INSERT INTO variant_input_bindings
                    (id, variant_id, role, media_version_id, ordinal, weight, source_approval_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()), variant_id, binding.role, binding.media_version_id, binding.ordinal,
                        binding.weight, approval_by_slot.get((binding.role, binding.ordinal)),
                    ),
                )
            job = JobService(self.database).create_job_in_transaction(
                connection,
                str(intent["project_id"]),
                "GENERATION_VARIANT",
                "GENERATION_VARIANT",
                variant_id,
                "GPU_H3",
                {
                    "variant_id": variant_id,
                    "workflow_version_id": workflow_version_id,
                    "execution_snapshot": {
                        "profile_version_id": plan.profile_version_id,
                        "profile_revision": dependencies["profile_revision"],
                        "runtime_version_id": dependencies["runtime_version_id"],
                        "workflow_version_id": workflow_version_id,
                        "workflow_content_hash": dependencies["workflow_content_hash"],
                        "model_bundle": dependencies["model_bundle"],
                        "model_bundle_hash": dependencies["model_bundle_hash"],
                        "manifest_sha256": dependencies["profile_manifest_sha256"],
                        "snapshot_hash": dependencies["profile_execution_snapshot_hash"],
                    },
                    "semantic_inputs": semantic_inputs,
                    "media_bindings": media_bindings_snapshot,
                    "recipe_hash": recipe_hash,
                },
                idempotency_key,
                execution_profile_version_id=plan.profile_version_id,
                max_attempts=1,
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES ('local-user', 'producer', 'GENERATION_VARIANT_SUBMITTED', 'generation_variant', ?, ?, ?)""",
                (variant_id, "原子创建 GenerationVariant 与 GPU_H3 Job", _canonical({"job_id": job["id"], "recipe_hash": recipe_hash})),
            )
        return {"variant": self.get_variant(variant_id), "job": job}

    def create_variant(self, intent_id: str, plan: VariantPlan) -> dict[str, Any]:
        variant_id = str(uuid.uuid4())
        ancestors, allowed_roles, dependencies = self._plan_context(intent_id, plan)
        plan.validate(variant_id=variant_id, ancestors=ancestors, allowed_roles=allowed_roles)
        approval_by_slot = {
            (str(item["role"]), int(item["ordinal"])): str(item["source_approval_id"])
            for item in dependencies.get("approvals", [])
        }
        now = _now()
        execution_recipe = self._execution_recipe(plan)
        binding_snapshots = [
            {**self._binding_dict(binding), "source_approval_id": approval_by_slot.get((binding.role, binding.ordinal))}
            for binding in plan.bindings
        ]
        recipe_hash = _digest({"execution": execution_recipe, "approvals": dependencies.get("approvals", [])})
        with self.database.transaction() as connection:
            intent = connection.execute("SELECT * FROM generation_intents WHERE id = ?", (intent_id,)).fetchone()
            if intent is None:
                raise DomainRuleError("GENERATION_INTENT_NOT_FOUND", "GenerationIntent 不存在", {"intent_id": intent_id})
            profile = connection.execute("SELECT id FROM execution_profile_versions WHERE id = ?", (plan.profile_version_id,)).fetchone()
            if profile is None:
                raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
            next_no = connection.execute("SELECT COALESCE(MAX(variant_no), 0) + 1 FROM generation_variants WHERE intent_id = ?", (intent_id,)).fetchone()[0]
            connection.execute(
                """INSERT INTO generation_variants (id, intent_id, variant_no, variant_type, parent_variant_id, branch_reason,
                prompt_revision_id, capability_profile_version_id, parameter_set_json, seed_policy, explicit_seed, provider_random_nonce,
                input_fingerprint, recipe_hash, status, created_at, updated_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PLANNED', ?, ?, 'local-user')""",
                (
                    variant_id,
                    intent_id,
                    next_no,
                    plan.variant_type,
                    plan.parent_variant_id,
                    plan.branch_reason,
                    plan.prompt_revision_id,
                    plan.profile_version_id,
                    _canonical(plan.parameter_set),
                    plan.seed_policy,
                    plan.explicit_seed,
                    plan.provider_random_nonce,
                    _digest(binding_snapshots),
                    recipe_hash,
                    now,
                    now,
                ),
            )
            for binding in plan.bindings:
                connection.execute(
                    """INSERT INTO variant_input_bindings (id, variant_id, role, media_version_id, ordinal, weight,
                    frame_time_us, source_approval_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        variant_id,
                        binding.role,
                        binding.media_version_id,
                        binding.ordinal,
                        binding.weight,
                        None,
                        approval_by_slot.get((binding.role, binding.ordinal)),
                    ),
                )
            connection.execute(
                """INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES ('local-user', 'producer', 'GENERATION_VARIANT_CREATED', 'generation_variant', ?, ?, ?)""",
                (variant_id, f"创建 {plan.variant_type} variant", _canonical({"intent_id": intent_id, "recipe_hash": recipe_hash})),
            )
        return self.get_variant(variant_id)

    def get_variant(self, variant_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM generation_variants WHERE id = ?", (variant_id,)).fetchone()
            bindings = connection.execute("SELECT * FROM variant_input_bindings WHERE variant_id = ? ORDER BY ordinal, id", (variant_id,)).fetchall()
        if row is None:
            raise DomainRuleError("GENERATION_VARIANT_NOT_FOUND", "GenerationVariant 不存在", {"variant_id": variant_id})
        result = dict(row)
        result["bindings"] = [dict(binding) for binding in bindings]
        return result

    def lineage(self, variant_id: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        current = variant_id
        seen: set[str] = set()
        while current:
            if current in seen:
                raise DomainRuleError("VARIANT_LINEAGE_CYCLE", "GenerationVariant 谱系不能形成环")
            seen.add(current)
            variant = self.get_variant(current)
            result.append(variant)
            current = str(variant["parent_variant_id"]) if variant.get("parent_variant_id") else ""
        result.reverse()
        return result
