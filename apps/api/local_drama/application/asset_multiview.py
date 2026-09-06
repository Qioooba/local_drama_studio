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
from typing import Any, ClassVar

from local_drama.application.generation import GenerationService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.queries.generation_preferences import GenerationPreferenceQueryService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import VariantInput
from local_drama.infrastructure.database.generation_preference_repository import SqliteGenerationPreferenceRepository
from local_drama.infrastructure.database.sqlite import Database

CAPABILITY = "IMAGE_MULTI_VIEW"
VIEW_SPECS = (
    ("FRONT", 0.0, "strict orthographic front view, exactly 0 degrees, face and torso square to camera, upright symmetrical neutral A-pose, arms relaxed straight beside body, feet parallel and knees straight, not three-quarter, no action pose"),
    ("LEFT", -90.0, "strict orthographic left profile, exactly minus 90 degrees, face nose torso hips and feet in pure side silhouette, character nose points horizontally toward image-left and back of head is on image-right, only the left side visible, upright neutral A-pose, arms relaxed straight beside body, feet parallel and knees straight, preserve the exact hairstyle silhouette from the input HERO, not three-quarter, do not turn toward camera, no action pose"),
    ("RIGHT", 90.0, "strict orthographic right profile, exactly plus 90 degrees, face nose torso hips and feet in pure side silhouette, character nose points horizontally toward image-right and back of head is on image-left, only the right side visible, upright neutral A-pose, arms relaxed straight beside body, feet parallel and knees straight, preserve the exact hairstyle silhouette from the input HERO, not three-quarter, do not turn toward camera, no action pose"),
    ("BACK", 180.0, "strict orthographic back view, exactly 180 degrees, face completely hidden, shoulders hips and heels seen from behind, upright symmetrical neutral A-pose, arms relaxed straight beside body, feet parallel and knees straight, preserve hairstyle and outfit construction, not three-quarter, no action pose"),
    ("TOP", 0.0, "top view, preserve silhouette and proportions"),
    ("BOTTOM", 0.0, "low underside view, preserve silhouette and proportions"),
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
    CAPABILITY_BLOCKER_CODE = "ASSET_MULTI_VIEW_CAPABILITY_UNAVAILABLE"
    CAPABILITY = CAPABILITY
    SPECS: ClassVar[tuple[tuple[str, float, str], ...]] = VIEW_SPECS
    PURPOSE = "ASSET_MULTI_VIEW"
    OUTPUT_REFERENCE_KIND: str | None = None
    PROMPT_PREFIX = "professional character turnaround reference sheet, same exact person as input, preserve identity face age hair outfit materials body proportions and distinctive details, full body head-to-toe, upright neutral A-pose with arms relaxed straight beside the body and feet parallel, knees straight, both hands empty, preserve worn accessories but remove held props weapons and tools, centered on a clean plain background, no crouching, no running, no combat pose, no raised arms"
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
        requested_slots: list[str] | None = None,
        prompt_bundle: dict[str, Any] | None = None,
        seed_offset: int = 0,
    ) -> dict[str, Any]:
        request = self._normalized_request(
            asset_state_id=asset_state_id,
            profile_version_id=profile_version_id,
            consistency_strength=consistency_strength,
            background=background,
            requested_slots=requested_slots,
            prompt_bundle=prompt_bundle,
            seed_offset=seed_offset,
        )
        requested = set(request["requested_slots"])
        selected_specs = [spec for spec in self.SPECS if spec[0] in requested]
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
                        self.CAPABILITY_BLOCKER_CODE,
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
                "views": [item[0] for item in selected_specs],
                "prompt_bundle": request["prompt_bundle"],
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
                "requested_slots": request["requested_slots"],
                "prompt_bundle": request["prompt_bundle"],
                "views": [
                    {"reference_kind": self._output_reference_kind(kind), "yaw_deg": yaw, "semantic_output": kind}
                    for kind, yaw, _prompt in selected_specs
                ],
                "plan_hash": _digest(authority),
                "would_persist_intent": False,
                "would_create_variants": 0 if blockers else len(selected_specs),
                "would_create_jobs": 0 if blockers else len(selected_specs),
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
        requested_slots: list[str] | None = None,
        prompt_bundle: dict[str, Any] | None = None,
        seed_offset: int = 0,
    ) -> dict[str, Any]:
        if not idempotency_key.strip() or len(idempotency_key) > 200:
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", f"{self.CAPABILITY} 提交必须提供 1—200 字符 Idempotency-Key")
        preflight = self.preflight(
            asset_id,
            asset_state_id=asset_state_id,
            profile_version_id=profile_version_id,
            consistency_strength=consistency_strength,
            background=background,
            requested_slots=requested_slots,
            prompt_bundle=prompt_bundle,
            seed_offset=seed_offset,
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
            "requested_slots": preflight["requested_slots"],
            "prompt_bundle": preflight.get("prompt_bundle"),
            "seed_offset": seed_offset,
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
        selected_kinds = {str(item["semantic_output"]) for item in preflight["views"]}
        selected_specs = [
            (index, kind, yaw, prompt)
            for index, (kind, yaw, prompt) in enumerate(self.SPECS)
            if kind in selected_kinds
        ]
        prompt_items = (preflight.get("prompt_bundle") or {}).get("items", {})
        plans = [
            self._variant_plan(
                kind=kind,
                yaw=yaw,
                prompt=str(prompt_items.get(kind, {}).get("positive_prompt") or prompt),
                negative_prompt=str(prompt_items.get(kind, {}).get("negative_prompt") or ""),
                hero_media_version_id=str(hero["media_version_id"]),
                input_role=str(resolution["input_role"]),
                profile_version_id=str(resolution["profile_version_id"]),
                asset_state_id=asset_state_id,
                consistency_strength=consistency_strength,
                background=background,
                seed_index=seed_index,
                seed_offset=seed_offset,
            )
            for seed_index, kind, yaw, prompt in selected_specs
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
            for (_seed_index, kind, _yaw, _prompt), plan, plan_result in zip(selected_specs, plans, planned, strict=True)
        ]
        with self.database.transaction() as connection:
            connection.execute("UPDATE generation_intents SET status='QUEUED', updated_at=CURRENT_TIMESTAMP WHERE id=?", (intent["id"],))
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES ('local-user','producer',?,'generation_intent',?,?,?)""",
                (self.AUDIT_ACTION, intent["id"], f"提交 {self.CAPABILITY} 独立候选任务", _canonical({"asset_id": asset_id, "hero_media_version_id": hero["media_version_id"], "plan_hash": plan_hash, "requested_slots": sorted(selected_kinds)})),
            )
        result = {
            "intent": self.generation.get_intent(str(intent["id"])),
            "items": [
                {"reference_kind": kind, "variant": item["variant"], "job": item["job"]}
                for (_seed_index, kind, _yaw, _prompt), item in zip(selected_specs, submitted, strict=True)
            ],
            "idempotent_replay": False,
        }
        self._store_idempotency(str(preflight["project_id"]), idempotency_key, request_snapshot, result)
        return result

    def _normalized_request(self, *, asset_state_id: str | None, profile_version_id: str | None, consistency_strength: str, background: str, requested_slots: list[str] | None, prompt_bundle: dict[str, Any] | None = None, seed_offset: int = 0) -> dict[str, Any]:
        strength = consistency_strength.strip().upper()
        backdrop = background.strip().upper()
        if strength not in {"LOW", "MEDIUM", "HIGH"}:
            raise DomainRuleError("ASSET_MULTI_VIEW_CONSISTENCY_INVALID", "一致性强度必须是 LOW、MEDIUM 或 HIGH")
        if backdrop not in {"CLEAN", "TRANSPARENT", "ORIGINAL"}:
            raise DomainRuleError("ASSET_MULTI_VIEW_BACKGROUND_INVALID", "背景必须是 CLEAN、TRANSPARENT 或 ORIGINAL")
        available = {kind for kind, _yaw, _prompt in self.SPECS}
        raw_slots = requested_slots if requested_slots is not None else [kind for kind, _yaw, _prompt in self.SPECS]
        normalized_slots = list(dict.fromkeys(str(item).strip().upper() for item in raw_slots))
        invalid = [slot for slot in normalized_slots if slot not in available]
        if not normalized_slots or invalid:
            raise DomainRuleError(
                "ASSET_GENERATION_SLOT_INVALID",
                "请求的生成槽不属于当前能力",
                {"requested_slots": normalized_slots, "available_slots": sorted(available), "invalid_slots": invalid},
            )
        requested = set(normalized_slots)
        slots = [kind for kind, _yaw, _prompt in self.SPECS if kind in requested]
        normalized_bundle = self._normalize_prompt_bundle(prompt_bundle, slots)
        return {"asset_state_id": asset_state_id, "profile_version_id": profile_version_id, "consistency_strength": strength, "background": backdrop, "requested_slots": slots, "prompt_bundle": normalized_bundle, "seed_offset": int(seed_offset)}

    @staticmethod
    def _normalize_prompt_text(value: Any, *, max_chars: int, max_clauses: int) -> str:
        """Keep model-authored prompt content while removing repetition and runaway lists."""
        raw = " ".join(str(value or "").split()).strip(" ,")
        if not raw:
            return ""
        clauses: list[str] = []
        seen: set[str] = set()
        for part in raw.split(","):
            clause = " ".join(part.split()).strip(" .")
            key = clause.casefold()
            if not clause or key in seen:
                continue
            candidate = ", ".join([*clauses, clause])
            if len(candidate) > max_chars or len(clauses) >= max_clauses:
                break
            seen.add(key)
            clauses.append(clause)
        return ", ".join(clauses)

    @staticmethod
    def _normalize_prompt_bundle(prompt_bundle: dict[str, Any] | None, slots: list[str]) -> dict[str, Any] | None:
        if prompt_bundle is None:
            return None
        if not isinstance(prompt_bundle, dict) or str(prompt_bundle.get("source") or "") != "LOCAL_LLM":
            raise DomainRuleError("ASSET_MULTI_VIEW_PROMPT_BUNDLE_INVALID", "多视图提示词必须来自页面调用的本机大模型")
        raw_items = prompt_bundle.get("items")
        if not isinstance(raw_items, dict):
            raise DomainRuleError("ASSET_MULTI_VIEW_PROMPT_BUNDLE_INVALID", "多视图提示词缺少槽位内容")
        items: dict[str, dict[str, str]] = {}
        for kind in slots:
            item = raw_items.get(kind)
            positive = AssetMultiViewService._normalize_prompt_text(
                item.get("positive_prompt") if isinstance(item, dict) else "",
                max_chars=2000,
                max_clauses=48,
            )
            negative = AssetMultiViewService._normalize_prompt_text(
                item.get("negative_prompt") if isinstance(item, dict) else "",
                max_chars=1200,
                max_clauses=40,
            )
            if not positive or not negative:
                raise DomainRuleError("ASSET_MULTI_VIEW_PROMPT_BUNDLE_INVALID", f"{kind} 缺少有效的正向或反向提示词")
            items[kind] = {"positive_prompt": positive, "negative_prompt": negative}
        return {
            "schema_version": "localdrama.asset-multiview-prompts.v1",
            "source": "LOCAL_LLM",
            "provider": str(prompt_bundle.get("provider") or "")[:120],
            "model": str(prompt_bundle.get("model") or "")[:200],
            "revision_guidance": str(prompt_bundle.get("revision_guidance") or "").strip()[:2000],
            "items": items,
        }

    def draft_prompts(
        self,
        asset_id: str,
        *,
        asset_state_id: str | None = None,
        consistency_strength: str = "HIGH",
        background: str = "CLEAN",
        requested_slots: list[str],
        revision_guidance: str = "",
    ) -> dict[str, Any]:
        request = self._normalized_request(
            asset_state_id=asset_state_id,
            profile_version_id=None,
            consistency_strength=consistency_strength,
            background=background,
            requested_slots=requested_slots,
            prompt_bundle=None,
            seed_offset=0,
        )
        with self.database.connect() as connection:
            asset = connection.execute("SELECT name,description,extra_json,kind,status FROM story_assets WHERE id=?", (asset_id,)).fetchone()
        if asset is None or str(asset["kind"]) != "CHARACTER" or str(asset["status"]) != "ACTIVE":
            raise DomainRuleError("STORY_ASSET_NOT_FOUND", "角色资产不存在或不可生成")
        constraints = {kind: prompt for kind, _yaw, prompt in self.SPECS if kind in request["requested_slots"]}
        try:
            extra = json.loads(str(asset["extra_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            extra = {}
        client = LocalLLMService(self.database, self.settings).client()
        system_prompt = (
            "You are a senior character concept-art prompt engineer. Return strict JSON only. "
            "Create truthful production prompts from the supplied character record; do not invent named props, extra people, text, logos, or story facts. "
            "Do not infer gender, age, colors, hairstyle, clothing, held objects, body traits, ethnicity, or species appearance unless that exact fact is explicitly present in the supplied record. "
            "For every unspecified visual trait, say to preserve it exactly from the input HERO image instead of describing a new trait. "
            "Never convert personality, skills, movement, or story action into the pose. Every view is an upright neutral turnaround A-pose with arms relaxed beside the body, straight knees, and parallel feet. "
            "Turnaround hands must be empty in every view. Preserve worn accessories, but never include held props, weapons, or tools even when the record mentions them; those belong in separate prop assets. "
            "The positive prompt must preserve the exact input identity and enforce the requested orthographic view. "
            "The reviewer may supply revision_guidance describing a visible defect and the intended correction. Apply those explicit visual requirements while retaining all unchanged character traits and view constraints. "
            "The negative prompt must explicitly reject wrong camera angle, three-quarter view, identity drift, anatomy defects, crop, extra subjects, text, logo, watermark, crouching, running, combat pose, bent knees, raised arms, and all held objects. "
            "Hard limit: positive_prompt at most 12 comma-separated clauses and negative_prompt at most 12; each clause at most 12 English words. No duplicate or near-duplicate clauses and no exhaustive cosmetic or hair-product lists."
        )
        by_kind: dict[str, Any] = {}
        for kind in request["requested_slots"]:
            response = client.chat_json(
                system=system_prompt,
                user=_canonical({
                    "asset_name": str(asset["name"]),
                    "asset_description": str(asset["description"] or ""),
                    "asset_record": extra if isinstance(extra, dict) else {},
                    "consistency_strength": request["consistency_strength"],
                    "background": request["background"],
                    "revision_guidance": revision_guidance.strip()[:2000],
                    "requested_view": {"kind": kind, "constraint": constraints[kind]},
                    "output_contract": {"kind": kind, "positive_prompt": "English", "negative_prompt": "English"},
                }),
                json_schema={
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": [kind]},
                        "positive_prompt": {"type": "string"},
                        "negative_prompt": {"type": "string"},
                    },
                    "required": ["kind", "positive_prompt", "negative_prompt"],
                },
                inference_options={"max_tokens": 1024, "temperature": 0},
            )
            if isinstance(response, dict):
                by_kind[kind] = response
        bundle = {
            "schema_version": "localdrama.asset-multiview-prompts.v1",
            "source": "LOCAL_LLM",
            "provider": str(getattr(client, "provider", "") or ""),
            "model": str(getattr(client, "model", "") or ""),
            "revision_guidance": revision_guidance.strip()[:2000],
            "items": by_kind,
        }
        normalized = self._normalize_prompt_bundle(bundle, request["requested_slots"])
        assert normalized is not None
        return {**normalized, "content_hash": _digest(normalized)}

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
        mandatory_media: list[str] = []
        for slot_role, slot_spec in slots.items():
            if not isinstance(slot_spec, dict) or int(slot_spec.get("min", 0)) <= 0:
                continue
            declared_media = slot_spec.get(
                "media_kinds",
                slot_spec.get("allowed_media_kinds", slot_spec.get("media_kind")),
            )
            declared_values = (
                {str(declared_media).upper()}
                if isinstance(declared_media, str)
                else {str(item).upper() for item in declared_media}
                if isinstance(declared_media, list)
                else set()
            )
            if declared_values or str(slot_role) in _REFERENCE_ROLE_PREFERENCE:
                mandatory_media.append(str(slot_role))
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
            # Required scalar roles such as PROMPT are supplied through semantic_inputs,
            # not media bindings.  Only a second mandatory media slot makes the single
            # HERO reference insufficient for this asset-generation facade.
            if any(item != role for item in mandatory_media):
                continue
            return role, None
        return None, "HERO_INPUT_SLOT_UNAVAILABLE"

    def _variant_plan(self, *, kind: str, yaw: float, prompt: str, negative_prompt: str = "", hero_media_version_id: str, input_role: str, profile_version_id: str, asset_state_id: str | None, consistency_strength: str, background: str, seed_index: int, seed_offset: int = 0) -> VariantPlan:
        # Stable per-view seeds make all three candidates independently
        # reproducible without pretending they are one opaque batch result.
        seed = 104729 + seed_index + seed_offset
        view_constraint = next(
            (constraint for view_kind, _view_yaw, constraint in self.SPECS if view_kind == kind),
            "",
        )
        return VariantPlan(
            variant_type="BASE",
            parent_variant_id=None,
            branch_reason=f"{self.PURPOSE}_{kind}",
            prompt_revision_id=None,
            profile_version_id=profile_version_id,
            parameter_set={
                # Put the non-negotiable camera direction before the shared identity
                # prefix.  Image text encoders can heavily down-weight or truncate late
                # clauses, which previously made LEFT and RIGHT converge on one profile.
                "PROMPT": f"{view_constraint}, {self.PROMPT_PREFIX}, {prompt}, {background.lower()} background",
                "NEGATIVE_PROMPT": negative_prompt,
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
    CAPABILITY_BLOCKER_CODE = "ASSET_EXPRESSION_CAPABILITY_UNAVAILABLE"
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
    CAPABILITY_BLOCKER_CODE = "ASSET_DETAIL_CAPABILITY_UNAVAILABLE"
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
