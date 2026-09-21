"""Capture tamper-evident evidence for a real production-session UAT.

The collector is read-only.  It records the durable session/review projection,
jobs and attempts created after the session, verifies every linked artifact and
selected media file against its stored SHA256, and checks ComfyUI history for
durable provider prompt ids.  It never creates approvals or repairs data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "api"))

from local_drama.application.production_session_review import (
    ProductionSessionReviewService,
)
from local_drama.application.production_sessions import ProductionSessionService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-root", type=Path, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--pipeline-run-id")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _file_fact(path: Path, expected_sha256: str | None) -> dict[str, Any]:
    exists = path.is_file()
    actual = _sha256(path) if exists else None
    return {
        "path": str(path),
        "exists": exists,
        "byte_size": path.stat().st_size if exists else None,
        "expected_sha256": expected_sha256,
        "actual_sha256": actual,
        "sha256_match": bool(exists and expected_sha256 and actual == expected_sha256),
    }


def _json(value: object) -> object:
    try:
        return json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _comfy_history(base_url: str, prompt_id: str) -> dict[str, Any]:
    try:
        with urlopen(f"{base_url.rstrip('/')}/history/{prompt_id}", timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        item = payload.get(prompt_id) or {}
        status = item.get("status") or {}
        outputs = item.get("outputs") or {}
        return {
            "prompt_id": prompt_id,
            "reachable": True,
            "status": status.get("status_str"),
            "completed": status.get("completed"),
            "output_node_count": len(outputs),
        }
    except (OSError, TimeoutError, ValueError, URLError) as error:
        return {
            "prompt_id": prompt_id,
            "reachable": False,
            "error": type(error).__name__,
        }


def _video_probe(path: Path, ffprobe_path: str | None) -> dict[str, Any]:
    if not ffprobe_path or not path.is_file():
        return {"path": str(path), "available": False}
    completed = subprocess.run(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if completed.returncode != 0:
        return {
            "path": str(path),
            "available": False,
            "returncode": completed.returncode,
        }
    payload = json.loads(completed.stdout)
    return {
        "path": str(path),
        "available": True,
        "duration_seconds": float((payload.get("format") or {}).get("duration") or 0),
        "streams": [
            {
                key: stream.get(key)
                for key in (
                    "codec_type",
                    "codec_name",
                    "width",
                    "height",
                    "r_frame_rate",
                    "sample_rate",
                    "channels",
                )
                if stream.get(key) is not None
            }
            for stream in payload.get("streams") or []
        ],
    }
def capture(args: argparse.Namespace) -> dict[str, Any]:
    instance_root = args.instance_root.expanduser().resolve()
    previous_instance = os.environ.get("LOCAL_DRAMA_INSTANCE_ROOT")
    os.environ["LOCAL_DRAMA_INSTANCE_ROOT"] = str(instance_root)
    try:
        settings = Settings.from_env()
        database = Database(settings.database_path)
        sessions = ProductionSessionService(database)
        session = sessions.get(args.session_id)
        items = sessions.list_items(args.session_id, cursor=0, limit=100)
        review = ProductionSessionReviewService(database).inspect(
            args.session_id,
            cursor=0,
            limit=100,
        )

        with database.connect() as connection:
            project = connection.execute(
                "SELECT id,code,root_rel FROM projects WHERE id=?",
                (session["project_id"],),
            ).fetchone()
            assert project is not None
            jobs = [
                dict(row)
                for row in connection.execute(
                    """SELECT id,type,state,channel,subject_type,subject_id,stage_code,
                              last_error_code,created_at,updated_at,started_at,finished_at
                       FROM jobs
                       WHERE project_id=? AND created_at>=?
                       ORDER BY created_at,id""",
                    (session["project_id"], session["created_at"]),
                ).fetchall()
            ]
            current_run_ids = sorted(
                {
                    str((item.get("progress") or {}).get("episode_run_id"))
                    for item in items.get("items") or []
                    if (item.get("progress") or {}).get("episode_run_id")
                }
            )
            current_run_rows: list[dict[str, Any]] = []
            if current_run_ids:
                marks = ",".join("?" for _ in current_run_ids)
                current_run_rows = [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM automation_workflow_runs WHERE id IN ({marks}) ORDER BY created_at,id",
                        current_run_ids,
                    ).fetchall()
                ]
            terminal_window_start = min(
                (str(row["created_at"]) for row in current_run_rows),
                default=str(session["created_at"]),
            )
            terminal_jobs = [
                row for row in jobs if str(row["created_at"]) >= terminal_window_start
            ]
            job_ids = [str(row["id"]) for row in jobs]
            attempts: list[dict[str, Any]] = []
            artifacts: list[dict[str, Any]] = []
            if job_ids:
                marks = ",".join("?" for _ in job_ids)
                attempts = [
                    dict(row)
                    for row in connection.execute(
                        f"""SELECT id,job_id,attempt_no,state,provider_job_id,comfy_prompt_id,
                                   error_code,created_at,updated_at,started_at,finished_at
                            FROM job_attempts WHERE job_id IN ({marks})
                            ORDER BY created_at,id""",
                        job_ids,
                    ).fetchall()
                ]
                attempt_ids = [str(row["id"]) for row in attempts]
                if attempt_ids:
                    attempt_marks = ",".join("?" for _ in attempt_ids)
                    artifacts = [
                        dict(row)
                        for row in connection.execute(
                            f"""SELECT * FROM artifacts WHERE job_attempt_id IN ({attempt_marks})
                                ORDER BY created_at,id""",
                            attempt_ids,
                        ).fetchall()
                    ]
            choices = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM production_choices WHERE session_id=? ORDER BY created_at,id",
                    (args.session_id,),
                ).fetchall()
            ]
            candidate_ids = [str(row["candidate_id"]) for row in choices]
            media_rows: list[dict[str, Any]] = []
            if candidate_ids:
                marks = ",".join("?" for _ in candidate_ids)
                media_rows = [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM media_versions WHERE id IN ({marks}) ORDER BY created_at,id",
                        candidate_ids,
                    ).fetchall()
                ]
            human_decision_count = int(
                connection.execute(
                    """SELECT COUNT(*) FROM production_choices
                       WHERE session_id=? AND human_review_decision_id IS NOT NULL""",
                    (args.session_id,),
                ).fetchone()[0]
            )
            pipeline: dict[str, Any] | None = None
            pipeline_idempotencies: list[dict[str, Any]] = []
            if args.pipeline_run_id:
                pipeline_row = connection.execute(
                    "SELECT * FROM pipeline_runs WHERE id=?",
                    (args.pipeline_run_id,),
                ).fetchone()
                pipeline = dict(pipeline_row) if pipeline_row is not None else None
                pipeline_idempotencies = [
                    dict(row)
                    for row in connection.execute(
                        """SELECT * FROM command_idempotencies
                           WHERE idempotency_key IN (?,?)
                           ORDER BY idempotency_key""",
                        (
                            f"pipeline:{args.pipeline_run_id}:production-session",
                            f"pipeline:{args.pipeline_run_id}:production-session:start",
                        ),
                    ).fetchall()
                ]
            identity_inputs = [
                dict(row)
                for row in connection.execute(
                    """SELECT * FROM production_session_identity_inputs
                       WHERE session_id=? ORDER BY created_at,id""",
                    (args.session_id,),
                ).fetchall()
            ]
            human_pack_approval_count = int(
                connection.execute(
                    """SELECT COUNT(*) FROM audit_events ae
                       JOIN character_identity_pack_versions pv ON pv.id=ae.subject_id
                       WHERE ae.action='CHARACTER_IDENTITY_PACK_APPROVED'
                         AND pv.project_id=? AND ae.occurred_at>=?""",
                    (session["project_id"], session["created_at"]),
                ).fetchone()[0]
            )

        project_root = settings.projects_root / str(project["root_rel"])
        artifact_files = [
            {
                "artifact_id": str(row["id"]),
                "job_attempt_id": str(row["job_attempt_id"]),
                **_file_fact(
                    settings.work_root / str(row["sandbox_rel_path"]),
                    str(row["sha256"]) if row["sha256"] else None,
                ),
            }
            for row in artifacts
        ]
        media_files = [
            {
                "media_version_id": str(row["id"]),
                "integrity_status": str(row["integrity_status"]),
                **_file_fact(
                    project_root / str(row["rel_path"]),
                    str(row["sha256"]) if row["sha256"] else None,
                ),
            }
            for row in media_rows
        ]
        preview_rows = [
            dict(item["preview_render"])
            for item in review.get("items") or []
            if isinstance(item.get("preview_render"), dict)
        ]
        preview_files = [
            {
                "render_version_id": str(row["id"]),
                "episode_id": str(row["episode_id"]),
                "integrity_status": str(row["integrity_status"]),
                **_file_fact(
                    project_root / str(row["rel_path"]),
                    str(row["sha256"]) if row.get("sha256") else None,
                ),
            }
            for row in preview_rows
        ]
        preview_probes = [
            _video_probe(project_root / str(row["rel_path"]), settings.ffprobe_path)
            for row in preview_rows
        ]
        prompt_ids = sorted(
            {
                str(row["comfy_prompt_id"] or row["provider_job_id"])
                for row in attempts
                if row["comfy_prompt_id"] or row["provider_job_id"]
            }
        )
        provider_history = [_comfy_history(args.comfy_url, prompt_id) for prompt_id in prompt_ids]
    finally:
        if previous_instance is None:
            os.environ.pop("LOCAL_DRAMA_INSTANCE_ROOT", None)
        else:
            os.environ["LOCAL_DRAMA_INSTANCE_ROOT"] = previous_instance

    failures: list[str] = []
    if session["status"] != "WAITING_REVIEW":
        failures.append("SESSION_NOT_WAITING_REVIEW")
    ready_episodes = sum(
        1
        for item in review.get("items") or []
        if item.get("review_status") == "READY_FOR_HUMAN_REVIEW"
        or (
            item.get("review_status") == "BLOCKED"
            and {b.get("code") for b in item.get("blockers") or []}
            == {"SESSION_ASSET_IDENTITIES_REVIEW_REQUIRED"}
            and item.get("current_stage") == "WAITING_REVIEW"
            and item.get("item_state") in {"WAITING", "COMPLETED"}
        )
    )
    if ready_episodes != int(session["item_count"]):
        failures.append("NOT_ALL_EPISODES_READY_FOR_REVIEW")
    if any(
        row["state"] in {"FAILED", "CANCELLED", "NEEDS_ATTENTION", "ORPHANED"}
        for row in terminal_jobs
    ):
        failures.append("SESSION_JOB_FAILURE_PRESENT")
    if any(not row["sha256_match"] for row in artifact_files):
        failures.append("ARTIFACT_FILE_ATTESTATION_FAILED")
    if any(not row["sha256_match"] or row["integrity_status"] != "VERIFIED" for row in media_files):
        failures.append("SELECTED_MEDIA_ATTESTATION_FAILED")
    if any(not row["sha256_match"] or row["integrity_status"] != "VERIFIED" for row in preview_files):
        failures.append("PREVIEW_RENDER_ATTESTATION_FAILED")
    if any(not row["available"] for row in preview_probes):
        failures.append("PREVIEW_RENDER_PROBE_FAILED")
    if any(not row.get("reachable") or row.get("status") != "success" for row in provider_history):
        failures.append("COMFY_HISTORY_NOT_SUCCESS")
    if human_decision_count:
        failures.append("HUMAN_APPROVAL_WRITTEN")
    if human_pack_approval_count:
        failures.append("HUMAN_IDENTITY_PACK_APPROVAL_WRITTEN")
    pipeline_checks: dict[str, Any] | None = None
    if args.pipeline_run_id:
        pipeline_input = _json((pipeline or {}).get("input_snapshot_json"))
        decoded_responses = [
            _json(row.get("response_json")) for row in pipeline_idempotencies
        ]
        response_session_ids = {
            str((response.get("session") or {}).get("id") or "")
            for response in decoded_responses
            if isinstance(response, dict)
        }
        pipeline_checks = {
            "exists": pipeline is not None,
            "same_project": bool(
                pipeline and str(pipeline["project_id"]) == str(session["project_id"])
            ),
            "succeeded": bool(pipeline and pipeline["state"] == "SUCCEEDED"),
            "applied": bool(pipeline and pipeline["apply_state"] == "APPLIED"),
            "llm_extraction": bool(
                pipeline
                and pipeline["extraction_mode"] == "LLM"
                and pipeline["llm_model"]
                and pipeline["llm_provider"]
                and not pipeline["llm_error"]
            ),
            "authorized_terminal_boundary": (
                (pipeline_input.get("production_authorization") or {}).get("endpoint")
                == "WAITING_REVIEW"
                if isinstance(pipeline_input, dict)
                else False
            ),
            "idempotency_record_count": len(pipeline_idempotencies),
            "idempotency_session_ids": sorted(response_session_ids),
        }
        if not all(
            pipeline_checks[key]
            for key in (
                "exists",
                "same_project",
                "succeeded",
                "applied",
                "llm_extraction",
                "authorized_terminal_boundary",
            )
        ):
            failures.append("PIPELINE_SOURCE_CHAIN_INVALID")
        if len(pipeline_idempotencies) != 2 or response_session_ids != {
            str(session["id"])
        }:
            failures.append("PIPELINE_PRODUCTION_CONTINUATION_INVALID")
    if not identity_inputs:
        failures.append("SESSION_TEMPORARY_IDENTITY_INPUT_MISSING")

    evidence = {
        "schema_version": "localdrama.production-session-uat-evidence.v1",
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "result": (
            "PASS_REAL_RAW_SOURCE_TO_WHOLE_DRAMA_REVIEW"
            if not failures and args.pipeline_run_id
            else "PASS_REAL_WHOLE_DRAMA_TO_REVIEW"
            if not failures
            else "IN_PROGRESS_OR_FAILED"
        ),
        "failures": failures,
        "isolated_instance": str(instance_root),
        "project": dict(project),
        "session": session,
        "items": items,
        "review_projection": review,
        "jobs": jobs,
        "terminal_job_window_start": terminal_window_start,
        "terminal_jobs": terminal_jobs,
        "historical_nonterminal_jobs": [
            row for row in jobs if str(row["created_at"]) < terminal_window_start
        ],
        "current_automation_runs": current_run_rows,
        "attempts": attempts,
        "artifacts": artifacts,
        "artifact_file_attestations": artifact_files,
        "choices": [{**row, "reason": _json(row.pop("reason_json", "{}"))} for row in choices],
        "selected_media": media_rows,
        "selected_media_file_attestations": media_files,
        "preview_render_file_attestations": preview_files,
        "preview_render_probes": preview_probes,
        "provider_history": provider_history,
        "human_approval_written": human_decision_count > 0,
        "human_identity_pack_approval_written": human_pack_approval_count > 0,
        "production_session_identity_inputs": identity_inputs,
        "pipeline_run": pipeline,
        "pipeline_idempotencies": pipeline_idempotencies,
        "pipeline_checks": pipeline_checks,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return evidence


if __name__ == "__main__":
    parsed_args = _args()
    result = capture(parsed_args)
    print(
        json.dumps(
            {
                "result": result["result"],
                "failures": result["failures"],
                "session_id": result["session"]["id"],
                "status": result["session"]["status"],
                "job_count": len(result["jobs"]),
                "attempt_count": len(result["attempts"]),
                "artifact_count": len(result["artifacts"]),
                "choice_count": len(result["choices"]),
                "output": str(parsed_args.output),
            },
            ensure_ascii=False,
        )
    )
