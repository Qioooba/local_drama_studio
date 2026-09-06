"""Cross-asset HERO image generation orchestration.

The batch aggregate owns only selection, recovery and per-item outcome. Actual
execution stays on the existing GenerationIntent -> GenerationVariant -> Job
chain, and accepted output stays an ordinary Asset Bible HERO reference.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar

from local_drama.application.ports.creative_generation import (
    AssetBibleCommandFactory,
    GenerationCommandPort,
    GenerationPreferenceResolverFactory,
    MediaPromotionPort,
)
from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.application.workflow_contracts import effective_workflow_contract
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.generation_planning import frozen_generation_contract
from local_drama.domain.image_input_roles import COMFY_IMAGE_INPUT_ROLES
from local_drama.domain.story_entities import assess_entity_name


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AssetImageSpec:
    capability: str
    prompt_prefix: str
    prompt_suffix: str


ASSET_IMAGE_SPECS: dict[str, AssetImageSpec] = {
    "CHARACTER": AssetImageSpec(
        "IMAGE_CHARACTER",
        "角色概念设定主图",
        "单人，全身或四分之三身，面部与服装结构清晰，姿态自然，干净中性背景，无文字无水印",
    ),
    "SCENE": AssetImageSpec(
        "IMAGE_SCENE",
        "场景概念设定主图",
        "建立镜头，空间结构、材质、光线与纵深清晰，无人物，无文字无水印",
    ),
    "PROP": AssetImageSpec(
        "IMAGE_CONCEPT",
        "关键道具概念设定主图",
        "单一道具居中，完整轮廓，材质和使用痕迹清晰，干净中性背景，无人物无文字无水印",
    ),
    "COSTUME": AssetImageSpec(
        "IMAGE_CONCEPT",
        "服装概念设定主图",
        "完整服装造型，版型、层次、材质与配饰清晰，中性人台或无身份模特，无文字无水印",
    ),
}


class AssetImageGenerationBatchService:
    MAX_ITEMS: ClassVar[int] = 100
    PURPOSE: ClassVar[str] = "ASSET_HERO_IMAGE"

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
    def prompt_for(kind: str, name: str, description: str) -> str:
        spec = ASSET_IMAGE_SPECS[kind]
        description_part = description.strip() or "依据项目故事资料形成清晰、可复用的标准外观"
        prompt = f"{spec.prompt_prefix}。名称：{name.strip()}。设定：{description_part}。要求：{spec.prompt_suffix}。"
        return prompt[:2000]

    @staticmethod
    def visual_description(extra_json: object, description: object) -> str:
        """Prefer the staged AI visual prompt without requiring a dossier."""
        try:
            extra = json.loads(str(extra_json or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            extra = {}
        if not isinstance(extra, dict):
            extra = {}
        visual_prompt = str(extra.get("visual_prompt") or "").strip()
        text_dossier = extra.get("text_dossier")
        if not visual_prompt and isinstance(text_dossier, dict):
            visual_prompt = str(text_dossier.get("visual_prompt") or "").strip()
        return visual_prompt or str(description or "").strip()

    @staticmethod
    def _seed(asset_id: str) -> int:
        return 100_000 + int(hashlib.sha256(asset_id.encode("utf-8")).hexdigest()[:8], 16) % 900_000

    @staticmethod
    def _input_slots(profile: Any) -> dict[str, Any]:
        try:
            contract = json.loads(str(profile["input_contract_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError, KeyError, IndexError):
            contract = {}
        if not isinstance(contract, dict):
            return {}
        slots = contract.get("input_slots")
        if isinstance(slots, dict):
            return {str(role): spec for role, spec in slots.items() if isinstance(spec, dict)}
        metadata = {"transport", "local_only", "requires_explicit_validation", "required_inputs", "required_nodes", "provider_kind"}
        return {str(role): spec for role, spec in contract.items() if str(role) not in metadata and isinstance(spec, dict)}

    @classmethod
    def _supports_text_to_image(cls, profile: Any) -> bool:
        slots = cls._input_slots(profile)
        prompt = slots.get("PROMPT")
        if not isinstance(prompt, dict) or int(prompt.get("min", 0) or 0) <= 0:
            return False
        return not any(
            str(role) in COMFY_IMAGE_INPUT_ROLES and (int(spec.get("min", 0) or 0) > 0 or spec.get("required") is True)
            for role, spec in slots.items()
        )

    @staticmethod
    def _profile_capability_allowed(profile_capability: str, capability: str) -> bool:
        normalized = str(profile_capability).upper()
        return normalized == capability or (capability in {"IMAGE_CHARACTER", "IMAGE_SCENE"} and normalized == "IMAGE_CONCEPT")

    @staticmethod
    def _published_workflow(connection: Any, profile: Any) -> bool:
        if not profile["workflow_version_id"]:
            return False
        workflow = connection.execute("SELECT status FROM workflow_versions WHERE id=?", (profile["workflow_version_id"],)).fetchone()
        return workflow is not None and str(workflow["status"]) == "PUBLISHED"

    @classmethod
    def _find_text_to_image_profile(cls, connection: Any, capability: str) -> Any | None:
        rows = connection.execute(
            """SELECT * FROM execution_profile_versions
            WHERE status='PUBLISHED' AND UPPER(capability)=? AND workflow_version_id IS NOT NULL
            ORDER BY updated_at DESC, version_no DESC, id DESC""",
            (capability,),
        ).fetchall()
        for row in rows:
            if not cls._published_workflow(connection, row):
                continue
            if cls._supports_text_to_image(row):
                return row
        return None

    def _variant_plan(self, kind: str, item: dict[str, Any], profile_id: str) -> VariantPlan:
        seed = self._seed(str(item["asset_id"]))
        return VariantPlan(
            variant_type="BASE", parent_variant_id=None,
            branch_reason=f"{self.PURPOSE}_{kind}", prompt_revision_id=None,
            profile_version_id=profile_id,
            parameter_set={"PROMPT": item["prompt"], "SEED": seed},
            seed_policy="EXPLICIT", explicit_seed=seed, bindings=(),
        )

    def _resolve_profile(self, connection: Any, project_id: str, capability: str, explicit_id: str | None) -> tuple[Any | None, dict[str, Any], list[dict[str, Any]]]:
        resolution = self.preference_resolver_factory(connection).resolve(
            project_id=project_id,
            capability=capability,
        )
        profile_id = explicit_id or resolution.get("profile_version_id")
        profile = connection.execute("SELECT * FROM execution_profile_versions WHERE id=?", (profile_id,)).fetchone() if profile_id else None
        issues: list[dict[str, Any]] = []

        if explicit_id is None:
            selected_profile = None
            selected_resolution = resolution
            if profile is not None and str(profile["status"]) == "PUBLISHED" and self._published_workflow(connection, profile) and self._supports_text_to_image(profile) and self._profile_capability_allowed(str(profile["capability"]), capability):
                selected_profile = profile
                selected_resolution = resolution
            if selected_profile is None:
                for candidate_capability in [capability, "IMAGE_CONCEPT"] if capability in {"IMAGE_CHARACTER", "IMAGE_SCENE"} else [capability]:
                    candidate = self._find_text_to_image_profile(connection, candidate_capability)
                    if candidate is None:
                        continue
                    selected_profile = candidate
                    selected_resolution = {
                        **resolution,
                        "source": "TEXT_TO_IMAGE_AUTO",
                        "profile_version_id": str(candidate["id"]),
                        "requested_capability": capability,
                        **({"crosswalk": "IMAGE_CONCEPT"} if candidate_capability == "IMAGE_CONCEPT" and capability != "IMAGE_CONCEPT" else {}),
                    }
                    break
            profile = selected_profile
            resolution = selected_resolution

        if profile is None:
            issues.append({"code": "ASSET_IMAGE_PROFILE_REQUIRED", "message": f"当前没有可执行的 {capability} 纯文生图配置"})
        elif str(profile["status"]) != "PUBLISHED":
            issues.append({"code": "ASSET_IMAGE_PROFILE_NOT_PUBLISHED", "message": "所选文生图配置尚未发布"})
        elif not self._profile_capability_allowed(str(profile["capability"]), capability):
            issues.append({"code": "ASSET_IMAGE_PROFILE_CAPABILITY_MISMATCH", "message": f"所选配置不支持 {capability}"})
        elif not self._supports_text_to_image(profile):
            issues.append({
                "code": "ASSET_IMAGE_PROFILE_REQUIRES_REFERENCE_IMAGE",
                "message": "所选文生图配置需要参考图；资产主图批量生成只接受纯文生图配置。",
            })
        elif not profile["workflow_version_id"]:
            issues.append({"code": "ASSET_IMAGE_PROFILE_WORKFLOW_REQUIRED", "message": "所选文生图配置没有绑定已发布工作流"})
        else:
            workflow = connection.execute("SELECT status FROM workflow_versions WHERE id=?", (profile["workflow_version_id"],)).fetchone()
            if workflow is None or str(workflow["status"]) != "PUBLISHED":
                issues.append({"code": "ASSET_IMAGE_WORKFLOW_UNAVAILABLE", "message": "所选文生图工作流不可执行"})
        return profile, {**resolution, "profile_version_id": str(profile["id"]) if profile is not None else (explicit_id or resolution.get("profile_version_id"))}, issues

    def plan(
        self,
        project_id: str,
        *,
        asset_kind: str,
        asset_ids: list[str],
        profile_version_id: str | None = None,
        mode: str = "MISSING_ONLY",
    ) -> dict[str, Any]:
        kind = asset_kind.strip().upper()
        if kind not in ASSET_IMAGE_SPECS:
            raise DomainRuleError("ASSET_IMAGE_KIND_UNSUPPORTED", "不支持为该资产类型批量生成主图")
        if mode != "MISSING_ONLY":
            raise DomainRuleError("ASSET_IMAGE_MODE_UNSUPPORTED", "当前只支持安全补齐缺失主图")
        normalized_ids = list(dict.fromkeys(str(item).strip() for item in asset_ids if str(item).strip()))
        if not normalized_ids:
            raise DomainRuleError("ASSET_IMAGE_BATCH_EMPTY", "请至少选择一个需要生成主图的资产")
        if len(normalized_ids) > self.MAX_ITEMS:
            raise DomainRuleError("ASSET_IMAGE_BATCH_TOO_LARGE", f"单次最多生成 {self.MAX_ITEMS} 张资产主图")

        spec = ASSET_IMAGE_SPECS[kind]
        with self.database.connect() as connection:
            project = connection.execute("SELECT id FROM projects WHERE id=?", (project_id,)).fetchone()
            if project is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在", {"project_id": project_id})
            placeholders = ",".join("?" for _ in normalized_ids)
            rows = connection.execute(
                f"""SELECT a.*,
                EXISTS(SELECT 1 FROM story_asset_references r WHERE r.story_asset_id=a.id
                    AND r.reference_kind='HERO' AND r.status='ACTIVE') AS has_hero
                FROM story_assets a WHERE a.id IN ({placeholders})""",
                normalized_ids,
            ).fetchall()
            by_id = {str(row["id"]): row for row in rows}
            profile, resolution, global_issues = self._resolve_profile(connection, project_id, spec.capability, profile_version_id)
            items: list[dict[str, Any]] = []
            issues = list(global_issues)
            for asset_id in normalized_ids:
                row = by_id.get(asset_id)
                blockers: list[dict[str, Any]] = []
                status = "READY"
                if row is None or str(row["project_id"]) != project_id or str(row["kind"]) != kind:
                    blockers.append({"code": "ASSET_IMAGE_ASSET_SCOPE_INVALID", "message": "资产不存在、类型不符或不属于当前项目"})
                    status = "BLOCKED"
                    item = {"asset_id": asset_id, "name": asset_id, "revision": None, "has_hero": False, "prompt": "", "status": status, "blockers": blockers}
                elif str(row["status"]) != "ACTIVE":
                    blockers.append({"code": "STORY_ASSET_NOT_ACTIVE", "message": "归档资产不会生成新主图"})
                    status = "BLOCKED"
                    item = {"asset_id": asset_id, "name": str(row["name"]), "revision": int(row["revision"]), "has_hero": bool(row["has_hero"]), "prompt": "", "status": status, "blockers": blockers}
                elif not assess_entity_name(kind, row["name"]).valid:
                    assessment = assess_entity_name(kind, row["name"])
                    blockers.append({"code": "ASSET_NAME_REVIEW_REQUIRED", "message": assessment.reason or "资产名称疑似文本碎片，请先修正或归档"})
                    status = "BLOCKED"
                    item = {"asset_id": asset_id, "name": str(row["name"]), "revision": int(row["revision"]), "has_hero": bool(row["has_hero"]), "prompt": "", "status": status, "blockers": blockers}
                elif bool(row["has_hero"]):
                    status = "SKIPPED"
                    item = {"asset_id": asset_id, "name": str(row["name"]), "revision": int(row["revision"]), "has_hero": True, "prompt": "", "status": status, "blockers": []}
                else:
                    blockers.extend(global_issues)
                    if blockers:
                        status = "BLOCKED"
                    item = {
                        "asset_id": asset_id,
                        "name": str(row["name"]),
                        "revision": int(row["revision"]),
                        "has_hero": False,
                        "prompt": self.prompt_for(
                            kind,
                            str(row["name"]),
                            self.visual_description(row["extra_json"], row["description"]),
                        ),
                        "status": status,
                        "blockers": blockers,
                    }
                if item["status"] == "READY" and profile is not None:
                    # The read-only plan must validate the same executable
                    # semantic contract as submission, before creating a batch.
                    workflow = connection.execute("SELECT * FROM workflow_versions WHERE id=?", (profile["workflow_version_id"],)).fetchone()
                    effective = effective_workflow_contract(connection, str(profile["workflow_version_id"]), workflow)
                    try:
                        frozen_generation_contract(
                            profile, workflow, self._variant_plan(kind, item, str(profile["id"])),
                            effective_workflow_bindings=effective["workflow_bindings"],
                            effective_workflow_contract=effective["workflow_contract"],
                        )
                    except DomainRuleError as error:
                        blockers.append({"code": error.code, "message": error.message})
                        item["status"] = "BLOCKED"
                items.append(item)
                issues.extend({**blocker, "asset_id": asset_id, "asset_name": item["name"]} for blocker in blockers if blocker not in global_issues)

            authority = {
                "project_id": project_id,
                "asset_kind": kind,
                "capability": spec.capability,
                "mode": mode,
                "profile_version_id": str(profile["id"]) if profile is not None else resolution.get("profile_version_id"),
                "profile_revision": int(profile["revision"]) if profile is not None else None,
                "items": [
                    {key: item[key] for key in ("asset_id", "revision", "has_hero", "prompt", "status")}
                    for item in items
                ],
            }
        ready = sum(item["status"] == "READY" for item in items)
        return {
            **authority,
            "plan_hash": _digest(authority),
            "valid": ready > 0 and not global_issues,
            "issues": issues,
            "items": items,
            "profile_resolution": resolution,
            "summary": {
                "selected": len(items),
                "ready": ready,
                "skipped": sum(item["status"] == "SKIPPED" for item in items),
                "blocked": sum(item["status"] == "BLOCKED" for item in items),
                "jobs": ready,
            },
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def submit(
        self,
        project_id: str,
        *,
        asset_kind: str,
        asset_ids: list[str],
        expected_plan_hash: str,
        idempotency_key: str,
        profile_version_id: str | None = None,
        mode: str = "MISSING_ONLY",
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if not idempotency_key.strip():
            raise DomainRuleError("IDEMPOTENCY_KEY_REQUIRED", "批量生成必须提供幂等键")
        with self.database.connect() as connection:
            replay = connection.execute(
                "SELECT id FROM asset_image_generation_batches WHERE project_id=? AND idempotency_key=?",
                (project_id, idempotency_key),
            ).fetchone()
        if replay is not None:
            result = self.get_batch(str(replay["id"]))
            result["idempotent_replay"] = True
            return result

        plan = self.plan(
            project_id,
            asset_kind=asset_kind,
            asset_ids=asset_ids,
            profile_version_id=profile_version_id,
            mode=mode,
        )
        if not hmac.compare_digest(str(plan["plan_hash"]), expected_plan_hash):
            raise DomainRuleError("ASSET_IMAGE_BATCH_PLAN_STALE", "资产、主图或文生图配置已变化，请重新检查")
        if not plan["valid"]:
            raise DomainRuleError("ASSET_IMAGE_BATCH_BLOCKED", "当前批量生成计划不可提交", {"issues": plan["issues"]})

        ready_items = [item for item in plan["items"] if item["status"] == "READY"]
        batch_id, now = str(uuid.uuid4()), _now()
        with self.database.transaction() as connection:
            connection.execute(
                """INSERT INTO asset_image_generation_batches
                (id,project_id,asset_kind,capability,profile_version_id,mode,status,plan_hash,idempotency_key,
                 selected_count,queued_count,created_at,updated_at,created_by,revision,schema_version)
                VALUES (?,?,?,?,?,?,'QUEUING',?,?,?,0,?,?,?,1,'v1')""",
                (batch_id, project_id, plan["asset_kind"], plan["capability"], plan["profile_version_id"], mode, expected_plan_hash, idempotency_key, len(ready_items), now, now, actor),
            )
            for item in ready_items:
                connection.execute(
                    """INSERT INTO asset_image_generation_batch_items
                    (id,batch_id,asset_id,asset_revision,status,prompt_snapshot,created_at,updated_at,revision,schema_version)
                    VALUES (?,?,?,?,'PLANNED',?,?,?,1,'v1')""",
                    (str(uuid.uuid4()), batch_id, item["asset_id"], item["revision"], item["prompt"], now, now),
                )

        profile_id = str(plan["profile_version_id"])
        queued_count = 0
        for item in ready_items:
            try:
                intent = self.generation.create_intent(
                    project_id,
                    "STORY_ASSET",
                    str(item["asset_id"]),
                    self.PURPOSE,
                    f"为{plan['asset_kind']}资产生成并自动绑定首张 HERO 主参考",
                )
                variant_plan = self._variant_plan(str(plan["asset_kind"]), item, profile_id)
                checked = self.generation.preflight_variant(str(intent["id"]), variant_plan)
                submitted = self.generation.submit_confirmed_variant(
                    str(intent["id"]),
                    variant_plan,
                    str(checked["plan_hash"]),
                    f"{idempotency_key}:{item['asset_id']}",
                    job_scope={"subject_kind": "STORY_ASSET", "scope_kind": "PROJECT", "scope_project_id": project_id, "stage_code": "ASSET_IMAGE"},
                )
                with self.database.transaction() as connection:
                    connection.execute("UPDATE generation_intents SET status='QUEUED',updated_at=? WHERE id=?", (_now(), intent["id"]))
                    connection.execute(
                        """UPDATE asset_image_generation_batch_items SET status='QUEUED',intent_id=?,variant_id=?,job_id=?,updated_at=?,revision=revision+1
                        WHERE batch_id=? AND asset_id=?""",
                        (intent["id"], submitted["variant"]["id"], submitted["job"]["id"], _now(), batch_id, item["asset_id"]),
                    )
                queued_count += 1
            except DomainRuleError as error:
                with self.database.transaction() as connection:
                    connection.execute(
                        """UPDATE asset_image_generation_batch_items SET status='FAILED',error_code=?,error_detail_redacted=?,updated_at=?,revision=revision+1
                        WHERE batch_id=? AND asset_id=?""",
                        (error.code, error.message, _now(), batch_id, item["asset_id"]),
                    )

        with self.database.transaction() as connection:
            final_status = "QUEUED" if queued_count else "FAILED"
            connection.execute(
                "UPDATE asset_image_generation_batches SET status=?,queued_count=?,updated_at=?,revision=revision+1 WHERE id=?",
                (final_status, queued_count, _now(), batch_id),
            )
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,summary,metadata_redacted_json)
                VALUES (?,'producer','ASSET_IMAGE_BATCH_SUBMITTED','asset_image_generation_batch',?,'提交资产主图批量生成',?)""",
                (actor, batch_id, _canonical({"project_id": project_id, "asset_kind": plan["asset_kind"], "selected_count": len(ready_items), "queued_count": queued_count, "plan_hash": expected_plan_hash})),
            )
        result = self.get_batch(batch_id)
        result["idempotent_replay"] = False
        return result

    def list_batches(self, project_id: str, *, asset_kind: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        where = ["project_id=?"]
        params: list[Any] = [project_id]
        if asset_kind:
            where.append("asset_kind=?")
            params.append(asset_kind.strip().upper())
        params.append(max(1, min(limit, 20)))
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT id FROM asset_image_generation_batches WHERE {' AND '.join(where)} ORDER BY created_at DESC,id DESC LIMIT ?",
                params,
            ).fetchall()
        return [self.get_batch(str(row["id"])) for row in rows]

    def get_batch(self, batch_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            batch = connection.execute("SELECT * FROM asset_image_generation_batches WHERE id=?", (batch_id,)).fetchone()
            if batch is None:
                raise DomainRuleError("ASSET_IMAGE_BATCH_NOT_FOUND", "资产主图批次不存在", {"batch_id": batch_id})
            rows = connection.execute(
                """SELECT bi.*,a.name,a.kind,j.state AS job_state,j.progress_json,j.last_error_code,j.last_error_detail_redacted
                FROM asset_image_generation_batch_items bi
                JOIN story_assets a ON a.id=bi.asset_id
                LEFT JOIN jobs j ON j.id=bi.job_id
                WHERE bi.batch_id=? ORDER BY a.code,a.id""",
                (batch_id,),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            status = str(row["status"])
            job_state = str(row["job_state"] or "")
            if status not in {"SUCCEEDED", "FAILED", "CANCELLED", "SUPERSEDED"}:
                if job_state in {"RUNNING", "CLAIMED"}:
                    status = "RUNNING"
                elif job_state in {"FAILED", "NEEDS_ATTENTION", "ORPHANED"}:
                    status = "FAILED"
                elif job_state == "CANCELLED":
                    status = "CANCELLED"
            try:
                progress = json.loads(str(row["progress_json"] or "{}"))
            except (TypeError, json.JSONDecodeError):
                progress = {}
            items.append({
                "id": str(row["id"]),
                "asset_id": str(row["asset_id"]),
                "asset_name": str(row["name"]),
                "asset_kind": str(row["kind"]),
                "status": status,
                "job_id": str(row["job_id"]) if row["job_id"] else None,
                "job_state": job_state or None,
                "progress": progress,
                "media_version_id": str(row["media_version_id"]) if row["media_version_id"] else None,
                "reference_id": str(row["reference_id"]) if row["reference_id"] else None,
                "error": ({"code": row["error_code"] or row["last_error_code"], "message": row["error_detail_redacted"] or row["last_error_detail_redacted"]} if row["error_code"] or row["last_error_code"] else None),
            })
        success = sum(item["status"] == "SUCCEEDED" for item in items)
        superseded = sum(item["status"] == "SUPERSEDED" for item in items)
        failed = sum(item["status"] in {"FAILED", "CANCELLED"} for item in items)
        terminal = success + superseded + failed
        if items and terminal == len(items):
            aggregate = "SUCCEEDED" if failed == 0 else "PARTIAL_FAILED" if success or superseded else "FAILED"
        elif any(item["status"] == "RUNNING" for item in items):
            aggregate = "PARTIAL_RUNNING" if success or failed else "RUNNING"
        else:
            aggregate = "QUEUED"
        return {
            "id": str(batch["id"]),
            "project_id": str(batch["project_id"]),
            "asset_kind": str(batch["asset_kind"]),
            "capability": str(batch["capability"]),
            "profile_version_id": str(batch["profile_version_id"]),
            "mode": str(batch["mode"]),
            "status": aggregate,
            "plan_hash": str(batch["plan_hash"]),
            "created_at": str(batch["created_at"]),
            "updated_at": str(batch["updated_at"]),
            "summary": {"total": len(items), "succeeded": success, "superseded": superseded, "failed": failed, "active": len(items) - terminal},
            "items": items,
        }


class AssetImageGenerationCompletionService:
    """Promote and bind only outputs owned by an asset HERO batch item."""

    def __init__(
        self,
        database: DatabaseUnitOfWork,
        settings: Settings,
        *,
        media: MediaPromotionPort,
        asset_commands: AssetBibleCommandFactory,
    ) -> None:
        self.database = database
        self.settings = settings
        self.media = media
        self.asset_commands = asset_commands

    def finalize_job(self, job_id: str, artifacts: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            item = connection.execute(
                """SELECT bi.*,b.project_id,b.id AS batch_id,a.canonical_media_version_id
                FROM asset_image_generation_batch_items bi
                JOIN asset_image_generation_batches b ON b.id=bi.batch_id
                JOIN story_assets a ON a.id=bi.asset_id
                WHERE bi.job_id=?""",
                (job_id,),
            ).fetchone()
        if item is None:
            return None
        if item["reference_id"] and item["media_version_id"]:
            return {"status": "SUCCEEDED", "media_version_id": str(item["media_version_id"]), "reference_id": str(item["reference_id"]), "idempotent_replay": True}
        if not artifacts:
            raise DomainRuleError("ASSET_IMAGE_OUTPUT_MISSING", "文生图任务成功但没有可登记的图片产物")

        promoted: dict[str, Any] | None = None
        last_error: DomainRuleError | None = None
        for artifact in artifacts:
            try:
                promoted = self.media.promote_job_artifact(
                    str(artifact["id"]),
                    purpose="ASSET_HERO_GENERATED",
                    media_kind="IMAGE",
                    stage="KEYFRAME",
                    actor="asset-image-worker",
                )
                break
            except DomainRuleError as error:
                last_error = error
        if promoted is None:
            raise last_error or DomainRuleError("ASSET_IMAGE_OUTPUT_INVALID", "文生图任务没有可登记的图片产物")
        media_version_id = str(promoted["media_version_id"])
        # Image content is never served directly.  Queue the standard
        # immutable thumbnail before publishing the HERO reference so the
        # creator-facing read model becomes visual without mutating on GET.
        self.media.submit_default_derivatives(media_version_id)
        with self.database.transaction() as connection:
            current = connection.execute(
                """SELECT id,media_version_id FROM story_asset_references
                WHERE story_asset_id=? AND reference_kind='HERO' AND status='ACTIVE'
                ORDER BY priority,created_at,id LIMIT 1""",
                (item["asset_id"],),
            ).fetchone()
            if current is not None:
                connection.execute(
                    """UPDATE asset_image_generation_batch_items SET status='SUPERSEDED',media_version_id=?,updated_at=?,revision=revision+1
                    WHERE id=?""",
                    (media_version_id, _now(), item["id"]),
                )
                result = {"status": "SUPERSEDED", "media_version_id": media_version_id, "reference_id": str(current["id"]), "idempotent_replay": False}
            else:
                command = self.asset_commands(connection)
                reference = command.add_reference(
                    str(item["project_id"]),
                    str(item["asset_id"]),
                    media_version_id,
                    "HERO",
                    label="批量文生图 · 主参考",
                    priority=10,
                    is_locked=True,
                    actor="asset-image-worker",
                )
                connection.execute(
                    """UPDATE asset_image_generation_batch_items SET status='SUCCEEDED',media_version_id=?,reference_id=?,updated_at=?,revision=revision+1
                    WHERE id=?""",
                    (media_version_id, reference["id"], _now(), item["id"]),
                )
                result = {"status": "SUCCEEDED", "media_version_id": media_version_id, "reference_id": str(reference["id"]), "idempotent_replay": False}
            connection.execute(
                """INSERT INTO audit_events
                (actor,role_context,action,subject_type,subject_id,job_id,summary,metadata_redacted_json)
                VALUES ('asset-image-worker','producer','ASSET_IMAGE_AUTO_BOUND','story_asset',?,?,?,?)""",
                (item["asset_id"], job_id, "文生图产物已登记并按安全规则绑定资产主参考", _canonical({"batch_id": item["batch_id"], "media_version_id": media_version_id, "status": result["status"]})),
            )
        return result

    def record_finalization_failure(self, job_id: str, error: DomainRuleError) -> bool:
        """Keep generation success truthful while exposing failed media adoption."""
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE asset_image_generation_batch_items
                SET status='FAILED',error_code=?,error_detail_redacted=?,updated_at=?,revision=revision+1
                WHERE job_id=? AND status NOT IN ('SUCCEEDED','SUPERSEDED')""",
                (error.code, error.message, _now(), job_id),
            )
            return cursor.rowcount > 0
