"""Asset Bible multi-view generation facade.

This module composes the existing capability resolver, GenerationIntent /
GenerationVariant services and persistent Job queue.  It deliberately owns no
generation facts of its own: the three requested views are semantic snapshots
on the variants and their jobs, while accepted media continues to be attached
through the ordinary Asset Bible reference command.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from local_drama.application.generation import GenerationService
from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import VariantInput
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.infrastructure.database.sqlite import Database

CAPABILITY = "IMAGE_MULTI_VIEW"
VIEW_SPECS = (
    ("FRONT", 0.0, "front view, facing camera"),
    ("LEFT", -90.0, "left profile view"),
    ("RIGHT", 90.0, "right profile view"),
)
_REFERENCE_ROLE_PREFERENCE = (
    "CHARACTER_REFERENCE",
    "REFERENCE_IMAGE",
    "SOURCE_IMAGE",
    "IMAGE_REFERENCE",
    "FIRST_FRAME",
)


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class AssetMultiViewService:
    CAPABILITY = CAPABILITY
    SPECS = VIEW_SPECS
    PURPOSE = "ASSET_MULTI_VIEW"
    OUTPUT_REFERENCE_KIND: str | None = None
    PROMPT_PREFIX = "character turnaround, preserve identity and outfit"
    IDEMPOTENCY_SCOPE = "asset-multiview"
    AUDIT_ACTION = "ASSET_MULTI_VIEW_SUBMITTED"

    def _output_reference_kind(self, slot_kind: str) -> str:
        return self.OUTPUT_REFERENCE_KIND or slot_kind

    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.generation = GenerationService(database, settings)

    def preflight(
        self,
        asset_id: str,
        *,
        asset_state_id: str | None = None,
        profile_version_id: str | None = None,
        consistency_strength: str = "HIGH",
        background: str = "CLEAN",
    ) -> dict[str, Any]:
        request = self._normalized_request(
            asset_state_id=asset_state_id,
            profile_version_id=profile_version_id,
            consistency_strength=consistency_strength,
            background=background,
        )
        with self.database.connect() as connection:
            asset = connection.execute("SELECT * FROM story_assets WHERE id=?", (asset_id,)).fetchone()
            if asset is None:
                raise DomainRuleError("STORY_ASSET_NOT_FOUND", "故事资产不存在", {"asset_id": asset_id})
            project_id = str(asset["project_id"])
            blockers: list[dict[str, Any]] = []
            if str(asset["kind"]) != "CHARACTER":
                blockers.append(self._blocker("ASSET_MULTI_VIEW_KIND_UNSUPPORTED", "只有角色资产可生成 FRONT / LEFT / RIGHT 三视图"))
            if str(asset["status"]) != "ACTIVE":
                blockers.append(self._blocker("STORY_ASSET_NOT_ACTIVE", "归档资产不能提交三视图生成"))

            hero = self._hero(connection, asset_id, asset_state_id)
            if hero is None:
                blockers.append(self._blocker("ASSET_MULTI_VIEW_HERO_REQUIRED", "生成三视图前必须先绑定当前 HERO 参考图", "先上传或选择 HERO"))

            resolution = GenerationPreferenceQueryService(
                SqliteGenerationPreferenceRepository(connection)
            ).resolve(project_id=project_id, capability=self.CAPABILITY)
            selected_profile_id = profile_version_id or resolution.get("profile_version_id")
            profile: Any = None
            input_role: str | None = None
            capability_reason = resolution.get("blocked_reason")
            if profile_version_id:
                capability_reason = None
                profile = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (profile_version_id,)).fetchone()
                if profile is None:
                    capability_reason = "PROFILE_NOT_FOUND"
                elif str(profile["status"]) != "PUBLISHED":
                    capability_reason = "PROFILE_NOT_PUBLISHED"
                elif str(profile["capability"]).upper() != self.CAPABILITY:
                    capability_reason = "PROFILE_CAPABILITY_MISMATCH"
            elif selected_profile_id:
                profile = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (selected_profile_id,)).fetchone()

            if profile is not None and capability_reason is None:
                input_role, contract_reason = self._profile_input_role(connection, profile)
                capability_reason = contract_reason
            if not selected_profile_id or profile is None or capability_reason is not None or input_role is None:
                blockers.append(
                    self._blocker(
                        "ASSET_MULTI_VIEW_CAPABILITY_UNAVAILABLE",
                        f"当前没有可执行的 {self.CAPABILITY} Published Profile",
                        f"在模型与能力中发布支持 HERO 图像语义输入的 {self.CAPABILITY} Profile",
                        reason=capability_reason or "NO_COMPATIBLE_PROFILE",
                    )
                )

            hero_snapshot = None if hero is None else {
                "reference_id": str(hero["id"]),
                "media_version_id": str(hero["media_version_id"]),
                "asset_state_id": hero["asset_state_id"],
                "revision": int(hero["revision"]),
            }
            authority = {
                "asset_id": asset_id,
                "asset_revision": int(asset["revision"]),
                "project_id": project_id,
                "hero": hero_snapshot,
                "profile_version_id": str(selected_profile_id) if selected_profile_id else None,
                "profile_revision": int(profile["revision"]) if profile is not None else None,
                "input_role": input_role,
                "settings": request,
                "views": [item[0] for item in self.SPECS],
            }
            return {
                "asset_id": asset_id,
                "project_id": project_id,
                "capability": self.CAPABILITY,
                "status": "READY" if not blockers else "BLOCKED",
                "ready": not blockers,
                "blockers": blockers,
                "hero": hero_snapshot,
                "profile_resolution": {
                    **resolution,
                    "profile_version_id": str(selected_profile_id) if selected_profile_id else None,
                    "input_role": input_role,
                },
                "views": [
                    {"reference_kind": self._output_reference_kind(kind), "yaw_deg": yaw, "semantic_output": kind}
                    for kind, yaw, _prompt in self.SPECS
                ],
                "plan_hash": _digest(authority),
                "would_persist_intent": False,
                "would_create_variants": 0 if blockers else len(self.SPECS),
                "would_create_jobs": 0 if blockers else len(self.SPECS),
            }

    def submit(
        self,
        asset_id: str,
        *,
        plan_hash: str,
        idempotency_key: str,
        asset_state_id: str | None = None,
        profile_version_id: str | None = None,
        consistency_strength: str = "HIGH",
        background: str = "CLEAN",
    ) -> dict[str, Any]:
        if not idempotency_key.strip() or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", f"{self.CAPABILITY} 提交必须提供 1—200 字符 Idempotency-Key")
        preflight = self.preflight(
            asset_id,
            asset_state_id=asset_state_id,
            profile_version_id=profile_version_id,
            consistency_strength=consistency_strength,
            background=background,
        )
        if not preflight["ready"]:
            first = preflight["blockers"][0]
            raise DomainRuleError(str(first["code"]), str(first["message"]), {"blockers": preflight["blockers"]}, suggested_action=first.get("suggested_action"))
        if not hmac.compare_digest(str(preflight["plan_hash"]), plan_hash):
            stale_code = "ASSET_MULTI_VIEW_PLAN_STALE" if self.PURPOSE == "ASSET_MULTI_VIEW" else "ASSET_EXPRESSION_PLAN_STALE"
            raise DomainRuleError(stale_code, "HERO、Profile 或生成设置已变化，请重新预检", {"current_plan_hash": preflight["plan_hash"]})

        request_snapshot = {
            "asset_id": asset_id,
            "plan_hash": plan_hash,
            "asset_state_id": asset_state_id,
            "profile_version_id": profile_version_id,
            "consistency_strength": consistency_strength,
            "background": background,
        }
        replay = self._idempotent_replay(str(preflight["project_id"]), idempotency_key, request_snapshot)
        if replay is not None:
            replay["idempotent_replay"] = True
            return replay

        hero = preflight["hero"]
        resolution = preflight["profile_resolution"]
        intent = self.generation.create_intent(
            str(preflight["project_id"]),
            "STORY_ASSET",
            asset_id,
            self.PURPOSE,
            f"Generate traceable {self.CAPABILITY} candidates from the frozen HERO reference",
        )
        plans = [
            self._variant_plan(
                kind=kind,
                yaw=yaw,
                prompt=prompt,
                hero_media_version_id=str(hero["media_version_id"]),
                input_role=str(resolution["input_role"]),
                profile_version_id=str(resolution["profile_version_id"]),
                asset_state_id=asset_state_id,
                consistency_strength=consistency_strength,
                background=background,
                seed_index=index,
            )
            for index, (kind, yaw, prompt) in enumerate(self.SPECS)
        ]
        # Validate every independent slot before the first queue write. Runtime failures are
        # intentionally independent and remain visible as partial completion.
        planned = [self.generation.preflight_variant(str(intent["id"]), plan) for plan in plans]
        submitted = [
            self.generation.submit_confirmed_variant(
                str(intent["id"]),
                plan,
                str(plan_result["plan_hash"]),
                f"{idempotency_key}:{kind.lower()}",
            )
            for (kind, _yaw, _prompt), plan, plan_result in zip(self.SPECS, plans, planned, strict=True)
        ]
        with self.database.transaction() as connection:
            connection.execute("UPDATE generation_intents SET status='QUEUED', updated_at=CURRENT_TIMESTAMP WHERE id=?", (intent["id"],))
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES ('local-user','producer',?,'generation_intent',?,?,?)""",
                (self.AUDIT_ACTION, intent["id"], f"提交 {self.CAPABILITY} 独立候选任务", _canonical({"asset_id": asset_id, "hero_media_version_id": hero["media_version_id"], "plan_hash": plan_hash})),
            )
        result = {
            "intent": self.generation.get_intent(str(intent["id"])),
            "items": [
                {"reference_kind": kind, "variant": item["variant"], "job": item["job"]}
                for (kind, _yaw, _prompt), item in zip(self.SPECS, submitted, strict=True)
            ],
            "idempotent_replay": False,
        }
        self._store_idempotency(str(preflight["project_id"]), idempotency_key, request_snapshot, result)
        return result

    @staticmethod
    def _normalized_request(*, asset_state_id: str | None, profile_version_id: str | None, consistency_strength: str, background: str) -> dict[str, Any]:
        strength = consistency_strength.strip().upper()
        backdrop = background.strip().upper()
        if strength not in {"LOW", "MEDIUM", "HIGH"}:
            raise DomainRuleError("ASSET_MULTI_VIEW_CONSISTENCY_INVALID", "一致性强度必须是 LOW、MEDIUM 或 HIGH")
        if backdrop not in {"CLEAN", "TRANSPARENT", "ORIGINAL"}:
            raise DomainRuleError("ASSET_MULTI_VIEW_BACKGROUND_INVALID", "背景必须是 CLEAN、TRANSPARENT 或 ORIGINAL")
        return {"asset_state_id": asset_state_id, "profile_version_id": profile_version_id, "consistency_strength": strength, "background": backdrop}

    @staticmethod
    def _hero(connection: Any, asset_id: str, asset_state_id: str | None) -> Any:
        if asset_state_id:
            state = connection.execute("SELECT story_asset_id,status FROM story_asset_states WHERE id=?", (asset_state_id,)).fetchone()
            if state is None or str(state["story_asset_id"]) != asset_id or str(state["status"]) != "ACTIVE":
                raise DomainRuleError("STORY_ASSET_STATE_NOT_FOUND", "资产状态不存在或不属于当前资产", {"asset_state_id": asset_state_id})
            return connection.execute(
                """SELECT * FROM story_asset_references WHERE story_asset_id=? AND reference_kind='HERO'
                AND status='ACTIVE' AND (asset_state_id=? OR asset_state_id IS NULL)
                ORDER BY CASE WHEN asset_state_id=? THEN 0 ELSE 1 END, priority, created_at DESC, id LIMIT 1""",
                (asset_id, asset_state_id, asset_state_id),
            ).fetchone()
        return connection.execute(
            """SELECT * FROM story_asset_references WHERE story_asset_id=? AND reference_kind='HERO'
            AND status='ACTIVE' ORDER BY CASE WHEN asset_state_id IS NULL THEN 0 ELSE 1 END, priority, created_at DESC, id LIMIT 1""",
            (asset_id,),
        ).fetchone()

    @staticmethod
    def _profile_input_role(connection: Any, profile: Any) -> tuple[str | None, str | None]:
        if not profile["workflow_version_id"]:
            return None, "PROFILE_WORKFLOW_REQUIRED"
        workflow = connection.execute("SELECT status,node_bindings_json,content_json FROM workflow_versions WHERE id=?", (profile["workflow_version_id"],)).fetchone()
        if workflow is None or str(workflow["status"]) != "PUBLISHED":
            return None, "WORKFLOW_UNAVAILABLE"
        try:
            contract = json.loads(str(profile["input_contract_json"] or "{}"))
            slots = contract.get("input_slots", contract)
            bindings = json.loads(str(workflow["node_bindings_json"] or "{}"))
            content = json.loads(str(workflow["content_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            return None, "PROFILE_CONTRACT_INVALID"
        if not isinstance(slots, dict) or not isinstance(bindings, dict) or not isinstance(content, dict):
            return None, "PROFILE_CONTRACT_INVALID"
        mandatory = [str(role) for role, spec in slots.items() if isinstance(spec, dict) and int(spec.get("min", 0)) > 0]
        for role in _REFERENCE_ROLE_PREFERENCE:
            spec = slots.get(role)
            binding = bindings.get(role)
            if not isinstance(spec, dict) or int(spec.get("max", 1)) < 1:
                continue
            media = spec.get("media_kinds", spec.get("allowed_media_kinds", spec.get("media_kind")))
            media_values = {str(media).upper()} if isinstance(media, str) else {str(item).upper() for item in media} if isinstance(media, list) else set()
            if media_values and "IMAGE" not in media_values and not any(item.startswith("IMAGE/") for item in media_values):
                continue
            if not isinstance(binding, dict) or not binding.get("node_id") or not binding.get("input"):
                continue
            node = content.get(str(binding["node_id"]))
            if not isinstance(node, dict) or not isinstance(node.get("inputs"), dict):
                continue
            if any(item != role for item in mandatory):
                continue
            return role, None
        return None, "HERO_INPUT_SLOT_UNAVAILABLE"

    def _variant_plan(self, *, kind: str, yaw: float, prompt: str, hero_media_version_id: str, input_role: str, profile_version_id: str, asset_state_id: str | None, consistency_strength: str, background: str, seed_index: int) -> VariantPlan:
        # Stable per-view seeds make all three candidates independently
        # reproducible without pretending they are one opaque batch result.
        seed = 104729 + seed_index
        return VariantPlan(
            variant_type="BASE",
            parent_variant_id=None,
            branch_reason=f"{self.PURPOSE}_{kind}",
            prompt_revision_id=None,
            profile_version_id=profile_version_id,
            parameter_set={
                "PROMPT": f"{self.PROMPT_PREFIX}, {prompt}, {background.lower()} background",
                "VIEW_KIND": kind,
                "SLOT_KIND": kind,
                "OUTPUT_REFERENCE_KIND": self._output_reference_kind(kind),
                "YAW_DEG": yaw,
                "CONSISTENCY_STRENGTH": consistency_strength.upper(),
                "BACKGROUND": background.upper(),
                "ASSET_STATE_ID": asset_state_id,
                "SEED": seed,
            },
            seed_policy="EXPLICIT",
            explicit_seed=seed,
            bindings=(VariantInput(input_role, hero_media_version_id, 0, None),),
        )

    @staticmethod
    def _blocker(code: str, message: str, suggested_action: str | None = None, **details: Any) -> dict[str, Any]:
        return {"code": code, "message": message, "suggested_action": suggested_action, "details": details}

    def _idempotent_replay(self, project_id: str, key: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            row = connection.execute("SELECT payload_hash,response_json FROM command_idempotencies WHERE scope=? AND idempotency_key=?", (f"{self.IDEMPOTENCY_SCOPE}:{project_id}", key)).fetchone()
        if row is None:
            return None
        payload_hash = _digest(payload)
        if not hmac.compare_digest(str(row["payload_hash"]), payload_hash):
            raise DomainRuleError("IDEMPOTENCY_PAYLOAD_MISMATCH", f"相同 Idempotency-Key 的 {self.CAPABILITY} 请求体不一致")
        return dict(json.loads(str(row["response_json"])))

    def _store_idempotency(self, project_id: str, key: str, payload: dict[str, Any], response: dict[str, Any]) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO command_idempotencies (scope,idempotency_key,payload_hash,response_json) VALUES (?,?,?,?)",
                (f"{self.IDEMPOTENCY_SCOPE}:{project_id}", key, _digest(payload), _canonical(response)),
            )


class AssetExpressionService(AssetMultiViewService):
    CAPABILITY = "IMAGE_EXPRESSION"
    PURPOSE = "ASSET_EXPRESSION_GRID"
    OUTPUT_REFERENCE_KIND = "EXPRESSION_GRID"
    PROMPT_PREFIX = "character expression sheet slot, preserve exact identity, hairstyle and outfit"
    IDEMPOTENCY_SCOPE = "asset-expression"
    AUDIT_ACTION = "ASSET_EXPRESSION_SUBMITTED"
    SPECS = (
        ("NEUTRAL", 0.0, "neutral resting expression"),
        ("HAPPY", 0.0, "happy joyful expression"),
        ("SAD", 0.0, "sad expression"),
        ("ANGRY", 0.0, "angry expression"),
        ("SURPRISED", 0.0, "surprised expression"),
        ("FEARFUL", 0.0, "fearful expression"),
        ("DISGUSTED", 0.0, "disgusted expression"),
        ("DETERMINED", 0.0, "determined expression"),
        ("CRYING", 0.0, "crying expression"),
    )


class AssetDetailService(AssetMultiViewService):
    CAPABILITY = "IMAGE_EDIT"
    PURPOSE = "ASSET_CLOSEUP_DETAIL"
    PROMPT_PREFIX = "character detail study, preserve exact identity, materials, colors and outfit"
    IDEMPOTENCY_SCOPE = "asset-detail"
    AUDIT_ACTION = "ASSET_DETAIL_SUBMITTED"
    SPECS = (
        ("FACE_CLOSEUP", 0.0, "tight face close-up, identity-defining facial features"),
        ("COSTUME_DETAIL", 0.0, "close detail of costume materials, trims and accessories"),
        ("DISTINCTIVE_DETAIL", 0.0, "close detail of the character's distinctive identifying feature"),
    )

    def _output_reference_kind(self, slot_kind: str) -> str:
        return "CLOSEUP" if slot_kind == "FACE_CLOSEUP" else "DETAIL"
