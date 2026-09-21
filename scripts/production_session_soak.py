"""Record resumable wall-clock evidence for one durable production session.

The recorder observes the real SQLite session, linked Jobs, disk reserve and
verified artifacts.  Re-running it with the same output file resumes the same
wall-clock window and records another process invocation.  A short run is
explicitly labelled ``SHORT_REHEARSAL``; only at least 24 observed hours can
set ``real_24h`` to true.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.production_session_runner import (
    ProductionSessionRunner,
)
from local_drama.application.production_sessions import (
    ProductionSessionService,
)
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import (
    Database,
)
from local_drama.infrastructure.filesystem.path_policy import (
    controlled_path,
)

REAL_SOAK_SECONDS = 24 * 60 * 60
MAX_OBSERVED_GAP_INTERVALS = 2
TERMINAL_SESSION_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def _load_evidence(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TypeError("existing soak evidence must be a JSON object")
    return payload


def _all_items(service: ProductionSessionService, session_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    cursor = 0
    while True:
        page = service.list_items(session_id, cursor=cursor, limit=200)
        items.extend(page["items"])
        next_cursor = page.get("next_cursor")
        if next_cursor is None:
            return items
        cursor = int(next_cursor)


def collect_snapshot(
    database: Database,
    settings: Settings,
    service: ProductionSessionService,
    session_id: str,
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    session = service.get(session_id)
    items = _all_items(service, session_id)
    with database.connect() as connection:
        job_states = {
            str(row["state"]): int(row["count"])
            for row in connection.execute(
                """SELECT j.state,COUNT(DISTINCT j.id) AS count
                   FROM production_session_job_links l JOIN jobs j ON j.id=l.job_id
                   WHERE l.session_id=? AND l.link_state='ACTIVE' GROUP BY j.state""",
                (session_id,),
            ).fetchall()
        }
        job_link_count = int(
            connection.execute(
                """SELECT COUNT(*) FROM production_session_job_links
                   WHERE session_id=? AND link_state='ACTIVE'""",
                (session_id,),
            ).fetchone()[0]
        )
        verified_artifact_count = int(
            connection.execute(
                """SELECT COUNT(DISTINCT a.id) FROM production_session_job_links l
                   JOIN job_attempts ja ON ja.job_id=l.job_id
                   JOIN artifacts a ON a.job_attempt_id=ja.id
                   WHERE l.session_id=? AND l.link_state='ACTIVE'
                     AND a.status='VERIFIED'""",
                (session_id,),
            ).fetchone()[0]
        )
    disk = shutil.disk_usage(settings.work_root)
    item_states: dict[str, int] = {}
    item_stages: dict[str, int] = {}
    for item in items:
        state = str(item["state"])
        stage = str(item["current_stage"])
        item_states[state] = item_states.get(state, 0) + 1
        item_stages[stage] = item_stages.get(stage, 0) + 1
    return {
        "observed_at": _iso(observed_at),
        "session_status": str(session["status"]),
        "session_stage": str(session["current_stage"]),
        "session_revision": int(session["revision"]),
        "counters": session["counters"],
        "item_count": len(items),
        "item_states": item_states,
        "item_stages": item_stages,
        "linked_job_count": job_link_count,
        "job_states": job_states,
        "verified_artifact_count": verified_artifact_count,
        "disk_free_bytes": int(disk.free),
    }


def verify_linked_artifacts(
    database: Database, settings: Settings, session_id: str
) -> dict[str, Any]:
    with database.connect() as connection:
        rows = connection.execute(
            """SELECT DISTINCT a.id,a.kind,a.sandbox_rel_path,a.sha256,a.status,j.id AS job_id
               FROM production_session_job_links l
               JOIN jobs j ON j.id=l.job_id
               JOIN job_attempts ja ON ja.job_id=j.id
               JOIN artifacts a ON a.job_attempt_id=ja.id
               WHERE l.session_id=? AND l.link_state='ACTIVE'
               ORDER BY a.created_at,a.id""",
            (session_id,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    valid = True
    for row in rows:
        reason: str | None = None
        actual_sha256: str | None = None
        byte_size: int | None = None
        try:
            path = controlled_path(
                settings.work_root,
                str(row["sandbox_rel_path"]),
                must_exist=True,
                require_file=True,
                code="SOAK_ARTIFACT_INVALID",
            )
            actual_sha256 = _sha256(path)
            byte_size = path.stat().st_size
            if actual_sha256 != str(row["sha256"]):
                reason = "SHA256_MISMATCH"
        except (DomainRuleError, OSError) as error:
            reason = type(error).__name__
        item_valid = str(row["status"]) == "VERIFIED" and reason is None
        valid = valid and item_valid
        items.append(
            {
                "artifact_id": str(row["id"]),
                "job_id": str(row["job_id"]),
                "kind": str(row["kind"]),
                "sandbox_rel_path": str(row["sandbox_rel_path"]),
                "recorded_sha256": str(row["sha256"]),
                "actual_sha256": actual_sha256,
                "byte_size": byte_size,
                "valid": item_valid,
                "reason": reason,
            }
        )
    return {"valid": valid, "count": len(items), "items": items}


def run_soak(
    *,
    settings: Settings,
    database: Database,
    session_id: str,
    output: Path,
    duration_seconds: float,
    sample_interval_seconds: float,
    max_samples: int,
    drive_reconcile: bool,
    clock: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if duration_seconds < 0:
        raise ValueError("duration_seconds must be non-negative")
    if not 1 <= sample_interval_seconds <= 3600:
        raise ValueError("sample_interval_seconds must be between 1 and 3600")
    if max_samples < 2:
        raise ValueError("max_samples must be at least 2")
    now_fn = clock or (lambda: datetime.now(UTC))
    service = ProductionSessionService(database)
    session = service.get(session_id)
    existing = _load_evidence(output)
    now = now_fn()
    if existing is None:
        evidence: dict[str, Any] = {
            "schema_version": "localdrama.production-session-soak.v1",
            "session_id": session_id,
            "project_id": str(session["project_id"]),
            "requested_duration_seconds": duration_seconds,
            "started_at": _iso(now),
            "last_observed_at": _iso(now),
            "elapsed_wall_clock_seconds": 0.0,
            "observed_duration_seconds": 0.0,
            "classification": "REAL_24H" if duration_seconds >= REAL_SOAK_SECONDS else "SHORT_REHEARSAL",
            "real_24h": False,
            "status": "IN_PROGRESS",
            "invocations": [],
            "samples": [],
            "observed_totals": {
                "max_linked_job_count": 0,
                "max_verified_artifact_count": 0,
                "non_ready_session_observed": False,
            },
            "artifact_verification": None,
            "database_integrity": None,
        }
    else:
        evidence = existing
        if str(evidence.get("session_id")) != session_id:
            raise ValueError("existing evidence belongs to another production session")
        if float(evidence.get("requested_duration_seconds", -1)) != duration_seconds:
            raise ValueError("existing evidence uses another requested duration")
        evidence.setdefault("observed_duration_seconds", 0.0)
    invocation = {
        "invocation_id": str(uuid.uuid4()),
        "pid": os.getpid(),
        "started_at": _iso(now),
        "finished_at": None,
    }
    evidence.setdefault("invocations", []).append(invocation)
    evidence["restart_count"] = max(0, len(evidence["invocations"]) - 1)
    runner = ProductionSessionRunner(database, settings) if drive_reconcile else None

    try:
        while True:
            now = now_fn()
            if runner is not None:
                current = service.get(session_id)
                if str(current["status"]) not in TERMINAL_SESSION_STATES | {
                    "READY",
                    "PAUSED",
                    "WAITING_USER",
                }:
                    runner.reconcile(session_id, actor="production-session-soak")
            sample = collect_snapshot(
                database, settings, service, session_id, observed_at=now
            )
            previous_observed_at = _parse(str(evidence["last_observed_at"]))
            observed_gap = max(0.0, (now - previous_observed_at).total_seconds())
            accepted_gap = min(
                observed_gap,
                sample_interval_seconds * MAX_OBSERVED_GAP_INTERVALS,
            )
            evidence["observed_duration_seconds"] = float(
                evidence.get("observed_duration_seconds") or 0.0
            ) + accepted_gap
            samples = list(evidence.get("samples") or [])
            samples.append(sample)
            evidence["samples"] = samples[-max_samples:]
            observed_totals = dict(evidence.get("observed_totals") or {})
            observed_totals["max_linked_job_count"] = max(
                int(observed_totals.get("max_linked_job_count") or 0),
                int(sample["linked_job_count"]),
            )
            observed_totals["max_verified_artifact_count"] = max(
                int(observed_totals.get("max_verified_artifact_count") or 0),
                int(sample["verified_artifact_count"]),
            )
            observed_totals["non_ready_session_observed"] = bool(
                observed_totals.get("non_ready_session_observed")
                or sample["session_status"] != "READY"
            )
            evidence["observed_totals"] = observed_totals
            elapsed = max(0.0, (now - _parse(str(evidence["started_at"]))).total_seconds())
            evidence["last_observed_at"] = _iso(now)
            evidence["elapsed_wall_clock_seconds"] = elapsed
            evidence["last_session_status"] = sample["session_status"]
            _atomic_write(output, evidence)
            completion_progress = (
                float(evidence["observed_duration_seconds"])
                if duration_seconds >= REAL_SOAK_SECONDS
                else elapsed
            )
            if completion_progress >= duration_seconds:
                break
            sleeper(min(sample_interval_seconds, duration_seconds - completion_progress))
    except KeyboardInterrupt:
        invocation["finished_at"] = _iso(now_fn())
        evidence["status"] = "IN_PROGRESS"
        evidence["interrupted"] = True
        _atomic_write(output, evidence)
        return evidence

    finished = now_fn()
    invocation["finished_at"] = _iso(finished)
    integrity = "unknown"
    with database.connect() as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    artifacts = verify_linked_artifacts(database, settings, session_id)
    elapsed = max(0.0, (finished - _parse(str(evidence["started_at"]))).total_seconds())
    observed_duration = float(evidence.get("observed_duration_seconds") or 0.0)
    real_24h = (
        duration_seconds >= REAL_SOAK_SECONDS
        and observed_duration >= REAL_SOAK_SECONDS
    )
    observed_totals = dict(evidence.get("observed_totals") or {})
    production_activity_valid = bool(
        observed_totals.get("non_ready_session_observed")
        and int(observed_totals.get("max_linked_job_count") or 0) > 0
        and int(artifacts["count"]) > 0
    )
    passed = bool(
        integrity == "ok"
        and artifacts["valid"]
        and (
            observed_duration >= duration_seconds
            if duration_seconds >= REAL_SOAK_SECONDS
            else elapsed >= duration_seconds
        )
        and (not real_24h or production_activity_valid)
    )
    limitations: list[str] = []
    if not real_24h:
        limitations.append("本次墙钟时长不足 24 小时，不能作为真实 24 小时 soak 证据。")
    elif not production_activity_valid:
        limitations.append(
            "观察期没有同时证明会话离开 READY、产生关联 Job 和保留至少一个可复核产物，"
            "不能作为真实生产长跑通过证据。"
        )
    evidence.update(
        {
            "last_observed_at": _iso(finished),
            "elapsed_wall_clock_seconds": elapsed,
            "observed_duration_seconds": observed_duration,
            "observation_coverage_ratio": (
                min(1.0, observed_duration / duration_seconds)
                if duration_seconds > 0
                else 1.0
            ),
            "database_integrity": integrity,
            "artifact_verification": artifacts,
            "real_24h": real_24h,
            "production_activity_valid": production_activity_valid,
            "status": (
                "PASS_REAL_24H"
                if passed and real_24h
                else "PASS_SHORT_REHEARSAL"
                if passed
                else "FAILED"
            ),
            "interrupted": False,
            "limitations": limitations,
        }
    )
    _atomic_write(output, evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--sample-interval-seconds", type=float, default=60.0)
    parser.add_argument("--max-samples", type=int, default=2000)
    parser.add_argument(
        "--drive-reconcile",
        action="store_true",
        help="also call the idempotent session reconciler; normally the worker supervisor does this",
    )
    args = parser.parse_args()
    settings = Settings.from_env()
    settings.ensure_roots()
    database = Database(settings.database_path)
    result = run_soak(
        settings=settings,
        database=database,
        session_id=args.session_id,
        output=args.output.resolve(),
        duration_seconds=args.duration_hours * 3600,
        sample_interval_seconds=args.sample_interval_seconds,
        max_samples=args.max_samples,
        drive_reconcile=args.drive_reconcile,
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "real_24h": result["real_24h"],
                "elapsed_wall_clock_seconds": result["elapsed_wall_clock_seconds"],
                "observed_duration_seconds": result.get(
                    "observed_duration_seconds", 0.0
                ),
                "production_activity_valid": result.get(
                    "production_activity_valid", False
                ),
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
        )
    )
    if result["status"] == "IN_PROGRESS":
        return 130
    return 0 if str(result["status"]).startswith("PASS_") else 1


if __name__ == "__main__":
    raise SystemExit(main())
