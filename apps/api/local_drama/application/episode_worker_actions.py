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
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

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
from .dialogue_facts import current_shot_dialogue
from .generation import GenerationService
from .jobs import JobService
from .keyframe_references import approved_keyframe_for_shot, approved_keyframes_for_shots
from .media import MediaService, infer_media_kind
from .production_choices import (
    ProductionChoiceService,
    session_keyframe_for_shot,
    session_keyframes_for_shots,
)
from .queries.generation_preferences import GenerationPreferenceQueryService
from .queries.media_eligibility import best_current_video, eligible_candidate_counts
from .queries.qc_policies import QcPolicyQueryService
from .reviews import ReviewService
from .shot_keyframe_generation import ShotKeyframeGenerationBatchService
from .timeline import TimelineService

ACTIVE_JOB_STATES = {"QUEUED", "CLAIMED", "RUNNING", "CANCEL_REQUESTED"}
FAILED_JOB_STATES = {"FAILED", "NEEDS_ATTENTION", "ORPHANED"}


def unpromoted_verified_keyframe_candidate_counts(
    connection: sqlite3.Connection,
    shot_ids: Iterable[str],
) -> dict[str, int]:
    ids = list(dict.fromkeys(str(value) for value in shot_ids if str(value)))
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    rows = connection.execute(
        f"""SELECT gi.owner_id AS shot_id, COUNT(DISTINCT gv.id) AS candidate_count
        FROM generation_intents gi
        JOIN generation_variants gv ON gv.intent_id=gi.id
        JOIN jobs j ON j.subject_type='GENERATION_VARIANT' AND j.subject_id=gv.id
        JOIN job_attempts ja ON ja.job_id=j.id
        JOIN artifacts a ON a.job_attempt_id=ja.id
        WHERE gi.owner_type='SHOT'
          AND gi.owner_id IN ({marks})
          AND gi.purpose='T2I'
          AND gv.is_stale=0
          AND j.state='SUCCEEDED'
          AND a.status='VERIFIED'
        GROUP BY gi.owner_id""",
        tuple(ids),
    ).fetchall()
    return {str(row["shot_id"]): int(row["candidate_count"]) for row in rows}


def reusable_keyframe_candidate_counts(
    connection: sqlite3.Connection,
    shot_ids: Iterable[str],
) -> dict[str, int]:
    ids = list(dict.fromkeys(str(value) for value in shot_ids if str(value)))
    if not ids:
        return {}
    counts: dict[str, int] = defaultdict(int)
    for (shot_id, slot_type), count in eligible_candidate_counts(connection, ids).items():
        if slot_type == "KEYFRAME":
            counts[str(shot_id)] = max(counts[str(shot_id)], count)
    for shot_id, count in unpromoted_verified_keyframe_candidate_counts(connection, ids).items():
        counts[str(shot_id)] = max(counts[str(shot_id)], count)
    return dict(counts)


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
            database,
            settings,
            generation=self.generation,
            preference_resolver_factory=lambda connection: GenerationPreferenceQueryService(SqliteGenerationPreferenceRepository(connection)),
        )

    def keyframe_generation(
        self,
        episode_id: str,
        run_id: str,
        task_id: str,
        *,
        candidate_count: int = 1,
        production_session_id: str | None = None,
        dispatch_job_limit: int | None = None,
    ) -> tuple[dict[str, Any], int]:
        """Dispatch through the shared batch authority; never approve outputs.

        The subsequent check depends on these jobs and remains a human gate.
        A task retry reuses the batch's idempotency key rather than creating a
        second generation queue or changing an existing frozen batch.
        """
        project_id, shots = self._episode(episode_id)
        shot_ids = [str(shot["id"]) for shot in shots]
        with self.database.connect() as connection:
            approved = approved_keyframes_for_shots(
                connection,
                shot_ids,
                project_id=project_id,
            )
            session_selected = (
                session_keyframes_for_shots(connection, production_session_id, shot_ids)
                if production_session_id
                else {}
            )
            candidate_counts = reusable_keyframe_candidate_counts(connection, shot_ids)
        candidate_covered = {
            shot_id
            for shot_id in shot_ids
            if shot_id not in approved
            and shot_id not in session_selected
            and candidate_counts.get(shot_id, 0) >= candidate_count
        }
        covered = set(approved) | set(session_selected) | candidate_covered
        targets = [
            {"shot_id": str(shot["id"]), "expected_revision": int(shot["revision"])}
            for shot in shots
            if str(shot["id"]) not in covered
        ]
        reused = [{"shot_id": shot_id, "status": "APPROVED_REUSED", **fact} for shot_id, fact in approved.items()]
        reused.extend(
            {"shot_id": shot_id, "status": "SESSION_TEMPORARY_REUSED", **fact}
            for shot_id, fact in session_selected.items()
            if shot_id not in approved
        )
        reused.extend(
            {
                "shot_id": shot_id,
                "status": "GENERATED_CANDIDATE_REUSED",
                "candidate_count": candidate_counts[shot_id],
            }
            for shot_id in shot_ids
            if shot_id in candidate_covered
        )
        if not targets:
            return self._report(
                "PASS",
                {"status": "PASS", "code": "KEYFRAME_INPUTS_REUSED"},
                {"items": reused},
                "全部镜头已有可复用关键帧输入",
            ), 0
        plan = self.keyframe_batches.plan(
            episode_id,
            targets=targets,
            frame_strategy="FIRST_ONLY",
            candidate_count=candidate_count,
            production_session_id=production_session_id,
        )
        # The interactive batch screen allows a partial submission. A full
        # episode stage must expose every blocker before spending on the rest.
        if not plan["summary"].get("jobs", 1) and not plan["summary"]["blocked"]:
            return self._report(
                "PASS",
                {"status": "PASS", "code": "KEYFRAME_CANDIDATES_READY"},
                {"items": reused},
                "全部镜头已有所需关键帧候选",
            ), 0
        if not plan["valid"] or plan["issues"] or plan["summary"]["blocked"]:
            return self._report(
                "NEEDS_HITL",
                {"status": "NEEDS_HITL", "code": "SHOT_KEYFRAME_BATCH_BLOCKED", "issues": plan["issues"]},
                {"items": reused, "plan": plan},
                "关键帧生成配置或镜头内容需修正，请到镜头画面检查",
            ), 0
        batch = self.keyframe_batches.submit(
            episode_id,
            targets=targets,
            frame_strategy="FIRST_ONLY",
            candidate_count=candidate_count,
            production_session_id=production_session_id,
            max_jobs=dispatch_job_limit,
            expected_plan_hash=plan["plan_hash"],
            idempotency_key=f"episode-keyframes:{run_id}:{task_id}",
            actor="episode-worker",
        )
        failed = [item for item in batch["items"] if item["status"] in {"FAILED", "CANCELLED"}]
        status = "NEEDS_HITL" if failed else "PASS"
        return self._report(
            status,
            {"status": status, "code": "SHOT_KEYFRAME_GENERATION_FAILED" if failed else "KEYFRAMES_DISPATCHED", "issues": failed},
            {
                "items": [*reused, *batch["items"]],
                "batch_id": batch["id"],
                "dispatch_job_limit": dispatch_job_limit,
            },
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
                    variant_id=variant_id,
                    machine_check_run_id=machine_check_run_id,
                    category="TECHNICAL",
                    actor="episode-worker",
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
                """SELECT s.id,s.code,s.scene_id,s.target_duration_ms,s.status,s.revision,s.current_revision_id,sr.fields_json
                FROM shots s LEFT JOIN shot_revisions sr ON sr.id=s.current_revision_id
                WHERE s.episode_id=? AND s.archived_at IS NULL ORDER BY CAST(s.order_key AS REAL),s.code""",
                (episode_id,),
            ).fetchall()
            shots = []
            for row in rows:
                shot = dict(row)
                shot["dialogue_facts"] = current_shot_dialogue(connection, str(row["id"]))
                shots.append(shot)
        return str(episode["project_id"]), shots

    def video_generation_preflight(
        self,
        episode_id: str,
        *,
        target_shot_ids: tuple[str, ...],
        production_session_id: str | None = None,
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
            unresolved_speakers = list((shot.get("dialogue_facts") or {}).get("unresolved_speaker_line_ids") or [])
            if unresolved_speakers:
                blockers.append(
                    {
                        "code": "DIALOGUE_SPEAKER_CONFIRMATION_REQUIRED",
                        "dialogue_line_ids": unresolved_speakers,
                    }
                )
            missing = missing_shot_fields(fields)
            if missing:
                blockers.append({"code": "SHOT_NOT_PRODUCTION_READY", "missing_fields": missing})
            keyframe = self._keyframe(shot_id, production_session_id=production_session_id)
            if keyframe is None:
                blockers.append({"code": ("SESSION_KEYFRAME_CHOICE_REQUIRED" if production_session_id else "APPROVED_KEYFRAME_REQUIRED")})
            profile: dict[str, Any] | None = None
            try:
                profile = self._video_profile(project_id, shot_id)
            except DomainRuleError as error:
                blockers.append({"code": error.code})
            if profile is not None:
                blockers.extend(self._video_profile_dependency_blockers(profile))
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
                    "keyframe_selection_authority": keyframe.get("selection_authority") if keyframe is not None else None,
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

    def operation_impact(
        self,
        episode_id: str,
        *,
        operation: str,
        target_shot_ids: tuple[str, ...] = (),
        target_take_count: int = 1,
        tts_enabled: bool = True,
        production_mode: str = "BALANCED",
        checkpoint_policy: str = "ON_EXCEPTION",
        production_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Preview creator-visible operations without submitting or retrying work."""
        operation = str(operation or "").strip().upper()
        allowed = {"CONTINUE_UNFINISHED", "RETRY_ORIGINAL", "NEW_TAKE", "RECOMPOSE_ONLY"}
        if operation not in allowed:
            raise DomainRuleError("EPISODE_OPERATION_INVALID", "不支持的本集操作", {"operation": operation})
        project_id, shots = self._episode(episode_id)
        with self.database.connect() as connection:
            episode_row = connection.execute("SELECT revision FROM episodes WHERE id=?", (episode_id,)).fetchone()
        episode_revision = int(episode_row["revision"]) if episode_row is not None else 1
        requested = tuple(dict.fromkeys(str(item).strip() for item in target_shot_ids if str(item).strip()))
        by_id = {str(shot["id"]): shot for shot in shots}
        unknown = sorted(set(requested) - set(by_id))
        if unknown:
            raise DomainRuleError("SHOT_BATCH_SCOPE_INVALID", "操作包含不属于当前集的镜头", {"shot_ids": unknown})
        selected = [shot for shot in shots if not requested or str(shot["id"]) in requested]
        expected_profile_version_ids: dict[str, str] = {}
        expected_input_fingerprints: dict[str, str] = {}
        preflight_fingerprint: str | None = None
        sets: dict[str, list[dict[str, Any]]] = {
            "reused": [],
            "waiting_in_flight": [],
            "retry_original": [],
            "needs_generation": [],
            "blocked_by_dependency": [],
            "requires_manual_confirmation": [],
            "compose_only": [],
        }
        if operation == "RECOMPOSE_ONLY":
            if production_session_id:
                assembly = self.timeline._timeline_assembly_plan(
                    episode_id,
                    require_stale_revision=False,
                    audio_strategy="EXTERNAL_TTS" if tts_enabled else "SILENT",
                    production_session_id=production_session_id,
                )
                if assembly["status"] != "READY":
                    sets["blocked_by_dependency"].extend(assembly["blockers"])
                else:
                    sets["compose_only"].append(
                        {
                            "timeline_revision_id": (assembly.get("source_timeline") or {}).get("id"),
                            "reason": "REASSEMBLE_SESSION_CHOICES",
                            "assembly_plan_hash": str(assembly["plan_hash"]),
                            "production_session_id": production_session_id,
                        }
                    )
            else:
                with self.database.connect() as connection:
                    timeline = connection.execute(
                        "SELECT id,revision_no,status FROM timeline_revisions WHERE episode_id=? ORDER BY revision_no DESC,id DESC LIMIT 1",
                        (episode_id,),
                    ).fetchone()
                if timeline is None:
                    sets["blocked_by_dependency"].append({"owner_type": "EPISODE", "owner_id": episode_id, "reason": "TIMELINE_REQUIRED"})
                else:
                    try:
                        compose_preflight = self.timeline.preflight_episode_render(str(timeline["id"]))
                    except DomainRuleError as error:
                        sets["blocked_by_dependency"].append(
                            {
                                "owner_type": "TIMELINE_REVISION",
                                "owner_id": str(timeline["id"]),
                                "reason": error.code,
                            }
                        )
                    else:
                        sets["compose_only"].append(
                            {
                                "timeline_revision_id": str(timeline["id"]),
                                "timeline_revision_no": int(timeline["revision_no"]),
                                "reason": "REUSE_CURRENT_TIMELINE_INPUTS",
                                "compose_fingerprint": str(compose_preflight["compose_fingerprint"]),
                            }
                        )
        elif operation == "RETRY_ORIGINAL":
            for shot in selected:
                shot_id = str(shot["id"])
                base = {
                    "shot_id": shot_id,
                    "shot_code": str(shot["code"]),
                    "shot_revision": int(shot["revision"]),
                }
                jobs = self._variant_jobs(shot_id)
                active = [job for job in jobs if str(job["state"]) in ACTIVE_JOB_STATES]
                failed = next((job for job in jobs if str(job["state"]) in FAILED_JOB_STATES), None)
                if active:
                    sets["waiting_in_flight"].append({**base, "reason": "EXISTING_JOB_IN_FLIGHT", "job_ids": [str(job["id"]) for job in active]})
                elif failed is None:
                    sets["reused"].append({**base, "reason": "NO_FAILED_ORIGINAL_INPUT_JOB"})
                elif str(failed.get("last_error_code") or "") in {
                    "COMFY_PROVIDER_ACCEPTANCE_UNKNOWN",
                    "PROVIDER_ACCEPTANCE_UNKNOWN",
                }:
                    sets["blocked_by_dependency"].append({**base, "reason": "PROVIDER_ACCEPTANCE_RECONCILIATION_REQUIRED", "job_id": str(failed["id"])})
                else:
                    sets["retry_original"].append(
                        {
                            **base,
                            "reason": "FAILED_JOB_SAME_FROZEN_INPUTS",
                            "job_id": str(failed["id"]),
                            "variant_id": str(failed["variant_id"]),
                            "seed": failed["explicit_seed"],
                        }
                    )
        else:
            preflight = self.video_generation_preflight(episode_id, target_shot_ids=tuple(str(shot["id"]) for shot in selected))
            preflight_fingerprint = str(preflight["input_fingerprint"])
            preflight_by_id = {str(item["shot_id"]): item for item in preflight["items"]}
            expected_profile_version_ids = {
                str(item["shot_id"]): str(item["profile_version_id"])
                for item in preflight["items"]
                if item.get("status") == "READY" and item.get("profile_version_id")
            }
            expected_input_fingerprints = {
                str(item["shot_id"]): hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                for item in preflight["items"]
            }
            stale = self._stale_working_media_shots(episode_id, set(preflight_by_id))
            for shot in selected:
                shot_id = str(shot["id"])
                item = preflight_by_id[shot_id]
                base = {
                    "shot_id": shot_id,
                    "shot_code": str(shot["code"]),
                    "shot_revision": int(shot["revision"]),
                }
                if item["status"] != "READY":
                    sets["blocked_by_dependency"].append({**base, "reason": "HARD_DEPENDENCY_BLOCKED", "blockers": item["blockers"]})
                    continue
                jobs = self._variant_jobs(shot_id)
                available = self._shot_video_count(shot_id)
                active = [job for job in jobs if str(job["state"]) in ACTIVE_JOB_STATES]
                failed = next((job for job in jobs if str(job["state"]) in FAILED_JOB_STATES), None)
                if operation == "NEW_TAKE":
                    sets["needs_generation"].append({**base, "reason": "EXPLICIT_NEW_CANDIDATE", "new_candidate_count": 1})
                elif active:
                    sets["waiting_in_flight"].append({**base, "reason": "EXISTING_JOB_IN_FLIGHT", "job_ids": [str(job["id"]) for job in active]})
                elif failed is not None:
                    sets["retry_original"].append(
                        {
                            **base,
                            "reason": "FAILED_JOB_SAME_FROZEN_INPUTS",
                            "job_id": str(failed["id"]),
                            "variant_id": str(failed["variant_id"]),
                            "seed": failed["explicit_seed"],
                        }
                    )
                elif operation == "RETRY_ORIGINAL":
                    sets["reused"].append({**base, "reason": "NO_FAILED_ORIGINAL_INPUT_JOB"})
                elif shot_id in stale or available < target_take_count:
                    sets["needs_generation"].append(
                        {
                            **base,
                            "reason": "WORKING_MEDIA_DEPENDENCY_CHANGED" if shot_id in stale else "CANDIDATE_TARGET_NOT_MET",
                            "new_candidate_count": 1 if shot_id in stale else max(0, target_take_count - available),
                        }
                    )
                else:
                    sets["reused"].append({**base, "reason": "VALID_CURRENT_CANDIDATE", "candidate_count": available})
        snapshot = {
            "schema_version": "episode-operation-impact/v1",
            "episode_id": episode_id,
            "project_id": project_id,
            "operation": operation,
            "production_session_id": production_session_id,
            "episode_revision": episode_revision,
            "tts_enabled": bool(tts_enabled),
            "production_mode": str(production_mode),
            "checkpoint_policy": str(checkpoint_policy),
            "target_shot_ids": [str(shot["id"]) for shot in selected],
            "target_take_count": target_take_count,
            "preflight_fingerprint": preflight_fingerprint,
            "expected_profile_version_ids": expected_profile_version_ids,
            "expected_input_fingerprints": expected_input_fingerprints,
            "sets": sets,
            "gpu_video_job_count": 0 if operation == "RECOMPOSE_ONLY" else sum(int(item.get("new_candidate_count") or 0) for item in sets["needs_generation"]),
            "mutated": False,
            "runtime_contacted": False,
            "network_contacted": False,
        }
        snapshot["plan_hash"] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        return snapshot

    @staticmethod
    def _fields(shot: dict[str, Any]) -> dict[str, Any]:
        try:
            value = json.loads(str(shot.get("fields_json") or "{}"))
        except (TypeError, ValueError):
            return {}
        fields = value if isinstance(value, dict) else {}
        dialogue_facts = shot.get("dialogue_facts")
        if isinstance(dialogue_facts, dict):
            fields = {**fields, "dialogue": list(dialogue_facts.get("lines") or [])}
        return fields

    def _shot_video(self, shot_id: str) -> dict[str, Any] | None:
        """Return the best current verified video, preserving human/QC facts."""
        with self.database.connect() as connection:
            row = best_current_video(connection, shot_id)
        if row is not None:
            row["machine_check_status"] = row.pop("current_qc_status", None)
        return row

    def _shot_video_count(self, shot_id: str) -> int:
        """Count verified candidates, not merely jobs that reported success."""
        with self.database.connect() as connection:
            return eligible_candidate_counts(connection, [shot_id]).get((shot_id, "VIDEO"), 0)

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
            rows = (
                connection.execute(
                    """SELECT a.id FROM artifacts a JOIN job_attempts ja ON ja.id=a.job_attempt_id
                WHERE ja.job_id IN ({}) AND ja.state='SUCCEEDED' AND a.status='VERIFIED'
                ORDER BY a.created_at,a.id""".format(",".join("?" for _ in jobs)),
                    tuple(str(item["id"]) for item in jobs),
                ).fetchall()
                if jobs
                else []
            )
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
        """Compatibility seam for the formal keyframe authority."""

        with self.database.connect() as connection:
            approved = approved_keyframe_for_shot(connection, shot_id)
        if approved is not None:
            approved["selection_authority"] = "HUMAN_APPROVED"
            approved["human_approved"] = True
        return approved

    def _keyframe(
        self,
        shot_id: str,
        *,
        production_session_id: str | None = None,
    ) -> dict[str, Any] | None:
        approved = self._approved_keyframe(shot_id)
        if approved is not None:
            return approved
        with self.database.connect() as connection:
            if production_session_id:
                return session_keyframe_for_shot(connection, production_session_id, shot_id)
            return None

    def _video_profile(self, project_id: str, shot_id: str) -> dict[str, Any]:
        """Resolve the exact shot-level preference used by the generation UI.

        The legacy project binding is a readiness/configuration fact, but it is
        not the authoritative per-shot model choice.  Production must use the
        same shot -> episode -> project preference resolver as Director and the
        Models workspace; otherwise the UI can display v19 while automation
        silently submits an older project binding such as v13.
        """
        with self.database.connect() as connection:
            resolution = GenerationPreferenceQueryService(SqliteGenerationPreferenceRepository(connection)).resolve(
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

    def _video_profile_dependency_blockers(self, profile: dict[str, Any]) -> list[dict[str, Any]]:
        """Reuse immutable publication attestations; never hash model weights here."""

        if "workflow_version_id" not in profile:
            return []  # compact test/adapter compatibility
        workflow_version_id = str(profile.get("workflow_version_id") or "").strip()
        if not workflow_version_id:
            return [{"code": "VIDEO_WORKFLOW_REQUIRED"}]
        with self.database.connect() as connection:
            workflow = connection.execute(
                "SELECT status,content_hash FROM workflow_versions WHERE id=?",
                (workflow_version_id,),
            ).fetchone()
            attestation = connection.execute(
                """SELECT status,workflow_content_hash,evidence_json FROM workflow_validation_attestations
                WHERE workflow_version_id=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (workflow_version_id,),
            ).fetchone()
        if workflow is None or str(workflow["status"]) != "PUBLISHED":
            return [{"code": "VIDEO_WORKFLOW_NOT_PUBLISHED", "workflow_version_id": workflow_version_id}]
        if attestation is None or str(attestation["status"]) != "PASS" or str(attestation["workflow_content_hash"]) != str(workflow["content_hash"]):
            return [{"code": "VIDEO_WORKFLOW_VALIDATION_REQUIRED", "workflow_version_id": workflow_version_id}]
        try:
            evidence = json.loads(str(attestation["evidence_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            evidence = {}
        missing_nodes = list(evidence.get("missing_nodes") or []) if isinstance(evidence, dict) else []
        schema_errors = list(evidence.get("schema_errors") or []) if isinstance(evidence, dict) else []
        blockers: list[dict[str, Any]] = []
        if missing_nodes:
            blockers.append(
                {
                    "code": "VIDEO_WORKFLOW_NODES_MISSING",
                    "workflow_version_id": workflow_version_id,
                    "missing_nodes": missing_nodes,
                }
            )
        if schema_errors:
            blockers.append(
                {
                    "code": "VIDEO_WORKFLOW_INPUT_SCHEMA_INVALID",
                    "workflow_version_id": workflow_version_id,
                    "schema_errors": schema_errors,
                }
            )
        runtime_layout = evidence.get("runtime_layout") if isinstance(evidence, dict) else None
        if isinstance(runtime_layout, dict) and str(runtime_layout.get("status") or "") != "PASS":
            blockers.append(
                {
                    "code": "VIDEO_MODEL_COMPONENTS_MISSING",
                    "workflow_version_id": workflow_version_id,
                    "missing_model_files": list(runtime_layout.get("missing_model_files") or []),
                }
            )
        return blockers

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
            source_media_version_id,
            position_mode="LAST_FRAME",
            role_hint="LAST_FRAME",
            actor="episode-run-auto",
        )
        return {"id": str(anchor["id"]), "extracted_media_version_id": str(anchor["extracted_media_version_id"]), "reused": False}

    def _end_frame_chain(
        self,
        shot: dict[str, Any],
        previous_shot: dict[str, Any] | None,
        fields: dict[str, Any],
        current_keyframe: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Assess an explicit predecessor-tail -> current-start dependency.

        The legacy name remains for compatibility, but the result is never an
        END_FRAME binding.  A predecessor tail can only constrain the current
        FIRST_FRAME, and a HARD bridge must already be frozen and agree with
        the approved current keyframe before submission.
        """
        del fields
        if previous_shot is None:
            return {"status": "SKIPPED", "reason": "NO_PREDECESSOR"}
        previous_scene = str(previous_shot.get("scene_id") or "").strip()
        current_scene = str(shot.get("scene_id") or "").strip()
        if not previous_scene or not current_scene:
            return {"status": "SKIPPED", "reason": "SCENE_ID_UNKNOWN"}
        if previous_scene != current_scene:
            return {"status": "SKIPPED", "reason": "SCENE_CUT"}
        with self.database.connect() as connection:
            transition = connection.execute(
                """SELECT * FROM shot_transition_constraints
                WHERE from_shot_id=? AND to_shot_id=? AND is_stale=0
                ORDER BY created_at DESC,id DESC LIMIT 1""",
                (previous_shot["id"], shot["id"]),
            ).fetchone()
            if transition is None:
                return {"status": "SKIPPED", "reason": "NO_EXPLICIT_TRANSITION"}
            if str(transition["constraint_type"]) not in {
                "START_FROM_PREVIOUS_LAST",
                "LAST_TO_FIRST",
                "SHARED_BOUNDARY_FRAME",
            }:
                return {"status": "SKIPPED", "reason": "CUT_TRANSITION"}
            if str(transition["enforcement"]).upper() not in {"HARD", "LOCKED"}:
                return {"status": "SKIPPED", "reason": "ADVISORY_CONTINUITY", "transition_id": str(transition["id"])}
        previous_video = self._shot_video(str(previous_shot["id"]))
        if previous_video is None:
            return {"status": "WAITING", "reason": "PREDECESSOR_VIDEO_MISSING", "transition_id": str(transition["id"])}
        if not transition["from_anchor_id"] or not transition["to_anchor_id"]:
            return {"status": "WAITING", "reason": "HARD_BRIDGE_NOT_FROZEN", "transition_id": str(transition["id"])}
        with self.database.connect() as connection:
            source = connection.execute("SELECT * FROM frame_anchors WHERE id=?", (transition["from_anchor_id"],)).fetchone()
            current = connection.execute("SELECT * FROM frame_anchors WHERE id=?", (transition["to_anchor_id"],)).fetchone()
        if source is None or current is None or int(source["is_stale"]) or int(current["is_stale"]):
            return {"status": "WAITING", "reason": "HARD_BRIDGE_STALE", "transition_id": str(transition["id"])}
        if str(source["source_media_version_id"]) != str(previous_video["media_version_id"]):
            return {"status": "WAITING", "reason": "PREDECESSOR_WORKING_VERSION_CHANGED", "transition_id": str(transition["id"])}
        current_media_id = str((current_keyframe or {}).get("media_version_id") or "")
        if str(current["extracted_media_version_id"]) != current_media_id:
            return {
                "status": "CONFLICT",
                "reason": "LOCKED_CURRENT_START_MISMATCH",
                "transition_id": str(transition["id"]),
                "approved_keyframe_media_version_id": current_media_id,
                "bridge_media_version_id": str(current["extracted_media_version_id"]),
            }
        return {
            "status": "SATISFIED",
            "transition_id": str(transition["id"]),
            "source_anchor_id": str(source["id"]),
            "current_anchor_id": str(current["id"]),
            "media_version_id": current_media_id,
            "source_media_version_id": str(previous_video["media_version_id"]),
            "source_sha256": str(source["source_sha256"] or ""),
            "source_frame_index": source["source_frame_index"],
            "source_time_us": source["source_time_us"],
        }

    @staticmethod
    def _seed(run_id: str, shot_id: str) -> int:
        return int(hashlib.sha256(f"{run_id}:{shot_id}".encode()).hexdigest()[:12], 16) % 2_147_483_647

    def _submit_shot(
        self,
        project_id: str,
        shot: dict[str, Any],
        run_id: str,
        task_id: str,
        *,
        take_index: int = 0,
        previous_shot: dict[str, Any] | None = None,
        expected_profile_version_id: str | None = None,
        production_session_id: str | None = None,
    ) -> dict[str, Any]:
        shot_id = str(shot["id"])
        unresolved_speakers = list((shot.get("dialogue_facts") or {}).get("unresolved_speaker_line_ids") or [])
        if unresolved_speakers:
            return {
                "shot_id": shot_id,
                "shot_code": str(shot["code"]),
                "status": "BLOCKED",
                "code": "DIALOGUE_SPEAKER_CONFIRMATION_REQUIRED",
                "dialogue_line_ids": unresolved_speakers,
            }
        keyframe = self._keyframe(shot_id, production_session_id=production_session_id)
        if keyframe is None:
            return {
                "shot_id": shot_id,
                "shot_code": str(shot["code"]),
                "status": "BLOCKED",
                "code": "SESSION_KEYFRAME_CHOICE_REQUIRED" if production_session_id else "APPROVED_KEYFRAME_REQUIRED",
            }
        try:
            profile = self._video_profile(project_id, shot_id)
        except DomainRuleError as error:
            return {"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": error.code}
        if expected_profile_version_id and str(profile["id"]) != expected_profile_version_id:
            return {
                "shot_id": shot_id,
                "shot_code": str(shot["code"]),
                "status": "BLOCKED",
                "code": "VIDEO_PROFILE_SNAPSHOT_STALE",
                "expected_profile_version_id": expected_profile_version_id,
                "actual_profile_version_id": str(profile["id"]),
            }
        role = self._first_frame_role(profile)
        if role is None:
            return {"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": "FIRST_FRAME_SLOT_REQUIRED"}

        fields = self._fields(shot)
        frame_bridge = self._end_frame_chain(shot, previous_shot, fields, keyframe)
        if frame_bridge["status"] in {"WAITING", "CONFLICT"}:
            return {
                "shot_id": shot_id,
                "shot_code": str(shot["code"]),
                "status": "BLOCKED",
                "code": "HARD_FRAME_BRIDGE_WAITING" if frame_bridge["status"] == "WAITING" else "HARD_FRAME_BRIDGE_CONFLICT",
                "frame_bridge": frame_bridge,
            }
        with self.database.connect() as connection:
            intent = connection.execute(
                """SELECT * FROM generation_intents WHERE project_id=? AND owner_type='SHOT' AND owner_id=?
                AND purpose='I2V_FORMAL' ORDER BY created_at DESC,id DESC LIMIT 1""",
                (project_id, shot_id),
            ).fetchone()
        if intent is None:
            intent = self.generation.create_intent(project_id, "SHOT", shot_id, "I2V_FORMAL", "Episode production shot video")
        seed = (self._seed(run_id, shot_id) + take_index) % 2_147_483_647
        base_prompt = compose_shot_prompt(fields, shot_code=str(shot["code"]))
        parameters: dict[str, object] = {
            "PROMPT": base_prompt,
            "SEED": seed,
            "DURATION_SECONDS": round(float(fields.get("target_duration_ms") or shot.get("target_duration_ms") or 4000) / 1000, 3),
        }
        if isinstance(fields.get("camera_plan"), dict):
            parameters["camera_plan"] = fields["camera_plan"]
        # The episode runner may execute after the project switches to a new
        # published video ProfileVersion.  Preserve the authored movement,
        # direction and prompt text, but re-adjudicate capability-derived
        # fields before freezing the Variant.  This keeps the automatic path
        # consistent with the Shot Studio preflight path.
        self.generation._retarget_camera_plan(parameters, str(profile["id"]))
        plan = VariantPlan(
            variant_type="BASE",
            parent_variant_id=None,
            branch_reason=f"EPISODE_PRODUCTION_RUN_TAKE_{take_index + 1}",
            prompt_revision_id=None,
            profile_version_id=str(profile["id"]),
            parameter_set=parameters,
            seed_policy="EXPLICIT",
            explicit_seed=seed,
            bindings=(VariantInput(role, str(keyframe["media_version_id"]), 0, None),),
            # A production-session keyframe is deliberately temporary rather
            # than globally approved.  Freeze the owning session into the
            # Variant plan so GenerationService can validate that exact
            # session-scoped choice without weakening generic I2V rules.
            production_session_id=production_session_id,
            # Use the same canonical prompt compiler as the Shot Studio entry;
            # this freezes the default negative constraints even when no page
            # override exists.
            prompt_bundle={"base_prompt": base_prompt},
        )
        try:
            preflight = self.generation.preflight_variant(str(intent["id"]), plan)
            submitted = self.generation.submit_confirmed_variant(
                str(intent["id"]),
                plan,
                str(preflight["plan_hash"]),
                f"episode-video:{run_id}:{task_id}:{shot_id}:{take_index}",
            )
        except DomainRuleError as error:
            return {"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": error.code}
        return {
            "shot_id": shot_id,
            "shot_code": str(shot["code"]),
            "status": "SUBMITTED",
            "variant_id": str(submitted["variant"]["id"]),
            "job_id": str(submitted["job"]["id"]),
            "take_index": take_index,
            "frame_bridge": frame_bridge,
            "keyframe_selection_authority": keyframe.get("selection_authority", "HUMAN_APPROVED"),
            "production_choice_id": keyframe.get("production_choice_id"),
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
        retry_original_only: bool = False,
        expected_profile_version_ids: dict[str, str] | None = None,
        expected_input_fingerprints: dict[str, str] | None = None,
        production_session_id: str | None = None,
        dispatch_job_limit: int | None = None,
    ) -> tuple[dict[str, Any], int]:
        if isinstance(target_take_count, bool):
            raise DomainRuleError("VIDEO_TARGET_TAKE_COUNT_INVALID", "单镜视频候选目标必须是 1—4 的整数")
        try:
            target_take_count = int(target_take_count)
        except (TypeError, ValueError) as error:
            raise DomainRuleError("VIDEO_TARGET_TAKE_COUNT_INVALID", "单镜视频候选目标必须是 1—4 的整数") from error
        if target_take_count not in {1, 2, 3, 4}:
            raise DomainRuleError(
                "VIDEO_TARGET_TAKE_COUNT_INVALID",
                "单镜视频候选目标必须是 1—4 的整数",
                {"target_take_count": target_take_count, "max_target_take_count": 4},
            )
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
        frozen_inputs = {
            str(shot_id): str(fingerprint)
            for shot_id, fingerprint in (expected_input_fingerprints or {}).items()
            if str(shot_id).strip() and str(fingerprint).strip()
        }
        if dispatch_job_limit is not None and (isinstance(dispatch_job_limit, bool) or int(dispatch_job_limit) < 1):
            raise DomainRuleError("VIDEO_DISPATCH_LIMIT_INVALID", "视频单轮任务上限必须是正整数")
        if frozen_inputs:
            session_preflight_kwargs = {"production_session_id": production_session_id} if production_session_id else {}
            current_preflight = self.video_generation_preflight(
                episode_id,
                target_shot_ids=tuple(str(shot["id"]) for shot in shots),
                **session_preflight_kwargs,
            )
            current_fingerprints = {
                str(item["shot_id"]): hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
                for item in current_preflight["items"]
            }
            changed = sorted(shot_id for shot_id, fingerprint in frozen_inputs.items() if current_fingerprints.get(shot_id) != fingerprint)
            if changed:
                raise DomainRuleError(
                    "EPISODE_OPERATION_PLAN_STALE",
                    "镜头或生成依赖在预览后发生变化，请重新预览",
                    {"shot_ids": changed},
                )
        stale_working_media_shots = self._stale_working_media_shots(
            episode_id,
            {str(shot["id"]) for shot in shots},
        )
        items: list[dict[str, Any]] = []
        episode_index = {str(shot["id"]): index for index, shot in enumerate(episode_shots)}
        frozen_profiles = {
            str(shot_id): str(profile_id)
            for shot_id, profile_id in (expected_profile_version_ids or {}).items()
            if str(shot_id).strip() and str(profile_id).strip()
        }
        session_submit_kwargs = {"production_session_id": production_session_id} if production_session_id else {}
        dispatch_jobs_remaining = int(dispatch_job_limit) if dispatch_job_limit is not None else None
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
            frozen_profile_kwargs = {"expected_profile_version_id": frozen_profiles[shot_id]} if shot_id in frozen_profiles else {}
            if retry_original_only:
                failed = next((item for item in jobs if str(item["state"]) in FAILED_JOB_STATES), None)
                if failed is None:
                    items.append({"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": "ORIGINAL_FAILED_JOB_REQUIRED"})
                    continue
                if dispatch_jobs_remaining is not None and dispatch_jobs_remaining <= 0:
                    items.append(
                        {
                            "shot_id": shot_id,
                            "shot_code": str(shot["code"]),
                            "status": "DEFERRED_CAPACITY",
                        }
                    )
                    continue
                try:
                    retried = self.jobs.retry(str(failed["id"]), actor="episode-worker")
                    if dispatch_jobs_remaining is not None:
                        dispatch_jobs_remaining -= 1
                    items.append(
                        {
                            "shot_id": shot_id,
                            "shot_code": str(shot["code"]),
                            "status": "RETRIED",
                            "variant_id": str(failed["variant_id"]),
                            "job_id": str(retried["id"]),
                            "retry_of_job_id": str(failed["id"]),
                            "frozen_input_reused": True,
                        }
                    )
                except DomainRuleError as error:
                    items.append({"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": error.code})
                continue
            if force_new_take or stale_working_media:
                if dispatch_jobs_remaining is not None and dispatch_jobs_remaining <= 0:
                    items.append(
                        {
                            "shot_id": shot_id,
                            "shot_code": str(shot["code"]),
                            "status": "DEFERRED_CAPACITY",
                        }
                    )
                    continue
                submission = self._submit_shot(
                    project_id,
                    shot,
                    run_id,
                    task_id,
                    # The task identity already distinguishes a creator's next
                    # explicit take.  A retry of this same task must keep slot
                    # zero even after its first Job becomes visible.
                    take_index=0,
                    previous_shot=previous_shot,
                    **session_submit_kwargs,
                    **frozen_profile_kwargs,
                )
                if submission.get("job_id") and dispatch_jobs_remaining is not None:
                    dispatch_jobs_remaining -= 1
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
                items.append(
                    {
                        "shot_id": shot_id,
                        "shot_code": str(shot["code"]),
                        "status": "READY_FOR_QC",
                        "media_version_id": str(video["media_version_id"]),
                        "candidate_count": available_count,
                        "target_take_count": target_take_count,
                        "promoted_media_version_ids": promoted,
                    }
                )
                continue
            if available_count + len(active_jobs) >= target_take_count:
                active = active_jobs[0]
                items.append(
                    {
                        "shot_id": shot_id,
                        "shot_code": str(shot["code"]),
                        "status": "ACTIVE",
                        "variant_id": str(active["variant_id"]),
                        "job_id": str(active["id"]),
                        "dependency_job_ids": [str(item["id"]) for item in active_jobs],
                        "candidate_count": available_count,
                        "target_take_count": target_take_count,
                    }
                )
                continue
            failed = next((item for item in jobs if str(item["state"]) in FAILED_JOB_STATES), None)
            dispatched_for_shot: list[dict[str, Any]] = []
            if dispatch_jobs_remaining is not None and dispatch_jobs_remaining <= 0:
                items.append(
                    {
                        "shot_id": shot_id,
                        "shot_code": str(shot["code"]),
                        "status": "DEFERRED_CAPACITY",
                    }
                )
                continue
            if failed is not None:
                try:
                    retried = self.jobs.retry(str(failed["id"]), actor="episode-worker")
                    if dispatch_jobs_remaining is not None:
                        dispatch_jobs_remaining -= 1
                    dispatched_for_shot.append(
                        {
                            "shot_id": shot_id,
                            "shot_code": str(shot["code"]),
                            "status": "RETRIED",
                            "variant_id": str(failed["variant_id"]),
                            "job_id": str(retried["id"]),
                            "retry_of_job_id": str(failed["id"]),
                        }
                    )
                except DomainRuleError as error:
                    items.append({"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": error.code})
                    continue
            missing = target_take_count - available_count - len(active_jobs) - len(dispatched_for_shot)
            if dispatch_jobs_remaining is not None:
                missing = min(missing, dispatch_jobs_remaining)
            submissions = [
                self._submit_shot(
                    project_id,
                    shot,
                    run_id,
                    task_id,
                    take_index=len(jobs) + offset,
                    previous_shot=previous_shot,
                    **session_submit_kwargs,
                    **frozen_profile_kwargs,
                )
                for offset in range(missing)
            ]
            if dispatch_jobs_remaining is not None:
                dispatch_jobs_remaining -= sum(1 for submission in submissions if submission.get("job_id"))
            submission_blockers = [item for item in submissions if item["status"] == "BLOCKED"]
            if submission_blockers:
                items.extend(submission_blockers)
            else:
                dispatched_for_shot.extend(submissions)
            if len(dispatched_for_shot) == 1:
                items.append(
                    {
                        **dispatched_for_shot[0],
                        "dependency_job_ids": [str(item["id"]) for item in active_jobs],
                        "candidate_count": available_count,
                        "target_take_count": target_take_count,
                    }
                )
            elif dispatched_for_shot:
                items.append(
                    {
                        "shot_id": shot_id,
                        "shot_code": str(shot["code"]),
                        "status": "SUBMITTED",
                        "candidate_count": available_count,
                        "target_take_count": target_take_count,
                        "submissions": dispatched_for_shot,
                        "dependency_job_ids": [str(item["id"]) for item in active_jobs],
                    }
                )
            else:
                items.append({"shot_id": shot_id, "shot_code": str(shot["code"]), "status": "BLOCKED", "code": "VIDEO_CANDIDATE_REQUIRED"})

        blocked = [item for item in items if item["status"] == "BLOCKED"]
        dispatched = [item for item in items if item["status"] in {"SUBMITTED", "RETRIED", "ACTIVE"}]
        ready = [item for item in items if item["status"] == "READY_FOR_QC"]
        valid_candidate_count = sum(int(item.get("candidate_count") or 0) for item in items)
        active_candidate_count = sum(
            len(item.get("submissions") or [])
            + len(item.get("dependency_job_ids") or [])
            + (1 if item["status"] in {"SUBMITTED", "RETRIED"} and not item.get("submissions") else 0)
            for item in items
        )
        technical_retry_count = sum(1 for item in items if item["status"] == "RETRIED") + sum(
            1 for item in items for candidate in item.get("submissions") or [] if candidate.get("status") == "RETRIED"
        )
        status = "NEEDS_HITL" if blocked else "PASS"
        evidence = {
            "status": status,
            "ok": not blocked,
            "checked_shots": len(shots),
            "ready_for_qc": len(ready),
            "dispatched_or_active": len(dispatched),
            "blocked_shots": blocked,
            "evidence_type": "GENERATION_DISPATCH",
            "human_approval_created": False,
            "target_take_count": target_take_count,
            "target_shot_ids": list(requested_ids),
            "expected_profile_version_ids": frozen_profiles,
            "expected_input_fingerprints": frozen_inputs,
            "force_new_take": force_new_take,
            "retry_original_only": retry_original_only,
            "target_candidate_count": len(shots) * target_take_count,
            "valid_candidate_count": valid_candidate_count,
            "active_candidate_count": active_candidate_count,
            "technical_retry_count": technical_retry_count,
            "dispatch_job_limit": dispatch_job_limit,
            "deferred_shots": sum(item["status"] == "DEFERRED_CAPACITY" for item in items),
        }
        summary = f"逐镜视频调度：可 QC {len(ready)}，已调度/运行 {len(dispatched)}，阻塞 {len(blocked)}"
        return self._report(status, evidence, {"items": items}, summary), 0

    def _qc_reroll(
        self,
        variant_id: str,
        run_id: str,
        task_id: str,
        project_id: str,
        episode_id: str,
        shot_id: str,
        policy_decision: dict[str, Any] | None,
    ) -> dict[str, Any]:
        parent = self.generation.get_variant(variant_id)
        with self.database.connect() as connection:
            count = int(
                connection.execute(
                    """WITH RECURSIVE lineage(id,parent_variant_id,branch_reason) AS (
                  SELECT id,parent_variant_id,branch_reason FROM generation_variants WHERE id=?
                  UNION ALL SELECT gv.id,gv.parent_variant_id,gv.branch_reason
                  FROM generation_variants gv JOIN lineage l ON gv.id=l.parent_variant_id
                ) SELECT COUNT(*) FROM lineage WHERE branch_reason LIKE 'QC_AUTO_RETRY%'""",
                    (variant_id,),
                ).fetchone()[0]
            )
        limit = self._qc_reroll_limit(project_id, episode_id, shot_id)
        if count >= limit:
            return {"status": "LIMIT_REACHED", "code": "QC_AUTO_REROLL_LIMIT_REACHED", "parent_variant_id": variant_id, "max_auto_rerolls": limit}
        seed = int(parent["explicit_seed"]) + 1 if parent.get("explicit_seed") is not None else None
        try:
            result = self.generation.reroll_variant(
                variant_id,
                reason_code="QC_AUTO_RETRY",
                reason_note="episode machine QC failed",
                explicit_seed=seed,
                profile_version_id=None,
                bindings=None,
                idempotency_key=f"episode-qc-reroll:{run_id}:{task_id}:{shot_id}",
            )
        except DomainRuleError as error:
            return {"status": "BLOCKED", "code": error.code, "parent_variant_id": variant_id}
        if policy_decision is not None:
            with self.database.connect() as connection:
                QcPolicyCommandService(SqliteQcPolicyRepository(connection)).attach_child(
                    link_id=str(policy_decision["id"]),
                    parent_variant_id=variant_id,
                    child_variant_id=str(result["variant"]["id"]),
                    actor="episode-worker",
                )
        return {
            "status": "REROLL_SUBMITTED",
            "parent_variant_id": variant_id,
            "variant_id": str(result["variant"]["id"]),
            "job_id": str(result["job"]["id"]),
            "max_auto_rerolls": limit,
        }

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

    def qc(
        self,
        episode_id: str,
        run_id: str,
        task_id: str,
        *,
        auto_select: bool = False,
        production_session_id: str | None = None,
    ) -> tuple[dict[str, Any], int]:
        project_id, shots = self._episode(episode_id)
        items: list[dict[str, Any]] = []
        for shot in shots:
            shot_id = str(shot["id"])
            jobs = self._variant_jobs(shot_id)
            self._promote_completed_outputs([item for item in jobs if str(item["state"]) == "SUCCEEDED"])
            video = self._shot_video(shot_id)
            if video is None:
                pending = next((job for job in jobs if str(job["state"]) in ACTIVE_JOB_STATES), None)
                items.append(
                    {
                        "shot_id": shot_id,
                        "shot_code": str(shot["code"]),
                        "status": "PENDING" if pending else "BLOCKED",
                        "code": "VIDEO_GENERATION_PENDING" if pending else "VIDEO_CANDIDATE_REQUIRED",
                        "job_id": str(pending["id"]) if pending else None,
                    }
                )
                continue
            media_version_id = str(video["media_version_id"])
            check = self.reviews.machine_check(media_version_id, actor="episode-worker")
            item: dict[str, Any] = {
                "shot_id": shot_id,
                "shot_code": str(shot["code"]),
                "media_version_id": media_version_id,
                "machine_check_run_id": str(check["id"]),
                "status": str(check["status"]),
                "human_approval_created": False,
            }
            if str(check["status"]) == "PASS" and production_session_id:
                try:
                    item["production_choice"] = ProductionChoiceService(self.database).record_video_choice(
                        production_session_id,
                        episode_id,
                        shot_id,
                        media_version_id,
                        str(check["id"]),
                    )
                except DomainRuleError as error:
                    item["production_status"] = "BLOCKED"
                    item["production_choice"] = {"status": "BLOCKED", "code": error.code}
            elif str(check["status"]) == "PASS" and auto_select:
                item["auto_selection"] = self._auto_select_video(video, media_version_id)
                if item["auto_selection"].get("status") == "BLOCKED":
                    item["production_status"] = "BLOCKED"
            if str(check["status"]) != "PASS" and video.get("variant_id"):
                policy_decision = self._qc_policy_decision(str(video["variant_id"]), str(check["id"]))
                if policy_decision is not None and str(policy_decision["disposition"]) != "AUTO_REROLL_ALLOWED":
                    item["repair"] = {
                        "status": "POLICY_GATE",
                        "code": "QC_AUTO_REROLL_NOT_ALLOWED",
                        "qc_link_id": str(policy_decision["id"]),
                        "disposition": str(policy_decision["disposition"]),
                    }
                else:
                    item["repair"] = self._qc_reroll(
                        str(video["variant_id"]),
                        run_id,
                        task_id,
                        project_id,
                        episode_id,
                        shot_id,
                        policy_decision,
                    )
            items.append(item)

        passed = [item for item in items if item["status"] == "PASS"]
        attention = [item for item in items if item["status"] != "PASS" or item.get("production_status") == "BLOCKED"]
        status = "PASS" if not attention else "NEEDS_HITL"
        auto_selected = [item for item in items if isinstance(item.get("auto_selection"), dict) and item["auto_selection"].get("status") == "SELECTED"]
        evidence = {
            "status": status,
            "ok": not attention,
            "checked_shots": len(shots),
            "passed_shots": len(passed),
            "attention_shots": attention,
            "evidence_type": "MACHINE_QC_ONLY",
            "human_approval_status": "PENDING",
            "human_approval_created": False,
            "auto_selected_shots": len(auto_selected),
        }
        summary = f"逐镜机器 QC：PASS {len(passed)}，待处理 {len(attention)}；QC evidence 不等于人工批准"
        if auto_selected:
            summary += f"；自动采用 {len(auto_selected)} 镜"
        return self._report(status, evidence, {"items": items}, summary), 0
