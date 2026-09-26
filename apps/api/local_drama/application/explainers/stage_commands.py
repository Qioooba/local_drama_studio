"""Explainers: commands that really persist an execution intent.

The audit found four closed loops that only *looked* closed.  ``narration:
resynthesize`` returned ``requested_stage=NARRATION_TTS`` with zero writes;
``renders`` returned ``SUBMITTED`` after updating ``edition.status`` and creating
no job; ``decisions{rerun_policy:true}`` described a re-run it never scheduled; and
``exports`` inserted a ``BUILDING`` package that no worker could ever claim.  In
every case the UI said "submitted" while nothing existed to be claimed.

This module owns the submission side of those four commands.  Two rules hold for
all of them:

1. **No fake acceptance.**  A command either writes a real, claimable job (through
   the existing ``jobs`` authority — never a second queue) plus its idempotency
   receipt in one transaction, or it returns a structured ``CAPABILITY_UNAVAILABLE``
   / ``BLOCKED`` result with the concrete missing piece.
2. **Freeze the input.**  The job snapshot pins the editions, revisions, hashes and
   parameters the command was authorized with, so a worker can never "read the
   latest" behind the operator's back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.explainers.contracts import ExplainerContractError, content_hash, normalize_locale
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

__all__ = [
    "EXPLAINER_STAGE_JOB_TYPES",
    "ExplainersCommandService",
    "REPAIR_STAGE_FOR_RESPONSIBLE_STEP",
    "STAGE_CAPABILITY_UNAVAILABLE",
    "build_explainers_command_service",
]

#: Stage code -> the job type a worker actually dispatches for it.
#:
#: Only entries listed here are enqueued.  ``NARRATION_TTS`` and
#: ``NARRATION_ALIGN`` have dedicated ``jobs.type`` providers, while
#: ``EXPLAINER_POLICY_EVALUATE`` runs through the generic ``EXPLAINER_TASK``
#: family with its own policy-evaluator port.  A stage with no provider is reported
#: as ``CAPABILITY_UNAVAILABLE`` rather than given an unclaimable job, which is
#: exactly the "already submitted" lie this module exists to remove.
EXPLAINER_STAGE_JOB_TYPES: Mapping[str, str] = {
    "NARRATION_TTS": "NARRATION_TTS",
    "NARRATION_ALIGN": "NARRATION_ALIGN",
    # The picture stage now produces real AI image-to-video clips, and it is the one
    # stage that turns an adopted keyframe into a composable clip.  It used to be
    # reachable only through the whole production graph, so a film whose graph run
    # had stopped could never get its remaining clips.  It has a first-party handler
    # in the same ``EXPLAINER_TASK`` family, so it is scheduled here too.
    "VISUAL_GENERATION": "EXPLAINER_TASK",
    # ``SUBTITLE_BUILD`` is a prerequisite of a complete delivery package, and the
    # export stage refuses to freeze a package whose declared subtitle locale has no
    # subtitle revision.  Without a standalone command the only way to produce one was
    # to re-run the whole production graph.
    "SUBTITLE_BUILD": "EXPLAINER_TASK",
    "COMPOSITION_RENDER": "EXPLAINER_TASK",
    "EXPLAINER_POLICY_EVALUATE": "EXPLAINER_TASK",
    "COMPOSITION_QC": "EXPLAINER_TASK",
    # The export command used to insert a ``BUILDING`` package and stop there: the
    # row was durable intent with no worker that could ever claim it.  The stage has
    # a first-party handler, so the command now also schedules the job that fills
    # the row it just wrote.
    "EXPLAINER_EXPORT": "EXPLAINER_TASK",
}

#: Reason code used when a stage cannot be scheduled in this build.
STAGE_CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"

#: A QC issue names the step responsible for it.  Only steps that own a standalone
#: command can be re-run on their own; the rest describe work that belongs to the
#: production graph (a beat's picture, the script) and cannot be scheduled as one
#: job.  Those are reported as unschedulable instead of being answered with a
#: fabricated acceptance (design §8.2).
REPAIR_STAGE_FOR_RESPONSIBLE_STEP: Mapping[str, str] = {
    "VISUAL_GENERATION": "VISUAL_GENERATION",
    "COMPOSITION_RENDER": "COMPOSITION_RENDER",
    "COMPOSITION_QC": "COMPOSITION_QC",
    "EXPLAINER_POLICY_EVALUATE": "EXPLAINER_POLICY_EVALUATE",
    "EXPLAINER_EXPORT": "EXPLAINER_EXPORT",
    "NARRATION_TTS": "NARRATION_TTS",
    "NARRATION_ALIGN": "NARRATION_ALIGN",
}


@dataclass(frozen=True)
class _StageRequest:
    stage_code: str
    project_id: str
    video_id: str
    subject_type: str
    subject_id: str
    subject_kind: str
    snapshot: Mapping[str, Any]
    idempotency_key: str = ""
    stage_code_for_job: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


class ExplainersCommandService:
    """Submission service for explainer stages that have a real consumer."""

    def __init__(self, database: DatabaseUnitOfWork, *, settings: Any | None = None, jobs: Any | None = None) -> None:
        self.database = database
        self.settings = settings
        # The JobService is injected by the composition root; building it here would
        # add new cross-service construction to the application layer.
        self._jobs = jobs

    # ------------------------------------------------------------------ plumbing
    def _job_service(self) -> Any:
        if self._jobs is None:
            raise DomainRuleError(
                "EXPLAINER_STAGE_JOB_SERVICE_MISSING",
                "解说阶段命令缺少 JobService 依赖，请通过组合根构建",
            )
        return self._jobs

    @staticmethod
    def _accepted(
        *,
        operation_id: str,
        job: Mapping[str, Any],
        stage_code: str,
        subject: Mapping[str, Any],
        frozen: Mapping[str, Any],
        replayed: bool,
    ) -> dict[str, Any]:
        return {
            "status": "ACCEPTED",
            "stage_code": stage_code,
            "operation_id": operation_id,
            "job_id": str(job.get("id") or ""),
            "job_state": str(job.get("state") or "QUEUED"),
            "subject": dict(subject),
            "frozen_plan": dict(frozen),
            "idempotent_replay": bool(replayed),
            "durable_intent_persisted": True,
            "would_create_jobs": True,
        }

    @staticmethod
    def _blocked(*, stage_code: str, blockers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        return {
            "status": "BLOCKED",
            "stage_code": stage_code,
            "operation_id": None,
            "job_id": None,
            "blockers": [dict(item) for item in blockers],
            "would_create_jobs": False,
            "durable_intent_persisted": False,
        }

    @staticmethod
    def _unavailable(*, stage_code: str, reason: str, detail: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "status": STAGE_CAPABILITY_UNAVAILABLE,
            "stage_code": stage_code,
            "operation_id": None,
            "job_id": None,
            "reason": reason,
            "detail": dict(detail or {}),
            "would_create_jobs": False,
            "durable_intent_persisted": False,
            "blockers": [
                {
                    "code": STAGE_CAPABILITY_UNAVAILABLE,
                    "message": "该阶段在当前构建中没有可领取的执行器，未创建任何任务",
                    "next_step": "接入对应 handler 后重试；界面不应显示“已提交”。",
                }
            ],
        }

    def _submit_stage(self, request: _StageRequest) -> dict[str, Any]:
        """Write a real job + receipt in one transaction, or report why not."""

        job_type = EXPLAINER_STAGE_JOB_TYPES.get(request.stage_code)
        if not job_type:
            return self._unavailable(
                stage_code=request.stage_code,
                reason="NO_REGISTERED_WORKER_HANDLER",
                detail={"registered_stages": sorted(EXPLAINER_STAGE_JOB_TYPES)},
            )
        if not request.idempotency_key:
            raise ExplainerContractError(
                "IDEMPOTENCY_KEY_REQUIRED", "该命令必须提供 Idempotency-Key 才能保证只提交一次"
            )
        snapshot = dict(request.snapshot)
        snapshot.setdefault("task_code", request.stage_code)
        snapshot.setdefault("stage_code", request.stage_code)
        snapshot.setdefault("video_id", request.video_id)
        snapshot.setdefault("project_id", request.project_id)
        snapshot["frozen_plan_hash"] = content_hash(dict(snapshot))
        job = self._job_service().create_job(
            request.project_id,
            job_type,
            request.subject_type,
            request.subject_id,
            # ``EXPLAINER`` is not one of the worker's configured channels, so a job
            # created with it was never claimed by anything: the command answered
            # ACCEPTED while the work sat in the queue forever.  These stages run as
            # ordinary local CPU-channel jobs; the narration stages additionally
            # declare their GPU runtime through their job type.
            channel="CPU",
            input_snapshot=snapshot,
            idempotency_key=request.idempotency_key,
            subject_kind=request.subject_kind,
            scope_kind="PROJECT",
            stage_code=request.stage_code,
        )
        replayed = bool(job.get("idempotent_replay"))
        if replayed:
            state = str(job.get("state") or "")
            if state in {"FAILED", "CANCELLED", "DEAD_LETTER"}:
                # Replaying a receipt for a job that is already dead must not look
                # like a fresh acceptance; the caller needs to retry deliberately.
                return {
                    **self._accepted(
                        operation_id=str(job.get("id") or ""),
                        job=job,
                        stage_code=request.stage_code,
                        subject={
                            "kind": request.subject_kind,
                            "id": request.subject_id,
                            "snapshot_hash": snapshot["frozen_plan_hash"],
                        },
                        frozen=snapshot,
                        replayed=True,
                    ),
                    "status": "RECOVERY_REQUIRED",
                    "recovery_action": "RETRY_FAILED_JOB",
                }
        return self._accepted(
            operation_id=str(job.get("id") or ""),
            job=job,
            stage_code=request.stage_code,
            subject={
                "kind": request.subject_kind,
                "id": request.subject_id,
                "snapshot_hash": snapshot["frozen_plan_hash"],
            },
            frozen=snapshot,
            replayed=replayed,
        )

    # ------------------------------------------------------------------ narration
    def submit_narration_resynthesis(
        self,
        *,
        edition_id: str,
        canonical_segment_id: str,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Freeze one segment re-read and schedule the real ``NARRATION_TTS`` job.

        The scope is the *frozen* script revision plus the edition's own voice
        locale, so a re-read can never resolve a segment from another language or
        an older revision through the loose ``video + canonical`` lookup.
        """

        with self.database.connect() as connection:
            repo = ExplainerRepository(connection)
            edition = repo.get("explainer_editions", edition_id)
            video = repo.get("explainer_videos", str(edition["video_id"]))
            frozen_script_revision_id = edition.get("frozen_script_revision_id")
            if not frozen_script_revision_id:
                return self._blocked(
                    stage_code="NARRATION_TTS",
                    blockers=[
                        {
                            "code": "SCHEMA_INVALID",
                            "message": "该输出版本还没有冻结讲稿，不能重读旁白",
                            "next_step": "先冻结讲稿版本。",
                        }
                    ],
                )
            locale = normalize_locale(str(edition["voice_locale"]))
            segment = repo.segment_in_scope(
                video_id=str(video["id"]),
                canonical_segment_id=canonical_segment_id,
                locale=locale,
                script_revision_id=str(frozen_script_revision_id),
            )
            if segment is None:
                raise ExplainerContractError(
                    "NOT_FOUND",
                    "在该输出版本的冻结讲稿里找不到这个段落",
                    {
                        "edition_id": edition_id,
                        "canonical_segment_id": canonical_segment_id,
                        "locale": locale,
                        "frozen_script_revision_id": str(frozen_script_revision_id),
                    },
                )
            voice_snapshot = self._voice_snapshot_for(repo, edition=edition, video=video, locale=locale)
            snapshot = {
                "semantic_inputs": {
                    "narration_segment_id": str(segment["id"]),
                    "canonical_segment_id": str(segment["canonical_segment_id"]),
                    "locale": locale,
                    "segment_hash": str(segment["segment_hash"]),
                    "edition_id": edition_id,
                    "frozen_script_revision_id": str(frozen_script_revision_id),
                    "reason": str(reason),
                },
                "voice_snapshot": voice_snapshot,
                "speech_rate": 1.0,
                "timeout_seconds": 120,
            }
            subject_id = str(segment["id"])
            subject_kind = "NARRATION_SEGMENT"
        request = _StageRequest(
            stage_code="NARRATION_TTS",
            project_id=str(video["project_id"]),
            video_id=str(video["id"]),
            subject_type="NARRATION_SEGMENT",
            subject_id=subject_id,
            subject_kind=subject_kind,
            snapshot=snapshot,
            idempotency_key=idempotency_key,
        )
        result = self._submit_stage(request)
        if result["status"] == "ACCEPTED" or result.get("job_id"):
            # The invalidation closure is now durable state, not a sentence in a
            # response: the downstream artifacts are marked stale in the same flow.
            self._mark_downstream_stale(
                video_id=str(video["id"]),
                upstream_kind="NARRATION_SEGMENT",
                upstream_id=subject_id,
            )
            result["invalidates"] = ["NARRATION_TAKE", "ALIGNMENT", "SUBTITLE_REVISION", "COMPOSITION_REVISION"]
            result["preserves"] = ["FACT_LEDGER", "VISUAL_ASSET", "OTHER_CHAPTER_ASSET"]
            result["neighbour_join_recheck_required"] = True
        return result

    @staticmethod
    def _voice_snapshot_for(
        repo: ExplainerRepository, *, edition: Mapping[str, Any], video: Mapping[str, Any], locale: str
    ) -> dict[str, Any]:
        """The authorized voice/model facts the TTS handler re-validates."""

        profile_version_id = video.get("current_channel_profile_version_id")
        voice: Mapping[str, Any] = {}
        if profile_version_id:
            profile_version = repo.get("channel_profile_versions", str(profile_version_id))
            raw = profile_version.get("voice_json")
            voice = raw if isinstance(raw, Mapping) else {}
        supported = [normalize_locale(str(item)) for item in (voice.get("supported_locales") or [])] or [locale]
        purposes = [str(item) for item in (voice.get("purposes") or [])] or ["NARRATION"]
        return {
            "voice_profile_version_id": voice.get("voice_profile_version_id") or profile_version_id,
            "voice_ref": str(voice.get("voice_ref") or ""),
            "model_ref": str(voice.get("model_ref") or voice.get("voice_ref") or "local-narration-default"),
            "supported_locales": supported,
            "purposes": purposes,
            "license_status": str(voice.get("license_status") or "UNVERIFIED").upper(),
            "license_evidence": voice.get("license_evidence"),
            "test_only_acknowledged": bool(voice.get("test_only_acknowledged")),
            "delivery_authorized": str(voice.get("license_status") or "").upper() in {"VERIFIED", "LICENSED"},
            "test_only": str(voice.get("license_status") or "").upper() in {"UNVERIFIED", "TEST_ONLY"},
        }

    def _mark_downstream_stale(
        self, *, video_id: str, upstream_kind: str, upstream_id: str
    ) -> None:
        with self.database.transaction() as connection:
            ExplainerRepository(connection).mark_dependents_stale(
                upstream_kind=upstream_kind,
                upstream_id=upstream_id,
                downstream_kinds=(
                    "NARRATION_TAKE",
                    "ALIGNMENT",
                    "SUBTITLE_REVISION",
                    "COMPOSITION_REVISION",
                ),
                reason="NARRATION_RESYNTHESIS_REQUESTED",
                invalidated_by=f"explainer-command:{video_id}",
            )

    # ------------------------------------------------------------------ policy
    def submit_policy_rerun(
        self, *, edition_id: str, idempotency_key: str, reason: str = "OPERATOR_REQUEST"
    ) -> dict[str, Any]:
        """Schedule the real policy evaluation instead of describing it in prose."""

        with self.database.connect() as connection:
            repo = ExplainerRepository(connection)
            edition = repo.get("explainer_editions", edition_id)
            video = repo.get("explainer_videos", str(edition["video_id"]))
            target = repo.current_review_target(edition_id)
        snapshot = {
            "semantic_inputs": {
                "edition_id": edition_id,
                "subject_kind": "COMPOSITION_RENDER" if target["has_render"] else "EDITION",
                "subject_revision_id": target["render_id"] or edition_id,
                "subject_hash": target["render_sha256"] or "",
                "policy_rule_version": "explainer_standard_v1",
                "reason": str(reason),
                "video_id": str(video["id"]),
            }
        }
        return self._submit_stage(
            _StageRequest(
                stage_code="EXPLAINER_POLICY_EVALUATE",
                project_id=str(video["project_id"]),
                video_id=str(video["id"]),
                subject_type="EXPLAINER_EDITION",
                subject_id=edition_id,
                subject_kind="EDITION",
                snapshot=snapshot,
                idempotency_key=idempotency_key,
            )
        )

    # ------------------------------------------------------------------ alignment
    def submit_alignment(
        self,
        *,
        edition_id: str,
        take_id: str,
        locale: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Schedule alignment for one take; used after a take becomes selected."""

        with self.database.connect() as connection:
            repo = ExplainerRepository(connection)
            edition = repo.get("explainer_editions", edition_id)
            video = repo.get("explainer_videos", str(edition["video_id"]))
            take = repo.get("narration_takes", take_id)
        if str(take["video_id"]) != str(video["id"]):
            raise ExplainerContractError("INVALID_REQUEST", "take 不属于该输出版本的作品")
        return self._submit_stage(
            _StageRequest(
                stage_code="NARRATION_ALIGN",
                project_id=str(video["project_id"]),
                video_id=str(video["id"]),
                subject_type="NARRATION_TAKE",
                subject_id=take_id,
                subject_kind="NARRATION_TAKE",
                snapshot={
                    "semantic_inputs": {
                        "take_id": take_id,
                        "edition_id": edition_id,
                        "locale": normalize_locale(locale),
                        "segment_id": str(take["segment_id"]),
                    }
                },
                idempotency_key=idempotency_key,
            )
        )

    def submit_alignment_rerun(
        self,
        *,
        edition_id: str,
        idempotency_key: str,
        take_ids: Sequence[str] = (),
    ) -> dict[str, Any]:
        """Re-run forced alignment for this edition's selected takes.

        Alignment revisions are append-only, and the pipeline align *stage* only
        processes takes that have no revision yet — so a take whose stored clock is
        unusable could never be re-measured from the page.  Each take becomes its own
        real ``NARRATION_ALIGN`` job; nothing is written unless the edition and its
        takes exist.
        """

        with self.database.connect() as connection:
            repo = ExplainerRepository(connection)
            edition = repo.get("explainer_editions", edition_id)
            if edition is None:
                raise ExplainerContractError("NOT_FOUND", "找不到该输出版本", {"edition_id": edition_id})
            video = repo.get("explainer_videos", str(edition["video_id"]))
            locale = normalize_locale(str(edition["voice_locale"]))
            takes = repo.selected_takes(str(video["id"]), locale)
            wanted = {str(item) for item in take_ids if str(item)}
            if wanted:
                takes = [item for item in takes if str(item["id"]) in wanted]
        if not takes:
            return self._blocked(
                stage_code="NARRATION_ALIGN",
                blockers=[
                    {
                        "code": "SCHEMA_INVALID",
                        "message": "该输出版本没有已选定的旁白条目，无法重新对齐",
                        "next_step": "先完成旁白合成并选定 take。",
                    }
                ],
            )
        submissions: list[dict[str, Any]] = []
        for take in takes:
            result = self.submit_alignment(
                edition_id=edition_id,
                take_id=str(take["id"]),
                locale=locale,
                idempotency_key=f"{idempotency_key}:{take['id']}",
            )
            submissions.append(
                {
                    "take_id": str(take["id"]),
                    "canonical_segment_id": str(take["canonical_segment_id"]),
                    "status": str(result.get("status") or ""),
                    "job_id": result.get("job_id"),
                    "blockers": result.get("blockers"),
                }
            )
        accepted = [item for item in submissions if item["status"] == "ACCEPTED"]
        return {
            "status": "ACCEPTED" if accepted else "BLOCKED",
            "stage_code": "NARRATION_ALIGN",
            "edition_id": edition_id,
            "locale": locale,
            "take_count": len(takes),
            "accepted_count": len(accepted),
            "job_ids": [str(item["job_id"]) for item in accepted if item["job_id"]],
            "submissions": submissions,
            "note": "每个 take 一个真实对齐任务；旧对齐修订保留，新修订追加。",
        }

    # ------------------------------------------------------------------ render
    def submit_visual_generation(
        self,
        *,
        video_id: str,
        idempotency_key: str,
        beat_ids: Sequence[str] = (),
        edition_id: str | None = None,
    ) -> dict[str, Any]:
        """Schedule the real picture stage for one film's remaining clips.

        The command exists so a film whose production graph run stopped (or that was
        driven page by page from the start) can still be finished: the stage itself
        reuses every human-adopted keyframe and every beat that already has a READY
        visual candidate, and generates a real image-to-video clip for the rest.
        Nothing is written unless the film really exists; ``beat_ids`` narrows the
        run to an explicit selection.
        """

        with self.database.connect() as connection:
            repo = ExplainerRepository(connection)
            video = repo.get("explainer_videos", video_id)
            if video is None:
                raise ExplainerContractError(
                    "NOT_FOUND", "找不到该解说作品", {"video_id": video_id}
                )
            editions = repo.list_where("explainer_editions", {"video_id": video_id})
            if not editions:
                return self._blocked(
                    stage_code="VISUAL_GENERATION",
                    blockers=[
                        {
                            "code": "SCHEMA_INVALID",
                            "message": "该作品没有输出版本，画面没有可落地的载体",
                            "next_step": "先在项目设置里声明至少一个输出版本。",
                        }
                    ],
                )
            beats = repo.beats(video_id)
            if not beats:
                return self._blocked(
                    stage_code="VISUAL_GENERATION",
                    blockers=[
                        {
                            "code": "SCHEMA_INVALID",
                            "message": "该作品还没有画面段，无法生成片段",
                            "next_step": "先完成第 4 步分镜与画面。",
                        }
                    ],
                )
            resolved_edition_id = str(edition_id or editions[0]["id"])
            if not any(str(item["id"]) == resolved_edition_id for item in editions):
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "所选输出版本不属于该作品",
                    {"edition_id": resolved_edition_id, "video_id": video_id},
                )
        return self._submit_stage(
            _StageRequest(
                stage_code="VISUAL_GENERATION",
                project_id=str(video["project_id"]),
                video_id=video_id,
                subject_type="EXPLAINER_VIDEO",
                subject_id=video_id,
                subject_kind="VIDEO",
                snapshot={
                    "semantic_inputs": {
                        "project_id": str(video["project_id"]),
                        "video_id": video_id,
                        "edition_id": resolved_edition_id,
                        "beat_ids": [str(item) for item in beat_ids],
                    }
                },
                idempotency_key=idempotency_key,
            )
        )

    def submit_subtitle_build(
        self,
        *,
        edition_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Schedule the real subtitle build for one edition.

        The command exists because a complete delivery package requires a subtitle
        revision for every locale the edition declares, and the export stage refuses
        an incomplete roster.  Subtitle building is a plain local stage, so it needs
        no capability gate beyond the edition existing.
        """

        with self.database.connect() as connection:
            repo = ExplainerRepository(connection)
            edition = repo.get("explainer_editions", edition_id)
            if edition is None:
                raise ExplainerContractError(
                    "NOT_FOUND", "找不到该输出版本", {"edition_id": edition_id}
                )
            video = repo.get("explainer_videos", str(edition["video_id"]))
        return self._submit_stage(
            _StageRequest(
                stage_code="SUBTITLE_BUILD",
                project_id=str(video["project_id"]),
                video_id=str(video["id"]),
                subject_type="EXPLAINER_EDITION",
                subject_id=edition_id,
                subject_kind="EDITION",
                snapshot={
                    "semantic_inputs": {
                        "project_id": str(video["project_id"]),
                        "video_id": str(video["id"]),
                        "edition_id": edition_id,
                    }
                },
                idempotency_key=idempotency_key,
            )
        )

    def submit_composition_qc(
        self,
        *,
        edition_id: str,
        render_id: str = "",
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Schedule the real technical/session QC for one rendered film.

        ``COMPOSITION_QC`` sits between the render and the machine policy decision,
        and the delivery package ships its report — but until now the only way to run
        it was to let the whole production graph reach it, so a film driven stage by
        stage shipped a package whose ``qc_report.json`` said ``NOT_RUN``.  The
        subject is pinned to a concrete render (the newest verified one by default),
        never re-resolved behind the operator's back.
        """

        from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

        with self.database.connect() as connection:
            repo = ExplainerRepository(connection)
            edition = repo.get("explainer_editions", edition_id)
            if edition is None:
                raise ExplainerContractError("NOT_FOUND", "找不到该输出版本", {"edition_id": edition_id})
            video = repo.get("explainer_videos", str(edition["video_id"]))
            resolved_render_id = str(render_id or "").strip()
            if resolved_render_id:
                render = repo.find("composition_renders", resolved_render_id)
                if render is None or str(render.get("edition_id") or "") != str(edition_id):
                    raise ExplainerContractError(
                        "NOT_FOUND",
                        "找不到该输出版本的渲染版本",
                        {"edition_id": edition_id, "render_id": resolved_render_id},
                    )
            else:
                target = repo.current_review_target(edition_id)
                resolved_render_id = str(target.get("render_id") or "")
            if not resolved_render_id:
                return self._blocked(
                    stage_code="COMPOSITION_QC",
                    blockers=[
                        {
                            "code": "SCHEMA_INVALID",
                            "message": "该输出版本还没有可质检的成片，无法运行技术质检",
                            "next_step": "先完成第 7 步全片渲染。",
                        }
                    ],
                )
            render_row = repo.find("composition_renders", resolved_render_id) or {}
            subject_hash = str(render_row.get("sha256") or "")
        return self._submit_stage(
            _StageRequest(
                stage_code="COMPOSITION_QC",
                project_id=str(video["project_id"]),
                video_id=str(video["id"]),
                subject_type="COMPOSITION_RENDER",
                subject_id=resolved_render_id,
                subject_kind="COMPOSITION_RENDER",
                snapshot={
                    "semantic_inputs": {
                        "project_id": str(video["project_id"]),
                        "video_id": str(video["id"]),
                        "edition_id": edition_id,
                        "subject_kind": "COMPOSITION_RENDER",
                        "subject_revision_id": resolved_render_id,
                        "render_id": resolved_render_id,
                        "subject_hash": subject_hash,
                    }
                },
                idempotency_key=idempotency_key,
            )
        )

    def submit_composition_render(
        self,
        *,
        edition_id: str,
        composition: Mapping[str, Any] | None,
        idempotency_key: str,
        confirm: bool,
    ) -> dict[str, Any]:
        """Register a real render job bound to the frozen manifest.

        ``COMPOSITION_RENDER`` now has a first-party worker handler, so the command
        creates a claimable ``EXPLAINER_TASK`` job for the edition instead of
        describing work no worker could pick up.  Nothing is written unless the
        composition really is frozen: the previous behaviour flipped the edition to
        ``RENDERING`` and returned ``SUBMITTED`` while no job existed.

        ``composition=None`` is the **first** render of an edition: the stage itself
        builds and freezes the manifest from the adopted clips and narration, so the
        command only carries the edition scope.  A caller that passes a composition
        must pass a frozen one — reading "the latest" behind the operator's back is
        exactly what this refusal prevents.
        """

        with self.database.connect() as connection:
            repo = ExplainerRepository(connection)
            edition = repo.get("explainer_editions", edition_id)
            video = repo.get("explainer_videos", str(edition["video_id"]))
        if composition is not None and str(composition.get("status")) != "FROZEN":
            return self._blocked(
                stage_code="COMPOSITION_RENDER",
                blockers=[
                    {
                        "code": "SCHEMA_INVALID",
                        "message": "渲染必须基于已冻结的 composition manifest",
                        "next_step": "先冻结 composition。",
                    }
                ],
            )
        semantic_inputs: dict[str, Any] = {
            "project_id": str(video["project_id"]),
            "video_id": str(video["id"]),
            "edition_id": edition_id,
        }
        if composition is not None:
            semantic_inputs["composition_revision_id"] = str(composition.get("id") or "")
            semantic_inputs["manifest_hash"] = str(composition.get("manifest_hash") or "")
        return self._submit_stage(
            _StageRequest(
                stage_code="COMPOSITION_RENDER",
                project_id=str(video["project_id"]),
                video_id=str(video["id"]),
                subject_type="EXPLAINER_EDITION",
                subject_id=edition_id,
                subject_kind="EDITION",
                snapshot={"semantic_inputs": semantic_inputs},
                idempotency_key=idempotency_key,
            )
        )


    # ------------------------------------------------------------------ repairs
    def submit_repair(
        self,
        *,
        project_id: str,
        video_id: str,
        issue_ids: Sequence[str],
        responsible_steps: Sequence[str],
        beat_ids: Sequence[str] = (),
        revision: int = 0,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Schedule one real job per re-runnable responsible step.

        The repair endpoint used to answer ``submitted: true`` while writing
        nothing at all: the reviewer pressed "fix this issue" and no job, no state
        change and no retry existed.  This submits through the same stage commands
        the manual buttons use, so every returned ``job_id`` is claimable.

        A responsible step with no standalone command (a beat's picture, the
        script) is reported in ``unschedulable`` with its reason.  When *no* step
        can be scheduled the whole command reports ``REPAIR_NOT_SCHEDULABLE``
        rather than a success, which is the design's required answer for a request
        that cannot be honoured.
        """

        submissions: list[dict[str, Any]] = []
        unschedulable: list[dict[str, Any]] = []
        for step in dict.fromkeys(str(item) for item in responsible_steps if str(item)):
            stage = REPAIR_STAGE_FOR_RESPONSIBLE_STEP.get(step)
            if stage is None:
                unschedulable.append(
                    {
                        "responsible_step_code": step,
                        "reason": "NO_STANDALONE_REPAIR_COMMAND",
                        "next_step": "该步骤由生产图中的画面/讲稿阶段承担，请重跑对应阶段而不是单独提交。",
                    }
                )
                continue
            result = self._submit_stage(
                _StageRequest(
                    stage_code=stage,
                    project_id=project_id,
                    video_id=video_id,
                    subject_type="EXPLAINER_VIDEO",
                    subject_id=video_id,
                    subject_kind="VIDEO",
                    snapshot={
                        "semantic_inputs": {
                            "project_id": project_id,
                            "video_id": video_id,
                            "issue_ids": [str(item) for item in issue_ids],
                            "beat_ids": [str(item) for item in beat_ids],
                            "repair_revision": int(revision),
                            "repair_of_step": step,
                        }
                    },
                    idempotency_key=f"{idempotency_key}:{stage}",
                )
            )
            submissions.append({"responsible_step_code": step, "stage_code": stage, **result})
        job_ids = [
            str(item.get("job_id") or item.get("operation_id") or "")
            for item in submissions
            if str(item.get("status")) in {"ACCEPTED", "RECOVERY_REQUIRED"}
        ]
        scheduled = [item for item in submissions if str(item.get("status")) == "ACCEPTED"]
        if not scheduled:
            return {
                "status": "REPAIR_NOT_SCHEDULABLE",
                "project_id": project_id,
                "video_id": video_id,
                "issue_ids": [str(item) for item in issue_ids],
                "submitted": False,
                "job_ids": job_ids,
                "stages": submissions,
                "unschedulable": unschedulable,
                "note": "没有任何可独立执行的返工阶段，未创建任务。",
            }
        return {
            "status": "ACCEPTED",
            "project_id": project_id,
            "video_id": video_id,
            "issue_ids": [str(item) for item in issue_ids],
            "submitted": True,
            "job_ids": [item for item in job_ids if item],
            "stages": submissions,
            "unschedulable": unschedulable,
            "note": "已按问题责任阶段提交真实返工任务；未列出的步骤无法单独执行。",
        }

    # ------------------------------------------------------------------ export
    def submit_export(
        self,
        *,
        edition_id: str,
        package_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Schedule the worker that builds an already-recorded publication package.

        The command flow is plan → confirm → durable ``BUILDING`` row → this job.  The
        job's snapshot pins the package id, so the worker completes exactly the row
        the operator confirmed instead of inventing a second package for the same
        edition.
        """

        with self.database.connect() as connection:
            repo = ExplainerRepository(connection)
            edition = repo.get("explainer_editions", edition_id)
            video = repo.get("explainer_videos", str(edition["video_id"]))
            package = repo.get("publication_packages", package_id)
        if str(package["edition_id"]) != str(edition_id):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "发布包不属于该输出版本",
                {"package_id": package_id, "edition_id": edition_id},
            )
        return self._submit_stage(
            _StageRequest(
                stage_code="EXPLAINER_EXPORT",
                project_id=str(video["project_id"]),
                video_id=str(video["id"]),
                subject_type="PUBLICATION_PACKAGE",
                subject_id=package_id,
                subject_kind="PUBLICATION_PACKAGE",
                snapshot={
                    "semantic_inputs": {
                        "project_id": str(video["project_id"]),
                        "video_id": str(video["id"]),
                        "edition_id": edition_id,
                        "package_id": package_id,
                        "render_id": str(package.get("render_id") or ""),
                        "composition_revision_id": str(package.get("composition_revision_id") or ""),
                    }
                },
                idempotency_key=idempotency_key,
            )
        )


def build_explainers_command_service(
    database: DatabaseUnitOfWork,
    settings: Any | None = None,
    *,
    jobs: Any | None = None,
) -> ExplainersCommandService:
    """Composition root for the explainer stage commands.

    The job authority — the only queue these commands are allowed to write to — is
    wired here instead of inside the service, so the application layer keeps
    depending on an injected collaborator rather than constructing a concrete
    service of its own.
    """

    if jobs is None:
        from local_drama.application.jobs import JobService

        jobs = JobService(database, settings)
    return ExplainersCommandService(database, settings=settings, jobs=jobs)
