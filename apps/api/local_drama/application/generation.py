from __future__ import annotations

import hashlib
import hmac
import json
import math
import shutil
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.generation_contracts import CameraPlan, MotionMask, PerformanceBinding, TimedDirection, resolve_camera_plan
from local_drama.domain.generation_planning import effective_configuration_snapshot, frozen_generation_contract
from local_drama.domain.policies import VariantInput
from local_drama.infrastructure.database.sqlite import Database

from .character_identity_packs import CharacterIdentityPackService
from .effective_configuration import EffectiveConfigurationService
from .jobs import JobService
from .media import MediaService
from .prompt_anchors import character_anchor_line, character_anchor_rows
from .queries.generation_style_context import build_generation_style_context


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


# Keys that pre-slot manifests attached to input contracts as transport/runtime
# metadata.  They are never semantic slots, so the legacy direct-slot fallback
# must skip them instead of failing contract validation on old Profiles.
_LEGACY_INPUT_CONTRACT_METADATA = frozenset(
    {
        "transport",
        "local_only",
        "requires_explicit_validation",
        "required_inputs",
        "required_nodes",
        "provider_kind",
    }
)


def _character_anchor_lines(connection: Any, intent_row: Any) -> list[str]:
    """Build the character appearance anchor lines for a SHOT-owned intent.

    Pure function over the *current* DB binding state: one line per ACTIVE
    CHARACTER story asset bound to the intent's shot, ordered by
    ``role_in_shot`` then ``name``. Returns ``[]`` when the intent is not
    SHOT-owned, has no owner id, or has no bound characters — in which case the
    caller leaves the prompt and job snapshot untouched.
    """
    if str(intent_row["owner_type"]) != "SHOT" or not intent_row["owner_id"]:
        return []
    rows = character_anchor_rows(connection, str(intent_row["owner_id"]))
    return [character_anchor_line(str(item["name"]), str(item.get("description") or ""), item.get("canonical_media_version_id") or None) for item in rows]


class GenerationService:
    _RESOURCE_ESTIMATE_FIELDS: dict[str, tuple[str, ...]] = {
        # Resource estimates are intentionally opt-in profile declarations.
        # Never derive these values from a model name, a fixed default, or the
        # host's current capacity: those would be misleading before execution.
        "duration_seconds": (
            "estimated_duration_seconds_per_take",
            "duration_seconds_per_take",
            "estimated_time_seconds_per_take",
            "time_seconds_per_take",
        ),
        "vram_bytes": (
            "estimated_vram_bytes_per_take",
            "vram_bytes_per_take",
            "estimated_gpu_memory_bytes_per_take",
            "gpu_memory_bytes_per_take",
        ),
        "disk_bytes": (
            "estimated_disk_bytes_per_take",
            "disk_bytes_per_take",
            "estimated_output_bytes_per_take",
            "output_bytes_per_take",
        ),
    }

    def _retarget_camera_plan(self, parameter_set: dict[str, Any], profile_version_id: str) -> bool:
        """Re-adjudicate a frozen camera intent when a Profile branch changes model.

        The creative camera semantics remain unchanged. Only the capability-derived
        mode/prompt and the immutable ProfileVersion authority may change.
        """
        payload = parameter_set.get("camera_plan")
        if payload is None:
            return False
        camera_plan = CameraPlan.from_payload(payload)
        if camera_plan.profile_version_id == profile_version_id:
            return False
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status,parameter_schema_json FROM execution_profile_versions WHERE id=?",
                (profile_version_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "目标 ProfileVersion 不存在")
        if str(row["status"]) != "PUBLISHED":
            raise DomainRuleError("PROFILE_NOT_PUBLISHED", "Profile branch 只能使用已发布 ProfileVersion")
        try:
            parameter_schema = json.loads(str(row["parameter_schema_json"] or "{}"))
        except (TypeError, json.JSONDecodeError) as error:
            raise DomainRuleError("PROFILE_PARAMETER_SCHEMA_INVALID", "目标 Profile 参数 Schema 无效") from error
        capabilities = parameter_schema.get("capabilities", {}) if isinstance(parameter_schema, dict) else {}
        camera_contract = capabilities.get("camera", {}) if isinstance(capabilities, dict) else {}
        support = str(camera_contract.get("support", "UNSUPPORTED")) if isinstance(camera_contract, dict) else "UNSUPPORTED"
        if support not in {"NATIVE", "PROMPT_FALLBACK", "UNSUPPORTED"}:
            raise DomainRuleError("PROFILE_CAMERA_CONTRACT_INVALID", "目标 Profile camera capability support 无效")
        fallback = support == "PROMPT_FALLBACK" and camera_contract.get("prompt_fallback") is True
        if support == "PROMPT_FALLBACK" and not fallback:
            raise DomainRuleError("PROFILE_CAMERA_FALLBACK_INVALID", "目标 Profile 的 Camera prompt fallback 未显式声明")
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
            raise DomainRuleError("CAMERA_PLAN_UNSUPPORTED", "目标 Profile 不支持父候选的结构化运镜，不能创建模型分支")
        parameter_set["camera_plan"] = resolved.to_dict()
        return True

    @classmethod
    def _resource_estimate(cls, resource_policy: dict[str, Any]) -> dict[str, Any]:
        """Return only explicitly declared, per-take resource estimates.

        The profile is the sole authority for these numbers.  Unknown values
        stay ``None`` and are surfaced as unknown to the client rather than
        being guessed from a static model/runtime default.
        """

        per_take: dict[str, float | int | None] = {}
        policy_keys: dict[str, str | None] = {}
        unknown: list[str] = []
        for field, aliases in cls._RESOURCE_ESTIMATE_FIELDS.items():
            value: float | int | None = None
            source_key: str | None = None
            for key in aliases:
                candidate = resource_policy.get(key)
                # bool is an int subclass but is not a meaningful estimate.
                if isinstance(candidate, (int, float)) and not isinstance(candidate, bool) and math.isfinite(float(candidate)) and float(candidate) >= 0:
                    value = candidate
                    source_key = key
                    break
            per_take[field] = value
            policy_keys[field] = source_key
            if value is None:
                unknown.append(field)
        status = "DECLARED" if not unknown else "PARTIAL" if len(unknown) < len(per_take) else "UNKNOWN"
        return {
            "status": status,
            "source": "PROFILE_RESOURCE_POLICY",
            "per_take": per_take,
            "policy_keys": policy_keys,
            "unknown": unknown,
            "take_count": 1,
            "bounded": True,
        }

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
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
        # Advanced inputs are still semantic slots.  Their workflow node is
        # profile-owned, but a profile must explicitly advertise the local
        # capability before a plan can bind one.  This prevents a V2V/
        # extension/motion request from being silently treated as a generic
        # image or prompt input.
        "SOURCE_VIDEO": ("source_video", "video_to_video", "v2v", "video_extend"),
        "MOTION_PATH": ("motion_path", "motion", "motion_control"),
        "MASK": ("mask", "inpaint", "outpaint", "motion_mask"),
    }

    _VARIANT_CAPABILITIES: dict[str, tuple[str, ...]] = {
        "VIDEO_EXTEND": ("video_extend", "video_extension", "extend"),
        "VIDEO_TO_VIDEO": ("video_to_video", "v2v", "video_transform"),
        "MOTION_CONTROL": ("motion_control", "motion", "motion_path"),
        "PERFORMANCE_DRIVEN": ("performance", "character_driving", "driving_video"),
    }

    @classmethod
    def _validate_variant_capability(cls, variant_type: str, capabilities: dict[str, Any], roles: set[str]) -> None:
        """Gate advanced variant modes on an explicit Published Profile capability.

        The capabilities are intentionally aliases: existing local manifests
        use both ``v2v`` and ``video_to_video`` naming.  Any declared object
        with ``enabled=false`` or ``support=UNSUPPORTED`` is treated as
        unavailable, and the error includes the required semantic role so the
        UI can offer an actionable configuration path.
        """
        required = cls._VARIANT_CAPABILITIES.get(variant_type)
        if not required:
            return
        matched_name = next((name for name in required if isinstance(capabilities.get(name), dict)), None)
        contract = capabilities.get(matched_name) if matched_name else None
        if not isinstance(contract, dict) or contract.get("enabled") is False or str(contract.get("support", "NATIVE")).upper() == "UNSUPPORTED":
            raise DomainRuleError(
                "PROFILE_VARIANT_UNSUPPORTED",
                f"当前 Published Profile 未声明 {variant_type} 的本地能力",
                {
                    "variant_type": variant_type,
                    "required_capabilities": list(required),
                    "bound_roles": sorted(roles),
                },
                suggested_action="配置并发布声明该高阶输入能力和语义 input_slots 的本地 Profile",
            )
        role_requirements = {
            "VIDEO_EXTEND": {"SOURCE_VIDEO"},
            "VIDEO_TO_VIDEO": {"SOURCE_VIDEO"},
            "MOTION_CONTROL": {"MOTION_PATH", "MASK"},
            "PERFORMANCE_DRIVEN": {
                "DRIVING_VIDEO",
                "POSE_SEQUENCE",
                "POSE_REFERENCE",
                "AUDIO_GUIDE",
                "FACE_REFERENCE",
                "CHARACTER_REFERENCE",
                "CHARACTER_DRIVING",
            },
        }
        if not roles.intersection(role_requirements.get(variant_type, set())):
            raise DomainRuleError(
                "VARIANT_INPUT_ROLE_REQUIRED",
                f"{variant_type} 必须绑定对应的语义输入槽",
                {"variant_type": variant_type, "required_roles": sorted(role_requirements.get(variant_type, set()))},
            )

    @staticmethod
    def _input_slots(input_contract: object) -> dict[str, Any]:
        """Return the frozen semantic input-slot contract.

        The blueprint has used both the current ``{"input_slots": {...}}``
        envelope and the older direct-slot form in fixtures.  Supporting both
        here keeps old local Profiles readable while still validating every
        slot instead of dropping an unknown shape.  Pre-slot manifests also
        attached non-slot metadata (e.g. ``required_inputs`` node lists next
        to ``transport``); those keys are metadata, never slots.
        """
        if not isinstance(input_contract, dict):
            raise DomainRuleError("PROFILE_INPUT_CONTRACT_INVALID", "Profile input contract 必须是对象")
        slots = input_contract.get("input_slots")
        if slots is None:
            slots = {str(key): value for key, value in input_contract.items() if str(key) not in _LEGACY_INPUT_CONTRACT_METADATA}
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

    def create_shot_intent(
        self,
        shot_id: str,
        *,
        purpose: str,
        creative_goal: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Create a Shot-owned intent as an audited, idempotent v2 command."""
        if purpose not in {"T2I", "I2V_PROXY", "T2V", "R2V"}:
            raise DomainRuleError("SHOT_GENERATION_PURPOSE_INVALID", "Shot 生成目标不受支持")
        normalized_goal = creative_goal.strip()
        if not normalized_goal:
            raise DomainRuleError("SHOT_GENERATION_GOAL_REQUIRED", "Shot 生成目标不能为空")
        payload_hash = _digest({"purpose": purpose, "creative_goal": normalized_goal})
        command_scope = f"shot-generation-intent:create:{shot_id}"
        now = _now()
        with self.database.transaction() as connection:
            shot = connection.execute(
                """SELECT sh.id,se.project_id FROM shots sh JOIN episodes e ON e.id=sh.episode_id
                JOIN seasons se ON se.id=e.season_id WHERE sh.id=? AND sh.archived_at IS NULL""",
                (shot_id,),
            ).fetchone()
            if shot is None:
                raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
            existing = connection.execute(
                """SELECT payload_hash,response_json FROM command_idempotencies
                WHERE scope=? AND idempotency_key=?""",
                (command_scope, idempotency_key),
            ).fetchone()
            if existing is not None:
                if not hmac.compare_digest(str(existing["payload_hash"]), payload_hash):
                    raise DomainRuleError(
                        "IDEMPOTENCY_PAYLOAD_MISMATCH",
                        "相同 Idempotency-Key 已用于不同 Shot 生成目标",
                    )
                replay = json.loads(str(existing["response_json"]))
                replay["idempotent_replay"] = True
                return replay
            intent_id = str(uuid.uuid4())
            project_id = str(shot["project_id"])
            connection.execute(
                """INSERT INTO generation_intents
                (id,project_id,owner_type,owner_id,purpose,creative_goal,status,created_at,updated_at,created_by)
                VALUES (?,?,'SHOT',?,?,?,'DRAFT',?,?,'local-user')""",
                (intent_id, project_id, shot_id, purpose, normalized_goal, now, now),
            )
            result = {
                "id": intent_id,
                "project_id": project_id,
                "owner_type": "SHOT",
                "owner_id": shot_id,
                "purpose": purpose,
                "creative_goal": normalized_goal,
                "status": "DRAFT",
                "created_at": now,
                "idempotent_replay": False,
            }
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES ('local-user','producer','SHOT_GENERATION_INTENT_CREATED','generation_intent',?,?,?)""",
                (
                    intent_id,
                    "建立镜头生成目标",
                    _canonical({"shot_id": shot_id, "purpose": purpose}),
                ),
            )
            connection.execute(
                """INSERT INTO outbox_events
                (type,project_id,subject_type,subject_id,payload_json)
                VALUES ('SHOT_GENERATION_INTENT_CREATED',?,'SHOT',?,?)""",
                (
                    project_id,
                    shot_id,
                    _canonical({"intent_id": intent_id, "purpose": purpose}),
                ),
            )
            connection.execute(
                """INSERT INTO command_idempotencies
                (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)""",
                (command_scope, idempotency_key, payload_hash, _canonical(result)),
            )
        return result

    def list_intents(self, project_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            if project_id:
                rows = connection.execute("SELECT * FROM generation_intents WHERE project_id=? ORDER BY created_at, id", (project_id,)).fetchall()
            else:
                rows = connection.execute("SELECT * FROM generation_intents ORDER BY created_at, id").fetchall()
        return [dict(row) for row in rows]

    def list_variants(self, intent_id: str) -> list[dict[str, Any]]:
        self.get_intent(intent_id)
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id FROM generation_variants WHERE intent_id=? ORDER BY variant_no", (intent_id,)).fetchall()
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
                "SELECT status, content_hash, content_json, contract_json, node_bindings_json, revision FROM workflow_versions WHERE id=?",
                (profile["workflow_version_id"],),
            ).fetchone()
            if workflow is None:
                raise DomainRuleError("WORKFLOW_VERSION_NOT_FOUND", "Profile 引用的 WorkflowVersion 不存在")
            if workflow["status"] != "PUBLISHED":
                raise DomainRuleError("WORKFLOW_NOT_PUBLISHED", "生成 Profile 不能引用未发布 WorkflowVersion")
            frozen_contract = frozen_generation_contract(profile, workflow, plan)
            workflow_bindings = frozen_contract.workflow_bindings
            workflow_content = frozen_contract.workflow_content
            model_bundle = frozen_contract.model_bundle
            input_contract = frozen_contract.input_contract
            parameter_schema = frozen_contract.parameter_schema
            resource_policy = frozen_contract.resource_policy
            resource_estimate = self._resource_estimate(resource_policy)
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
            # Validate the high-level mode after per-slot driving checks so a
            # missing local driving declaration retains its precise legacy
            # PROFILE_DRIVING_UNSUPPORTED diagnostic.
            self._validate_variant_capability(plan.variant_type, capabilities, set(bindings_by_role))
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
                    """SELECT mv.version_no, mv.parent_version_id, mv.stage, mv.sha256, mv.byte_size, mv.integrity_status,
                    mv.probe_json, ma.media_kind, ma.project_id, ma.owner_type, ma.owner_id,
                    ma.purpose, ma.approved_version_id, ma.selected_version_id
                    FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id WHERE mv.id=?""",
                    (binding.media_version_id,),
                ).fetchone()
                if media is None:
                    raise DomainRuleError("MEDIA_VERSION_NOT_FOUND", "Variant 输入必须引用已注册 MediaVersion")
                grant_snapshot: dict[str, Any] | None = None
                if str(media["project_id"]) != str(intent["project_id"]):
                    # Cross-project media is legal only through an ACTIVE
                    # ProjectAssetGrant whose authorization and immutable
                    # source hash/size still match the current MediaVersion.
                    # This keeps source branches useful for shared workspace
                    # references without weakening project isolation.
                    grant = connection.execute(
                        """SELECT pag.id, pag.source_project_id, pag.target_project_id, pag.source_authorization_id,
                        pag.access_mode, pag.status, pag.source_revision, pag.source_sha256, pag.source_byte_size,
                        waa.authorization_status, waa.revision AS authorization_revision
                        FROM project_asset_grants pag
                        JOIN workspace_asset_authorizations waa ON waa.id=pag.source_authorization_id
                        WHERE pag.target_project_id=? AND pag.media_version_id=? ORDER BY pag.created_at DESC LIMIT 1""",
                        (intent["project_id"], binding.media_version_id),
                    ).fetchone()
                    if grant is None:
                        raise DomainRuleError("VARIANT_INPUT_PROJECT_MISMATCH", "Variant 输入必须属于 GenerationIntent 所在项目或通过有效跨项目授权")
                    if str(grant["status"]) != "ACTIVE" or str(grant["authorization_status"]) != "AUTHORIZED":
                        raise DomainRuleError("ASSET_GRANT_NOT_USABLE", "跨项目资产授权已撤回或不再可用", {"grant_id": grant["id"]})
                    if (
                        str(grant["source_sha256"]) != str(media["sha256"])
                        or int(grant["source_byte_size"]) != int(media["byte_size"])
                        or int(grant["source_revision"]) != int(grant["authorization_revision"])
                    ):
                        raise DomainRuleError("ASSET_GRANT_SOURCE_CHANGED", "跨项目授权源 MediaVersion 已发生变化，请重新授权", {"grant_id": grant["id"]})
                    grant_snapshot = {
                        "id": str(grant["id"]),
                        "source_project_id": str(grant["source_project_id"]),
                        "source_authorization_id": str(grant["source_authorization_id"]),
                        "access_mode": str(grant["access_mode"]),
                        "source_revision": int(grant["source_revision"]),
                    }
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
                        raise DomainRuleError("FRAME_DIMENSIONS_REQUIRED", "首尾/关键帧必须先完成可验证的宽高 probe", {"role": binding.role})
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
                        # Source branch and replay evidence must identify the
                        # immutable MediaVersion revision, not only its path
                        # hash.  Paths may be re-homed while version lineage
                        # and approval ownership remain auditable.
                        "version_no": int(media["version_no"]),
                        "parent_version_id": media["parent_version_id"],
                        "stage": media["stage"],
                        "owner_type": media["owner_type"],
                        "owner_id": media["owner_id"],
                        "purpose": media["purpose"],
                        "approved_version_id": media["approved_version_id"],
                        "selected_version_id": media["selected_version_id"],
                        "sha256": verified_media["sha256"],
                        "integrity_status": "VERIFIED",
                        "source_approval_id": approval_dependencies.get((binding.role, binding.ordinal)),
                        **({"project_asset_grant": grant_snapshot} if grant_snapshot else {}),
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
                    raise DomainRuleError("WORKFLOW_SLOT_UNSUPPORTED", "已发布 Workflow 未声明该语义输入槽", {"role": role})
                node = workflow_content.get(str(workflow_binding["node_id"]))
                if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                    raise DomainRuleError("WORKFLOW_BINDING_INVALID", "Workflow semantic binding 指向不存在或无 inputs 的节点", {"role": role})
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
                expected_parameters = json.loads(parent["parameter_set_json"])
                self._retarget_camera_plan(expected_parameters, plan.profile_version_id)
                parent_snapshot = {
                    "prompt_revision_id": parent["prompt_revision_id"],
                    "parameter_set": expected_parameters,
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
                    raise DomainRuleError(
                        "PROFILE_BRANCH_SCOPE_INVALID",
                        "Profile branch 只能改变 ProfileVersion，并允许同一 CameraPlan 语义按目标 Profile 重新裁决",
                    )
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
            director_recipe = connection.execute(
                """SELECT b.recipe_version_id,v.recipe_hash FROM project_director_recipe_bindings b
                JOIN director_recipe_versions v ON v.id=b.recipe_version_id WHERE b.project_id=?""",
                (intent["project_id"],),
            ).fetchone()
            identity_pack_snapshot = CharacterIdentityPackService.generation_snapshot_for_intent(connection, intent)
            intent_project_id = str(intent["project_id"])
            intent_owner_type = str(intent["owner_type"] or "")
            intent_owner_id = str(intent["owner_id"] or "")
            profile_capability = str(profile["capability"])
            valid_episode_id = None
            valid_shot_id = None
            if intent_owner_type == "EPISODE":
                owner = connection.execute(
                    "SELECT e.id FROM episodes e JOIN seasons s ON s.id=e.season_id WHERE e.id=? AND s.project_id=?",
                    (intent_owner_id, intent_project_id),
                ).fetchone()
                valid_episode_id = intent_owner_id if owner is not None else None
            elif intent_owner_type == "SHOT":
                owner = connection.execute(
                    """SELECT sh.id FROM shots sh JOIN episodes e ON e.id=sh.episode_id
                    JOIN seasons s ON s.id=e.season_id WHERE sh.id=? AND s.project_id=?""",
                    (intent_owner_id, intent_project_id),
                ).fetchone()
                valid_shot_id = intent_owner_id if owner is not None else None

        effective_configuration = EffectiveConfigurationService(
            self.database,
            self.settings.manifest_path,
        ).resolve(
            project_id=intent_project_id,
            episode_id=valid_episode_id,
            shot_id=valid_shot_id,
            capability_code=profile_capability,
            requested_profile_version_id=plan.profile_version_id,
            run_overrides=self._runtime_overrides(plan.parameter_set),
        )
        expected_fingerprint = plan.expected_effective_configuration_fingerprint
        if expected_fingerprint and not hmac.compare_digest(expected_fingerprint, str(effective_configuration["fingerprint"])):
            raise DomainRuleError(
                "CONFIGURATION_CHANGED",
                "有效配置在预检后发生变化，请重新预检",
                {
                    "expected_effective_configuration_fingerprint": expected_fingerprint,
                    "current_effective_configuration_fingerprint": effective_configuration["fingerprint"],
                },
            )
        dependencies = {
            "intent_id": intent_id,
            "project_id": str(intent["project_id"]),
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
            "resource_estimate": resource_estimate,
            "director_recipe_version_id": str(director_recipe["recipe_version_id"]) if director_recipe else None,
            "director_recipe_hash": str(director_recipe["recipe_hash"]) if director_recipe else None,
            "parent_recipe_hash": parent["recipe_hash"] if parent is not None else None,
            "prompt_revision_hash": prompt_revision["content_hash"] if prompt_revision is not None else None,
            "media": sorted(media_dependencies, key=lambda item: str(item["id"])),
            "approvals": [
                {"role": role, "ordinal": ordinal, "source_approval_id": approval_id} for (role, ordinal), approval_id in sorted(approval_dependencies.items())
            ],
            "identity_pack_snapshot": identity_pack_snapshot,
            "effective_configuration": effective_configuration_snapshot(effective_configuration),
        }
        return ancestors, allowed_roles, dependencies

    @staticmethod
    def _runtime_overrides(parameter_set: dict[str, Any]) -> dict[str, Any]:
        """Extract only declared runtime overrides from a Variant parameter set."""
        overrides: dict[str, Any] = {}
        declared = parameter_set.get("runtime_overrides")
        if isinstance(declared, dict):
            overrides.update(declared)
        for key in ("production_tier", "sigma_points", "acceleration", "lora_strength", "native_audio", "take_count"):
            if key in parameter_set:
                overrides[key] = parameter_set[key]
        # Generation UI historically called the H3 tier field ``tier``. Keep
        # that wire spelling compatible while the effective-config contract is
        # canonicalized to production_tier.
        if "production_tier" not in overrides and "tier" in parameter_set:
            overrides["production_tier"] = parameter_set["tier"]
        return overrides

    def _disk_gate(self, dependencies: dict[str, Any]) -> dict[str, Any]:
        required = dependencies["resource_estimate"]["per_take"].get("disk_bytes")
        project_id = str(dependencies["project_id"])
        with self.database.connect() as connection:
            project = connection.execute("SELECT root_rel FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None:
            raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
        project_root = (self.settings.projects_root / str(project["root_rel"])).resolve()
        expected_root = self.settings.projects_root.resolve()
        if not project_root.is_relative_to(expected_root) or not project_root.is_dir():
            raise DomainRuleError("PROJECT_ROOT_INVALID", "项目根目录不存在或越界", {"project_id": project_id})
        try:
            free_bytes: int | None = int(shutil.disk_usage(project_root).free)
        except OSError:
            free_bytes = None
        estimate_known = isinstance(required, (int, float)) and not isinstance(required, bool)
        required_bytes = int(required) if estimate_known else None
        blocking = bool(estimate_known and (free_bytes is None or free_bytes < required_bytes))
        return {
            "status": "BLOCKED" if blocking else "PASS" if estimate_known else "ESTIMATE_UNAVAILABLE",
            "blocking": blocking,
            "code": "GENERATION_DISK_SPACE_LOW" if blocking else None,
            "free_bytes": free_bytes,
            "required_bytes": required_bytes,
            "estimate_source": "FROZEN_PROFILE_RESOURCE_POLICY" if estimate_known else "UNKNOWN",
            "filesystem_source": "PROJECT_ROOT",
            "project_id": project_id,
        }

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
            "expected_effective_configuration_fingerprint": plan.expected_effective_configuration_fingerprint,
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
            "expected_effective_configuration_fingerprint": plan.expected_effective_configuration_fingerprint,
            "bindings": [GenerationService._binding_dict(binding) for binding in plan.bindings],
        }

    def preflight_variant(self, intent_id: str, plan: VariantPlan) -> dict[str, Any]:
        _ancestors, _allowed_roles, dependencies = self._plan_context(intent_id, plan)
        recipe = self._recipe(plan)
        execution_recipe = self._execution_recipe(plan)
        evidence_recipe = {"execution": execution_recipe, "approvals": dependencies.get("approvals", [])}
        with self.database.connect() as connection:
            intent = connection.execute("SELECT project_id FROM generation_intents WHERE id=?", (intent_id,)).fetchone()
            style_context = build_generation_style_context(connection, str(intent["project_id"])) if intent is not None else None
        plan_evidence: dict[str, Any] = {"intent_id": intent_id, "recipe": recipe, "dependencies": dependencies}
        if style_context is not None:
            style_context_hash = str(style_context["context_hash"])
            evidence_recipe["style_context_hash"] = style_context_hash
            plan_evidence["style_context_hash"] = style_context_hash
        disk_gate = self._disk_gate(dependencies)
        effective_snapshot = dependencies.get("effective_configuration", {})
        configuration_blockers = list(effective_snapshot.get("blocking_errors", [])) if isinstance(effective_snapshot, dict) else []
        blockers = ([disk_gate] if disk_gate["blocking"] else []) + configuration_blockers
        result = {
            "intent_id": intent_id,
            "status": "BLOCKED" if blockers else "READY",
            "plan_hash": _digest(plan_evidence),
            "recipe_hash": _digest(evidence_recipe),
            "dependencies": dependencies,
            "resource_estimate": dependencies["resource_estimate"],
            "disk_gate": disk_gate,
            "blockers": blockers,
            "effective_configuration": effective_snapshot,
            "would_persist_variant": False,
            "would_create_job": False,
            "reproducibility": {
                "level": "NON_REPRODUCIBLE" if plan.seed_policy == "PROVIDER_RANDOM" else dependencies["determinism"],
                "determinism": dependencies["determinism"],
                "seed_support": dependencies["seed_support"],
                "claim": "不保证重复结果" if plan.seed_policy == "PROVIDER_RANDOM" else "遵循 Profile determinism 声明",
            },
        }
        if style_context is not None:
            result["style_context"] = {
                "context_hash": style_context["context_hash"],
                "brand_kit_count": len(style_context["brand_kits"]),
                "style_entry_count": len(style_context["style_entries"]),
                "style_reference_count": len(style_context["style_references"]),
            }
        return result

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
            VariantInput(
                str(item["role"]), str(item["media_version_id"]), int(item["ordinal"]), float(item["weight"]) if item.get("weight") is not None else None
            )
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
                VariantInput(item.role, first_frame_media_version_id, item.ordinal, item.weight) if item.role == "FIRST_FRAME" and item.ordinal == 0 else item
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
        camera_retargeted = False
        if operation == "PROFILE_BRANCH":
            camera_retargeted = self._retarget_camera_plan(parameter_set, target_profile_version_id)
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
                    snapshot = {}
                    frozen_hash = None
                current_hash = preflight["dependencies"].get("profile_execution_snapshot_hash")
                if frozen_hash and frozen_hash != current_hash:
                    raise DomainRuleError(
                        "EXACT_REPLAY_EXECUTION_SNAPSHOT_MISMATCH",
                        "Exact replay 的 workflow/model 快照已变化，不能复用旧 Variant 的执行权威",
                        {"frozen_snapshot_hash": frozen_hash, "current_snapshot_hash": current_hash},
                        suggested_action="重新发布匹配的本地 Profile/Workflow，或选择新的 Profile branch",
                    )
                frozen_style_hash = (snapshot.get("style_context") or {}).get("context_hash")
                current_style_hash = (preflight.get("style_context") or {}).get("context_hash")
                if frozen_style_hash != current_style_hash:
                    raise DomainRuleError(
                        "EXACT_REPLAY_STYLE_CONTEXT_MISMATCH",
                        "Exact replay 的 Style/Brand 快照已变化，不能冒充相同输入",
                        {
                            "frozen_style_context_hash": frozen_style_hash,
                            "current_style_context_hash": current_style_hash,
                        },
                        suggested_action="恢复匹配的 Style/Brand 版本，或使用新的创意分支",
                    )
        draft = self._recipe(plan)
        draft["intent_id"] = str(parent["intent_id"])
        changed_fields = {
            "RESAMPLE_NEW_SEED": ["explicit_seed"] + (["parameter_set.SEED"] if "SEED" in parameter_set else []),
            "RESUBMIT_PROVIDER_RANDOM": ["seed_policy", "explicit_seed", "provider_random_nonce"],
            "EXACT_REPLAY": [],
            "PROMPT_BRANCH": ["prompt_revision_id"],
            "SOURCE_IMAGE_BRANCH": ["bindings.FIRST_FRAME[0]"],
            "PROFILE_BRANCH": ["profile_version_id"] + (["parameter_set.camera_plan.resolution"] if camera_retargeted else []),
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
                "first_frame_media_version_id": next(item.media_version_id for item in parent_bindings if item.role == "FIRST_FRAME" and item.ordinal == 0)
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
            if camera_retargeted:
                preserved.remove("parameter_set")
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

    @staticmethod
    def _reroll_reason(reason_code: str, reason_note: str | None) -> str:
        allowed = {
            "USER_REROLL",
            "FACE_FIX",
            "IDENTITY_FIX",
            "COMPOSITION_FIX",
            "MOTION_FIX",
            "CONTINUITY_FIX",
            "FRAME_BRIDGE_FIX",
            "MODEL_COMPARE",
            "PROMPT_TUNE",
            "QC_AUTO_RETRY",
            "OTHER",
        }
        if reason_code not in allowed:
            raise DomainRuleError("REROLL_REASON_INVALID", "reroll reason_code 不受支持")
        note = (reason_note or "").strip()
        return reason_code if not note else f"{reason_code} | {note}"

    def _reroll_plan(
        self,
        parent: dict[str, Any],
        *,
        reason_code: str,
        reason_note: str | None,
        explicit_seed: int | None,
        profile_version_id: str | None,
        bindings: tuple[VariantInput, ...] | None,
    ) -> VariantPlan:
        if int(parent["is_stale"]):
            raise DomainRuleError(
                "VARIANT_PARENT_STALE",
                "已失效 GenerationVariant 不能 reroll",
                {"variant_id": parent["id"], "stale_reason": parent["stale_reason"]},
            )
        parent_bindings = tuple(
            VariantInput(
                str(item["role"]),
                str(item["media_version_id"]),
                int(item["ordinal"]),
                float(item["weight"]) if item.get("weight") is not None else None,
            )
            for item in parent["bindings"]
        )
        target_bindings = parent_bindings if bindings is None else bindings
        parent_profile = str(parent["capability_profile_version_id"])
        target_profile = profile_version_id or parent_profile
        bindings_changed = tuple(sorted((item.role, item.media_version_id, item.ordinal, item.weight) for item in target_bindings)) != tuple(
            sorted((item.role, item.media_version_id, item.ordinal, item.weight) for item in parent_bindings)
        )
        profile_changed = target_profile != parent_profile
        parent_seed = int(parent["explicit_seed"]) if parent.get("explicit_seed") is not None else None
        parameter_set = json.loads(str(parent["parameter_set_json"]))
        if profile_changed:
            self._retarget_camera_plan(parameter_set, target_profile)
        seed_policy = str(parent["seed_policy"])
        provider_random_nonce = parent.get("provider_random_nonce")

        if bindings_changed:
            variant_type = "REFERENCE_BRANCH"
            if explicit_seed is not None and explicit_seed != parent_seed:
                raise DomainRuleError("REROLL_SCOPE_INVALID", "替换 input bindings 时不能同时改变 seed")
        elif profile_changed:
            variant_type = "PROFILE_BRANCH"
            if explicit_seed is not None and explicit_seed != parent_seed:
                raise DomainRuleError("REROLL_SCOPE_INVALID", "覆盖 Profile 时不能同时改变 seed")
        elif seed_policy == "EXPLICIT":
            variant_type = "RESAMPLE_NEW_SEED"
            if explicit_seed is None:
                raise DomainRuleError("SEED_REQUIRED", "显式 seed 的 Variant reroll 必须提供新 explicit_seed")
            if explicit_seed == parent_seed:
                raise DomainRuleError("RESAMPLE_SEED_UNCHANGED", "reroll 必须使用不同 seed")
            if "SEED" in parameter_set:
                parameter_set["SEED"] = explicit_seed
        elif seed_policy == "PROVIDER_RANDOM":
            if explicit_seed is not None:
                raise DomainRuleError("REROLL_SCOPE_INVALID", "Provider random reroll 不能携带 explicit_seed")
            variant_type = "RESUBMIT_PROVIDER_RANDOM"
            provider_random_nonce = str(uuid.uuid4())
        else:
            raise DomainRuleError("REROLL_SEED_POLICY_UNSUPPORTED", "当前 seed policy 不支持无覆盖 reroll")

        return VariantPlan(
            variant_type=variant_type,
            parent_variant_id=str(parent["id"]),
            branch_reason=self._reroll_reason(reason_code, reason_note),
            prompt_revision_id=str(parent["prompt_revision_id"]) if parent.get("prompt_revision_id") else None,
            profile_version_id=target_profile,
            parameter_set=parameter_set,
            seed_policy=seed_policy,
            explicit_seed=parent_seed if (bindings_changed or profile_changed) else explicit_seed,
            bindings=target_bindings,
            provider_random_nonce=provider_random_nonce,
        )

    @staticmethod
    def _variant_matches_plan(variant: dict[str, Any], plan: VariantPlan) -> bool:
        frozen_bindings = sorted((str(item["role"]), str(item["media_version_id"]), int(item["ordinal"]), item.get("weight")) for item in variant["bindings"])
        planned_bindings = sorted((item.role, item.media_version_id, item.ordinal, item.weight) for item in plan.bindings)
        return (
            str(variant["parent_variant_id"] or "") == str(plan.parent_variant_id or "")
            and str(variant["variant_type"]) == plan.variant_type
            and str(variant["branch_reason"]) == plan.branch_reason
            and str(variant["capability_profile_version_id"]) == plan.profile_version_id
            and variant.get("explicit_seed") == plan.explicit_seed
            and frozen_bindings == planned_bindings
        )

    @staticmethod
    def _variant_fully_matches_plan(variant: dict[str, Any], plan: VariantPlan) -> bool:
        """Compare every persisted execution input for command replay recovery."""
        try:
            frozen_parameters = json.loads(str(variant["parameter_set_json"]))
        except (KeyError, TypeError, json.JSONDecodeError):
            return False
        return (
            GenerationService._variant_matches_plan(variant, plan)
            and (str(variant["prompt_revision_id"]) if variant.get("prompt_revision_id") else None) == plan.prompt_revision_id
            and frozen_parameters == plan.parameter_set
            and str(variant["seed_policy"]) == plan.seed_policy
            and (str(variant["provider_random_nonce"]) if variant.get("provider_random_nonce") else None) == plan.provider_random_nonce
        )

    @staticmethod
    def _validate_shot_base_plan(plan: VariantPlan) -> None:
        if plan.variant_type != "BASE" or plan.parent_variant_id is not None:
            raise DomainRuleError(
                "SHOT_BASE_PLAN_INVALID",
                "Shot 基础生成必须使用无父候选的 BASE 计划",
            )

    def _shot_generation_scope(
        self,
        shot_id: str,
        intent_id: str,
        *,
        expected_shot_revision: int | None = None,
    ) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT gi.id AS intent_id,gi.project_id,gi.owner_type,gi.owner_id,
                s.revision AS shot_revision,s.episode_id
                FROM generation_intents gi
                LEFT JOIN shots s ON s.id=gi.owner_id AND gi.owner_type='SHOT'
                WHERE gi.id=?""",
                (intent_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError(
                "GENERATION_INTENT_NOT_FOUND",
                "GenerationIntent 不存在",
                {"intent_id": intent_id},
            )
        if str(row["owner_type"]) != "SHOT" or str(row["owner_id"]) != shot_id or row["shot_revision"] is None:
            raise DomainRuleError(
                "SHOT_GENERATION_INTENT_SCOPE_MISMATCH",
                "生成意图不属于当前镜头",
                {"shot_id": shot_id, "intent_id": intent_id},
            )
        current_revision = int(row["shot_revision"])
        if expected_shot_revision is not None and current_revision != expected_shot_revision:
            raise DomainRuleError(
                "SHOT_REVISION_CONFLICT",
                "镜头已发生变化，请重新执行生成预检",
                {
                    "shot_id": shot_id,
                    "expected_revision": expected_shot_revision,
                    "current_revision": current_revision,
                },
            )
        return {
            "intent_id": str(row["intent_id"]),
            "project_id": str(row["project_id"]),
            "shot_id": shot_id,
            "shot_revision": current_revision,
            "episode_id": str(row["episode_id"]),
        }

    def preflight_shot_base_variant(
        self,
        shot_id: str,
        intent_id: str,
        plan: VariantPlan,
        expected_shot_revision: int,
        stage_code: str,
    ) -> dict[str, Any]:
        """Produce a read-only, shot-scoped confirmation token for BASE generation."""
        self._validate_shot_base_plan(plan)
        if stage_code not in {"SHOT_IMAGE", "VIDEO"}:
            raise DomainRuleError("SHOT_GENERATION_STAGE_INVALID", "Shot 生成阶段必须是 SHOT_IMAGE 或 VIDEO")
        scope = self._shot_generation_scope(
            shot_id,
            intent_id,
            expected_shot_revision=expected_shot_revision,
        )
        variant_preflight = self.preflight_variant(intent_id, plan)
        variant_plan_hash = str(variant_preflight["plan_hash"])
        outer_plan_hash = _digest(
            {
                "shot_id": shot_id,
                "shot_revision": scope["shot_revision"],
                "intent_id": intent_id,
                "stage_code": stage_code,
                "variant_plan_hash": variant_plan_hash,
            }
        )
        return {
            **variant_preflight,
            "shot_id": shot_id,
            "shot_revision": scope["shot_revision"],
            "variant_plan_hash": variant_plan_hash,
            "plan_hash": outer_plan_hash,
        }

    def _shot_base_idempotent_replay(
        self,
        intent_id: str,
        plan: VariantPlan,
        idempotency_key: str,
        stage_code: str,
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT j.id,j.subject_id,j.stage_code
                FROM jobs j
                JOIN generation_variants gv ON gv.id=j.subject_id
                WHERE gv.intent_id=? AND j.idempotency_key=?
                  AND j.subject_type='GENERATION_VARIANT'
                ORDER BY j.created_at,j.id LIMIT 1""",
                (intent_id, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        variant = self.get_variant(str(row["subject_id"]))
        if not self._variant_fully_matches_plan(variant, plan) or str(row["stage_code"]) != stage_code:
            raise DomainRuleError(
                "IDEMPOTENCY_PAYLOAD_MISMATCH",
                "相同 Idempotency-Key 已用于不同 Shot 基础生成请求",
            )
        job = JobService(self.database).get_job(str(row["id"]))
        job["idempotent_replay"] = True
        return {"variant": variant, "job": job, "idempotent_replay": True}

    def submit_shot_base_variant(
        self,
        shot_id: str,
        intent_id: str,
        plan: VariantPlan,
        *,
        expected_shot_revision: int,
        stage_code: str,
        plan_hash: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Submit a BASE variant under the URL Shot and recover exact command replays."""
        self._validate_shot_base_plan(plan)
        # Owner scope is always enforced, while a successful replay deliberately
        # survives later Shot revisions: clients may safely retry a lost response.
        scope = self._shot_generation_scope(shot_id, intent_id)
        replay = self._shot_base_idempotent_replay(intent_id, plan, idempotency_key, stage_code)
        if replay is not None:
            return replay
        preflight = self.preflight_shot_base_variant(
            shot_id,
            intent_id,
            plan,
            expected_shot_revision,
            stage_code,
        )
        if not hmac.compare_digest(str(preflight["plan_hash"]), plan_hash):
            raise DomainRuleError(
                "VARIANT_PLAN_STALE",
                "Shot 生成计划已变化，请重新执行 preflight",
                {"current_plan_hash": preflight["plan_hash"]},
            )
        try:
            result = self.submit_confirmed_variant(
                intent_id,
                plan,
                str(preflight["variant_plan_hash"]),
                idempotency_key,
                job_scope={
                    "subject_kind": "GENERATION_VARIANT",
                    "scope_project_id": scope["project_id"],
                    "scope_episode_id": scope["episode_id"],
                    "scope_shot_id": shot_id,
                    "stage_code": stage_code,
                },
            )
        except DomainRuleError as error:
            if error.code != "IDEMPOTENCY_PAYLOAD_MISMATCH":
                raise
            replay = self._shot_base_idempotent_replay(intent_id, plan, idempotency_key, stage_code)
            if replay is None:
                raise
            return replay
        result["idempotent_replay"] = False
        return result

    def _reroll_idempotent_replay(
        self,
        parent: dict[str, Any],
        plan: VariantPlan,
        idempotency_key: str,
        expected_stage_code: str | None = None,
    ) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT j.id,j.subject_id,j.stage_code FROM jobs j
                JOIN generation_intents gi ON gi.project_id=j.project_id
                WHERE gi.id=? AND j.idempotency_key=? AND j.subject_type='GENERATION_VARIANT'
                ORDER BY j.created_at, j.id LIMIT 1""",
                (parent["intent_id"], idempotency_key),
            ).fetchone()
        if row is None:
            return None
        variant = self.get_variant(str(row["subject_id"]))
        if not self._variant_matches_plan(variant, plan) or (expected_stage_code is not None and str(row["stage_code"]) != expected_stage_code):
            raise DomainRuleError("IDEMPOTENCY_PAYLOAD_MISMATCH", "相同 Idempotency-Key 已用于不同 reroll 请求")
        job = JobService(self.database).get_job(str(row["id"]))
        job["idempotent_replay"] = True
        return {"variant": variant, "job": job, "reroll": {"retry": False, "parent_variant_id": parent["id"]}}

    def reroll_variant(
        self,
        parent_variant_id: str,
        *,
        reason_code: str,
        reason_note: str | None,
        explicit_seed: int | None,
        profile_version_id: str | None,
        bindings: tuple[VariantInput, ...] | None,
        idempotency_key: str,
        job_scope: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Create a child Variant and Job; this is never an attempt retry."""
        parent = self.get_variant(parent_variant_id)
        plan = self._reroll_plan(
            parent,
            reason_code=reason_code,
            reason_note=reason_note,
            explicit_seed=explicit_seed,
            profile_version_id=profile_version_id,
            bindings=bindings,
        )
        expected_stage_code = job_scope.get("stage_code") if job_scope else None
        replay = self._reroll_idempotent_replay(parent, plan, idempotency_key, expected_stage_code)
        if replay is not None:
            return replay
        preflight = self.preflight_variant(str(parent["intent_id"]), plan)
        try:
            result = self.submit_confirmed_variant(
                str(parent["intent_id"]),
                plan,
                str(preflight["plan_hash"]),
                idempotency_key,
                job_scope=job_scope,
            )
        except DomainRuleError as error:
            # SQLite serializes the write transaction.  If two identical calls
            # raced, the loser reaches JobService's idempotency mismatch because
            # its temporary Variant id differs; recover the committed business
            # result after its transaction rolls back.
            if error.code != "IDEMPOTENCY_PAYLOAD_MISMATCH":
                raise
            replay = self._reroll_idempotent_replay(parent, plan, idempotency_key, expected_stage_code)
            if replay is None:
                raise
            return replay
        result["reroll"] = {"retry": False, "parent_variant_id": parent_variant_id}
        return result

    def reroll_shot_variant(
        self,
        shot_id: str,
        parent_variant_id: str,
        *,
        reason_code: str,
        reason_note: str | None,
        explicit_seed: int | None,
        profile_version_id: str | None,
        idempotency_key: str,
        stage_code: str,
    ) -> dict[str, Any]:
        """Scope a creative reroll to the Shot Studio subject in the URL."""
        with self.database.connect() as connection:
            owner = connection.execute(
                """SELECT gi.owner_type,gi.owner_id,gi.project_id,s.episode_id FROM generation_variants gv
                JOIN generation_intents gi ON gi.id=gv.intent_id
                LEFT JOIN shots s ON s.id=gi.owner_id AND gi.owner_type='SHOT'
                WHERE gv.id=?""",
                (parent_variant_id,),
            ).fetchone()
        if owner is None:
            raise DomainRuleError("GENERATION_VARIANT_NOT_FOUND", "GenerationVariant 不存在")
        if str(owner["owner_type"]) != "SHOT" or str(owner["owner_id"]) != shot_id:
            raise DomainRuleError(
                "SHOT_VARIANT_SCOPE_MISMATCH",
                "父候选不属于当前镜头",
                {"shot_id": shot_id, "parent_variant_id": parent_variant_id},
            )
        if stage_code not in {"SHOT_IMAGE", "VIDEO"}:
            raise DomainRuleError("SHOT_GENERATION_STAGE_INVALID", "Shot 生成阶段必须是 SHOT_IMAGE 或 VIDEO")
        return self.reroll_variant(
            parent_variant_id,
            reason_code=reason_code,
            reason_note=reason_note,
            explicit_seed=explicit_seed,
            profile_version_id=profile_version_id,
            bindings=None,
            idempotency_key=idempotency_key,
            job_scope={
                "subject_kind": "GENERATION_VARIANT",
                "scope_project_id": str(owner["project_id"]),
                "scope_episode_id": str(owner["episode_id"]),
                "scope_shot_id": shot_id,
                "stage_code": stage_code,
            },
        )

    def create_confirmed_variant(self, intent_id: str, plan: VariantPlan, plan_hash: str) -> dict[str, Any]:
        preflight = self.preflight_variant(intent_id, plan)
        if preflight["status"] != "READY":
            raise DomainRuleError(
                "GENERATION_DISK_PREFLIGHT_BLOCKED",
                "生成输出所需项目磁盘空间不足",
                {"intent_id": intent_id, "disk_gate": preflight["disk_gate"]},
            )
        if not hmac.compare_digest(str(preflight["plan_hash"]), plan_hash):
            raise DomainRuleError("VARIANT_PLAN_STALE", "Variant 计划已变化，请重新执行 preflight", {"current_plan_hash": preflight["plan_hash"]})
        return self.create_variant(intent_id, plan)

    def submit_confirmed_variant(
        self,
        intent_id: str,
        plan: VariantPlan,
        plan_hash: str,
        idempotency_key: str,
        *,
        job_scope: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        preflight = self.preflight_variant(intent_id, plan)
        if preflight["status"] != "READY":
            raise DomainRuleError(
                "GENERATION_DISK_PREFLIGHT_BLOCKED",
                "生成输出所需项目磁盘空间不足",
                {"intent_id": intent_id, "disk_gate": preflight["disk_gate"]},
            )
        if not hmac.compare_digest(str(preflight["plan_hash"]), plan_hash):
            raise DomainRuleError("VARIANT_PLAN_STALE", "Variant 计划已变化，请重新执行 preflight")
        variant_id = str(uuid.uuid4())
        ancestors, allowed_roles, dependencies = self._plan_context(intent_id, plan)
        plan.validate(variant_id=variant_id, ancestors=ancestors, allowed_roles=allowed_roles)
        now = _now()
        recipe = self._execution_recipe(plan)
        workflow_version_id = str(dependencies["workflow_version_id"])
        semantic_inputs = dict(plan.parameter_set)
        approval_by_slot = {(str(item["role"]), int(item["ordinal"])): str(item["source_approval_id"]) for item in dependencies.get("approvals", [])}
        media_bindings_snapshot = [
            {**self._binding_dict(binding), "source_approval_id": approval_by_slot.get((binding.role, binding.ordinal))} for binding in plan.bindings
        ]
        with self.database.transaction() as connection:
            intent = connection.execute("SELECT * FROM generation_intents WHERE id=?", (intent_id,)).fetchone()
            if intent is None:
                raise DomainRuleError("GENERATION_INTENT_NOT_FOUND", "GenerationIntent 不存在")
            style_context = build_generation_style_context(connection, str(intent["project_id"]))
            preflight_style_hash = (preflight.get("style_context") or {}).get("context_hash")
            current_style_hash = style_context.get("context_hash") if style_context is not None else None
            if preflight_style_hash != current_style_hash:
                raise DomainRuleError(
                    "VARIANT_PLAN_STALE",
                    "Style/Brand 生成上下文已变化，请重新执行 preflight",
                    {"current_style_context_hash": current_style_hash},
                )
            recipe_evidence: dict[str, Any] = {
                "execution": recipe,
                "approvals": dependencies.get("approvals", []),
            }
            if current_style_hash is not None:
                recipe_evidence["style_context_hash"] = current_style_hash
            recipe_hash = _digest(recipe_evidence)
            input_evidence: object = media_bindings_snapshot
            if current_style_hash is not None:
                input_evidence = {
                    "media_bindings": media_bindings_snapshot,
                    "style_context_hash": current_style_hash,
                }
            identity_pack_snapshot = dependencies.get("identity_pack_snapshot")
            current_identity_pack_snapshot = CharacterIdentityPackService.generation_snapshot_for_intent(
                connection,
                intent,
            )
            expected_identity_hash = identity_pack_snapshot.get("snapshot_hash") if isinstance(identity_pack_snapshot, dict) else None
            current_identity_hash = current_identity_pack_snapshot.get("snapshot_hash") if isinstance(current_identity_pack_snapshot, dict) else None
            if expected_identity_hash != current_identity_hash:
                raise DomainRuleError(
                    "VARIANT_PLAN_STALE",
                    "角色身份包绑定在提交前发生变化，请重新执行 preflight",
                    {"current_identity_pack_snapshot_hash": current_identity_hash},
                )
            if isinstance(identity_pack_snapshot, dict):
                if not isinstance(input_evidence, dict):
                    input_evidence = {"media_bindings": media_bindings_snapshot}
                input_evidence["identity_pack_snapshot_hash"] = identity_pack_snapshot["snapshot_hash"]
            next_no = int(
                connection.execute("SELECT COALESCE(MAX(variant_no),0)+1 AS n FROM generation_variants WHERE intent_id=?", (intent_id,)).fetchone()["n"]
            )
            connection.execute(
                """INSERT INTO generation_variants
                (id, intent_id, variant_no, variant_type, parent_variant_id, branch_reason, prompt_revision_id,
                capability_profile_version_id, parameter_set_json, seed_policy, explicit_seed, provider_random_nonce,
                input_fingerprint, recipe_hash, director_recipe_version_id, director_recipe_hash,
                identity_pack_snapshot_json,status, created_at, updated_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', ?, ?, 'local-user')""",
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
                    _digest(input_evidence),
                    recipe_hash,
                    dependencies["director_recipe_version_id"],
                    dependencies["director_recipe_hash"],
                    _canonical(identity_pack_snapshot or {}),
                    now,
                    now,
                ),
            )
            for binding in plan.bindings:
                connection.execute(
                    """INSERT INTO variant_input_bindings
                    (id, variant_id, role, media_version_id, ordinal, weight, source_approval_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        str(uuid.uuid4()),
                        variant_id,
                        binding.role,
                        binding.media_version_id,
                        binding.ordinal,
                        binding.weight,
                        approval_by_slot.get((binding.role, binding.ordinal)),
                    ),
                )
            # G11 P0-1: inject the shot's bound CHARACTER appearance anchors into
            # the *executed* PROMPT semantic input. This deliberately mutates
            # only the ephemeral job input snapshot — never plan.parameter_set,
            # the frozen variant row, or plan_hash — so preflight / EXACT_REPLAY
            # semantics stay intact. Derivation replays from the parent's
            # parameter_set (anchor-free) and re-injects from the *current*
            # binding state at submit time; the original anchor text + sha256
            # are frozen into the job snapshot and audit trail below, keeping
            # every execution auditable even when the story library changes
            # later. With no bound characters the snapshot is byte-identical to
            # the pre-injection behaviour (no story_assets key).
            if style_context is not None:
                prompt_context = style_context.get("prompt_context")
                if isinstance(prompt_context, str) and prompt_context and isinstance(semantic_inputs.get("PROMPT"), str):
                    semantic_inputs["PROMPT"] = semantic_inputs["PROMPT"].rstrip() + "\n\n" + prompt_context
                negative_context = style_context.get("negative_prompt_context")
                if isinstance(negative_context, str) and negative_context and isinstance(semantic_inputs.get("NEGATIVE_PROMPT"), str):
                    semantic_inputs["NEGATIVE_PROMPT"] = semantic_inputs["NEGATIVE_PROMPT"].rstrip() + "\n\n" + negative_context
            anchor_lines = _character_anchor_lines(connection, intent)
            story_assets_snapshot: dict[str, str] | None = None
            if anchor_lines and "PROMPT" in semantic_inputs and isinstance(semantic_inputs["PROMPT"], str) and semantic_inputs["PROMPT"].strip():
                anchor_text = "\n".join(anchor_lines)
                semantic_inputs["PROMPT"] = semantic_inputs["PROMPT"] + "\n\n" + anchor_text
                story_assets_snapshot = {
                    "anchor": anchor_text,
                    "anchor_sha256": hashlib.sha256(anchor_text.encode("utf-8")).hexdigest(),
                }
            job_input_snapshot: dict[str, Any] = {
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
                    "effective_configuration": dependencies["effective_configuration"],
                },
                "semantic_inputs": semantic_inputs,
                "media_bindings": media_bindings_snapshot,
                "recipe_hash": recipe_hash,
            }
            if dependencies["director_recipe_version_id"] is not None:
                job_input_snapshot["director_recipe"] = {
                    "version_id": dependencies["director_recipe_version_id"],
                    "recipe_hash": dependencies["director_recipe_hash"],
                }
            if story_assets_snapshot is not None:
                job_input_snapshot["story_assets"] = story_assets_snapshot
            if isinstance(identity_pack_snapshot, dict):
                job_input_snapshot["identity_packs"] = identity_pack_snapshot
            if style_context is not None:
                job_input_snapshot["style_context"] = style_context
            job = JobService(self.database).create_job_in_transaction(
                connection,
                str(intent["project_id"]),
                "GENERATION_VARIANT",
                "GENERATION_VARIANT",
                variant_id,
                "GPU_H3",
                job_input_snapshot,
                idempotency_key,
                execution_profile_version_id=plan.profile_version_id,
                max_attempts=1,
                **(job_scope or {}),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                VALUES ('local-user', 'producer', 'GENERATION_VARIANT_SUBMITTED', 'generation_variant', ?, ?, ?)""",
                (variant_id, "原子创建 GenerationVariant 与 GPU_H3 Job", _canonical({"job_id": job["id"], "recipe_hash": recipe_hash})),
            )
            if story_assets_snapshot is not None:
                # Dedicated audit action: the existing SUBMITTED event stays
                # byte-identical, and this one records the injected anchor's
                # original text hash + line count for replay/audit.
                connection.execute(
                    """INSERT INTO audit_events
                    (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                    VALUES ('local-user', 'producer', 'GENERATION_CHARACTER_ANCHOR_INJECTED', 'generation_variant', ?, ?, ?)""",
                    (
                        variant_id,
                        "提交时按分镜当前绑定将角色外观锚点注入 PROMPT 语义输入并冻结原文哈希",
                        _canonical(
                            {
                                "job_id": job["id"],
                                "character_anchor_sha256": story_assets_snapshot["anchor_sha256"],
                                "character_anchor_lines": len(anchor_lines),
                            }
                        ),
                    ),
                )
            if isinstance(identity_pack_snapshot, dict):
                connection.execute(
                    """INSERT INTO audit_events
                    (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                    VALUES ('local-user', 'producer', 'GENERATION_IDENTITY_PACK_SNAPSHOT_FROZEN',
                    'generation_variant', ?, ?, ?)""",
                    (
                        variant_id,
                        "提交时冻结镜头角色身份包版本与三视图媒体证据",
                        _canonical(
                            {
                                "job_id": job["id"],
                                "snapshot_hash": identity_pack_snapshot["snapshot_hash"],
                                "pack_version_ids": [item["pack_version_id"] for item in identity_pack_snapshot["packs"]],
                            }
                        ),
                    ),
                )
            if style_context is not None:
                connection.execute(
                    """INSERT INTO audit_events
                    (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json)
                    VALUES ('local-user', 'producer', 'GENERATION_STYLE_CONTEXT_FROZEN', 'generation_variant', ?, ?, ?)""",
                    (
                        variant_id,
                        "提交时冻结项目 Style/Brand 声明式上下文及版本哈希",
                        _canonical(
                            {
                                "job_id": job["id"],
                                "style_context_hash": style_context["context_hash"],
                                "brand_kit_count": len(style_context["brand_kits"]),
                                "style_entry_count": len(style_context["style_entries"]),
                                "style_reference_count": len(style_context["style_references"]),
                            }
                        ),
                    ),
                )
        return {"variant": self.get_variant(variant_id), "job": job}

    def create_variant(self, intent_id: str, plan: VariantPlan) -> dict[str, Any]:
        variant_id = str(uuid.uuid4())
        ancestors, allowed_roles, dependencies = self._plan_context(intent_id, plan)
        plan.validate(variant_id=variant_id, ancestors=ancestors, allowed_roles=allowed_roles)
        approval_by_slot = {(str(item["role"]), int(item["ordinal"])): str(item["source_approval_id"]) for item in dependencies.get("approvals", [])}
        now = _now()
        execution_recipe = self._execution_recipe(plan)
        binding_snapshots = [
            {**self._binding_dict(binding), "source_approval_id": approval_by_slot.get((binding.role, binding.ordinal))} for binding in plan.bindings
        ]
        identity_pack_snapshot = dependencies.get("identity_pack_snapshot")
        fingerprint_evidence: object = binding_snapshots
        if isinstance(identity_pack_snapshot, dict):
            fingerprint_evidence = {
                "media_bindings": binding_snapshots,
                "identity_pack_snapshot_hash": identity_pack_snapshot["snapshot_hash"],
            }
        recipe_hash = _digest({"execution": execution_recipe, "approvals": dependencies.get("approvals", [])})
        with self.database.transaction() as connection:
            intent = connection.execute("SELECT * FROM generation_intents WHERE id = ?", (intent_id,)).fetchone()
            if intent is None:
                raise DomainRuleError("GENERATION_INTENT_NOT_FOUND", "GenerationIntent 不存在", {"intent_id": intent_id})
            current_identity_pack_snapshot = CharacterIdentityPackService.generation_snapshot_for_intent(connection, intent)
            if (identity_pack_snapshot.get("snapshot_hash") if isinstance(identity_pack_snapshot, dict) else None) != (
                current_identity_pack_snapshot.get("snapshot_hash") if isinstance(current_identity_pack_snapshot, dict) else None
            ):
                raise DomainRuleError("VARIANT_PLAN_STALE", "角色身份包绑定发生变化，请重新创建计划")
            profile = connection.execute("SELECT id FROM execution_profile_versions WHERE id = ?", (plan.profile_version_id,)).fetchone()
            if profile is None:
                raise DomainRuleError("PROFILE_VERSION_NOT_FOUND", "ExecutionProfileVersion 不存在")
            next_no = connection.execute("SELECT COALESCE(MAX(variant_no), 0) + 1 FROM generation_variants WHERE intent_id = ?", (intent_id,)).fetchone()[0]
            connection.execute(
                """INSERT INTO generation_variants (id, intent_id, variant_no, variant_type, parent_variant_id, branch_reason,
                prompt_revision_id, capability_profile_version_id, parameter_set_json, seed_policy, explicit_seed, provider_random_nonce,
                input_fingerprint, recipe_hash, director_recipe_version_id, director_recipe_hash,
                identity_pack_snapshot_json,status, created_at, updated_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PLANNED', ?, ?, 'local-user')""",
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
                    _digest(fingerprint_evidence),
                    recipe_hash,
                    dependencies["director_recipe_version_id"],
                    dependencies["director_recipe_hash"],
                    _canonical(identity_pack_snapshot or {}),
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
        try:
            result["identity_pack_snapshot"] = json.loads(str(result.get("identity_pack_snapshot_json") or "{}"))
        except (TypeError, json.JSONDecodeError):
            result["identity_pack_snapshot"] = {}
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
