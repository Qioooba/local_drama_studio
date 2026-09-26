"""Project a finished V2 model job onto its reserved explainer candidate.

Design reference: 解说工厂-体验功能与代码改造实施规范 §D2.1 (``finalize_candidate``
is idempotent at ``(candidate_id, source_job_attempt_id, media_sha256)``), §D6
(worker completion, recovery, and composition dependencies) and §F2.4.

Why this is a separate service
------------------------------
``ExplainerVisualGenerationService`` *reserves* a candidate and submits a real job.
The job then runs on the model worker, and nothing in the explainer domain owns the
moment the output artifacts exist.  This service is that owner, and it is
deliberately the only place a candidate becomes ``READY``.

Three properties matter, and the code is shaped around them:

* **A miss is normal.**  The worker calls this for every finished model job, so a
  job that is not an explainer candidate must return ``None`` silently instead of
  raising and polluting the worker's error path.
* **Finalization is idempotent.**  A duplicate callback, a replayed late event or
  the startup reconcile scan can all call this with the same artifacts; the second
  call must recognise an already registered candidate and change nothing.
* **Failure is projected truthfully.**  A failed job marks its candidate ``FAILED``
  with the real code, and never touches an already ``READY`` or adopted candidate.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Protocol, Sequence

from local_drama.domain.errors import DomainRuleError

__all__ = [
    "ExplainerVisualGenerationCompletionService",
    "MediaPromotionPort",
    "build_explainer_visual_completion_service",
]


class MediaPromotionPort(Protocol):  # pragma: no cover - structural typing only
    def promote_job_artifact(self, artifact_id: str, **kwargs: Any) -> Mapping[str, Any]: ...

    def submit_default_derivatives(self, media_version_id: str) -> Any: ...


def _now() -> str:
    from local_drama.domain.explainers.contracts import utc_now_iso

    return utc_now_iso()


class ExplainerVisualGenerationCompletionService:
    """Register explainer candidate outputs exactly once."""

    def __init__(self, database: Any, settings: Any, *, media: MediaPromotionPort) -> None:
        self.database = database
        self.settings = settings
        self.media = media

    # ------------------------------------------------------------------ lookup
    def candidate_for_job(self, job_id: str) -> Mapping[str, Any] | None:
        """The candidate this job belongs to, or ``None`` when the job is not ours."""

        if not job_id:
            return None
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT c.*, v.project_id AS project_id
                     FROM explainer_media_candidates c
                     JOIN explainer_videos v ON v.id = c.video_id
                    WHERE c.job_id = ?
                    ORDER BY c.created_at DESC LIMIT 1""",
                (job_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    # ------------------------------------------------------------------ success
    def finalize_job(self, job_id: str, artifacts: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
        candidate = self.candidate_for_job(job_id)
        if candidate is None:
            return None
        candidate_id = str(candidate["id"])
        if str(candidate.get("status")) == "READY" and candidate.get("media_version_id"):
            # Idempotent replay: the media version and hash already exist, so this
            # callback adds nothing.  Re-registering would create a second media
            # version for one candidate and break the version chain.
            return {
                "status": "READY",
                "candidate_id": candidate_id,
                "media_version_id": str(candidate["media_version_id"]),
                "media_sha256": candidate.get("media_sha256"),
                "idempotent_replay": True,
            }
        if not artifacts:
            raise DomainRuleError(
                "EXPLAINER_CANDIDATE_OUTPUT_MISSING",
                "生成任务报告成功，但没有任何可登记的媒体产物。",
                {"candidate_id": candidate_id, "job_id": job_id},
            )

        media_kind = "VIDEO" if str(candidate.get("purpose")) == "VISUAL" and _is_video_job(candidate) else "IMAGE"
        promoted: Mapping[str, Any] | None = None
        last_error: DomainRuleError | None = None
        for artifact in artifacts:
            try:
                promoted = self.media.promote_job_artifact(
                    str(artifact["id"]),
                    purpose=f"EXPLAINER_{str(candidate.get('purpose') or 'VISUAL')}_CANDIDATE",
                    media_kind=media_kind,
                    stage="KEYFRAME" if media_kind == "IMAGE" else "CLIP",
                    actor="explainer-visual-worker",
                )
                break
            except DomainRuleError as error:
                last_error = error
        if promoted is None:
            raise last_error or DomainRuleError(
                "EXPLAINER_CANDIDATE_OUTPUT_INVALID",
                "生成任务的产物无法登记为媒体版本。",
                {"candidate_id": candidate_id, "job_id": job_id},
            )

        media_version_id = str(promoted["media_version_id"])
        media_sha256 = str(promoted.get("sha256") or promoted.get("media_sha256") or "")
        media_asset_id = str(promoted.get("media_asset_id") or "") or None
        # Image content is never served directly and a clip needs its poster frame,
        # so the immutable thumbnail (plus filmstrip/proxy for clips) is queued
        # before the candidate becomes visible: a read model can then show the card
        # without mutating on GET, and the read-only thumbnail endpoint does not have
        # to answer ``MEDIA_DERIVATIVE_NOT_READY`` for a candidate that is READY.
        try:
            self.media.submit_default_derivatives(media_version_id)
        except Exception:  # a derivative failure must not lose a valid candidate
            pass

        with self.database.transaction() as connection:
            current = connection.execute(
                "SELECT status, media_version_id, source_job_attempt_id FROM explainer_media_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
            if current is None:
                raise DomainRuleError(
                    "EXPLAINER_CANDIDATE_NOT_FOUND",
                    "候选记录已不存在，拒绝登记产物。",
                    {"candidate_id": candidate_id},
                )
            if str(current["status"]) == "READY" and current["media_version_id"]:
                # Another concurrent callback won the race; the first registration is
                # the truth and this one is a replay.
                return {
                    "status": "READY",
                    "candidate_id": candidate_id,
                    "media_version_id": str(current["media_version_id"]),
                    "media_sha256": media_sha256,
                    "idempotent_replay": True,
                }
            connection.execute(
                """UPDATE explainer_media_candidates
                      SET status = 'READY', media_version_id = ?, media_asset_id = ?, media_sha256 = ?,
                          source_job_attempt_id = COALESCE(source_job_attempt_id, ?),
                          render_type_actual = COALESCE(render_type_actual, ?),
                          qc_summary_json = ?, updated_at = ?, revision = revision + 1
                    WHERE id = ? AND status <> 'READY'""",
                (
                    media_version_id,
                    media_asset_id,
                    media_sha256,
                    str(artifacts[0].get("job_attempt_id") or "") or None,
                    None if media_kind == "VIDEO" else None,
                    json.dumps(
                        {
                            "file_valid": True,
                            "content_checked": False,
                            "content_check_note": "视觉语义检查未运行；可播放不等于角色/内容已通过检查。",
                        },
                        ensure_ascii=False,
                    ),
                    _now(),
                    candidate_id,
                ),
            )
        return {
            "status": "READY",
            "candidate_id": candidate_id,
            "media_version_id": media_version_id,
            "media_sha256": media_sha256,
            "idempotent_replay": False,
        }

    # ------------------------------------------------------------------ failure
    def record_failure(self, job_id: str, error: DomainRuleError) -> bool:
        """Project a real job failure onto the reserved candidate.

        A candidate that is already ``READY`` (or was adopted) is never rewritten:
        the design keeps previously successful media usable when a later job fails.
        """

        candidate = self.candidate_for_job(job_id)
        if candidate is None:
            return False
        with self.database.transaction() as connection:
            cursor = connection.execute(
                """UPDATE explainer_media_candidates
                      SET status = 'FAILED', fallback_reason = ?, updated_at = ?, revision = revision + 1
                    WHERE job_id = ? AND status IN ('PENDING','GENERATING')""",
                (f"{error.code}：{error.message}", _now(), job_id),
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------------ recovery
    def reconcile_pending(self, *, limit: int = 200) -> dict[str, Any]:
        """Self-heal candidates whose completion callback was lost.

        The design requires a startup/periodic scan precisely because a lost callback
        must not leave a candidate stuck in ``GENERATING`` forever (spec §D6).  The
        scan is read-mostly and idempotent: a candidate whose job already succeeded is
        finalized, and one whose job is still running is left alone.
        """

        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT c.id AS candidate_id, c.job_id AS job_id, j.state AS job_state
                     FROM explainer_media_candidates c
                     JOIN jobs j ON j.id = c.job_id
                    WHERE c.status IN ('PENDING','GENERATING')
                    ORDER BY c.created_at ASC LIMIT ?""",
                (int(limit),),
            ).fetchall()
        finalized = 0
        failed = 0
        pending = 0
        for row in rows:
            job_id = str(row["job_id"] or "")
            state = str(row["job_state"] or "").upper()
            if state in {"SUCCEEDED", "COMPLETED"}:
                artifacts = self._successful_artifacts(job_id)
                if not artifacts:
                    pending += 1
                    continue
                self.finalize_job(job_id, artifacts)
                finalized += 1
            elif state in {"FAILED", "CANCELLED", "NEEDS_ATTENTION"}:
                self.record_failure(
                    job_id,
                    DomainRuleError(
                        "EXPLAINER_GENERATION_JOB_FAILED",
                        f"生成任务未成功完成（{state}）。",
                    ),
                )
                failed += 1
            else:
                pending += 1
        return {
            "scanned": len(rows),
            "finalized": finalized,
            "failed": failed,
            "still_pending": pending,
        }

    def _successful_artifacts(self, job_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT a.id, a.kind, a.status, a.sandbox_rel_path, a.sha256, a.job_attempt_id
                     FROM artifacts a
                     JOIN job_attempts at ON at.id = a.job_attempt_id
                    WHERE at.job_id = ? AND a.status = 'VERIFIED'
                    ORDER BY a.created_at ASC""",
                (job_id,),
            ).fetchall()
        return [dict(row) for row in rows]


def _is_video_job(candidate: Mapping[str, Any]) -> bool:
    snapshot = candidate.get("execution_snapshot_json")
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot or "{}")
        except (TypeError, ValueError):
            snapshot = {}
    if not isinstance(snapshot, Mapping):
        return False
    return str(snapshot.get("mode") or "").upper() == "IMAGE_TO_VIDEO"


def build_explainer_visual_completion_service(database: Any, settings: Any) -> ExplainerVisualGenerationCompletionService:
    """Construct the completion service with the shared media promotion port."""

    from local_drama.application.media import MediaService

    return ExplainerVisualGenerationCompletionService(database, settings, media=MediaService(database, settings))
