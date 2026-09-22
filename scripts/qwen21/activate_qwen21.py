"""Activate Qwen-Image-2.1 Profiles through the real Worker and ComfyUI.

``onboard_qwen21.py`` leaves every binding at ``SCHEMA_VALIDATED``.  A V2 Profile
can only be published on top of a capability smoke that *actually produced a
registered artifact*, so this script:

``submit``   queues one durable ``MODEL_PLATFORM_COMFY_SMOKE`` Job per binding
``run``      drives a real ``LocalMediaWorker`` until those Jobs settle.  The
             worker takes the shared ``GPU:0:EXCLUSIVE`` lease, so a second
             heavy GPU task cannot interleave on the same card.
``publish``  provisions the V2 Profile, records its smoke and publishes it
``all``      submit -> run -> publish
``status``   read-only summary

Nothing here fabricates evidence: if ComfyUI fails, the Job fails and the
Profile is not published.

The activation must target the 2.1 runtime, so ``--comfy-base-url`` defaults to
the pinned 8189 endpoint and is applied to ``Settings`` for the whole run.  That
also makes the GPU coordinator's eviction adapter and the smoke worker talk to
the same ComfyUI instance.

Example::

    python scripts/qwen21/activate_qwen21.py --all
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "apps" / "api") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "apps" / "api"))

from local_drama.application.gpu_runtime import GpuRuntimeCoordinator  # noqa: E402
from local_drama.application.job_resources import GPU_EXCLUSIVE_RESOURCE as GPU_RESOURCE_KEY  # noqa: E402
from local_drama.application.model_licensing import assert_may_publish  # noqa: E402
from local_drama.application.worker import LocalMediaWorker  # noqa: E402
from local_drama.config import Settings  # noqa: E402
from local_drama.domain.errors import DomainRuleError  # noqa: E402
from local_drama.infrastructure.database.sqlite import Database  # noqa: E402
from local_drama.model_platform.application.comfy_capability_smoke_jobs import ComfyCapabilitySmokeSubmissionService  # noqa: E402
from local_drama.model_platform.application.comfy_workflow_profiles import ComfyWorkflowProfileService  # noqa: E402
from local_drama.model_platform.application.profile_publication import ProfilePublicationService  # noqa: E402

from onboard_qwen21 import DEFINITION_PLAN, MODEL_CODE, RUNTIME_CODE  # noqa: E402

SMOKE_JOB_TYPE = "MODEL_PLATFORM_COMFY_SMOKE"
WORKER_CHANNEL = "GPU_H3"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _release_installation(database: Database) -> str:
    with database.connect() as connection:
        row = connection.execute(
            """SELECT installation.id FROM mp_runtime_model_installations installation
               JOIN mp_model_releases release ON release.id=installation.release_id
               JOIN mp_runtime_installation_versions version ON version.id=installation.runtime_installation_version_id
               JOIN mp_runtime_installations runtime ON runtime.id=version.runtime_installation_id
               WHERE release.code=? AND runtime.code=?
               ORDER BY installation.created_at DESC,installation.id DESC LIMIT 1""",
            (MODEL_CODE, RUNTIME_CODE),
        ).fetchone()
    if row is None:
        raise DomainRuleError("QWEN21_INSTALLATION_NOT_FOUND", "尚未登记 Qwen-Image-2.1 运行时模型安装，请先运行 onboard_qwen21.py。")
    return str(row["id"])


def _binding_for(database: Database, installation_id: str, workflow_version_id: str) -> str:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT id FROM mp_runtime_model_workflow_bindings WHERE runtime_model_installation_id=? AND workflow_version_id=?",
            (installation_id, workflow_version_id),
        ).fetchone()
    if row is None:
        raise DomainRuleError("QWEN21_BINDING_NOT_FOUND", "尚未绑定该工作流版本，请先运行 onboard_qwen21.py 的 bind 阶段。", {"workflow_version_id": workflow_version_id})
    return str(row["id"])


def _workflow_version_id(database: Database, definition_code: str) -> str:
    with database.connect() as connection:
        row = connection.execute(
            "SELECT wv.id FROM workflow_versions wv JOIN workflows w ON w.id=wv.workflow_id WHERE w.code=? AND wv.status='PUBLISHED' ORDER BY wv.version_no DESC LIMIT 1",
            (definition_code,),
        ).fetchone()
    if row is None:
        raise DomainRuleError("QWEN21_WORKFLOW_NOT_PUBLISHED", "该定义尚无已发布版本。", {"definition_code": definition_code})
    return str(row["id"])


def _smoke_passed(database: Database, binding_id: str) -> dict[str, Any] | None:
    """Return the artifact-backed CAPABILITY_SMOKE run for this binding, if any."""

    with database.connect() as connection:
        rows = connection.execute(
            """SELECT run.id,run.result_json,run.finished_at FROM mp_validation_runs run
               JOIN mp_capability_offerings offering ON offering.id=run.target_id
               WHERE run.target_kind='CAPABILITY_OFFERING' AND run.validation_kind='CAPABILITY_SMOKE'
                 AND run.status='SMOKE_PASSED'
                 AND EXISTS (SELECT 1 FROM mp_validation_evidence evidence
                             WHERE evidence.validation_run_id=run.id AND evidence.artifact_ref IS NOT NULL)
               ORDER BY run.finished_at DESC,run.id DESC""",
        ).fetchall()
    for row in rows:
        try:
            result = json.loads(str(row["result_json"]))
        except ValueError:
            continue
        if result.get("adapter") == "comfy.workflow.smoke.v1" and str(result.get("workflow_binding_id")) == binding_id:
            return {"validation_run_id": str(row["id"]), "finished_at": row["finished_at"], "result": result}
    return None


def _pending_smoke_jobs(database: Database) -> list[dict[str, Any]]:
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id,state FROM jobs WHERE type=? AND state IN ('QUEUED','RUNNING','RETRYING') ORDER BY created_at",
            (SMOKE_JOB_TYPE,),
        ).fetchall()
    return [dict(row) for row in rows]


# ------------------------------------------------------------------- actions --
def _smoke_key(database: Database, binding_id: str) -> str:
    """Idempotency key that changes only after a terminal failure.

    A replay while the Job is queued or running must return the same Job, but a
    retry after a failed attempt needs a fresh one -- ``max_attempts=1`` means a
    failed smoke can never be resumed in place.
    """

    with database.connect() as connection:
        attempts, failures = connection.execute(
            """SELECT COUNT(*) AS attempts,
                      COALESCE(SUM(CASE WHEN state='FAILED' THEN 1 ELSE 0 END),0) AS failures
               FROM jobs WHERE type=? AND subject_id=?""",
            (SMOKE_JOB_TYPE, binding_id),
        ).fetchone()
    return f"qwen21-smoke-{binding_id}-{int(attempts)}-{int(failures)}"


def cmd_submit(database: Database, settings: Settings, bindings: list[dict[str, Any]]) -> dict[str, Any]:
    service = ComfyCapabilitySmokeSubmissionService(database, settings)
    submitted: list[dict[str, Any]] = []
    for item in bindings:
        binding_id = str(item["workflow_binding_id"])
        if _smoke_passed(database, binding_id) is not None:
            submitted.append({**item, "status": "ALREADY_SMOKE_PASSED"})
            continue
        try:
            result = service.submit(binding_id, _smoke_key(database, binding_id))
        except DomainRuleError as error:
            submitted.append({**item, "status": "SUBMIT_FAILED", "error_code": error.code, "message": error.message})
            continue
        submitted.append({**item, "status": "ALREADY_QUEUED" if result.idempotent_replay else "QUEUED", "job_id": result.job_id})
    return {"action": "submit", "runs": submitted}


def cmd_run(database: Database, settings: Settings, *, max_seconds: float, poll_seconds: float) -> dict[str, Any]:
    # The smoke job is a GPU-heavy task on a single card.  A previous run that
    # left weights resident on the *target* runtime (for example a direct
    # /prompt probe, which never takes the coordinator lease) would otherwise
    # make the VRAM gate refuse every attempt with GPU_VRAM_NOT_RELEASED, and a
    # stale DEGRADED row would keep the coordinator from re-adopting the device.
    released = _release_target_vram(settings)
    reconciled = _reconcile_degraded_state(database)
    coordinator = GpuRuntimeCoordinator(database, settings)
    worker = LocalMediaWorker(database, settings, gpu_coordinator=coordinator)
    worker_id = f"qwen21-activation-{int(time.time())}"
    deadline = time.monotonic() + max_seconds
    processed: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        pending = _pending_smoke_jobs(database)
        if not pending:
            break
        outcome = worker.run_once(worker_id, [WORKER_CHANNEL], job_types=[SMOKE_JOB_TYPE])
        if outcome is None:
            # Another worker may own the job, or the GPU lease is held.
            time.sleep(poll_seconds)
            continue
        attempt = (outcome.get("poll") or {})
        processed.append(
            {
                "job_id": str(outcome["job"]["id"]),
                "status": str(attempt.get("status") or "UNKNOWN"),
                "error_code": attempt.get("error_code"),
                "error_detail": attempt.get("error_detail_redacted"),
            }
        )
    remaining = _pending_smoke_jobs(database)
    return {
        "action": "run",
        "released_target_vram": released,
        "reconciled_degraded_state": reconciled,
        "processed": processed,
        "remaining": len(remaining),
        "timed_out": bool(remaining),
    }


def _release_target_vram(settings: Settings) -> dict[str, Any]:
    """Best-effort unload of the target ComfyUI runtime before the run."""

    from local_drama.infrastructure.comfy import ComfyClient

    client = ComfyClient(settings.comfy_base_url, settings.comfy_output_root, allow_private_network=False)
    try:
        client.free_memory(unload_models=True, free_memory=True)
    except Exception as error:  # noqa: BLE001 - the gate below is the real check
        return {"requested": False, "error": f"{type(error).__name__}: {error}"}
    return {"requested": True, "base_url": settings.comfy_base_url}


def _reconcile_degraded_state(database: Database) -> bool:
    """Clear a stale DEGRADED/resident claim so the coordinator re-evaluates.

    The coordinator treats a DEGRADED device as retryable ("the next switch sees
    DEGRADED and retries strict eviction"), but it also trusts
    ``resident_runtime``; when that claim is stale it skips evicting the runtime
    that actually holds the memory.  Only the claim is cleared -- the VRAM gate
    still decides, so this cannot start a task on an occupied device.
    """

    with database.transaction() as connection:
        cursor = connection.execute(
            """UPDATE gpu_runtime_state SET resident_runtime=NULL,status='IDLE',
               last_error_code=NULL,last_error_detail_redacted=NULL,updated_at=?
               WHERE resource_key=? AND status='DEGRADED'""",
            (_now(), GPU_RESOURCE_KEY),
        )
    return cursor.rowcount > 0


def cmd_publish(
    database: Database,
    settings: Settings,
    installation_id: str,
    bindings: list[dict[str, Any]],
    *,
    acknowledge_research_only: bool = False,
) -> dict[str, Any]:
    # Qwen-Image-2.1 is Qwen Research Licensed: without a recorded commercial
    # authorization it must not become the production default.  Checked before
    # any profile is provisioned so a blocked run leaves no half-published state.
    licence = assert_may_publish(
        settings.environment,
        MODEL_CODE,
        acknowledged=acknowledge_research_only,
    )
    profiles = ComfyWorkflowProfileService(database, settings)
    publication = ProfilePublicationService(database)
    results: list[dict[str, Any]] = []
    for item in bindings:
        binding_id = str(item["workflow_binding_id"])
        smoke = _smoke_passed(database, binding_id)
        record: dict[str, Any] = {**item}
        if smoke is None:
            record.update(status="BLOCKED_NO_SMOKE_EVIDENCE")
            results.append(record)
            continue
        suffix = item.get("profile_code_suffix")
        try:
            provisioned = profiles.provision(installation_id, str(item["capability"]), binding_id, profile_code_suffix=suffix)
            smoke_result = profiles.smoke(provisioned.profile_version_id)
            publication.publish(
                provisioned.profile_version_id,
                validation_run_id=smoke_result.validation_run_id,
                reason="Qwen-Image-2.1 acceptance: capability smoke passed with a real artifact",
            )
            record.update(
                status="PUBLISHED",
                profile_code=provisioned.profile_code,
                profile_version_id=provisioned.profile_version_id,
                profile_smoke_validation_run_id=smoke_result.validation_run_id,
                source_smoke_validation_run_id=smoke["validation_run_id"],
            )
        except DomainRuleError as error:
            record.update(status="PUBLISH_FAILED", error_code=error.code, message=error.message)
        results.append(record)
    return {"action": "publish", "license": licence, "profiles": results}


def cmd_status(database: Database, installation_id: str, bindings: list[dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for item in bindings:
        smoke = _smoke_passed(database, str(item["workflow_binding_id"]))
        rows.append({**item, "smoke_passed": smoke is not None, "smoke": smoke})
    with database.connect() as connection:
        published = connection.execute(
            """SELECT profile.code,version.version_no,publication.status
               FROM mp_profile_publications publication
               JOIN mp_execution_profile_versions version ON version.id=publication.execution_profile_version_id
               JOIN mp_execution_profiles profile ON profile.id=version.profile_id
               WHERE publication.status='PUBLISHED' AND profile.code LIKE ?
               ORDER BY profile.code,version.version_no""",
            (f"comfy-{MODEL_CODE}%",),
        ).fetchall()
    return {"action": "status", "bindings": rows, "published_profiles": [dict(row) for row in published]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("submit", "run", "publish", "all", "status"))
    parser.add_argument("--pin", type=Path, default=_REPO_ROOT / "config" / "comfyui-qwen21-runtime.json")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--comfy-base-url", help="2.1 ComfyUI endpoint; defaults to the pinned 8189 runtime.")
    parser.add_argument("--max-seconds", type=float, default=3600.0, help="Budget for the 'run' action.")
    parser.add_argument("--poll-seconds", type=float, default=3.0)
    parser.add_argument(
        "--acknowledge-research-only",
        action="store_true",
        help="Explicitly accept research/evaluation-only scope so a non-production instance may publish 2.1 Profiles.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    pin = json.loads(args.pin.read_text(encoding="utf-8"))
    base_url = args.comfy_base_url or f"http://{pin['host']}:{pin['port']}"
    # The isolated runtime owns its own input/output directories, so the whole
    # activation must address the same instance end to end: the coordinator's
    # eviction adapter, the smoke worker's ComfyClient, and the artifact
    # collection all read these settings.
    io_root = Path(str(pin["io_root"]))
    # from_env() is what the service actually uses: it loads config/config.json,
    # so `environment` reflects the real deployment.  A bare Settings() defaults
    # to "development", which would silently defeat the licence gate.
    settings = Settings.from_env()
    overrides: dict[str, Any] = {
        "comfy_base_url": base_url,
        "comfy_input_root": io_root / "input",
        "comfy_output_root": io_root / "output",
    }
    if args.database:
        overrides["database_path"] = args.database
    settings = settings.model_copy(update=overrides)
    database = Database(settings.database_path)

    installation_id = _release_installation(database)
    bindings = [
        {
            "definition_code": definition_code,
            "capability": capability,
            "profile_code_suffix": suffix,
            "workflow_binding_id": _binding_for(database, installation_id, _workflow_version_id(database, definition_code)),
        }
        for definition_code, capability, suffix in DEFINITION_PLAN
    ]

    report: dict[str, Any] = {
        "schema_version": "localdrama.qwen21-activation.v1",
        "generated_at": _now(),
        "comfy_base_url": base_url,
        "runtime_model_installation_id": installation_id,
        "actions": [],
    }
    if args.command in {"submit", "all"}:
        report["actions"].append(cmd_submit(database, settings, bindings))
    if args.command in {"run", "all"}:
        report["actions"].append(cmd_run(database, settings, max_seconds=args.max_seconds, poll_seconds=args.poll_seconds))
    if args.command in {"publish", "all"}:
        report["actions"].append(
            cmd_publish(
                database,
                settings,
                installation_id,
                bindings,
                acknowledge_research_only=args.acknowledge_research_only,
            )
        )
    if args.command == "status":
        report["actions"].append(cmd_status(database, installation_id, bindings))

    profiles = next((action for action in report["actions"] if action["action"] == "publish"), None)
    report["passed"] = bool(profiles) and all(item["status"] == "PUBLISHED" for item in profiles["profiles"])
    if args.command == "status":
        report["passed"] = True
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
