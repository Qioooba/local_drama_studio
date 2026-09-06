"""Project-production batch orchestration for shot first/end frame candidates."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from local_drama.application.ports.creative_generation import (
    GenerationCommandPort,
    GenerationPreferenceResolverFactory,
    MediaPromotionPort,
)
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import VariantInput
from local_drama.application.shot_identity_references import shot_identity_references
from local_drama.domain.shot_keyframe_route import SHOT_KEYFRAME_SINGLE_FRAME, validate_shot_keyframe_route
from local_drama.domain.shot_prompt import compose_shot_prompt
from local_drama.domain.shot_prompt_bundle import (
    DEFAULT_SHOT_NEGATIVE_PROMPT,
    PROMPT_BUNDLE_SCHEMA_VERSION,
    compile_shot_prompt_bundle,
    normalize_frame_reframe_mode,
)

from .workflow_contracts import effective_workflow_contract, latest_bound_profile_for_capability


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class ShotKeyframeGenerationBatchService:
    MAX_SHOTS = 100

    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        generation: GenerationCommandPort,
        preference_resolver_factory: GenerationPreferenceResolverFactory,
    ) -> None:
        self.database = database
        self.settings = settings
        self.generation = generation
        self.preference_resolver_factory = preference_resolver_factory

    @staticmethod
    def _seed(shot_id: str, role: str, candidate_index: int, draw_key: str = "") -> int:
        # candidate_index is deliberately scoped to one draw (1—4).  Include
        # the visible submit's idempotency key so a later redraw gets genuinely
        # different deterministic seeds while an idempotent replay stays stable.
        source = f"{shot_id}:{role}:{candidate_index}:{draw_key}"
        return 100_000 + int(hashlib.sha256(source.encode()).hexdigest()[:8], 16) % 900_000

    @staticmethod
    def _next_candidate_indices(completed: int, requested: int) -> range:
        """Return the candidate slots for one draw; completed is informational."""
        del completed
        return range(1, requested + 1)

    @staticmethod
    def _action_endpoint(fields: dict[str, Any]) -> str:
        action = str(
            fields.get("subject_action")
            or fields.get("action")
            or (fields.get("performance") or {}).get("body_action")
            or ""
        ).strip()
        if not action:
            return ""
        transition_parts = re.split(r"(?:切至|转至|随后|继而|最后|最终|镜头转向)", action)
        endpoint = transition_parts[-1].strip(" ，。；;、")
        if len(transition_parts) == 1:
            sentences = [part.strip() for part in re.split(r"[。！？!?；;]+", endpoint) if part.strip()]
            if sentences:
                endpoint = sentences[-1]
        return endpoint[:800]

    @staticmethod
    def _action_segments(fields: dict[str, Any]) -> list[str]:
        action = str(
            fields.get("subject_action")
            or fields.get("action")
            or (fields.get("performance") or {}).get("body_action")
            or ""
        ).strip()
        if not action:
            return []
        segments = re.split(r"(?:切至|转至|随后|继而|最后|最终|镜头转向)|[。！？!?；;]+", action)
        return [segment.strip(" ，。；;、") for segment in segments if segment.strip(" ，。；;、")]

    @staticmethod
    def _clean_frame_moment(value: str) -> str:
        # Camera/editing verbs describe a sequence, not a drawable still.
        # Remove only leading verbs; keep the shot's concrete subjects and
        # action words intact for traceability.
        cleaned = re.sub(
            r"^(?:镜头\s*)?(?:扫过|掠过|推进到|推近至|拉近至|转向|对准|跟随|特写|切至|转至)\s*",
            "",
            value.strip(),
        )
        return cleaned.strip(" ，。；;、") or value.strip(" ，。；;、")

    @classmethod
    def _single_moment_prompt(cls, fields: dict[str, Any], shot_code: str, role: str) -> str:
        segments = cls._action_segments(fields)
        selected = cls._clean_frame_moment(segments[0] if role == "FIRST_FRAME" else segments[-1]) if segments else ""
        if not selected:
            selected = str(fields.get("environment") or fields.get("creative_intent") or "当前镜头主体与空间关系").strip()
        role_label = "首帧只呈现第一个视觉瞬间" if role == "FIRST_FRAME" else "尾帧只呈现最后一个视觉瞬间"
        parts = [
            f"镜头 {shot_code}",
            "单一画面重构",
            f"{role_label}：{selected}",
        ]
        # Do not append the full creative intent/environment here.  Those
        # fields frequently contain the complete edit sentence (for example
        # “扫过…切至…”), which turns a still-frame request back into a
        # storyboard/contact-sheet request.  The immutable source base stays
        # in PromptBundle for audit; only the role-specific drawable moment
        # is sent to the image workflow.
        parts.append(
            "只呈现一个连续瞬间和一个空间位置；不要呈现其他时序、后续或前序动作、场景切换；"
            "单镜头、满画幅竖屏、单帧电影画面，无文字无水印"
        )
        return "；".join(parts)[:3000]

    @classmethod
    def _frame_prompt(
        cls,
        fields: dict[str, Any],
        shot_code: str,
        role: str,
        *,
        reframe_mode: str = "NONE",
    ) -> str:
        if normalize_frame_reframe_mode(reframe_mode) == "SINGLE_MOMENT":
            return cls._single_moment_prompt(fields, shot_code, role)
        base = compose_shot_prompt(fields, shot_code=shot_code)
        if role == "END_FRAME":
            endpoint = ShotKeyframeGenerationBatchService._action_endpoint(fields)
            endpoint_focus = f"画面只呈现动作终点：{endpoint}；" if endpoint else ""
            prefix = f"镜头结束定格；{endpoint_focus}不要重复镜头开场画面。"
            suffix = "保持人物身份、服装、场景空间、光线和轴线连续；单帧电影画面，无文字无水印"
            return f"{prefix}{suffix}。镜头上下文：{base}"[:3000]
        else:
            suffix = "镜头开始定格，清楚建立主体、动作起点与空间关系；保持项目人物和场景设定；单帧电影画面，无文字无水印"
        return f"{base}。{suffix}"[:3000]

    @staticmethod
    def _capability_for_shot(connection: Any, shot_id: str) -> str:
        kinds = {
            str(row[0]) for row in connection.execute(
                """SELECT DISTINCT a.kind FROM shot_asset_bindings b
                JOIN story_assets a ON a.id=b.asset_id
                WHERE b.shot_id=? AND a.status='ACTIVE'""",
                (shot_id,),
            ).fetchall()
        }
        if "CHARACTER" in kinds:
            return "IMAGE_CHARACTER"
        if "SCENE" in kinds:
            return "IMAGE_SCENE"
        return "IMAGE_CONCEPT"

    @staticmethod
    def _workflow_route(connection: Any, profile: Any) -> dict[str, Any]:
        """Resolve and validate the single-frame route for a published profile."""

        if profile is None or str(profile["status"]) != "PUBLISHED" or not profile["workflow_version_id"]:
            return {
                "status": "BLOCKED",
                "route_capability": "",
                "bindings": {},
                "blockers": [],
                "facts": {"source": "NONE"},
            }
        workflow_version_id = str(profile["workflow_version_id"])
        effective = effective_workflow_contract(connection, workflow_version_id)
        static_contract = effective["workflow_version_contract"]
        route = validate_shot_keyframe_route(
            route_capability=effective["capability"] if effective["source"] == "APP_CONTRACT" else "",
            profile_capability=profile["capability"],
            workflow_capability=static_contract.get("capability") if isinstance(static_contract, dict) else "",
            contract=effective["workflow_contract"],
            bindings=effective["workflow_bindings"],
            workflow=effective["workflow_content"],
        )
        if effective["source"] != "APP_CONTRACT" and effective["published_contract_id"]:
            route["blockers"] = [
                *route["blockers"],
                {
                    "code": "SHOT_KEYFRAME_RUNTIME_BINDING_REQUIRED",
                    "message": "已发布的单画幅契约尚未绑定已发布 Comfy 运行环境",
                },
            ]
            route["status"] = "BLOCKED"
        route["facts"] = {
            **route.get("facts", {}),
            "source": effective["source"],
            "profile_version_id": str(profile["id"]),
            "profile_capability": str(profile["capability"]),
            "workflow_version_id": workflow_version_id,
            "contract_id": effective["contract_id"],
            "published_contract_id": effective["published_contract_id"],
            "runtime_environment_version_id": effective["runtime_environment_version_id"],
            "published_contract_bound": effective["published_contract_bound"],
        }
        return route

    @staticmethod
    def _supports_keyframe_semantics(connection: Any, profile: Any) -> bool:
        """Return whether a published profile passes the strict shot route."""

        return ShotKeyframeGenerationBatchService._workflow_route(connection, profile)["status"] == "READY"

    @staticmethod
    def _workflow_bindings(connection: Any, profile: Any) -> dict[str, Any]:
        """Read the same effective semantic bindings used by execution."""

        route = ShotKeyframeGenerationBatchService._workflow_route(connection, profile)
        return route["bindings"] if isinstance(route.get("bindings"), dict) else {}

    @staticmethod
    def _blocked_prompt_bundle() -> dict[str, Any]:
        """Keep the read-only blocked plan serializable without inventing a prompt."""
        return {
            "schema_version": PROMPT_BUNDLE_SCHEMA_VERSION,
            "base_prompt": "",
            "effective_base_prompt": "",
            "frame_role": None,
            "frame_reframe_mode": "NONE",
            "positive_override": "",
            "negative_prompt": DEFAULT_SHOT_NEGATIVE_PROMPT,
            "provenance": "AI_GENERATED",
            "compiler_mode": "PROMPT_AVOID_FALLBACK",
            "final_prompt": "",
        }

    def plan(
        self,
        episode_id: str,
        *,
        targets: list[dict[str, Any]],
        frame_strategy: str = "FIRST_ONLY",
        candidate_count: int = 2,
        profile_version_id: str | None = None,
        prompt_bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if frame_strategy not in {"FIRST_ONLY", "FIRST_AND_LAST"}:
            raise DomainRuleError("SHOT_KEYFRAME_STRATEGY_INVALID", "镜头帧策略必须是只生成首帧或同时生成首尾帧")
        if candidate_count not in {1, 2, 3, 4}:
            raise DomainRuleError("SHOT_KEYFRAME_CANDIDATE_COUNT_INVALID", "每镜候选数必须为 1—4")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw in targets:
            shot_id = str(raw.get("shot_id") or "").strip()
            if not shot_id or shot_id in seen:
                continue
            seen.add(shot_id)
            normalized.append({"shot_id": shot_id, "expected_revision": int(raw.get("expected_revision") or 0)})
        if not normalized:
            raise DomainRuleError("SHOT_KEYFRAME_BATCH_EMPTY", "请至少选择一个镜头")
        if len(normalized) > self.MAX_SHOTS:
            raise DomainRuleError("SHOT_KEYFRAME_BATCH_TOO_LARGE", f"单批最多处理 {self.MAX_SHOTS} 个镜头")

        with self.database.connect() as connection:
            episode = connection.execute(
                """SELECT e.id,se.project_id FROM episodes e JOIN seasons se ON se.id=e.season_id WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "分集不存在")
            project_id = str(episode["project_id"])
            resolver = self.preference_resolver_factory(connection)
            items: list[dict[str, Any]] = []
            issues: list[dict[str, Any]] = []
            roles = ("FIRST_FRAME", "END_FRAME") if frame_strategy == "FIRST_AND_LAST" else ("FIRST_FRAME",)
            reframe_mode = normalize_frame_reframe_mode((prompt_bundle or {}).get("frame_reframe_mode"))
            for target in normalized:
                row = connection.execute(
                    """SELECT sh.id,sh.code,sh.revision,sh.current_revision_id,sr.fields_json
                    FROM shots sh LEFT JOIN shot_revisions sr ON sr.id=sh.current_revision_id
                    WHERE sh.id=? AND sh.episode_id=? AND sh.archived_at IS NULL""",
                    (target["shot_id"], episode_id),
                ).fetchone()
                if row is None:
                    issues.append({"code": "SHOT_NOT_FOUND", "shot_id": target["shot_id"], "message": "镜头不存在或不属于当前分集"})
                    continue
                fields = json.loads(str(row["fields_json"] or "{}"))
                # A shot code is an identifier, not creative content.  Keep it
                # in the emitted prompt, but do not let it satisfy the
                # no-prompt guard by itself.
                prompt_base = compose_shot_prompt(fields, shot_code=None)
                capability = self._capability_for_shot(connection, str(row["id"]))
                resolution = resolver.resolve(project_id=project_id, episode_id=episode_id, shot_id=str(row["id"]), capability=capability)
                selected_profile_id = profile_version_id or resolution.get("profile_version_id")
                profile = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (selected_profile_id,)).fetchone() if selected_profile_id else None
                # AUTO profile resolution predates Workflow App Contracts and
                # may keep returning an older IMAGE_CONCEPT profile even after
                # the operator has explicitly bound a different single-frame
                # workflow/runtime tuple.  For shot keyframes the bound route
                # is authoritative: prefer its newest published profile, but
                # preserve an explicit project/episode/shot selection and let
                # the normal readiness checks fail closed when no route exists.
                if profile_version_id is None and str(resolution.get("source") or "") == "AUTO":
                    identity_reference_count = int(connection.execute(
                        """SELECT COUNT(*) FROM shot_asset_bindings b JOIN story_assets a ON a.id=b.asset_id
                        WHERE b.shot_id=? AND a.kind='CHARACTER' AND b.identity_pack_version_id IS NOT NULL""",
                        (row["id"],),
                    ).fetchone()[0])
                    bound_profile = latest_bound_profile_for_capability(
                        connection,
                        route_capability=SHOT_KEYFRAME_SINGLE_FRAME,
                        profile_capabilities=(capability, "IMAGE_CONCEPT", "IMAGE_EDIT"),
                        identity_reference_count=identity_reference_count,
                    )
                    if bound_profile is not None:
                        selected_profile_id = str(bound_profile["id"])
                        profile = bound_profile
                if (
                    profile_version_id is None
                    and capability in {"IMAGE_CHARACTER", "IMAGE_SCENE"}
                    and not self._supports_keyframe_semantics(connection, profile)
                ):
                    generic_resolution = resolver.resolve(
                        project_id=project_id,
                        episode_id=episode_id,
                        shot_id=str(row["id"]),
                        capability="IMAGE_CONCEPT",
                    )
                    generic_profile_id = generic_resolution.get("profile_version_id")
                    generic_profile = connection.execute(
                        "SELECT * FROM execution_profile_versions WHERE id=?", (generic_profile_id,)
                    ).fetchone() if generic_profile_id else None
                    if self._supports_keyframe_semantics(connection, generic_profile):
                        selected_profile_id = generic_profile_id
                        profile = generic_profile
                        resolution = {
                            **generic_resolution,
                            "requested_capability": capability,
                            "crosswalk": "IMAGE_CONCEPT",
                        }
                workflow_route = self._workflow_route(connection, profile)
                workflow_bindings = workflow_route["bindings"] if isinstance(workflow_route.get("bindings"), dict) else {}
                shot_blockers: list[dict[str, Any]] = []
                if int(row["revision"]) != target["expected_revision"]:
                    shot_blockers.append({"code": "SHOT_REVISION_CONFLICT", "message": "镜头已变化，请重新检查", "current_revision": int(row["revision"])})
                if not prompt_base.strip():
                    shot_blockers.append({"code": "SHOT_KEYFRAME_PROMPT_REQUIRED", "message": "镜头还没有可用于关键帧的导演描述"})
                profile_capability = str(profile["capability"]).upper() if profile is not None else ""
                compatible_profile = profile_capability == capability or (
                    capability in {"IMAGE_CHARACTER", "IMAGE_SCENE"} and profile_capability in {"IMAGE_CONCEPT", "IMAGE_EDIT"}
                )
                if profile is None or str(profile["status"]) != "PUBLISHED" or not compatible_profile:
                    shot_blockers.append({"code": "SHOT_KEYFRAME_PROFILE_REQUIRED", "message": f"当前镜头没有可执行的 {capability} 图片配置"})
                elif workflow_route["status"] != "READY" and not workflow_route.get("blockers"):
                    shot_blockers.append({
                        "code": "SHOT_KEYFRAME_WORKFLOW_BINDINGS_REQUIRED",
                        "message": "当前图片配置未绑定关键帧生成所需的提示词与随机种子",
                    })
                shot_blockers.extend(workflow_route.get("blockers", []))
                missing_refs = connection.execute(
                    """SELECT a.id,a.name FROM shot_asset_bindings b JOIN story_assets a ON a.id=b.asset_id
                    WHERE b.shot_id=? AND a.status='ACTIVE' AND a.kind IN ('CHARACTER','SCENE','PROP')
                    AND a.canonical_media_version_id IS NULL
                    AND NOT EXISTS(SELECT 1 FROM story_asset_references r WHERE r.story_asset_id=a.id AND r.reference_kind='HERO' AND r.status='ACTIVE')""",
                    (row["id"],),
                ).fetchall()
                if missing_refs:
                    shot_blockers.append({"code": "SHOT_ASSET_HERO_REQUIRED", "message": "绑定资产缺少主参考", "assets": [str(item["name"]) for item in missing_refs]})
                identity_inputs: dict[str, Any] = {"snapshot_hash": None, "references": [], "prompt": ""}
                try:
                    identity_inputs = shot_identity_references(connection, project_id, str(row["id"]), workflow_bindings)
                    reference_roles = {ref["role"] for ref in identity_inputs["references"]}
                    declared_slots = json.loads(str(profile["input_contract_json"] or "{}")) if profile is not None else {}
                    profile_slots = declared_slots.get("input_slots", {})
                    for reference_role in reference_roles:
                        if reference_role not in profile_slots:
                            shot_blockers.append({"code": "SHOT_IDENTITY_PROFILE_INPUT_REQUIRED", "message": f"图片配置没有声明 {reference_role} 媒体输入槽，请在能力配置中更新输入契约"})
                    contract_inputs = workflow_route.get("contract", {}).get("inputs", {})
                    for reference_role, spec in contract_inputs.items():
                        if str(reference_role).startswith("REFERENCE_IMAGE") and isinstance(spec, dict) and spec.get("required", True) and reference_role not in reference_roles:
                            shot_blockers.append({"code": "SHOT_IDENTITY_REFERENCE_INPUT_REQUIRED", "message": f"当前工作流要求 {reference_role}，镜头没有对应人物身份包；请选择匹配人数的工作流或绑定人物身份包"})
                except DomainRuleError as error:
                    shot_blockers.append({"code": error.code, "message": error.message, **(error.details or {})})
                for role in roles:
                    if role == "FIRST_FRAME":
                        completed = connection.execute(
                            """SELECT COUNT(*) FROM media_versions mv JOIN media_assets ma ON ma.id=mv.media_asset_id
                            JOIN generation_variants gv ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
                            JOIN generation_intents gi ON gi.id=gv.intent_id
                            WHERE gi.owner_type='SHOT' AND gi.owner_id=? AND gi.purpose='T2I'
                            AND mv.stage='KEYFRAME' AND mv.integrity_status='VERIFIED'""",
                            (row["id"],),
                        ).fetchone()[0]
                    else:
                        completed = connection.execute(
                            """SELECT COUNT(*) FROM shot_keyframe_generation_batch_items i
                            JOIN shot_keyframe_generation_batches b ON b.id=i.batch_id
                            WHERE i.shot_id=? AND i.frame_role=? AND i.status='SUCCEEDED'""",
                            (row["id"], role),
                        ).fetchone()[0]
                    for candidate_index in self._next_candidate_indices(int(completed), candidate_count):
                        item_blockers = list(shot_blockers)
                        frame_prompt = self._frame_prompt(
                            fields,
                            str(row["code"]),
                            role,
                            reframe_mode=reframe_mode,
                        )
                        if identity_inputs["prompt"]:
                            frame_prompt = f"{identity_inputs['prompt']}。镜头画面：{frame_prompt}"
                        if prompt_base.strip():
                            item_prompt_bundle = compile_shot_prompt_bundle(
                                prompt_bundle,
                                base_prompt=prompt_base,
                                effective_base_prompt=frame_prompt,
                                frame_role=role,
                                frame_reframe_mode=reframe_mode,
                                workflow_bindings=workflow_bindings,
                            )
                        else:
                            item_prompt_bundle = self._blocked_prompt_bundle()
                        item = {
                            "shot_id": str(row["id"]), "shot_code": str(row["code"]), "shot_revision": int(row["revision"]),
                            "frame_role": role, "candidate_index": candidate_index,
                            "capability": capability,
                            "profile_version_id": str(profile["id"]) if profile is not None else None,
                            "shot_keyframe_route": workflow_route.get("facts", {}),
                            "workflow_bindings": workflow_bindings,
                            "identity_inputs": identity_inputs,
                            "prompt": str(item_prompt_bundle["final_prompt"]),
                            "prompt_bundle": item_prompt_bundle,
                            "status": "BLOCKED" if item_blockers else "READY", "blockers": item_blockers,
                        }
                        item["semantic_inputs"] = {
                            "PROMPT": str(item_prompt_bundle["final_prompt"]),
                            **(
                                {"NEGATIVE_PROMPT": str(item_prompt_bundle["negative_prompt"])}
                                if "NEGATIVE_PROMPT" in workflow_bindings
                                else {}
                            ),
                            # The plan is read-only and does not yet have the
                            # submit idempotency key used to derive each
                            # candidate's explicit seed.  Preserve the
                            # declared semantic slot in the evidence so the
                            # page can show the complete contract without
                            # pretending a seed has already been frozen.
                            **({"SEED": None} if "SEED" in workflow_bindings else {}),
                        }
                        items.append(item)
                        issues.extend({**blocker, "shot_id": str(row["id"]), "frame_role": role} for blocker in item_blockers)
            representative = next((item for item in items if item.get("shot_keyframe_route")), None)
            representative_route = representative.get("shot_keyframe_route", {}) if representative else {}
            representative_bindings = representative.get("workflow_bindings", {}) if representative else {}
            representative_bundle = representative.get("prompt_bundle", {}) if representative else {}
            execution_contract = {
                "source": representative_route.get("source") or "NONE",
                "contract_id": representative_route.get("contract_id"),
                "runtime_environment_version_id": representative_route.get("runtime_environment_version_id"),
                "published_contract_bound": bool(representative_route.get("published_contract_bound")),
                "compiler_mode": representative_bundle.get("compiler_mode"),
                "semantic_roles": sorted(str(role) for role in representative_bindings),
                "workflow_bindings": representative_bindings if isinstance(representative_bindings, dict) else {},
                "seed_policy": "EXPLICIT_SUBMIT_SEED" if "SEED" in representative_bindings else None,
            }
            authority = {
                "episode_id": episode_id, "project_id": project_id, "targets": normalized,
                "frame_strategy": frame_strategy, "candidate_count": candidate_count,
                "prompt_bundle": prompt_bundle,
                "execution_contract": execution_contract,
                "items": [{key: item[key] for key in ("shot_id", "shot_revision", "frame_role", "candidate_index", "capability", "profile_version_id", "shot_keyframe_route", "identity_inputs", "semantic_inputs", "prompt", "prompt_bundle", "status")} for item in items],
            }
        ready = sum(item["status"] == "READY" for item in items)
        unique_issues: list[dict[str, Any]] = []
        seen_issues: set[str] = set()
        for issue in issues:
            signature = json.dumps(
                {key: value for key, value in issue.items() if key != "frame_role"},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if signature in seen_issues:
                continue
            seen_issues.add(signature)
            unique_issues.append(issue)
        return {
            **authority, "plan_hash": _digest(authority), "valid": ready > 0,
            "issues": unique_issues, "items": items,
            "summary": {"shots": len(normalized), "jobs": ready, "blocked": sum(item["status"] == "BLOCKED" for item in items)},
            "runtime_contacted": False, "network_contacted": False, "mutated": False,
        }

    def submit(self, episode_id: str, *, targets: list[dict[str, Any]], frame_strategy: str, candidate_count: int,
               expected_plan_hash: str, idempotency_key: str, profile_version_id: str | None = None,
               prompt_bundle: dict[str, Any] | None = None,
               actor: str = "local-user") -> dict[str, Any]:
        if not idempotency_key.strip():
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "提交关键帧批次需要幂等键")
        with self.database.connect() as connection:
            replay = connection.execute(
                "SELECT id FROM shot_keyframe_generation_batches WHERE episode_id=? AND idempotency_key=?",
                (episode_id, idempotency_key),
            ).fetchone()
        if replay:
            result = self.get_batch(str(replay["id"]))
            result["idempotent_replay"] = True
            return result
        plan = self.plan(
            episode_id,
            targets=targets,
            frame_strategy=frame_strategy,
            candidate_count=candidate_count,
            profile_version_id=profile_version_id,
            prompt_bundle=prompt_bundle,
        )
        if not hmac.compare_digest(str(plan["plan_hash"]), expected_plan_hash):
            raise DomainRuleError("SHOT_KEYFRAME_PLAN_STALE", "镜头、资产或生成配置已变化，请重新检查")
        if not plan["valid"]:
            raise DomainRuleError("SHOT_KEYFRAME_BATCH_BLOCKED", "关键帧批次存在阻塞", {"issues": plan["issues"]})
        ready = [item for item in plan["items"] if item["status"] == "READY"]
        batch_id, now = str(uuid.uuid4()), _now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO shot_keyframe_generation_batches
                (id,project_id,episode_id,frame_strategy,candidate_count,status,plan_hash,idempotency_key,selected_shot_count,queued_count,created_at,updated_at,created_by,revision,schema_version,input_snapshot_json)
                VALUES (?,?,?,?,?,'QUEUING',?,?,?,0,?,?,?,1,'v1',?)""",
                (batch_id, plan["project_id"], episode_id, frame_strategy, candidate_count, expected_plan_hash, idempotency_key, plan["summary"]["shots"], now, now, actor, _canonical({
                    "schema_version": PROMPT_BUNDLE_SCHEMA_VERSION,
                    "targets": targets,
                    "frame_strategy": frame_strategy,
                    "candidate_count": candidate_count,
                    "prompt_bundle_request": prompt_bundle or {},
                    "prompt_bundles": [item["prompt_bundle"] for item in ready],
                    "semantic_inputs": [item["semantic_inputs"] for item in ready],
                    "shot_keyframe_routes": [item["shot_keyframe_route"] for item in ready],
                })),
            )
            for item in ready:
                connection.execute(
                    """INSERT INTO shot_keyframe_generation_batch_items
                    (id,batch_id,shot_id,shot_revision,frame_role,candidate_index,profile_version_id,status,prompt_snapshot,input_snapshot_json,created_at,updated_at,revision,schema_version)
                    VALUES (?,?,?,?,?,?,?,'PLANNED',?,?,?,?,1,'v1')""",
                    (str(uuid.uuid4()), batch_id, item["shot_id"], item["shot_revision"], item["frame_role"], item["candidate_index"], item["profile_version_id"], item["prompt"], _canonical({
                        "schema_version": PROMPT_BUNDLE_SCHEMA_VERSION,
                        "shot_id": item["shot_id"],
                        "shot_code": item["shot_code"],
                        "shot_revision": item["shot_revision"],
                        "frame_role": item["frame_role"],
                        "candidate_index": item["candidate_index"],
                        "profile_version_id": item["profile_version_id"],
                        "prompt_bundle": item["prompt_bundle"],
                        "semantic_inputs": item["semantic_inputs"],
                        "shot_keyframe_route": item["shot_keyframe_route"],
                        "identity_inputs": item["identity_inputs"],
                    }), now, now),
                )
        queued = 0
        for item in ready:
            try:
                intent = self.generation.create_shot_intent(
                    item["shot_id"], purpose="T2I", creative_goal=item["prompt"],
                    idempotency_key=f"{idempotency_key}:intent:{item['shot_id']}:{item['frame_role']}:{item['candidate_index']}",
                )
                seed = self._seed(
                    item["shot_id"], item["frame_role"], item["candidate_index"], idempotency_key
                )
                variant = VariantPlan(
                    variant_type="BASE", parent_variant_id=None,
                    branch_reason=f"SHOT_{item['frame_role']}_CANDIDATE_{item['candidate_index']}", prompt_revision_id=None,
                    profile_version_id=item["profile_version_id"], parameter_set={**item["semantic_inputs"], "SEED": seed},
                    seed_policy="EXPLICIT", explicit_seed=seed,
                    bindings=tuple(VariantInput(ref["role"], ref["media_version_id"], 0, None)
                                   for ref in item["identity_inputs"]["references"]),
                    prompt_bundle=item["prompt_bundle"],
                    expected_identity_pack_snapshot_hash=item["identity_inputs"]["snapshot_hash"],
                )
                checked = self.generation.preflight_shot_base_variant(item["shot_id"], str(intent["id"]), variant, item["shot_revision"], "SHOT_IMAGE")
                submitted = self.generation.submit_shot_base_variant(
                    item["shot_id"], str(intent["id"]), variant, expected_shot_revision=item["shot_revision"],
                    stage_code="SHOT_IMAGE", plan_hash=str(checked["plan_hash"]),
                    idempotency_key=f"{idempotency_key}:job:{item['shot_id']}:{item['frame_role']}:{item['candidate_index']}",
                )
                with self.database.transaction() as connection:
                    connection.execute(
                        """UPDATE shot_keyframe_generation_batch_items SET status='QUEUED',intent_id=?,variant_id=?,job_id=?,updated_at=?,revision=revision+1
                        WHERE batch_id=? AND shot_id=? AND frame_role=? AND candidate_index=?""",
                        (intent["id"], submitted["variant"]["id"], submitted["job"]["id"], _now(), batch_id, item["shot_id"], item["frame_role"], item["candidate_index"]),
                    )
                queued += 1
            except DomainRuleError as error:
                with self.database.transaction() as connection:
                    connection.execute(
                        """UPDATE shot_keyframe_generation_batch_items SET status='FAILED',error_code=?,error_detail_redacted=?,updated_at=?,revision=revision+1
                        WHERE batch_id=? AND shot_id=? AND frame_role=? AND candidate_index=?""",
                        (error.code, error.message, _now(), batch_id, item["shot_id"], item["frame_role"], item["candidate_index"]),
                    )
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE shot_keyframe_generation_batches SET status=?,queued_count=?,updated_at=?,revision=revision+1 WHERE id=?",
                ("QUEUED" if queued else "FAILED", queued, _now(), batch_id),
            )
        result = self.get_batch(batch_id)
        result["idempotent_replay"] = False
        return result

    def list_batches(self, episode_id: str, limit: int = 10) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id FROM shot_keyframe_generation_batches WHERE episode_id=? ORDER BY created_at DESC,id DESC LIMIT ?",
                (episode_id, max(1, min(limit, 20))),
            ).fetchall()
        return [self.get_batch(str(row["id"])) for row in rows]

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            batch = connection.execute("SELECT * FROM shot_keyframe_generation_batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None:
                raise DomainRuleError("SHOT_KEYFRAME_BATCH_NOT_FOUND", "关键帧批次不存在")
            rows = connection.execute(
                """SELECT i.*,sh.code AS shot_code,j.state AS job_state,j.progress_json,j.last_error_code,j.last_error_detail_redacted
                FROM shot_keyframe_generation_batch_items i JOIN shots sh ON sh.id=i.shot_id
                LEFT JOIN jobs j ON j.id=i.job_id WHERE i.batch_id=? ORDER BY sh.order_key,i.frame_role,i.candidate_index""",
                (batch_id,),
            ).fetchall()
        try:
            batch_snapshot = json.loads(str(batch["input_snapshot_json"] or "{}"))
        except (KeyError, TypeError, json.JSONDecodeError):
            batch_snapshot = {}
        items = []
        for row in rows:
            status, job_state = str(row["status"]), str(row["job_state"] or "")
            if status not in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                if job_state in {"RUNNING", "CLAIMED"}:
                    status = "RUNNING"
                elif job_state in {"FAILED", "NEEDS_ATTENTION", "ORPHANED"}:
                    status = "FAILED"
                elif job_state == "CANCELLED":
                    status = "CANCELLED"
            try:
                item_snapshot = json.loads(str(row["input_snapshot_json"] or "{}"))
            except (KeyError, TypeError, json.JSONDecodeError):
                item_snapshot = {}
            items.append({
                "id": str(row["id"]), "shot_id": str(row["shot_id"]), "shot_code": str(row["shot_code"]),
                "frame_role": str(row["frame_role"]), "candidate_index": int(row["candidate_index"]), "status": status,
                "job_id": str(row["job_id"]) if row["job_id"] else None, "job_state": job_state or None,
                "media_version_id": str(row["media_version_id"]) if row["media_version_id"] else None,
                "prompt_bundle": item_snapshot.get("prompt_bundle") if isinstance(item_snapshot, dict) else None,
                "error": ({"code": row["error_code"] or row["last_error_code"], "message": row["error_detail_redacted"] or row["last_error_detail_redacted"]} if row["error_code"] or row["last_error_code"] else None),
            })
        success = sum(item["status"] == "SUCCEEDED" for item in items)
        failed = sum(item["status"] in {"FAILED", "CANCELLED"} for item in items)
        active = len(items) - success - failed
        status = "SUCCEEDED" if items and success == len(items) else "PARTIAL_FAILED" if failed and success else "FAILED" if failed == len(items) and items else "RUNNING" if any(item["status"] == "RUNNING" for item in items) else "QUEUED"
        return {
            "id": str(batch["id"]), "project_id": str(batch["project_id"]), "episode_id": str(batch["episode_id"]),
            "frame_strategy": str(batch["frame_strategy"]), "candidate_count": int(batch["candidate_count"]),
            "status": status, "plan_hash": str(batch["plan_hash"]), "created_at": str(batch["created_at"]),
            "prompt_bundle": batch_snapshot.get("prompt_bundles", [None])[0] if isinstance(batch_snapshot, dict) and batch_snapshot.get("prompt_bundles") else None,
            "summary": {"total": len(items), "succeeded": success, "failed": failed, "active": active}, "items": items,
        }


class ShotKeyframeGenerationCompletionService:
    def __init__(self, database: DatabaseUnitOfWork, settings: Settings, *, media: MediaPromotionPort) -> None:
        self.database = database
        self.settings = settings
        self.media = media

    def finalize_job(self, job_id: str, artifacts: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            item = connection.execute("SELECT * FROM shot_keyframe_generation_batch_items WHERE job_id=?", (job_id,)).fetchone()
        if item is None:
            return None
        if item["media_version_id"]:
            return {"status": "SUCCEEDED", "media_version_id": str(item["media_version_id"]), "idempotent_replay": True}
        if not artifacts:
            raise DomainRuleError("SHOT_KEYFRAME_OUTPUT_MISSING", "关键帧任务成功但没有图片产物")
        promoted = None
        for artifact in artifacts:
            try:
                promoted = self.media.promote_job_artifact(
                    str(artifact["id"]), purpose="KEYFRAME_END" if item["frame_role"] == "END_FRAME" else "KEYFRAME",
                    media_kind="IMAGE", stage="KEYFRAME", actor="shot-keyframe-worker",
                )
                break
            except DomainRuleError:
                continue
        if promoted is None:
            raise DomainRuleError("SHOT_KEYFRAME_OUTPUT_INVALID", "关键帧任务没有可登记的图片产物")
        self.media.submit_default_derivatives(str(promoted["media_version_id"]))
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE shot_keyframe_generation_batch_items SET status='SUCCEEDED',media_version_id=?,updated_at=?,revision=revision+1 WHERE id=?",
                (promoted["media_version_id"], _now(), item["id"]),
            )
        return {"status": "SUCCEEDED", "media_version_id": str(promoted["media_version_id"]), "idempotent_replay": False}

    def record_failure(self, job_id: str, error: DomainRuleError) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE shot_keyframe_generation_batch_items SET status='FAILED',error_code=?,error_detail_redacted=?,updated_at=?,revision=revision+1
                WHERE job_id=? AND status!='SUCCEEDED'""",
                (error.code, error.message, _now(), job_id),
            )
            return cursor.rowcount > 0
