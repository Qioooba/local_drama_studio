"""Creator-facing episode production facade over durable automation runs.

No queue or duplicate run aggregate lives here.  Commands delegate to
AutomationWorkflowService and JobService; queries rebuild stages from workflow
tasks plus existing production facts.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from local_drama.domain.capabilities import (
    VIDEO_GENERATION_CAPABILITIES,
    normalize_capability,
)
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.policies import missing_shot_fields
from local_drama.infrastructure.adapters import AdapterContractRegistry
from local_drama.infrastructure.database.sqlite import Database

from .automation_workflows import AutomationWorkflowService
from .capacity import CapacitySnapshotService
from .diagnostics import _probe_loopback
from .episode_front_half_actions import EpisodeFrontHalfActionService
from .jobs import JobService

STAGE_DEFINITIONS = (
    ("STORY_ANALYSIS", "故事解析", ("STORY_READY",)),
    ("ASSET_EXTRACTION", "资产提取", ("ASSET_READY",)),
    ("ASSET_COMPLETION", "资产补全", ("ASSET_READY",)),
    ("SHOT_PLANNING", "分集 / 分镜规划", ("SHOT_PLAN_READY",)),
    ("SHOT_IMAGE", "镜头画面", ("KEYFRAME_GENERATION",)),
    ("VIDEO", "视频", ("VIDEO_GENERATION",)),
    ("AUDIO_SUBTITLE", "声音 / 字幕", ("AUDIO",)),
    ("COMPOSE_QC", "合成 / QC", ("QC", "TIMELINE", "EPISODE_COMPOSE", "HUMAN_REVIEW", "DELIVERY_READY")),
)
ACTION_STAGE = {
    "STORY_PARSE": "STORY_ANALYSIS",
    "SCRIPT_BREAKDOWN": "STORY_ANALYSIS",
    "ASSET_IDENTITY": "ASSET_EXTRACTION",
    "ASSET_COMPLETION": "ASSET_COMPLETION",
    "EPISODE_PLAN": "SHOT_PLANNING",
    "KEYFRAME_CHECK": "SHOT_IMAGE",
    "VIDEO_GENERATION": "VIDEO",
    "QC": "COMPOSE_QC",
    "TTS_BATCH": "AUDIO_SUBTITLE",
    "RENDER": "COMPOSE_QC",
    "DELIVERY": "COMPOSE_QC",
}
FRONT_HALF_ACTIONS = (
    "STORY_PARSE",
    "SCRIPT_BREAKDOWN",
    "ASSET_IDENTITY",
    "ASSET_COMPLETION",
    "EPISODE_PLAN",
    "KEYFRAME_CHECK",
)
BACK_HALF_ACTIONS = ("VIDEO_GENERATION", "QC")
FRONT_HALF_REPORT_BUDGET_BYTES = 8 * 1024 * 1024
TERMINAL_JOB_STATES = {"SUCCEEDED", "FAILED", "CANCELLED"}
PRODUCTION_MODE_POLICIES: dict[str, dict[str, Any]] = {
    "DRAFT": {"target_take_count": 1, "label": "草稿", "intent": "快速验证叙事与节奏"},
    "BALANCED": {"target_take_count": 2, "label": "平衡", "intent": "兼顾候选空间与本机耗时"},
    "QUALITY": {"target_take_count": 4, "label": "精品", "intent": "为正式选择保留更多候选"},
}
CHECKPOINT_POLICIES = frozenset({"AUTO_CONTINUE", "AFTER_ASSETS", "AFTER_SHOT_PLAN", "BEFORE_VIDEO", "ON_EXCEPTION"})


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


class EpisodeProductionRunService:
    def __init__(self, database: Database, settings: Any) -> None:
        self.database = database
        self.settings = settings
        self.automation = AutomationWorkflowService(database)

    def _episode(self, episode_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT e.id,e.code,e.title,e.production_status,e.narrative_status,
                s.project_id,p.title AS project_title,p.root_rel FROM episodes e
                JOIN seasons s ON s.id=e.season_id JOIN projects p ON p.id=s.project_id
                WHERE e.id=?""",
                (episode_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
        return dict(row)

    @staticmethod
    def _check(code: str, label: str, passed: bool, detail: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"code": code, "label": label, "status": "PASS" if passed else "BLOCKED", "blocking": not passed, "detail": detail, "evidence": evidence or {}}

    @staticmethod
    def _mode_policy(production_mode: str) -> tuple[str, dict[str, Any]]:
        mode = str(production_mode or "").strip().upper()
        policy = PRODUCTION_MODE_POLICIES.get(mode)
        if policy is None:
            raise DomainRuleError("EPISODE_PRODUCTION_MODE_INVALID", "生产模式必须是 DRAFT、BALANCED 或 QUALITY", {"production_mode": production_mode})
        return mode, dict(policy)

    @staticmethod
    def _resolved_mode_policies(profiles: list[Any]) -> dict[str, dict[str, Any]]:
        configured: list[int] = []
        for profile in profiles:
            try:
                policy = json.loads(str(profile["resource_policy_json"] or "{}"))
            except (TypeError, ValueError):
                policy = {}
            value = policy.get("default_takes") if isinstance(policy, dict) else None
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 1 <= int(value) <= 16:
                configured.append(int(value))
        base = max(configured) if configured else int(PRODUCTION_MODE_POLICIES["BALANCED"]["target_take_count"])
        source = "PROFILE_RESOURCE_POLICY" if configured else "SAFE_FALLBACK"
        return {
            "DRAFT": {"target_take_count": max(1, round(base / 2)), "label": "草稿", "intent": "快速验证叙事与节奏", "source": source},
            "BALANCED": {"target_take_count": base, "label": "平衡", "intent": "使用已绑定视频 Profile 的默认候选数", "source": source},
            "QUALITY": {"target_take_count": min(16, max(base + 1, base * 2)), "label": "精品", "intent": "在 Profile 默认候选数上扩大选择空间", "source": source},
        }

    @staticmethod
    def _checkpoint_policy(checkpoint_policy: str) -> str:
        policy = str(checkpoint_policy or "").strip().upper()
        if policy not in CHECKPOINT_POLICIES:
            raise DomainRuleError(
                "EPISODE_CHECKPOINT_POLICY_INVALID",
                "HITL checkpoint_policy 必须是 AUTO_CONTINUE、AFTER_ASSETS、AFTER_SHOT_PLAN、BEFORE_VIDEO 或 ON_EXCEPTION",
                {"checkpoint_policy": checkpoint_policy},
            )
        return policy

    def preflight(
        self,
        episode_id: str,
        *,
        tts_enabled: bool = True,
        production_mode: str = "BALANCED",
        checkpoint_policy: str = "ON_EXCEPTION",
        min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024,
        include_front_half: bool = False,
        _include_checkpoint_in_fingerprint: bool = True,
    ) -> dict[str, Any]:
        episode = self._episode(episode_id)
        production_mode, mode_policy = self._mode_policy(production_mode)
        checkpoint_policy = self._checkpoint_policy(checkpoint_policy)
        project_id = str(episode["project_id"])
        with self.database.connect() as connection:
            shots = connection.execute(
                """SELECT s.id,s.code,s.status,s.current_revision_id,sr.fields_json FROM shots s
                LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? AND s.archived_at IS NULL ORDER BY CAST(s.order_key AS REAL),s.code""",
                (episode_id,),
            ).fetchall()
            bindings = connection.execute(
                """SELECT sab.shot_id,sa.id,sa.code,sa.kind,sa.canonical_media_version_id,sa.status,
                COALESCE(sab.asset_state_id,easb.asset_state_id) AS effective_asset_state_id,
                sas.status AS effective_state_status,sas.code AS effective_state_code
                FROM shot_asset_bindings sab JOIN shots sh ON sh.id=sab.shot_id
                JOIN story_assets sa ON sa.id=sab.asset_id
                LEFT JOIN episode_asset_state_bindings easb ON easb.episode_id=sh.episode_id AND easb.story_asset_id=sa.id
                LEFT JOIN story_asset_states sas ON sas.id=COALESCE(sab.asset_state_id,easb.asset_state_id)
                WHERE sh.episode_id=? AND sh.archived_at IS NULL""",
                (episode_id,),
            ).fetchall()
            references = connection.execute(
                """SELECT r.story_asset_id,r.asset_state_id,r.media_version_id,r.reference_kind,r.is_locked,r.status,
                mv.integrity_status FROM story_asset_references r
                JOIN media_versions mv ON mv.id=r.media_version_id WHERE r.project_id=?""",
                (project_id,),
            ).fetchall()
            recipe = connection.execute(
                """SELECT drv.id,drv.recipe_hash,drv.recipe_json FROM project_director_recipe_bindings b
                JOIN director_recipe_versions drv ON drv.id=b.recipe_version_id WHERE b.project_id=?""",
                (project_id,),
            ).fetchone()
            profiles = connection.execute(
                """SELECT ppb.capability,ppb.status AS binding_status,epv.id,epv.status,
                epv.model_bundle_json,epv.resource_policy_json FROM project_profile_bindings ppb
                JOIN execution_profile_versions epv ON epv.id=ppb.execution_profile_version_id
                WHERE ppb.project_id=?""",
                (project_id,),
            ).fetchall()
            runtimes = connection.execute("SELECT code,status,transport,base_url FROM local_runtimes ORDER BY code").fetchall()
            models = connection.execute("SELECT code,machine_path_ref,status FROM model_artifacts ORDER BY code").fetchall()
            dialogue_count = int(connection.execute("SELECT COUNT(*) FROM dialogue_lines WHERE episode_id=?", (episode_id,)).fetchone()[0])
            voice_count = int(connection.execute(
                """SELECT COUNT(*) FROM character_voice_bindings cvb
                JOIN voice_profile_versions vpv ON vpv.id=cvb.voice_profile_version_id
                WHERE cvb.project_id=? AND vpv.status IN ('ACTIVE','PUBLISHED')""",
                (project_id,),
            ).fetchone()[0])

        incomplete_shots: list[dict[str, Any]] = []
        for row in shots:
            try:
                fields = json.loads(str(row["fields_json"] or "{}"))
            except (TypeError, ValueError):
                fields = {}
            missing = missing_shot_fields(fields)
            if not row["current_revision_id"] or missing or str(row["status"]) not in {"READY", "GENERATING", "REVIEW", "APPROVED"}:
                incomplete_shots.append({"shot_id": str(row["id"]), "shot_code": str(row["code"]), "missing_fields": missing, "status": str(row["status"])})

        bound_shot_ids = {str(row["shot_id"]) for row in bindings}
        missing_asset_shots = [str(row["code"]) for row in shots if str(row["id"]) not in bound_shot_ids]
        invalid_assets = [str(row["code"]) for row in bindings if str(row["status"]) != "ACTIVE" or not row["canonical_media_version_id"] or (row["effective_asset_state_id"] and str(row["effective_state_status"]) != "ACTIVE")]
        required_character_refs: list[str] = []
        if recipe is not None:
            try:
                recipe_payload = json.loads(str(recipe["recipe_json"] or "{}"))
            except (TypeError, ValueError):
                recipe_payload = {}
            configured_refs = recipe_payload.get("asset_policy", {}).get("character_required_refs", []) if isinstance(recipe_payload, dict) else []
            if isinstance(configured_refs, list) and configured_refs:
                required_character_refs = [str(item).upper() for item in configured_refs]
        refs_by_asset: dict[str, list[Any]] = {}
        for reference in references:
            refs_by_asset.setdefault(str(reference["story_asset_id"]), []).append(reference)
        missing_required_references: list[dict[str, Any]] = []
        for binding in bindings:
            if str(binding["kind"]) != "CHARACTER":
                continue
            effective_state_id = str(binding["effective_asset_state_id"]) if binding["effective_asset_state_id"] else None
            available = {
                str(reference["reference_kind"]).upper()
                for reference in refs_by_asset.get(str(binding["id"]), [])
                if str(reference["status"]) == "ACTIVE"
                and str(reference["integrity_status"]) == "VERIFIED"
                and (reference["asset_state_id"] is None or str(reference["asset_state_id"]) == effective_state_id)
            }
            missing = sorted(set(required_character_refs) - available)
            if missing:
                missing_required_references.append({"shot_id": str(binding["shot_id"]), "asset_id": str(binding["id"]), "asset_code": str(binding["code"]), "effective_asset_state_id": effective_state_id, "missing_reference_kinds": missing})
        video_profiles: list[Any] = []
        for row in profiles:
            try:
                cap = normalize_capability(str(row["capability"]))
            except ValueError:
                cap = str(row["capability"])
            if cap in VIDEO_GENERATION_CAPABILITIES:
                video_profiles.append(row)
        valid_profiles = [row for row in video_profiles if str(row["binding_status"]) == "ACTIVE" and str(row["status"]) in {"ACTIVE", "PUBLISHED"}]
        available_mode_policies = self._resolved_mode_policies(valid_profiles)
        mode_policy = dict(available_mode_policies[production_mode])
        declared_model_refs: set[str] = set()
        for profile in valid_profiles:
            try:
                bundle = json.loads(str(profile["model_bundle_json"] or "{}"))
            except (TypeError, ValueError):
                bundle = {}
            values = bundle.values() if isinstance(bundle, dict) else bundle if isinstance(bundle, list) else []
            declared_model_refs.update(str(value) for value in values if isinstance(value, (str, int)))
        usable_models = [row for row in models if str(row["status"]) in {"ACTIVE", "READY", "AVAILABLE", "VERIFIED"} and Path(str(row["machine_path_ref"])).is_file()]

        adapter = AdapterContractRegistry(self.settings).inspect()
        comfy_contract = next((item for item in adapter["contracts"] if item["kind"] == "COMFY"), None)
        comfy_runtime = next((row for row in runtimes if "comfy" in str(row["code"]).casefold()), None)
        comfy_contract_status = str(comfy_contract["status"]) if comfy_contract else "MISSING"
        comfy_runtime_status = str(comfy_runtime["status"]) if comfy_runtime else "MISSING"
        comfy_base_url = str(comfy_runtime["base_url"]) if comfy_runtime and comfy_runtime["base_url"] else None
        if self.settings.allows_private_network:
            comfy_probe_status, comfy_probe_evidence = _probe_loopback(
                comfy_base_url,
                allow_private_network=True,
            )
        else:
            # Preserve the narrow local-only probe seam for callers and tests.
            comfy_probe_status, comfy_probe_evidence = _probe_loopback(comfy_base_url)
        comfy_ok = comfy_contract_status == "DECLARED" and bool(comfy_runtime) and comfy_probe_status == "PASS"
        if comfy_contract_status != "DECLARED":
            comfy_message = f"Comfy adapter contract 状态为 {comfy_contract_status}，需 DECLARED"
        elif not comfy_runtime:
            comfy_message = "Comfy 本地 runtime 未登记"
        elif comfy_probe_status != "PASS":
            reason = str(comfy_probe_evidence.get("reason") or "不可达")
            comfy_message = f"Comfy 本地 loopback 实时探测为 {comfy_probe_status}（{reason}）"
        else:
            comfy_message = f"Comfy adapter 已声明，loopback 实时探测可用（登记状态 {comfy_runtime_status}）"
        capacity = CapacitySnapshotService(self.database, self.settings).inspect(project_id)
        gpu_ok = capacity["gpu"].get("source") != "UNAVAILABLE" and bool(capacity["gpu"].get("name") or capacity["gpu"].get("total_bytes"))
        project_root = (self.settings.projects_root / str(episode["root_rel"])).resolve()
        expected_projects_root = self.settings.projects_root.resolve()
        if not project_root.is_relative_to(expected_projects_root) or not project_root.is_dir():
            free_bytes = None
        else:
            try:
                free_bytes = int(shutil.disk_usage(project_root).free)
            except OSError:
                free_bytes = None
        disk_per_take_values: list[int] = []
        for profile in valid_profiles:
            try:
                policy = json.loads(str(profile["resource_policy_json"] or "{}"))
            except (TypeError, ValueError):
                policy = {}
            if isinstance(policy, dict):
                for key in ("estimated_disk_bytes_per_take", "disk_bytes_per_take", "estimated_output_bytes_per_take", "output_bytes_per_take"):
                    value = policy.get(key)
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                        disk_per_take_values.append(int(value))
                        break
        disk_per_take = max(disk_per_take_values) if disk_per_take_values else None
        estimated_output_bytes = disk_per_take * len(shots) * int(mode_policy["target_take_count"]) if disk_per_take is not None else None
        required_free_bytes = max(min_free_disk_bytes, estimated_output_bytes or 0)
        ffmpeg_ref = self.settings.ffmpeg_path or shutil.which("ffmpeg")

        checks = [
            self._check("SCRIPT_SHOT_PLAN_MISSING", "剧本与镜头计划", bool(shots) and not incomplete_shots, "镜头计划已具备可生产 revision" if shots and not incomplete_shots else "存在缺失或未 production-ready 的镜头", {"shot_count": len(shots), "incomplete_shots": incomplete_shots}),
            self._check("CRITICAL_ASSETS_MISSING", "关键资产", bool(shots) and not missing_asset_shots and not invalid_assets, "每个镜头的关键资产均有有效 canonical reference" if shots and not missing_asset_shots and not invalid_assets else "镜头未绑定关键资产，或资产缺少 canonical reference", {"binding_count": len(bindings), "missing_asset_shots": missing_asset_shots, "invalid_asset_codes": invalid_assets}),
            self._check("ASSET_REFERENCE_REQUIREMENTS_MISSING", "生效资产状态与参考图", not missing_required_references, "生效角色状态满足 Director Recipe 的参考图要求" if not missing_required_references else "部分镜头的生效角色状态缺少 Recipe 要求的已验证参考图", {"required_character_refs": required_character_refs, "missing": missing_required_references, "director_recipe_version_id": str(recipe["id"]) if recipe else None, "director_recipe_hash": str(recipe["recipe_hash"]) if recipe else None}),
            self._check("PROFILE_CAPABILITY_MISSING", "生成 Profile 能力", bool(valid_profiles), "已绑定发布的视频生成 Profile" if valid_profiles else "项目缺少 ACTIVE/PUBLISHED 视频生成 Profile", {"profile_version_ids": [str(row["id"]) for row in valid_profiles]}),
            self._check("LOCAL_MODEL_FILES_MISSING", "本地模型文件", bool(usable_models), "已发现可用的本地模型文件" if usable_models else "未发现状态有效且文件存在的本地模型", {"declared_model_refs": sorted(declared_model_refs), "usable_model_codes": [str(row["code"]) for row in usable_models]}),
            self._check("COMFY_ADAPTER_UNAVAILABLE", "Comfy/Adapter", comfy_ok, comfy_message, {"contract_status": comfy_contract_status, "registered_runtime_status": comfy_runtime_status, "probe_status": comfy_probe_status, "probe": comfy_probe_evidence}),
            self._check("GPU_CAPACITY_UNAVAILABLE", "GPU/容量", gpu_ok, "本机 GPU 容量信息可用" if gpu_ok else "本地 manifest 未提供 GPU 容量", {"gpu": capacity["gpu"], "gpu_active_count": capacity["gpu_active_count"], "gpu_concurrency_limit": capacity["gpu_concurrency_limit"]}),
            self._check("DISK_SPACE_LOW", "磁盘", isinstance(free_bytes, int) and free_bytes >= required_free_bytes, "可用磁盘空间满足冻结输出估算与运行阈值" if isinstance(free_bytes, int) and free_bytes >= required_free_bytes else "可用磁盘空间低于冻结输出估算/阈值或无法读取", {"free_bytes": free_bytes, "required_free_bytes": required_free_bytes, "operator_min_free_bytes": min_free_disk_bytes, "estimated_output_bytes": estimated_output_bytes, "disk_bytes_per_take": disk_per_take, "take_count": len(shots) * int(mode_policy["target_take_count"]), "estimate_source": "FROZEN_PROFILE_RESOURCE_POLICY" if disk_per_take is not None else "UNKNOWN"}),
            self._check("FFMPEG_UNAVAILABLE", "FFmpeg", bool(ffmpeg_ref and Path(ffmpeg_ref).is_file()), "FFmpeg 可执行文件存在" if ffmpeg_ref else "未配置 FFmpeg", {"executable_ref": str(ffmpeg_ref) if ffmpeg_ref else None}),
        ]
        if tts_enabled:
            checks.append(self._check("TTS_CONFIGURATION_MISSING", "TTS", dialogue_count == 0 or voice_count > 0, "TTS 未发现阻塞项" if dialogue_count == 0 or voice_count > 0 else "存在对白但未绑定有效角色音色", {"dialogue_count": dialogue_count, "voice_binding_count": voice_count}))
        front_half_snapshot: dict[str, Any] | None = None
        if include_front_half:
            # A normal Episode Production Run executes ASSET_COMPLETION before
            # shot generation.  Use that exact authoritative projection as a
            # launch gate as well; otherwise the coarse canonical-reference
            # checks above can pass and the completed run later regresses to a
            # permanently pending asset stage.
            front_service = EpisodeFrontHalfActionService(self.database, self.settings)
            asset_completion_report, _ = front_service.asset_completion(episode_id)
            asset_completion_check = dict(asset_completion_report.get("machine_check") or {})
            asset_completion_ok = str(asset_completion_check.get("status") or "") in {"PASS", "SKIPPED"}
            checks.append(
                self._check(
                    "ASSET_COMPLETION_REQUIRED",
                    "角色三视图身份包与镜头绑定",
                    asset_completion_ok,
                    str(
                        asset_completion_check.get("detail")
                        or "角色资产缺少已批准三视图身份包，或镜头未绑定当前生效版本"
                    ),
                    asset_completion_check,
                )
            )
            # Source/draft/proposal/pack revisions are creative inputs too.
            # Freezing them prevents crash recovery from silently continuing
            # against a different human-reviewed front-half state.
            front_half_snapshot = front_service.snapshot(episode_id)

        blockers = [item for item in checks if item["blocking"]]
        # Capacity and free disk are intentionally excluded: they are launch
        # gates, not creative inputs.  A retry with the same idempotency key
        # must resolve to the same immutable workflow snapshot even if disk
        # usage changes by a few bytes between requests.
        fingerprint_source = {
            "episode_id": episode_id,
            "tts_enabled": tts_enabled,
            "production_mode": production_mode,
            "mode_policy": mode_policy,
            "available_mode_policies": available_mode_policies,
            "shots": [{"id": str(row["id"]), "revision_id": row["current_revision_id"], "status": str(row["status"])} for row in shots],
            "assets": [{"shot_id": str(row["shot_id"]), "id": str(row["id"]), "canonical_media_version_id": row["canonical_media_version_id"], "status": str(row["status"])} for row in bindings],
            "asset_states": [{"shot_id": str(row["shot_id"]), "asset_id": str(row["id"]), "state_id": row["effective_asset_state_id"], "state_status": row["effective_state_status"]} for row in bindings],
            "asset_references": [
                {
                    "asset_id": str(row["story_asset_id"]),
                    "state_id": row["asset_state_id"],
                    "media_version_id": str(row["media_version_id"]),
                    "kind": str(row["reference_kind"]),
                    "locked": bool(row["is_locked"]),
                    "status": str(row["status"]),
                    "integrity_status": str(row["integrity_status"]),
                }
                for row in sorted(
                    references,
                    key=lambda item: (
                        str(item["story_asset_id"]),
                        str(item["asset_state_id"] or ""),
                        str(item["reference_kind"]),
                        str(item["media_version_id"]),
                    ),
                )
            ],
            "asset_reference_requirements": {"required": required_character_refs, "missing": missing_required_references, "recipe_hash": str(recipe["recipe_hash"]) if recipe else None},
            "profiles": [{"id": str(row["id"]), "capability": str(row["capability"]), "status": str(row["status"]), "binding_status": str(row["binding_status"])} for row in profiles],
            "models": [{"code": str(row["code"]), "path": str(row["machine_path_ref"]), "status": str(row["status"])} for row in models],
        }
        if _include_checkpoint_in_fingerprint:
            fingerprint_source["checkpoint_policy"] = checkpoint_policy
        if include_front_half:
            fingerprint_source["front_half"] = front_half_snapshot
        fingerprint = hashlib.sha256(_canonical(fingerprint_source).encode("utf-8")).hexdigest()
        return {
            "episode": episode,
            "status": "PASS" if not blockers else "BLOCKED",
            "checks": checks,
            "blockers": blockers,
            "input_fingerprint": fingerprint,
            "tts_enabled": tts_enabled,
            "production_mode": production_mode,
            "mode_policy": mode_policy,
            "available_mode_policies": available_mode_policies,
            "checkpoint_policy": checkpoint_policy,
            "include_front_half": include_front_half,
            "front_half_snapshot": front_half_snapshot,
            "front_half_only": False,
            "would_create_jobs": False,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    def front_half_preflight(
        self,
        episode_id: str,
        *,
        production_mode: str = "BALANCED",
        checkpoint_policy: str = "ON_EXCEPTION",
    ) -> dict[str, Any]:
        """Fail-closed launch gate for a front-half-only durable run.

        This deliberately does not weaken the existing full-production
        preflight.  It only authorizes read-only front-half validators when a
        committed source (or an existing episode plan for a legacy project)
        is present.  Missing creative facts become worker ``NEEDS_HITL``
        reports; the run never auto-applies or auto-approves them.
        """
        baseline = self.preflight(
            episode_id,
            tts_enabled=False,
            production_mode=production_mode,
            checkpoint_policy=checkpoint_policy,
            min_free_disk_bytes=1,
            include_front_half=True,
        )
        source_report, _ = EpisodeFrontHalfActionService(self.database, self.settings).story_parse(episode_id)
        source_check = dict(source_report["machine_check"])
        launch_ok = str(source_check.get("status")) in {"PASS", "SKIPPED"}
        front_half_fingerprint = hashlib.sha256(
            _canonical(
                {
                    "base_input_fingerprint": baseline["input_fingerprint"],
                    "workflow_scope": "FRONT_HALF_ONLY",
                }
            ).encode("utf-8")
        ).hexdigest()
        check = self._check(
            "FRONT_HALF_SOURCE_NOT_READY",
            "前半链路源事实",
            launch_ok,
            str(source_check.get("detail") or "前半链路源事实不可用"),
            source_check,
        )
        return {
            **baseline,
            "status": "PASS" if launch_ok else "BLOCKED",
            "checks": [check],
            "blockers": [] if launch_ok else [check],
            "input_fingerprint": front_half_fingerprint,
            "tts_enabled": False,
            "include_front_half": True,
            "front_half_only": True,
            "strict_production_preflight": {
                "status": baseline["status"],
                "blocker_codes": [str(item["code"]) for item in baseline["blockers"]],
            },
        }

    def _workflow_for_snapshot(self, episode: dict[str, Any], preflight: dict[str, Any], *, actor: str) -> dict[str, Any]:
        episode_id = str(episode["id"])
        code = f"EPISODE_RUN_{episode_id.replace('-', '')[:16]}_{preflight['input_fingerprint'][:12]}"
        workflows = self.automation.list_workflows(str(episode["project_id"]), include_archived=True)["items"]
        prior = next((item for item in workflows if item["code"] == code), None)
        if prior:
            return prior
        include_front_half = bool(preflight.get("include_front_half"))
        front_half_only = bool(preflight.get("front_half_only"))
        actions: list[str] = []
        if include_front_half:
            # The order is part of the immutable plan.  In particular,
            # ASSET_COMPLETION must not be skipped between identity decisions
            # and episode-plan validation.
            actions.extend(FRONT_HALF_ACTIONS)
        else:
            # Compatibility for existing frozen workflows created before the
            # front-half vertical slice.
            actions.append("KEYFRAME_CHECK")
        if not front_half_only:
            actions.extend(BACK_HALF_ACTIONS)
            if preflight.get("tts_enabled", True):
                actions.append("TTS_BATCH")
            actions.extend(["RENDER", "DELIVERY"])
        production_mode = str(preflight.get("production_mode") or "BALANCED")
        mode_policy = dict(preflight.get("mode_policy") or PRODUCTION_MODE_POLICIES[production_mode])
        checkpoint_policy = self._checkpoint_policy(str(preflight.get("checkpoint_policy") or "ON_EXCEPTION"))
        items = [
            {
                "key": f"{episode['code']}:{action}",
                "payload": {
                    "action": action,
                    "episode_id": episode_id,
                    "input_fingerprint": preflight["input_fingerprint"],
                    "production_mode": production_mode,
                    "mode_policy": mode_policy,
                    "checkpoint_policy": checkpoint_policy,
                    "front_half_managed": include_front_half,
                },
            }
            for action in actions
        ]
        task_cap = len(items) + 1
        disk_check = next((item for item in preflight["checks"] if item.get("code") == "DISK_SPACE_LOW"), None)
        if disk_check is None:
            disk_check = next((item for item in reversed(preflight["checks"]) if item.get("evidence", {}).get("required_free_bytes")), None)
        required_free_bytes = int((disk_check or {}).get("evidence", {}).get("required_free_bytes") or 1)
        if front_half_only:
            required_free_bytes = max(required_free_bytes, FRONT_HALF_REPORT_BUDGET_BYTES)
        return self.automation.create_workflow(
            str(episode["project_id"]), code=code, title=f"{episode['code']} 整集生产",
            mode="BATCH_AUTOMATED", nodes=[{"id": "episode-production", "type": "EPISODE_PRODUCTION_TASK", "metadata": {"episode_id": episode_id, "input_fingerprint": preflight["input_fingerprint"], "production_mode": production_mode, "mode_policy": mode_policy, "checkpoint_policy": checkpoint_policy, "include_front_half": include_front_half, "front_half_only": front_half_only}}],
            batch_items=items,
            conditions=[{"field": "machine_check.status", "operator": "IN", "value": ["FAIL", "FAILED", "BLOCKED", "NEEDS_HITL"], "action": "PAUSE_HITL"}],
            max_iterations=task_cap, max_tasks=task_cap, max_disk_bytes=max(1, required_free_bytes),
            human_gate="ON_CONDITION", repeat_batch=False, actor=actor,
        )

    def start(
        self,
        episode_id: str,
        *,
        idempotency_key: str,
        tts_enabled: bool = True,
        production_mode: str = "BALANCED",
        checkpoint_policy: str = "ON_EXCEPTION",
        min_free_disk_bytes: int = 5 * 1024 * 1024 * 1024,
        front_half_only: bool = False,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        preflight = (
            self.front_half_preflight(
                episode_id,
                production_mode=production_mode,
                checkpoint_policy=checkpoint_policy,
            )
            if front_half_only
            else self.preflight(
                episode_id,
                tts_enabled=tts_enabled,
                production_mode=production_mode,
                checkpoint_policy=checkpoint_policy,
                min_free_disk_bytes=min_free_disk_bytes,
                include_front_half=True,
            )
        )
        if preflight["status"] != "PASS":
            code = "EPISODE_FRONT_HALF_PREFLIGHT_BLOCKED" if front_half_only else "EPISODE_PRODUCTION_PREFLIGHT_BLOCKED"
            message = "前半链路 preflight 未通过" if front_half_only else "整集生产 preflight 未通过"
            raise DomainRuleError(code, message, {"episode_id": episode_id, "blocker_codes": [item["code"] for item in preflight["blockers"]], "preflight": preflight})
        workflow = self._workflow_for_snapshot(preflight["episode"], preflight, actor=actor)
        run = self.automation.start_run(str(workflow["id"]), plan_hash=str(workflow["plan_hash"]), idempotency_key=idempotency_key, actor=actor)
        return self._view(run, include_jobs=True)

    def _episode_id_for_run(self, run: dict[str, Any]) -> str:
        workflow = self.automation.get_workflow(str(run["workflow_id"]))
        nodes = workflow["definition"].get("nodes", [])
        episode_id = str(nodes[0].get("metadata", {}).get("episode_id", "")) if nodes else ""
        if not episode_id:
            raise DomainRuleError("EPISODE_PRODUCTION_RUN_NOT_FOUND", "该 automation run 不是 Episode Production Run", {"run_id": run["id"]})
        return episode_id

    @staticmethod
    def _state(completed: int, total: int, running: int, failed: int, hitl: int, run_status: str) -> str:
        if total > 0 and completed >= total:
            return "COMPLETED"
        # A terminally cancelled run must never make partially completed or
        # untouched stages look active.  Job/fact counts remain visible, while
        # the stage label truthfully communicates that no work is running.
        if run_status == "CANCELLED":
            return "CANCELLED"
        if failed:
            return "BLOCKED"
        if hitl:
            return "PAUSED"
        if running or completed:
            return "PAUSED" if run_status == "PAUSED_HITL" else "RUNNING"
        return "PENDING"

    def _view(self, run: dict[str, Any], *, include_jobs: bool = False) -> dict[str, Any]:
        episode_id = self._episode_id_for_run(run)
        with self.database.connect() as connection:
            story_analysis_done = int(connection.execute(
                """SELECT EXISTS(SELECT 1 FROM audit_events WHERE action='SCRIPT_BREAKDOWN_APPLIED'
                AND json_extract(metadata_redacted_json,'$.episode_id')=?)""",
                (episode_id,),
            ).fetchone()[0])
            shot_count = int(connection.execute("SELECT COUNT(*) FROM shots WHERE episode_id=? AND archived_at IS NULL", (episode_id,)).fetchone()[0])
            plan_ready = int(connection.execute("SELECT COUNT(*) FROM shots WHERE episode_id=? AND archived_at IS NULL AND current_revision_id IS NOT NULL AND status IN ('READY','GENERATING','REVIEW','APPROVED')", (episode_id,)).fetchone()[0])
            asset_ready = int(connection.execute("SELECT COUNT(DISTINCT s.id) FROM shots s JOIN shot_asset_bindings sab ON sab.shot_id=s.id JOIN story_assets sa ON sa.id=sab.asset_id WHERE s.episode_id=? AND s.archived_at IS NULL AND sa.status='ACTIVE' AND sa.canonical_media_version_id IS NOT NULL", (episode_id,)).fetchone()[0])
            keyframes = int(connection.execute(
                """SELECT COUNT(DISTINCT ma.owner_id) FROM media_assets ma
                JOIN media_versions mv ON mv.id=ma.approved_version_id
                JOIN shots s ON s.id=ma.owner_id
                WHERE s.episode_id=? AND s.archived_at IS NULL
                AND ma.owner_type='SHOT' AND ma.purpose='KEYFRAME'
                AND ma.media_kind='IMAGE' AND mv.stage='KEYFRAME'
                AND mv.integrity_status='VERIFIED' AND EXISTS (
                  SELECT 1 FROM review_decisions rd
                  WHERE rd.subject_type='MEDIA_VERSION' AND rd.subject_id=mv.id
                  AND rd.decision='APPROVED' AND rd.is_stale=0
                )""",
                (episode_id,),
            ).fetchone()[0])
            videos = int(connection.execute(
                """SELECT COUNT(DISTINCT shot_id) FROM (
                SELECT ma.owner_id AS shot_id FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
                JOIN shots s ON s.id=ma.owner_id WHERE s.episode_id=? AND s.archived_at IS NULL AND ma.owner_type='SHOT'
                AND ma.media_kind='VIDEO' AND mv.integrity_status='VERIFIED'
                UNION SELECT gi.owner_id FROM generation_intents gi JOIN generation_variants gv ON gv.intent_id=gi.id
                JOIN media_assets ma ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
                JOIN media_versions mv ON mv.media_asset_id=ma.id JOIN shots s ON s.id=gi.owner_id
                WHERE s.episode_id=? AND s.archived_at IS NULL AND gi.owner_type='SHOT' AND ma.media_kind='VIDEO' AND mv.integrity_status='VERIFIED')""",
                (episode_id, episode_id),
            ).fetchone()[0])
            qc_passed = int(connection.execute(
                """SELECT COUNT(DISTINCT shot_id) FROM (
                SELECT ma.owner_id AS shot_id,mcr.status FROM machine_check_runs mcr
                JOIN media_versions mv ON mv.id=mcr.subject_id JOIN media_assets ma ON ma.id=mv.media_asset_id
                JOIN shots s ON s.id=ma.owner_id WHERE mcr.subject_type='MEDIA_VERSION' AND ma.owner_type='SHOT'
                AND s.episode_id=? AND s.archived_at IS NULL AND mcr.status='PASS'
                UNION SELECT gi.owner_id,mcr.status FROM machine_check_runs mcr
                JOIN media_versions mv ON mv.id=mcr.subject_id JOIN media_assets ma ON ma.id=mv.media_asset_id
                JOIN generation_variants gv ON ma.owner_type='GENERATION_VARIANT' AND gv.id=ma.owner_id
                JOIN generation_intents gi ON gi.id=gv.intent_id JOIN shots s ON s.id=gi.owner_id
                WHERE mcr.subject_type='MEDIA_VERSION' AND gi.owner_type='SHOT' AND s.episode_id=? AND s.archived_at IS NULL AND mcr.status='PASS')""",
                (episode_id, episode_id),
            ).fetchone()[0])
            audio_total = int(connection.execute("SELECT COUNT(*) FROM dialogue_lines WHERE episode_id=?", (episode_id,)).fetchone()[0])
            audio_done = int(connection.execute("SELECT COUNT(DISTINCT dl.id) FROM dialogue_lines dl JOIN dialogue_candidate_selections dcs ON dcs.dialogue_line_id=dl.id WHERE dl.episode_id=?", (episode_id,)).fetchone()[0])
            compose_done = int(connection.execute("SELECT COUNT(*) FROM episode_render_versions WHERE episode_id=?", (episode_id,)).fetchone()[0])

        tasks_by_stage: dict[str, list[dict[str, Any]]] = {code: [] for code, _, _ in STAGE_DEFINITIONS}
        for task in run["tasks"]:
            action = str(task.get("item", {}).get("payload", {}).get("action", ""))
            stage_code = ACTION_STAGE.get(action)
            if stage_code:
                tasks_by_stage[stage_code].append(task)
        has_front_half_tasks = any(
            str(task.get("item", {}).get("payload", {}).get("action", "")) == "STORY_PARSE"
            for task in run["tasks"]
        )
        front_completion: dict[str, int] = {}
        if has_front_half_tasks:
            front_service = EpisodeFrontHalfActionService(self.database, self.settings)
            reports = {
                "STORY_PARSE": front_service.story_parse(episode_id)[0],
                "SCRIPT_BREAKDOWN": front_service.script_breakdown(episode_id)[0],
                "ASSET_IDENTITY": front_service.asset_identity(episode_id)[0],
                "ASSET_COMPLETION": front_service.asset_completion(episode_id)[0],
                "EPISODE_PLAN": front_service.episode_plan(episode_id)[0],
            }
            front_completion = {
                action: int(str(report.get("machine_check", {}).get("status")) in {"PASS", "SKIPPED"})
                for action, report in reports.items()
            }

        facts = {
            "STORY_ANALYSIS": (
                min(front_completion["STORY_PARSE"], front_completion["SCRIPT_BREAKDOWN"])
                if has_front_half_tasks else story_analysis_done,
                1,
            ),
            "ASSET_EXTRACTION": (front_completion["ASSET_IDENTITY"] if has_front_half_tasks else story_analysis_done, 1),
            "ASSET_COMPLETION": (front_completion["ASSET_COMPLETION"], 1) if has_front_half_tasks else (asset_ready, shot_count),
            "SHOT_PLANNING": (plan_ready if not has_front_half_tasks or front_completion["EPISODE_PLAN"] else 0, shot_count),
            "SHOT_IMAGE": (keyframes, shot_count),
            "VIDEO": (videos, shot_count),
            "AUDIO_SUBTITLE": (audio_done, audio_total),
            "COMPOSE_QC": (qc_passed + (1 if compose_done else 0), shot_count + 1),
        }
        stages = []
        for index, (code, label, background_stages) in enumerate(STAGE_DEFINITIONS, 1):
            stage_tasks = tasks_by_stage[code]
            completed_count, total = facts[code]
            running = sum(1 for item in stage_tasks if item.get("job_state") in {"QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"})
            failed = sum(1 for item in stage_tasks if item.get("job_state") in {"FAILED", "NEEDS_ATTENTION", "ORPHANED"})
            hitl = sum(1 for item in stage_tasks if item.get("status") == "BLOCKED_HITL")
            stage_view: dict[str, Any] = {
                "ordinal": index, "code": code, "label": label,
                "background_stages": list(background_stages),
                "status": self._state(completed_count, total, running, failed, hitl, str(run["status"])),
                "completed": completed_count, "total": total,
                "remaining_count": max(total - completed_count, 0),
                "running_jobs": running, "failed": failed, "failed_jobs": failed,
                "needs_human_decision": hitl, "hitl_jobs": hitl,
                "estimated_remaining_seconds": None, "estimate_status": "NOT_AVAILABLE",
            }
            if include_jobs:
                stage_view["jobs"] = [{"task_id": item["id"], "job_id": item.get("job_id"), "job_state": item.get("job_state"), "item_key": item["item_key"], "status": item["status"]} for item in stage_tasks]
                issues: list[dict[str, Any]] = []
                for task in stage_tasks:
                    context = task.get("machine_context", {})
                    check = context.get("machine_check", {}) if isinstance(context, dict) else {}
                    if not isinstance(check, dict):
                        continue
                    for key in ("blocked_shots", "attention_shots", "incomplete_shots", "missing_required_references"):
                        values = check.get(key, [])
                        if not isinstance(values, list):
                            continue
                        for value in values:
                            if not isinstance(value, dict) or not value.get("shot_id"):
                                continue
                            issue = {
                                "shot_id": str(value["shot_id"]),
                                "shot_code": str(value.get("shot_code") or "镜头"),
                                "status": str(value.get("status") or "BLOCKED"),
                                "code": str(value.get("code") or key.upper()),
                                "job_id": str(value["job_id"]) if value.get("job_id") else task.get("job_id"),
                            }
                            if issue not in issues:
                                issues.append(issue)
                stage_view["issues"] = issues[:100]
            stages.append(stage_view)
        recoverable_jobs = [
            {"task_id": item["id"], "job_id": item.get("job_id"), "job_state": item.get("job_state")}
            for item in run["tasks"] if item.get("job_state") in {"ORPHANED", "NEEDS_ATTENTION"}
        ]
        workflow_metadata = self.automation.get_workflow(str(run["workflow_id"]))["definition"]["nodes"][0]["metadata"]
        return {
            "id": run["id"],
            "episode_id": episode_id,
            "project_id": run["project_id"],
            "automation_run_id": run["id"],
            "automation_workflow_id": run["workflow_id"],
            "status": run["status"],
            "input_fingerprint": workflow_metadata.get("input_fingerprint"),
            "production_mode": workflow_metadata.get("production_mode", "BALANCED"),
            "mode_policy": workflow_metadata.get("mode_policy", PRODUCTION_MODE_POLICIES["BALANCED"]),
            "checkpoint_policy": workflow_metadata.get("checkpoint_policy", "ON_EXCEPTION"),
            "include_front_half": bool(workflow_metadata.get("include_front_half", False)),
            "front_half_only": bool(workflow_metadata.get("front_half_only", False)),
            "stages": stages,
            "pending_gate": run["pending_gate"],
            "started_at": run["started_at"],
            "completed_at": run["completed_at"],
            "updated_at": run["updated_at"],
            "revision": run["revision"],
            "recovery": {
                "recoverable": bool(recoverable_jobs) or str(run["status"]) in {"RUNNING", "PAUSED_HITL"},
                "recoverable_jobs": recoverable_jobs,
            },
            "local_only": True,
            "queue_reused": True, "idempotent_replay": bool(run.get("idempotent_replay", False)),
        }

    def get(self, run_id: str, *, include_jobs: bool = False) -> dict[str, Any]:
        return self._view(self.automation.get_run(run_id), include_jobs=include_jobs)

    def watchdog(
        self,
        *,
        stale_seconds: int = 30,
        limit: int = 20,
        actor: str = "episode-run-watchdog",
    ) -> dict[str, Any]:
        """Converge stale active episode runs without replaying valid work.

        The durable worker calls this only on startup and bounded idle
        intervals. Generic automation runs are ignored: an episode workflow is
        recognized through the same frozen metadata gate used by ``recover``.
        """
        cutoff = (datetime.now(UTC) - timedelta(seconds=max(0, stale_seconds))).isoformat()
        bounded_limit = max(1, min(int(limit), 100))
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id FROM automation_workflow_runs
                WHERE status='RUNNING' AND updated_at<=?
                ORDER BY updated_at ASC,id ASC LIMIT ?""",
                (cutoff, bounded_limit),
            ).fetchall()
        recovered: list[str] = []
        skipped: list[str] = []
        errors: list[dict[str, str]] = []
        for row in rows:
            run_id = str(row["id"])
            try:
                self.recover(run_id, actor=actor)
                recovered.append(run_id)
            except DomainRuleError as error:
                if error.code == "EPISODE_PRODUCTION_RUN_NOT_FOUND":
                    skipped.append(run_id)
                else:
                    errors.append({"run_id": run_id, "code": error.code})
        return {
            "scanned": len(rows),
            "recovered_run_ids": recovered,
            "skipped_non_episode_run_ids": skipped,
            "errors": errors,
            "stale_seconds": max(0, stale_seconds),
            "bounded": True,
        }

    def pause(self, run_id: str, *, reason: str, actor: str = "local-user") -> dict[str, Any]:
        self._episode_id_for_run(self.automation.get_run(run_id))
        return self._view(self.automation.pause_run(run_id, reason=reason, actor=actor), include_jobs=True)

    def resume(self, run_id: str, *, note: str, actor: str = "local-user") -> dict[str, Any]:
        self._episode_id_for_run(self.automation.get_run(run_id))
        self.automation.resume_run(run_id, decision="HUMAN_APPROVED", note=note, actor=actor)
        return self.recover(run_id, actor=actor)["run"]

    def cancel(self, run_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        self._episode_id_for_run(self.automation.get_run(run_id))
        return self._view(self.automation.cancel_run(run_id, actor=actor), include_jobs=True)

    def _completed_report(self, job_id: str) -> tuple[dict[str, Any], int] | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT a.sandbox_rel_path FROM artifacts a JOIN job_attempts ja ON ja.id=a.job_attempt_id
                WHERE ja.job_id=? AND ja.state='SUCCEEDED' AND a.status='VERIFIED'
                AND a.kind='AUTOMATION_TASK_REPORT' ORDER BY a.created_at DESC,a.id DESC LIMIT 1""", (job_id,),
            ).fetchone()
        if row is None:
            return None
        root = self.settings.work_root.resolve()
        path = (root / str(row["sandbox_rel_path"])).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
            return None
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, TypeError, ValueError):
            return None
        return (report, path.stat().st_size) if isinstance(report, dict) else None

    def recover(self, run_id: str, *, actor: str = "local-user") -> dict[str, Any]:
        """Reconcile durable leases and resume a run without replaying valid work."""
        initial = self.automation.get_run(run_id)
        episode_id = self._episode_id_for_run(initial)
        workflow = self.automation.get_workflow(str(initial["workflow_id"]))
        tts_enabled = any(str(item.get("payload", {}).get("action")) == "TTS_BATCH" for item in workflow["definition"].get("batch_items", []))
        metadata = workflow["definition"]["nodes"][0].get("metadata", {})
        production_mode = str(metadata.get("production_mode") or "BALANCED")
        has_checkpoint_snapshot = "checkpoint_policy" in metadata
        checkpoint_policy = str(metadata.get("checkpoint_policy") or "ON_EXCEPTION")
        include_front_half = bool(metadata.get("include_front_half", False))
        front_half_only = bool(metadata.get("front_half_only", False))
        current_preflight = (
            self.front_half_preflight(
                episode_id,
                production_mode=production_mode,
                checkpoint_policy=checkpoint_policy,
            )
            if front_half_only
            else self.preflight(
                episode_id, tts_enabled=tts_enabled, production_mode=production_mode,
                checkpoint_policy=checkpoint_policy, min_free_disk_bytes=1,
                include_front_half=include_front_half,
                _include_checkpoint_in_fingerprint=has_checkpoint_snapshot,
            )
        )
        current_fingerprint = str(current_preflight["input_fingerprint"])
        lease_reconcile = JobService(self.database, self.settings).reconcile(actor="episode-run-recovery")
        lease_requeued = {
            str(item.get("job_id"))
            for item in lease_reconcile.get("items", [])
            if isinstance(item, dict) and str(item.get("job_state")) == "QUEUED"
        }
        refreshed: list[str] = []
        requeued: list[str] = []
        skipped: list[str] = []
        stale_completed: list[str] = []
        rebuilt: list[str] = []
        rebuild_jobs: list[str] = []
        advance_job_id: str | None = None
        old_fingerprint = str(workflow["definition"]["nodes"][0].get("metadata", {}).get("input_fingerprint") or "")
        with self.database.transaction() as connection:
            run_row = connection.execute("SELECT * FROM automation_workflow_runs WHERE id=?", (run_id,)).fetchone()
            if run_row is None:
                raise DomainRuleError("AUTOMATION_RUN_NOT_FOUND", "workflow run 不存在")
            tasks = connection.execute(
                """SELECT t.*,j.state AS job_state,j.last_error_code,
                (SELECT provider_job_id FROM job_attempts a WHERE a.job_id=j.id ORDER BY attempt_no DESC LIMIT 1) provider_job_id
                FROM automation_workflow_run_tasks t JOIN jobs j ON j.id=t.job_id
                WHERE t.run_id=? ORDER BY t.ordinal""", (run_id,),
            ).fetchall()
            for task in tasks:
                item = json.loads(str(task["item_json"] or "{}"))
                payload = item.get("payload", {}) if isinstance(item, dict) else {}
                task_fingerprint = str(payload.get("input_fingerprint") or old_fingerprint)
                job_state = str(task["job_state"])
                if job_state == "QUEUED" and str(task["job_id"]) in lease_requeued:
                    requeued.append(str(task["job_id"]))
                if job_state == "SUCCEEDED":
                    if task_fingerprint == current_fingerprint:
                        skipped.append(str(task["id"]))
                    else:
                        stale_completed.append(str(task["id"]))
                    continue
                if task_fingerprint != current_fingerprint and job_state in {"QUEUED", "NEEDS_ATTENTION", "ORPHANED"}:
                    if isinstance(payload, dict):
                        payload["input_fingerprint"] = current_fingerprint
                        item["payload"] = payload
                    snapshot_row = connection.execute("SELECT input_snapshot_json FROM jobs WHERE id=?", (task["job_id"],)).fetchone()
                    snapshot = json.loads(str(snapshot_row["input_snapshot_json"] or "{}")) if snapshot_row else {}
                    snapshot["recovery_input_fingerprint"] = current_fingerprint
                    snapshot["recovery_previous_input_fingerprint"] = task_fingerprint
                    connection.execute("UPDATE automation_workflow_run_tasks SET item_json=?,updated_at=?,revision=revision+1 WHERE id=?", (_canonical(item), _now(), task["id"]))
                    connection.execute("UPDATE jobs SET input_snapshot_json=?,updated_at=?,revision=revision+1 WHERE id=?", (_canonical(snapshot), _now(), task["job_id"]))
                    refreshed.append(str(task["id"]))
                if (
                    str(run_row["status"]) == "RUNNING" and job_state in {"ORPHANED", "NEEDS_ATTENTION"}
                    and str(task["last_error_code"] or "") == "WORKER_LEASE_EXPIRED" and not task["provider_job_id"]
                ):
                    connection.execute(
                        """UPDATE jobs SET state='QUEUED',next_run_at=?,last_error_code=NULL,last_error_detail_redacted=NULL,
                        finished_at=NULL,updated_at=?,revision=revision+1 WHERE id=? AND state IN ('ORPHANED','NEEDS_ATTENTION')""",
                        (_now(), _now(), task["job_id"]),
                    )
                    requeued.append(str(task["job_id"]))
            latest = tasks[-1] if tasks else None
            if latest and str(run_row["status"]) == "RUNNING" and str(latest["job_state"]) == "SUCCEEDED" and int(latest["ordinal"]) == int(run_row["task_count"]):
                latest_item = json.loads(str(latest["item_json"] or "{}"))
                latest_payload = latest_item.get("payload", {}) if isinstance(latest_item, dict) else {}
                latest_fingerprint = str(latest_payload.get("input_fingerprint") or old_fingerprint)
                if latest_fingerprint != current_fingerprint:
                    # The worker may have durably completed just before a crash
                    # while a creative input changed.  Keep that terminal Job
                    # as immutable evidence and attach a fresh Job to the same
                    # bounded workflow task; no parallel task/queue model is
                    # introduced and repeated recovery calls remain idempotent.
                    if isinstance(latest_payload, dict):
                        latest_payload["input_fingerprint"] = current_fingerprint
                        latest_item["payload"] = latest_payload
                    previous = connection.execute(
                        "SELECT job_id FROM automation_workflow_run_tasks WHERE run_id=? AND ordinal<? ORDER BY ordinal DESC LIMIT 1",
                        (run_id, latest["ordinal"]),
                    ).fetchone()
                    dependencies = [str(previous["job_id"])] if previous and previous["job_id"] else []
                    replacement = self.automation.jobs.create_job_in_transaction(
                        connection,
                        str(run_row["project_id"]),
                        "AUTOMATION_WORKFLOW_TASK",
                        "AUTOMATION_WORKFLOW_TASK",
                        str(latest["id"]),
                        "CPU",
                        {
                            "schema_version": "localdrama.automation-task.v1",
                            "automation_workflow_id": str(run_row["workflow_id"]),
                            "automation_run_id": run_id,
                            "automation_task_id": str(latest["id"]),
                            "ordinal": int(latest["ordinal"]),
                            "item_key": str(latest["item_key"]),
                            "plan_hash": str(run_row["plan_hash"]),
                            "recovery_rebuild": True,
                            "recovery_source_job_id": str(latest["job_id"]),
                            "recovery_input_fingerprint": current_fingerprint,
                            "approval_required": False,
                            "approval_status": "NOT_REQUIRED",
                            "local_only": True,
                            "network_contacted": False,
                        },
                        f"automation-recovery:{latest['id']}:{current_fingerprint}",
                        max_attempts=1,
                        depends_on_job_ids=dependencies,
                        actor=actor,
                    )
                    task_machine = json.loads(str(latest["machine_context_json"] or "{}"))
                    history = task_machine.setdefault("recovery_rebuilds", [])
                    if isinstance(history, list):
                        history.append({"source_job_id": str(latest["job_id"]), "replacement_job_id": str(replacement["id"]), "previous_input_fingerprint": latest_fingerprint, "input_fingerprint": current_fingerprint})
                    connection.execute(
                        "UPDATE automation_workflow_run_tasks SET item_json=?,machine_context_json=?,job_id=?,updated_at=?,revision=revision+1 WHERE id=?",
                        (_canonical(latest_item), _canonical(task_machine), replacement["id"], _now(), latest["id"]),
                    )
                    rebuilt.append(str(latest["id"]))
                    rebuild_jobs.append(str(replacement["id"]))
                else:
                    advance_job_id = str(latest["job_id"])
            machine = json.loads(str(run_row["machine_context_json"] or "{}"))
            machine["recovery"] = {
                "input_fingerprint": current_fingerprint, "previous_input_fingerprint": old_fingerprint,
                "refreshed_task_ids": refreshed, "stale_completed_task_ids": stale_completed,
                "rebuilt_task_ids": rebuilt, "rebuild_job_ids": rebuild_jobs,
            }
            connection.execute("UPDATE automation_workflow_runs SET machine_context_json=?,updated_at=?,revision=revision+1 WHERE id=?", (_canonical(machine), _now(), run_id))
            self.automation._event(connection, run_id, "RECOVERY_RECONCILED", {"requeued_job_ids": requeued, "skipped_task_ids": skipped, "refreshed_task_ids": refreshed, "stale_completed_task_ids": stale_completed, "rebuilt_task_ids": rebuilt, "rebuild_job_ids": rebuild_jobs, "current_input_fingerprint": current_fingerprint}, actor)
        advanced = False
        missing_report = False
        completion_deferred_by_pause: str | None = None
        completion_skipped_terminal_run: str | None = None
        if advance_job_id:
            completed = self._completed_report(advance_job_id)
            if completed is None:
                missing_report = True
            else:
                report, produced_bytes = completed
                machine_check = report.get("machine_check", {}) if isinstance(report.get("machine_check"), dict) else {}
                status = str(machine_check.get("status", "PASS"))
                try:
                    self.automation.step_run(
                        run_id, machine_context={"status": status, "machine_check": machine_check},
                        produced_bytes=produced_bytes, expected_completed_job_id=advance_job_id, actor=actor,
                    )
                    advanced = True
                except DomainRuleError as error:
                    if error.code != "AUTOMATION_RUN_NOT_RUNNING":
                        raise
                    # CAS boundary: the recovery read transaction deliberately
                    # ends before report I/O and step_run. A human pause (or a
                    # concurrent recovery completing the run) may win in that
                    # interval. Preserve that newer state and report a durable
                    # no-op instead of turning a valid pause into a 4xx retry.
                    current_status = str(self.automation.get_run(run_id)["status"])
                    if current_status == "PAUSED_HITL":
                        completion_deferred_by_pause = advance_job_id
                    elif current_status in {"SUCCEEDED", "STOPPED", "FAILED", "CANCELLED", "LIMIT_REACHED"}:
                        completion_skipped_terminal_run = advance_job_id
                    else:
                        raise
        result = self._view(self.automation.get_run(run_id), include_jobs=True)
        return {"run": result, "recovery": {"lease_reconcile": lease_reconcile, "requeued_job_ids": requeued, "skipped_task_ids": skipped, "refreshed_task_ids": refreshed, "stale_completed_task_ids": stale_completed, "rebuilt_task_ids": rebuilt, "rebuild_job_ids": rebuild_jobs, "advanced_completed_job": advanced, "missing_completed_report": missing_report, "completion_deferred_by_pause_job_id": completion_deferred_by_pause, "completion_skipped_terminal_run_job_id": completion_skipped_terminal_run, "input_fingerprint": current_fingerprint}}
