"""Shot-level lip sync commands: queue LatentSync over verified media.

The Job pins both MediaVersions (id + sha256); the worker handler re-verifies
at run time and the output promotes back onto the shot as a dedicated
LIPSYNC-purpose PROXY video, so the desk can play it without touching the
generation candidate lineage.
"""

from __future__ import annotations

from typing import Any

from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database


class LipsyncService:
    def __init__(self, database: Database, settings: Any, *, jobs: JobService | None = None, media: MediaService | None = None) -> None:
        self.database = database
        self.settings = settings
        self.jobs = jobs
        self.media = media

    def _media(self) -> MediaService:
        if self.media is None:
            raise DomainRuleError("LIPSYNC_MEDIA_PORT_REQUIRED", "唇形同步媒体端口未配置")
        return self.media

    def _shot(self, shot_id: str) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT s.id, s.code, se.project_id FROM shots s
                JOIN episodes e ON e.id=s.episode_id JOIN seasons se ON se.id=e.season_id WHERE s.id=?""",
                (shot_id,),
            ).fetchone()
        if row is None:
            raise DomainRuleError("SHOT_NOT_FOUND", "镜头不存在", {"shot_id": shot_id})
        return dict(row)

    def _verified_media(self, project_id: str, media_version_id: str, *, kind: str) -> dict[str, Any]:
        media = self._media()
        version = media.get_version(media_version_id)
        if (
            str(version["project_id"]) != project_id
            or str(version["media_kind"]).upper() != kind
            or str(version["integrity_status"]).upper() != "VERIFIED"
        ):
            raise DomainRuleError(
                "LIPSYNC_INPUT_INVALID",
                f"唇形同步{'视频' if kind == 'VIDEO' else '音频'}输入必须是同项目已验证的 {kind} MediaVersion",
                {"media_version_id": media_version_id},
            )
        return version

    def create_job(
        self,
        shot_id: str,
        *,
        video_media_version_id: str,
        audio_media_version_id: str,
        idempotency_key: str,
        actor: str = "local-user",
    ) -> dict[str, Any]:
        if self.jobs is None:
            raise DomainRuleError("LIPSYNC_JOB_PORT_REQUIRED", "唇形同步任务端口未配置")
        shot = self._shot(shot_id)
        project_id = str(shot["project_id"])
        video = self._verified_media(project_id, video_media_version_id, kind="VIDEO")
        audio = self._verified_media(project_id, audio_media_version_id, kind="AUDIO")
        snapshot = {
            "schema_version": "localdrama.lipsync-job.v1",
            "shot_id": shot_id,
            "video_media_version_id": video_media_version_id,
            "video_sha256": str(video["sha256"]),
            "audio_media_version_id": audio_media_version_id,
            "audio_sha256": str(audio["sha256"]),
            "network_allowed": False,
        }
        return self.jobs.create_job(
            project_id,
            "LIPSYNC_GENERATION",
            "SHOT",
            shot_id,
            "CPU",
            snapshot,
            idempotency_key,
            max_attempts=1,
            actor=actor,
            subject_kind="SHOT",
            scope_project_id=project_id,
        )

    def _audio_sha256(self, audio_media_version_id: str) -> str:
        version = self._media().get_version(audio_media_version_id)
        return str(version["sha256"])

    def list_shot_jobs(self, shot_id: str, *, limit: int = 20) -> dict[str, Any]:
        self._shot(shot_id)
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT j.id, j.state, j.created_at, j.updated_at,
                mv.id AS output_media_version_id
                FROM jobs j
                LEFT JOIN media_versions mv ON mv.source_artifact_id=(
                    SELECT a.id FROM artifacts a JOIN job_attempts ja ON ja.id=a.job_attempt_id
                    WHERE ja.job_id=j.id AND a.kind='LIPSYNC_VIDEO' ORDER BY a.created_at DESC LIMIT 1
                )
                WHERE j.type='LIPSYNC_GENERATION' AND j.subject_type='SHOT' AND j.subject_id=?
                ORDER BY j.created_at DESC, j.id DESC LIMIT ?""",
                (shot_id, max(1, min(int(limit), 50))),
            ).fetchall()
        return {"items": [dict(row) for row in rows], "shot_id": shot_id}

    def finalize_job(self, job_id: str, actor: str = "local-user") -> dict[str, Any]:
        if self.media is None:
            raise DomainRuleError("LIPSYNC_MEDIA_PORT_REQUIRED", "唇形同步媒体端口未配置")
        with self.database.connect() as connection:
            job = connection.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            artifact = connection.execute(
                """SELECT a.id FROM artifacts a JOIN job_attempts ja ON ja.id=a.job_attempt_id
                WHERE ja.job_id=? AND ja.state='SUCCEEDED' AND a.status='VERIFIED' AND a.kind='LIPSYNC_VIDEO'
                ORDER BY a.created_at DESC LIMIT 1""",
                (job_id,),
            ).fetchone()
        if job is None or job["type"] != "LIPSYNC_GENERATION" or job["state"] != "SUCCEEDED" or artifact is None:
            raise DomainRuleError("LIPSYNC_JOB_NOT_FINALIZABLE", "只有成功且具有 VERIFIED LIPSYNC_VIDEO artifact 的 Job 可以登记")
        promoted = self.media.promote_job_artifact(str(artifact["id"]), purpose="LIPSYNC", media_kind="VIDEO", stage="PROXY", actor=actor)
        media_version_id = str(promoted.get("media_version_id") or promoted["id"])
        media = self.media.get_version(media_version_id)
        return {"job_id": job_id, "media": media, "idempotent_replay": bool(promoted.get("duplicate"))}
