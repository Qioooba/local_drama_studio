import json
import sys
import time
from pathlib import Path

repo_root = Path(__file__).resolve().parents[4] if "antigravity" in str(Path(__file__)) else Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo_root / "apps" / "api"))
sys.path.insert(0, str(repo_root))

from local_drama.application.comfy_jobs import ComfyGenerationService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database


def run_worker(timeout_seconds=300):
    settings = Settings.from_env()
    settings.comfy_input_root = (repo_root / "work" / "comfy-production" / "input").resolve()
    settings.comfy_output_root = (repo_root / "work" / "comfy-production" / "output").resolve()
    
    db = Database(settings.database_path)
    service = ComfyGenerationService(db, settings)
    
    print(f"Checking for queued GPU_H3 jobs in database: {settings.database_path}")
    submission = None
    for _ in range(20):
        try:
            submission = service.submit_next("real-e2e-worker")
            if submission is not None:
                break
        except Exception as err:  # noqa: BLE001 - bounded CLI worker skips one unclaimable queued job
            print(f"Skipping unrunnable job: {err}")
            continue

    if submission is None:
        print(json.dumps({"status": "NO_JOBS", "message": "No claimable GPU_H3 jobs found in queue."}))
        return 0

    attempt_id = str(submission["attempt"]["id"])
    prompt_id = str(submission["prompt_id"])
    job_id = str(submission["job"]["id"])
    print(f"Claimed job {job_id}, prompt_id: {prompt_id}, attempt_id: {attempt_id}")

    deadline = time.monotonic() + timeout_seconds
    poll_states = []
    result = None

    while time.monotonic() < deadline:
        try:
            result = service.poll_attempt(attempt_id, "real-e2e-worker")
            state = str(result.get("status"))
            poll_states.append(state)
            print(f"Polling state: {state}")
            if state in {"SUCCEEDED", "FAILED"}:
                break
        except Exception as e:  # noqa: BLE001 - provider/runtime failures are reported by this CLI boundary
            print(f"Poll attempt exception: {e}")
        time.sleep(4)

    output = {
        "status": result.get("status") if result else "TIMEOUT",
        "job_id": job_id,
        "attempt_id": attempt_id,
        "prompt_id": prompt_id,
        "poll_states": poll_states,
        "result": result,
    }
    print("WORKER_RESULT_JSON:" + json.dumps(output, ensure_ascii=False))
    return 0 if output["status"] == "SUCCEEDED" else 1

if __name__ == "__main__":
    sys.exit(run_worker())
