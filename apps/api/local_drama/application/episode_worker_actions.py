"""Shot-local VIDEO_GENERATION and QC actions for episode automation runs.

This module is deliberately an orchestrator over existing authorities:
GenerationVariant/Job own execution, MediaVersion owns immutable outputs,
MachineCheckRun owns QC evidence, and ReviewDecision remains human-owned.
It does not introduce another queue or an episode-specific result table.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from local_drama.config import Settings
from local_drama.domain.capabilities import VIDEO_GENERATION_CAPABILITIES
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.generation import VariantPlan
from local_drama.domain.policies import VariantInput, missing_shot_fields
from local_drama.domain.shot_prompt import compose_shot_prompt
from local_drama.infrastructure.database.episode_production_repository import (
    SqliteEpisodeProductionReadRepository,
)
from local_drama.infrastructure.database.generation_preference_repository import (
    SqliteGenerationPreferenceRepository,
)
from local_drama.infrastructure.database.qc_policy_repository import SqliteQcPolicyRepository
from local_drama.infrastructure.database.sqlite import Database

from .commands.qc_policies import QcPolicyCommandService
from .generation import GenerationService
from .jobs import JobService
from .keyframe_references import approved_keyframe_for_shot, approved_keyframes_for_shots
from .shot_keyframe_generation import ShotKeyframeGenerationBatchService
from .media import MediaService, infer_media_kind
from .queries.generation_preferences import GenerationPreferenceQueryService
from .queries.qc_policies import QcPolicyQueryService
from .reviews import ReviewService
from .timeline import TimelineService

ACTIVE_JOB_STATES = {"QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"}
FAILED_JOB_STATES = {"FAILED", "NEEDS_ATTENTION", "ORPHANED"}


class EpisodeWorkerActionService:
    """Dispatch bounded per-shot work while preserving existing fact models."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        qc_reroll_limit_resolver: Callable[[str, str, str, str], int] | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.generation = GenerationService(database, settings)
        self.jobs = JobService(database, settings)
        self.media = MediaService(database, settings)
        self.reviews = ReviewService(database, settings)
        self.timeline = TimelineService(database, settings)
        # 0045's policy resolver can be injected without coupling the worker to
        # a policy persistence adapter.  Installations without a policy retain
        # the safe, bounded product default of one automatic reroll per shot.
        self.qc_reroll_limit_resolver = qc_reroll_limit_resolver
        self.keyframe_batches = ShotKeyframeGenerationBatchService(
            database, settings, generation=self.generation,
            preference_resolver_factory=lambda connection: GenerationPreferenceQueryService(
                SqliteGenerationPreferenceRepository(connection)
            ),
        )

    def keyframe_generation(
        self, episode_id: str, run_id: str, task_id: str, *, candidate_count: int = 1,
    ) -> tuple[dict[str, Any], int]:
        """Dispatch through the shared batch authority; never approve outputs.

        The subsequent check depends on these jobs and remains a human gate.
        A task retry reuses the batch's idempotency key rather than creating a
        second generation queue or changing an existing frozen batch.
        """
        project_id, shots = self._episode(episode_id)
        with self.database.connect() as connection:
            approved = approved_keyframes_for_shots(
                connection, (str(shot["id"]) for shot in shots), project_id=project_id,
            )
        targets = [
            {"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}
            for shot in shots if str(shot["id"]) not in approved
        ]
        reused = [{"shot_id": shot_id, "status": "APPROVED_REUSED", **fact} for shot_id, fact in approved.items()]
        if not targets:
            return self._report("PASS", {"status": "PASS"}, {"items": reused}, "全部镜头已有人工批准关键帧"), 0
        args = {"targets": targets, "frame_strategy": "FIRST_ONLY", "candidate_count": candidate_count}
        plan = self.keyframe_batches.plan(episode_id, **args)
        # The interactive batch screen allows a partial submission. A full
        # episode stage must expose every blocker before spending on the rest.
        if not plan["valid"] or plan["issues"] or plan["summary"]["blocked"]:
            return self._report(
                "NEEDS_HITL", {"status": "NEEDS_HITL", "code": "SHOT_KEYFRAME_BATCH_BLOCKED", "issues": plan["issues"]},
                {"items": reused, "plan": plan}, "关键帧生成配置或镜头内容需修正，请到镜头画面检查",
            ), 0
        batch = self.keyframe_batches.submit(
            episode_id, **args, expected_plan_hash=plan["plan_hash"],
            idempotency_key=f"episode-keyframes:{run_id}:{task_id}", actor="episode-worker",
        )
        failed = [item for item in batch["items"] if item["status"] in {"FAILED", "CANCELLED"}]
        status = "NEEDS_HITL" if failed else "PASS"
        return self._report(
            status, {"status": status, "code": "SHOT_KEYFRAME_GENERATION_FAILED" if failed else "KEYFRAMES_DISPATCHED", "issues": failed},
            {"items": [*reused, *batch["items"]], "batch_id": batch["id"]},
            "关键帧生成失败，请到任务中心修复后继续" if failed else "已提交关键帧，生成完成后逐镜人工审核",
        ), 0

    def _qc_reroll_limit(self, project_id: str, episode_id: str, shot_id: str) -> int:
        try:
            if self.qc_reroll_limit_resolver is not None:
                value = int(self.qc_reroll_limit_resolver(project_id, episode_id, shot_id, "VIDEO"))
            else:
                with self.database.connect() as connection:
                    policy = QcPolicyQueryService(SqliteQcPolicyRepository(connection)).resolve_for_context(
                        context={"project_id": project_id, "episode_id": episode_id, "shot_id": shot_id, "stage": "VIDEO"},
                    )
                value = 1 if policy is None else int(policy["max_auto_rerolls"])
        except (DomainRuleError, TypeError, ValueError, sqlite3.DatabaseError):
            return 1
        return max(0, min(value, 10))

    def _qc_policy_decision(self, variant_id: str, machine_check_run_id: str) -> dict[str, Any] | None:
        """Persist 0045 disposition when configured; no policy keeps safe v1 default."""
        try:
            with self.database.connect() as connection:
                return QcPolicyCommandService(SqliteQcPolicyRepository(connection)).decide(
                    variant_id=variant_id, machine_check_run_id=machine_check_run_id,
                    category="TECHNICAL", actor="episode-worker",
                )
        except DomainRuleError as error:
            if error.code == "QC_POLICY_REQUIRED":
                return None
            raise

    @staticmethod
    def _report(status: str, machine_check: dict[str, Any], produced: dict[str, Any], summary: str) -> dict[str, Any]:
        return {"status": status, "machine_check": machine_check, "produced": produced, "summary": summary}

    def _episode(self, episode_id: str) -> tuple[str, list[dict[str, Any]]]:
        with self.database.connect() as connection:
            episode = connection.execute(
                "SELECT se.project_id FROM episodes e JOIN seasons se ON se.id=e.season_id WHERE e.id=?",
                (episode_id,),
            ).fetchone()
            if episode is None:
                raise DomainRuleError("EPISODE_NOT_FOUND", "集不存在", {"episode_id": episode_id})
            rows = connection.execute(
                """SELECT s.id,s.code,s.target_duration_ms,s.status,s.revision,s.current_revision_id,sr.fields_json
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? AND s.archived_at IS NULL ORDER BY CAST(s.order_key AS REAL),s.code""",
                (episode_id,),
            ).fetchall()
        return str(episode["project_id"]), [dict(row) for row in rows]

    def video_generation_preflight(
        self,
        episode_id: str,
        *,
        target_shot_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        """Read-only, bounded preflight shared by batch commands and workers."""
        project_id, episode_shots = self._episode(episode_id)
        requested_ids = tuple(dict.fromkeys(str(item).strip() for item in target_shot_ids if str(item).strip()))
        if not requested_ids:
            raise DomainRuleError("SHOT_BATCH_EMPTY", "批量生成至少需要一个镜头")
        if len(requested_ids) > 100:
            raise DomainRuleError("SHOT_BATCH_TOO_LARGE", "单次批量生成最多包含 100 个镜头")
        by_id = {str(shot["id"]): shot for shot in episode_shots}
        unknown_ids = [shot_id for shot_id in requested_ids if shot_id not in by_id]
        if unknown_ids:
            raise DomainRuleError(
                "SHOT_BATCH_SCOPE_INVALID",
                "批量生成包含不属于当前集的镜头",
                {"episode_id": episode_id, "shot_ids": unknown_ids},
            )
        items: list[dict[str, Any]] = []
        for shot_id in requested_ids:
            shot = by_id[shot_id]
            fields = self._fields(shot)
            blockers: list[dict[str, Any]] = []
            missing = missing_shot_fields(fields)
            if missing:
                blockers.append({"code": "SHOT_NOT_PRODUCTION_READY", "missing_fields": missing})
            keyframe = self._approved_keyframe(shot_id)
            if keyframe is None:
                blockers.append({"code": "APPROVED_KEYFRAME_REQUIRED"})
            profile: dict[str, Any] | None = None
            try:
                profile = self._video_profile(project_id, shot_id)
            except DomainRuleError as error:
                blockers.append({"code": error.code})
            first_frame_role = self._first_frame_role(profile) if profile is not None else None
            if profile is not None and first_frame_role is None:
                blockers.append({"code": "FIRST_FRAME_SLOT_REQUIRED"})
            items.append(
                {
                    "shot_id": shot_id,
                    "shot_code": str(shot["code"]),
                    "shot_revision": int(shot["revision"]),
                    "shot_revision_id": str(shot["current_revision_id"] or ""),
                    "prompt": compose_shot_prompt(fields, shot_code=str(shot["code"])),
                    "prompt_modifiers": list(fields.get("prompt_modifiers") or []),
                    "profile_version_id": str(profile["id"]) if profile is not None else None,
                    "keyframe_media_version_id": str(keyframe["media_version_id"]) if keyframe is not None else None,
                    "first_frame_role": first_frame_role,
                    "status": "READY" if not blockers else "BLOCKED",
                    "blockers": blockers,
                }
            )
        fingerprint_payload = {
            "episode_id": episode_id,
            "project_id": project_id,
            "items": items,
        }
        return {
            **fingerprint_payload,
            "status": "READY" if all(item["status"] == "READY" for item in items) else "BLOCKED",
            "input_fingerprint": hashlib.sha256(
                json.dumps(fingerprint_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest(),
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }

    @staticmethod
    def _fields(shot: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(str(shot.get("fields_json") or "{}"))
        except (TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _shot_video(self, shot_id: str) -> dict[str, Any] | None:
        """Return the newest usable video, including GenerationVariant output."""
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT mv.id AS media_version_id,mv.media_asset_id,mv.stage,mv.integrity_status,
                ma.owner_type,ma.owner_id,ma.selected_version_id,ma.approved_version_id,gv.id AS variant_id
                FROM generation_intents gi JOIN generation_variants gv ON gv.intent_id=gi.id
                JOIN media_assets ma ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
                JOIN media_versions mv ON mv.media_asset_id=ma.id
                WHERE gi.owner_type='SHOT' AND gi.owner_id=? AND ma.media_kind='VIDEO'
                AND gv.is_stale=0 AND mv.integrity_status='VERIFIED'
                ORDER BY CASE WHEN ma.approved_version_id=mv.id THEN 0 WHEN ma.selected_version_id=mv.id THEN 1 ELSE 2 END,
                mv.created_at DESC,mv.id DESC LIMIT 1""",
                (shot_id,),
            ).fetchone()
            if row is None:
                row = connection.execute(
                    """SELECT mv.id AS media_version_id,mv.media_asset_id,mv.stage,mv.integrity_status,
                    ma.owner_type,ma.owner_id,ma.selected_version_id,ma.approved_version_id,NULL AS variant_id
                    FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
                    WHERE ma.owner_type='SHOT' AND ma.owner_id=? AND ma.media_kind='VIDEO'
                    AND mv.integrity_status='VERIFIED'
                    ORDER BY CASE WHEN ma.approved_version_id=mv.id THEN 0 WHEN ma.selected_version_id=mv.id THEN 1 ELSE 2 END,
                    mv.created_at DESC,mv.id DESC LIMIT 1""",
                    (shot_id,),
                ).fetchone()
        return dict(row) if row else None

    def _shot_video_count(self, shot_id: str) -> int:
        """Count verified candidates, not merely jobs that reported success."""
        with self.database.connect() as connection:
            return int(connection.execute(
                """SELECT COUNT(DISTINCT media_version_id) FROM (
                SELECT mv.id AS media_version_id FROM generation_intents gi
                JOIN generation_variants gv ON gv.intent_id=gi.id
                JOIN media_assets ma ON ma.owner_type='GENERATION_VARIANT' AND ma.owner_id=gv.id
                JOIN media_versions mv ON mv.media_asset_id=ma.id
                WHERE gi.owner_type='SHOT' AND gi.owner_id=? AND ma.media_kind='VIDEO'
                AND gv.is_stale=0 AND mv.integrity_status='VERIFIED'
                UNION SELECT mv.id FROM media_assets ma JOIN media_versions mv ON mv.media_asset_id=ma.id
                WHERE ma.owner_type='SHOT' AND ma.owner_id=? AND ma.media_kind='VIDEO'
                AND mv.integrity_status='VERIFIED')""",
                (shot_id, shot_id),
            ).fetchone()[0])

    def _stale_working_media_shots(self, episode_id: str, shot_ids: set[str]) -> set[str]:
        """Resolve dynamic working-media staleness from the episode read model.

        ``generation_variants.is_stale`` covers persisted invalidation, while
        the production read model also compares frozen prompt/profile/reference
        inputs with the current project/episode/shot context.  The latter is
        what surfaces ``WORKING_MEDIA_STALE`` in the episode UI.  Reusing that
        authority here keeps dispatch and the page in agreement and prevents a
        verified historical candidate from satisfying the current run.
        """
        if not shot_ids:
            return set()
        try:
            facts = SqliteEpisodeProductionReadRepository(self.database).shot_facts(
                episode_id,
                cursor=0,
                limit=max(100, len(shot_ids)),
                states=set(),
            )
        except DomainRuleError as error:
            # Unit seams may provide an in-memory _episode projection without a
            # persisted episode.  A real production call has already resolved
            # the episode above, so only that test seam gets the empty result;
            # all other domain failures remain visible to the caller.
            if error.code == "EPISODE_NOT_FOUND":
                return set()
            raise
        stale: set[str] = set()
        for item in facts.get("items", []):
            item_id = str(item.get("shot_id") or "")
            if item_id not in shot_ids:
                continue
            if any(str(blocker.get("code") or "") == "WORKING_MEDIA_STALE" for blocker in item.get("blockers", [])):
                stale.add(item_id)
        return stale

    def _variant_jobs(self, shot_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT j.*,gv.id AS variant_id,gv.explicit_seed,gv.branch_reason
                FROM generation_intents gi JOIN generation_variants gv ON gv.intent_id=gi.id
                JOIN jobs j ON j.subject_type='GENERATION_VARIANT' AND j.subject_id=gv.id
                WHERE gi.owner_type='SHOT' AND gi.owner_id=?
                ORDER BY j.created_at DESC,j.id DESC""",
                (shot_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _promoted_version_id(item: dict[str, Any]) -> str:
        """Read MediaService's canonical MediaVersion response shape."""
        version_id = str(item.get("media_version_id") or item.get("id") or "").strip()
        if not version_id:
            raise DomainRuleError("MEDIA_PROMOTION_RESPONSE_INVALID", "媒体晋升响应缺少 MediaVersion id")
        return version_id

    def _promote_completed_outputs(self, jobs: list[dict[str, Any]]) -> list[str]:
        promoted: list[str] = []
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT a.id FROM artifacts a JOIN job_attempts ja ON ja.id=a.job_attempt_id
                WHERE ja.job_id IN ({}) AND ja.state='SUCCEEDED' AND a.status='VERIFIED'
                ORDER BY a.created_at,a.id""".format(",".join("?" for _ in jobs)),
                tuple(str(item["id"]) for item in jobs),
            ).fetchall() if jobs else []
        for row in rows:
            with self.database.connect() as connection:
                artifact = connection.execute("SELECT sandbox_rel_path FROM artifacts WHERE id=?", (row["id"],)).fetchone()
            if artifact is None or infer_media_kind(Path(str(artifact["sandbox_rel_path"]))) != "VIDEO":
                continue
            try:
                item = self.media.promote_job_artifact(str(row["id"]), purpose="SHOT_VIDEO_CANDIDATE", stage="FORMAL", actor="episode-worker")
            except DomainRuleError as error:
                # A Comfy workflow may emit preview images beside the video.
                # Those remain verified artifacts; only video candidates are
                # relevant to this action.
                if error.code in {"ARTIFACT_FILE_MISSING", "ARTIFACT_INTEGRITY_FAILED", "ARTIFACT_NOT_PROMOTABLE"}:
                    raise
                continue
            if str(item.get("mime_type") or "").startswith("video/"):
                promoted.append(self._promoted_version_id(item))
        return promoted

    def _approved_keyframe(self, shot_id: str) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            return approved_keyframe_for_shot(connection, shot_id)

    def _video_profile(self, project_id: str, shot_id: str) -> dict[str, Any]:
        """Resolve the exact shot-level preference used by the generation UI.

        The legacy project binding is a readiness/configuration fact, but it is
        not the authoritative per-shot model choice.  Production must use the
        same shot -> episode -> project preference resolver as Director and the
        Models workspace; otherwise the UI can display v19 while automation
        silently submits an older project binding such as v13.
        """
        with self.database.connect() as connection:
            resolution = GenerationPreferenceQueryService(
                SqliteGenerationPreferenceRepository(connection)
            ).resolve(
                project_id=project_id,
                shot_id=shot_id,
                capability="VIDEO_I2V",
            )
            profile_version_id = resolution.get("profile_version_id")
            if not profile_version_id:
                raise DomainRuleError(
                    "VIDEO_PROFILE_RESOLUTION_BLOCKED",
                    "当前镜头没有可执行的 VIDEO_I2V Profile",
                    {
                        "project_id": project_id,
                        "shot_id": shot_id,
                        "source": resolution.get("source"),
                        "blocked_reason": resolution.get("blocked_reason"),
                    },
                )
            row = connection.execute(
                """SELECT * FROM execution_profile_versions
                WHERE id=? AND status='PUBLISHED'""",
                (str(profile_version_id),),
            ).fetchone()
        if row is None or str(row["capability"]) not in VIDEO_GENERATION_CAPABILITIES:
            raise DomainRuleError(
                "VIDEO_PROFILE_RESOLUTION_INVALID",
                "解析结果没有指向已发布的精确视频 Profile",
                {"profile_version_id": profile_version_id, "shot_id": shot_id},
            )
        return dict(row)

    @staticmethod
    def _first_frame_role(profile: dict[str, Any]) -> str | None:
        try:
            contract = json.loads(str(profile.get("input_contract_json") or "{}"))
        except (TypeError, ValueError):
            return None
        slots = contract.get("input_slots", contract) if isinstance(contract, dict) else {}
        if not isinstance(slots, dict):
            return None
        for role in ("FIRST_FRAME", "REFERENCE_IMAGE"):
            spec = slots.get(role)
            if isinstance(spec, dict) and int(spec.get("max", 1)) >= 1:
                return role
        return None

    @staticmethod
    def _end_frame_role(profile: dict[str, Any]) -> str | None:
        try:
            contract = json.loads(str(profile.get("input_contract_json") or "{}"))
        except (TypeError, ValueError):
            return None
        slots = contract.get("input_slots", contract) if isinstance(contract, dict) else {}
        if not isinstance(slots, dict):
            return None
        spec = slots.get("END_FRAME")
        if isinstance(spec, dict) and int(spec.get("max", 1)) >= 1:
            return "END_FRAME"
        return None

    def _last_frame_anchor(self, source_media_version_id: str) -> dict[str, Any]:
        """Reuse the freshest LAST_FRAME anchor for a source video, else extract one."""
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT id, extracted_media_version_id FROM frame_anchors WHERE source_media_version_id=? AND role_hint='LAST_FRAME' AND is_stale=0 ORDER BY created_at DESC, id DESC LIMIT 1",
                (source_media_version_id,),
            ).fetchone()
        if row is not None:
            return {"id": str(row["id"]), "extracted_media_version_id": str(row["extracted_media_version_id"]), "reused": True}
        anchor = self.timeline.create_frame_anchor(
            source_media_version_id, position_mode="LAST_FRAME", role_hint="LAST_FRAME", actor="episode-run-auto",
        )
        return {"id": str(anchor["id"]), "extracted_media_version_id": str(anchor["extracted_media_version_id"]), "reused": False}

    def _end_frame_chain(self, shot: dict[str, Any], previous_shot: dict[str, Any] | None, fields: dict[str, Any]) -> dict[str, Any]:
        """Resolve the previous shot's last frame as this shot's END_FRAME input.

        Scene cuts break the chain on purpose: a new environment must not
        inherit the previous scene's closing frame.  Anything that prevents a
        trustworthy chain is a SKIPPED reason, never a blocker.
        """
        if previous_shot is None:
            return {"status": "SKIPPED", "reason": "NO_PREDECESSOR"}
        previous_fields = self._fields(previous_shot)
        if str(fields.get("environment") or "").strip() != str(previous_fields.get("environment") or "").strip():
            return {"status": "SKIPPED", "reason": "SCENE_CUT"}
        previous_video = self._shot_video(str(previous_shot["id"]))
        if previous_video is None:
            return {"status": "SKIPPED", "reason": "PREDECESSOR_VIDEO_MISSING"}
        anchor = self._last_frame_anchor(str(previous_video["media_version_id"]))
        return {
            "status": "CHAINED",
            "frame_anchor_id": anchor["id"],
            "media_version_id": anchor["extracted_media_version_id"],
            "source_media_version_id": str(previous_video["media_version_id"]),
            "anchor_reused": anchor["reused"],
        }

    @staticmethod
    def _seed(run_id: str, shot_id: str) -> int:
        return int(hashlib.sha256(f"{run_id}:{shot_id}".encode()).hexdigest()[:12], 16) % 2_147_483_647

    def _submit_shot(
        self, project_id: str, shot: dict[str, Any], run_id: str, task_id: str, *,
        take_index: int = 0, previous_shot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        shot_id = str(shot["id"])
        keyframe = self._approved_keyframe(shot_id)
        if keyframe is None:
            return {"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": "APPROVED_KEYFRAME_REQUIRED"}
        try:
            profile = self._video_profile(project_id, shot_id)
        except DomainRuleError as error:
            return {"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": error.code}
        role = self._first_frame_role(profile)
        if role is None:
            return {"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": "FIRST_FRAME_SLOT_REQUIRED"}

        fields = self._fields(shot)
        end_frame_chain: dict[str, Any] = {"status": "SLOT_ABSENT"}
        end_bindings: tuple[VariantInput, ...] = ()
        if self._end_frame_role(profile) is not None:
            end_frame_chain = self._end_frame_chain(shot, previous_shot, fields)
            if end_frame_chain["status"] == "CHAINED":
                end_bindings = (VariantInput("END_FRAME", str(end_frame_chain["media_version_id"]), 0, None),)
        with self.database.connect() as connection:
            intent = connection.execute(
                """SELECT * FROM generation_intents WHERE project_id=? AND owner_type='SHOT' AND owner_id=?
                AND purpose='I2V_FORMAL' ORDER BY created_at DESC,id DESC LIMIT 1""",
                (project_id, shot_id),
            ).fetchone()
        if intent is None:
            intent = self.generation.create_intent(project_id, "SHOT", shot_id, "I2V_FORMAL", "Episode production shot video")
        seed = (self._seed(run_id, shot_id) + take_index) % 2_147_483_647
        parameters: dict[str, object] = {
            "PROMPT": compose_shot_prompt(fields, shot_code=str(shot["code"])),
            "SEED": seed,
            "DURATION_SECONDS": round(float(fields.get("target_duration_ms") or shot.get("target_duration_ms") or 4000) / 1000, 3),
        }
        plan = VariantPlan(
            variant_type="BASE", parent_variant_id=None, branch_reason=f"EPISODE_PRODUCTION_RUN_TAKE_{take_index + 1}",
            prompt_revision_id=None, profile_version_id=str(profile["id"]), parameter_set=parameters,
            seed_policy="EXPLICIT", explicit_seed=seed,
            bindings=(VariantInput(role, str(keyframe["media_version_id"]), 0, None), *end_bindings),
        )
        try:
            preflight = self.generation.preflight_variant(str(intent["id"]), plan)
            submitted = self.generation.submit_confirmed_variant(
                str(intent["id"]), plan, str(preflight["plan_hash"]), f"episode-video:{run_id}:{task_id}:{shot_id}:{take_index}",
            )
        except DomainRuleError as error:
            return {"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": error.code}
        return {
            "shot_id": shot_id, "shot_code": str(shot["code"]), "status": "SUBMITTED",
            "variant_id": str(submitted["variant"]["id"]), "job_id": str(submitted["job"]["id"]), "take_index": take_index,
            "end_frame_chain": end_frame_chain,
        }

    def video_generation(
        self,
        episode_id: str,
        run_id: str,
        task_id: str,
        *,
        target_take_count: int = 1,
        target_shot_ids: tuple[str, ...] | None = None,
        force_new_take: bool = False,
    ) -> tuple[dict[str, Any], int]:
        target_take_count = max(1, min(int(target_take_count), 4))
        project_id, episode_shots = self._episode(episode_id)
        requested_ids = tuple(dict.fromkeys(str(item).strip() for item in (target_shot_ids or ()) if str(item).strip()))
        episode_ids = {str(shot["id"]) for shot in episode_shots}
        unknown_ids = sorted(set(requested_ids) - episode_ids)
        if unknown_ids:
            raise DomainRuleError(
                "SHOT_BATCH_SCOPE_INVALID",
                "批量生成包含不属于当前集的镜头",
                {"episode_id": episode_id, "shot_ids": unknown_ids},
            )
        shots = [shot for shot in episode_shots if not requested_ids or str(shot["id"]) in requested_ids]
        stale_working_media_shots = self._stale_working_media_shots(
            episode_id,
            {str(shot["id"]) for shot in shots},
        )
        items: list[dict[str, Any]] = []
        episode_index = {str(shot["id"]): index for index, shot in enumerate(episode_shots)}
        for shot in shots:
            index = episode_index[str(shot["id"])]
            previous_shot = episode_shots[index - 1] if index > 0 else None
            shot_id = str(shot["id"])
            jobs = self._variant_jobs(shot_id)
            promoted = self._promote_completed_outputs([item for item in jobs if str(item["state"]) == "SUCCEEDED"])
            video = self._shot_video(shot_id)
            active_jobs = [item for item in jobs if str(item["state"]) in ACTIVE_JOB_STATES]
            available_count = self._shot_video_count(shot_id)
            stale_working_media = shot_id in stale_working_media_shots
            if force_new_take or stale_working_media:
                submission = self._submit_shot(
                    project_id,
                    shot,
                    run_id,
                    task_id,
                    take_index=len(jobs),
                    previous_shot=previous_shot,
                )
                items.append(
                    {
                        **submission,
                        "candidate_count": available_count,
                        "target_take_count": 1,
                        "forced_new_take": True,
                        "stale_working_media": stale_working_media,
                        "refresh_reason": "WORKING_MEDIA_DEPENDENCY_CHANGED" if stale_working_media else None,
                        "promoted_media_version_ids": promoted,
                    }
                )
                continue
            if video is not None and available_count >= target_take_count:
                items.append({"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "READY_FOR_QC", "media_version_id": str(video["media_version_id"]), "candidate_count": available_count, "target_take_count": target_take_count, "promoted_media_version_ids": promoted})
                continue
            if available_count + len(active_jobs) >= target_take_count:
                active = active_jobs[0]
                items.append({"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "ACTIVE", "variant_id": str(active["variant_id"]), "job_id": str(active["id"]), "dependency_job_ids": [str(item["id"]) for item in active_jobs], "candidate_count": available_count, "target_take_count": target_take_count})
                continue
            failed = next((item for item in jobs if str(item["state"]) in FAILED_JOB_STATES), None)
            dispatched_for_shot: list[dict[str, Any]] = []
            if failed is not None:
                try:
                    retried = self.jobs.retry(str(failed["id"]), actor="episode-worker")
                    dispatched_for_shot.append({"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "RETRIED", "variant_id": str(failed["variant_id"]), "job_id": str(retried["id"]), "retry_of_job_id": str(failed["id"])})
                except DomainRuleError as error:
                    items.append({"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": error.code})
                    continue
            missing = target_take_count - available_count - len(active_jobs) - len(dispatched_for_shot)
            submissions = [
                self._submit_shot(project_id, shot, run_id, task_id, take_index=len(jobs) + offset, previous_shot=previous_shot)
                for offset in range(missing)
            ]
            submission_blockers = [item for item in submissions if item["status"] == "BLOCKED"]
            if submission_blockers:
                items.extend(submission_blockers)
            else:
                dispatched_for_shot.extend(submissions)
            if len(dispatched_for_shot) == 1:
                items.append({**dispatched_for_shot[0], "dependency_job_ids": [str(item["id"]) for item in active_jobs], "candidate_count": available_count, "target_take_count": target_take_count})
            elif dispatched_for_shot:
                items.append({
                    "shot_id": shot_id, "shot_code": str(shot["code"]), "status": "SUBMITTED",
                    "candidate_count": available_count, "target_take_count": target_take_count,
                    "submissions": dispatched_for_shot, "dependency_job_ids": [str(item["id"]) for item in active_jobs],
                })
            else:
                items.append({"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": "VIDEO_CANDIDATE_REQUIRED"})

        blocked = [item for item in items if item["status"] == "BLOCKED"]
        dispatched = [item for item in items if item["status"] in {"SUBMITTED", "RETRIED", "ACTIVE"}]
        ready = [item for item in items if item["status"] == "READY_FOR_QC"]
        status = "NEEDS_HITL" if blocked else "PASS"
        evidence = {
            "status": status, "ok": not blocked, "checked_shots": len(shots), "ready_for_qc": len(ready),
            "dispatched_or_active": len(dispatched), "blocked_shots": blocked,
            "evidence_type": "GENERATION_DISPATCH", "human_approval_created": False,
            "target_take_count": target_take_count,
            "target_shot_ids": list(requested_ids),
            "force_new_take": force_new_take,
        }
        summary = f"逐镜视频调度：可 QC {len(ready)}，已调度/运行 {len(dispatched)}，阻塞 {len(blocked)}"
        return self._report(status, evidence, {"items": items}, summary), 0

    def _qc_reroll(
        self, variant_id: str, run_id: str, task_id: str, project_id: str, episode_id: str, shot_id: str,
        policy_decision: dict[str, Any] | None,
    ) -> dict[str, Any]:
        parent = self.generation.get_variant(variant_id)
        with self.database.connect() as connection:
            count = int(connection.execute(
                """WITH RECURSIVE lineage(id,parent_variant_id,branch_reason) AS (
                  SELECT id,parent_variant_id,branch_reason FROM generation_variants WHERE id=?
                  UNION ALL SELECT gv.id,gv.parent_variant_id,gv.branch_reason
                  FROM generation_variants gv JOIN lineage l ON gv.id=l.parent_variant_id
                ) SELECT COUNT(*) FROM lineage WHERE branch_reason LIKE 'QC_AUTO_RETRY%'""", (variant_id,),
            ).fetchone()[0])
        limit = self._qc_reroll_limit(project_id, episode_id, shot_id)
        if count >= limit:
            return {"status": "LIMIT_REACHED", "code": "QC_AUTO_REROLL_LIMIT_REACHED", "parent_variant_id": variant_id, "max_auto_rerolls": limit}
        seed = int(parent["explicit_seed"]) + 1 if parent.get("explicit_seed") is not None else None
        try:
            result = self.generation.reroll_variant(
                variant_id, reason_code="QC_AUTO_RETRY", reason_note="episode machine QC failed",
                explicit_seed=seed, profile_version_id=None, bindings=None,
                idempotency_key=f"episode-qc-reroll:{run_id}:{task_id}:{shot_id}",
            )
        except DomainRuleError as error:
            return {"status": "BLOCKED", "code": error.code, "parent_variant_id": variant_id}
        if policy_decision is not None:
            with self.database.connect() as connection:
                QcPolicyCommandService(SqliteQcPolicyRepository(connection)).attach_child(
                    link_id=str(policy_decision["id"]), parent_variant_id=variant_id,
                    child_variant_id=str(result["variant"]["id"]), actor="episode-worker",
                )
        return {"status": "REROLL_SUBMITTED", "parent_variant_id": variant_id, "variant_id": str(result["variant"]["id"]), "job_id": str(result["job"]["id"]), "max_auto_rerolls": limit}

    def _auto_select_video(self, video: dict[str, Any], media_version_id: str) -> dict[str, Any]:
        """Fill an empty timeline selection with the QC-passing video.

        Auto-selection only fills the slot: when a human already selected or
        approved a version for this asset the fact stands and the run must not
        override it.  The selection type matches the media stage because
        select_version refuses to record a FORMAL_SELECTION on a PROXY version.
        """
        if str(video.get("approved_version_id") or "") or str(video.get("selected_version_id") or ""):
            return {"status": "ALREADY_CURRENT"}
        selection_type = "FORMAL_SELECTION" if str(video["stage"]) == "FORMAL" else "PROXY_WINNER"
        try:
            self.reviews.select_version(media_version_id, selection_type, actor="episode-run-auto")
        except DomainRuleError as error:
            return {"status": "BLOCKED", "code": error.code, "selection_type": selection_type}
        return {"status": "SELECTED", "selection_type": selection_type}

    def qc(self, episode_id: str, run_id: str, task_id: str, *, auto_select: bool = False) -> tuple[dict[str, Any], int]:
        project_id, shots = self._episode(episode_id)
        items: list[dict[str, Any]] = []
        for shot in shots:
            shot_id = str(shot["id"])
            jobs = self._variant_jobs(shot_id)
            self._promote_completed_outputs([item for item in jobs if str(item["state"]) == "SUCCEEDED"])
            video = self._shot_video(shot_id)
            if video is None:
                pending = next((job for job in jobs if str(job["state"]) in ACTIVE_JOB_STATES), None)
                items.append({"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "PENDING" if pending else "BLOCKED", "code": "VIDEO_GENERATION_PENDING" if pending else "VIDEO_CANDIDATE_REQUIRED", "job_id": str(pending["id"]) if pending else None})
                continue
            media_version_id = str(video["media_version_id"])
            check = self.reviews.machine_check(media_version_id, actor="episode-worker")
            item: dict[str, Any] = {
                "shot_id": shot_id, "shot_code": str(shot["code"]), "media_version_id": media_version_id,
                "machine_check_run_id": str(check["id"]), "status": str(check["status"]),
                "human_approval_created": False,
            }
            if str(check["status"]) == "PASS" and auto_select:
                item["auto_selection"] = self._auto_select_video(video, media_version_id)
            if str(check["status"]) != "PASS" and video.get("variant_id"):
                policy_decision = self._qc_policy_decision(str(video["variant_id"]), str(check["id"]))
                if policy_decision is not None and str(policy_decision["disposition"]) != "AUTO_REROLL_ALLOWED":
                    item["repair"] = {
                        "status": "POLICY_GATE", "code": "QC_AUTO_REROLL_NOT_ALLOWED",
                        "qc_link_id": str(policy_decision["id"]), "disposition": str(policy_decision["disposition"]),
                    }
                else:
                    item["repair"] = self._qc_reroll(
                        str(video["variant_id"]), run_id, task_id, project_id, episode_id, shot_id, policy_decision,
                    )
            items.append(item)

        passed = [item for item in items if item["status"] == "PASS"]
        attention = [item for item in items if item["status"] != "PASS"]
        status = "PASS" if not attention else "NEEDS_HITL"
        auto_selected = [item for item in items if isinstance(item.get("auto_selection"), dict) and item["auto_selection"].get("status") == "SELECTED"]
        evidence = {
            "status": status, "ok": not attention, "checked_shots": len(shots), "passed_shots": len(passed),
            "attention_shots": attention, "evidence_type": "MACHINE_QC_ONLY",
            "human_approval_status": "PENDING", "human_approval_created": False,
            "auto_selected_shots": len(auto_selected),
        }
        summary = f"逐镜机器 QC：PASS {len(passed)}，待处理 {len(attention)}；QC evidence 不等于人工批准"
        if auto_selected:
            summary += f"；自动采用 {len(auto_selected)} 镜"
        return self._report(status, evidence, {"items": items}, summary), 0
