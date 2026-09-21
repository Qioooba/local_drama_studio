"""Safe, reviewable cleanup of expired video-upscale worker intermediates."""

from __future__ import annotations

import hashlib
import hmac
import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from local_drama.application.ports.database import DatabaseUnitOfWork
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.filesystem.path_policy import controlled_path, is_reparse_point

_TERMINAL_STATES = {"SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"}


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: object) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise DomainRuleError("UPSCALE_CLEANUP_TOKEN_INVALID", "清理计划时间无效，请重新预览") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _tree_size_without_links(root: Path) -> tuple[int, bool]:
    """Measure a tree without following links; report any reparse entry as unsafe."""

    if is_reparse_point(root):
        return 0, True
    total = 0
    pending = [root]
    while pending:
        current = pending.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            return total, True
        for entry in entries:
            if is_reparse_point(entry):
                return total, True
            try:
                if entry.is_dir():
                    pending.append(entry)
                elif entry.is_file():
                    total += entry.stat().st_size
            except OSError:
                return total, True
    return total, False


class VideoUpscaleCleanupService:
    """Plan then delete only expired, DB-owned NCNN intermediate directories."""

    def __init__(self, database: DatabaseUnitOfWork, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def plan(
        self,
        project_id: str,
        *,
        retention_days: int = 7,
        eligible_before: str | None = None,
    ) -> dict[str, Any]:
        if retention_days < 1 or retention_days > 365:
            raise DomainRuleError("UPSCALE_CLEANUP_RETENTION_INVALID", "清理保留期必须为 1—365 天")
        cutoff = (
            _parse_utc(eligible_before)
            if eligible_before is not None
            else datetime.now(UTC) - timedelta(days=retention_days)
        )
        cutoff_text = cutoff.isoformat()
        now_text = datetime.now(UTC).isoformat()
        with self.database.connect() as connection:
            if connection.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
                raise DomainRuleError("PROJECT_NOT_FOUND", "项目不存在")
            rows = connection.execute(
                """SELECT run.id AS run_id,run.job_id,job.state,
                COALESCE(job.finished_at,job.updated_at) AS terminal_at
                FROM video_upscale_runs run
                JOIN jobs job ON job.id=run.job_id
                WHERE run.project_id=?
                  AND job.state IN ('SUCCEEDED','FAILED','CANCELLED','NEEDS_ATTENTION','ORPHANED')
                  AND COALESCE(job.finished_at,job.updated_at)<=?
                  AND NOT EXISTS (
                    SELECT 1 FROM job_attempts attempt
                    WHERE attempt.job_id=job.id
                      AND attempt.state IN ('CLAIMED','RUNNING')
                      AND (attempt.lease_expires_at IS NULL OR attempt.lease_expires_at>?)
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM job_resource_leases lease
                    WHERE lease.job_id=job.id AND lease.released_at IS NULL
                  )
                ORDER BY run.id""",
                (project_id, cutoff_text, now_text),
            ).fetchall()

        candidates: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for row in rows:
            relative = f"jobs/{row['job_id']}/ncnn-upscale"
            try:
                path = controlled_path(self.settings.work_root, relative, code="UPSCALE_CLEANUP_PATH_UNSAFE")
            except DomainRuleError:
                skipped.append({"run_id": str(row["run_id"]), "reason": "PATH_OR_REPARSE_UNSAFE"})
                continue
            if not path.exists():
                continue
            if not path.is_dir():
                skipped.append({"run_id": str(row["run_id"]), "reason": "NOT_A_DIRECTORY"})
                continue
            byte_size, unsafe = _tree_size_without_links(path)
            if unsafe:
                skipped.append({"run_id": str(row["run_id"]), "reason": "CONTAINS_REPARSE_POINT"})
                continue
            candidates.append(
                {
                    "run_id": str(row["run_id"]),
                    "job_id": str(row["job_id"]),
                    "job_state": str(row["state"]),
                    "terminal_at": str(row["terminal_at"]),
                    "rel_path": relative,
                    "byte_size": byte_size,
                }
            )
        frozen = {
            "schema_version": "localdrama.video-upscale-cleanup-plan.v1",
            "project_id": project_id,
            "retention_days": retention_days,
            "eligible_before": cutoff_text,
            "candidates": candidates,
        }
        return {
            **frozen,
            "plan_hash": _hash(frozen),
            "candidate_count": len(candidates),
            "reclaimable_bytes": sum(int(item["byte_size"]) for item in candidates),
            "skipped": skipped,
            "mutated": False,
            "runtime_contacted": False,
            "network_contacted": False,
        }

    def commit(
        self,
        project_id: str,
        *,
        retention_days: int,
        eligible_before: str,
        plan_hash: str,
    ) -> dict[str, Any]:
        plan = self.plan(
            project_id,
            retention_days=retention_days,
            eligible_before=eligible_before,
        )
        if not hmac.compare_digest(str(plan["plan_hash"]), plan_hash):
            raise DomainRuleError("UPSCALE_CLEANUP_PLAN_STALE", "可清理内容已变化，请重新预览")
        deleted: list[dict[str, Any]] = []
        validated: list[tuple[dict[str, Any], Path]] = []
        with self.database.transaction() as connection:
            for item in plan["candidates"]:
                row = connection.execute(
                    """SELECT job.state FROM video_upscale_runs run
                    JOIN jobs job ON job.id=run.job_id
                    WHERE run.id=? AND run.project_id=? AND run.job_id=?""",
                    (item["run_id"], project_id, item["job_id"]),
                ).fetchone()
                if row is None or str(row["state"]) not in _TERMINAL_STATES:
                    raise DomainRuleError("UPSCALE_CLEANUP_PLAN_STALE", "任务状态已变化，请重新预览")
                active = connection.execute(
                    """SELECT 1 FROM job_attempts attempt
                    WHERE attempt.job_id=? AND attempt.state IN ('CLAIMED','RUNNING')
                    AND (attempt.lease_expires_at IS NULL OR attempt.lease_expires_at>?)
                    LIMIT 1""",
                    (item["job_id"], datetime.now(UTC).isoformat()),
                ).fetchone()
                resource = connection.execute(
                    "SELECT 1 FROM job_resource_leases WHERE job_id=? AND released_at IS NULL LIMIT 1",
                    (item["job_id"],),
                ).fetchone()
                if active is not None or resource is not None:
                    raise DomainRuleError("UPSCALE_CLEANUP_ACTIVE_LEASE", "任务已恢复运行，不能清理其中间文件")
                path = controlled_path(
                    self.settings.work_root,
                    str(item["rel_path"]),
                    must_exist=True,
                    code="UPSCALE_CLEANUP_PATH_UNSAFE",
                )
                current_size, unsafe = _tree_size_without_links(path)
                if unsafe or current_size != int(item["byte_size"]):
                    raise DomainRuleError("UPSCALE_CLEANUP_PLAN_STALE", "中间文件已变化，请重新预览")
                validated.append((dict(item), path))
            # Validate the complete frozen set before the first destructive
            # operation.  Holding the immediate transaction prevents retry
            # from changing a Job back to QUEUED between validation and delete.
            for item, path in validated:
                shutil.rmtree(path)
                deleted.append(item)
        return {
            "schema_version": "localdrama.video-upscale-cleanup-result.v1",
            "project_id": project_id,
            "deleted": deleted,
            "deleted_count": len(deleted),
            "released_bytes": sum(int(item["byte_size"]) for item in deleted),
            "mutated": bool(deleted),
            "runtime_contacted": False,
            "network_contacted": False,
        }
