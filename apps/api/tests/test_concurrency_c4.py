from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from tests.test_episode_run_recovery import _complete_with_report, _run


def test_pause_wins_between_recovery_read_and_completion_cas_without_duplicate_task(workspace, database, monkeypatch) -> None:
    service, run, _episode = _run(workspace, database, "concurrency_c4", current_fingerprint=True)
    completed_job_id = _complete_with_report(workspace, database, run)
    run_id = str(run["id"])

    reached_completion_cas = threading.Event()
    release_completion_cas = threading.Event()
    original_step = service.automation.step_run

    def blocked_step(*args, **kwargs):
        reached_completion_cas.set()
        assert release_completion_cas.wait(timeout=10), "test failed to release completion CAS"
        return original_step(*args, **kwargs)

    monkeypatch.setattr(service.automation, "step_run", blocked_step)
    with ThreadPoolExecutor(max_workers=1) as executor:
        recovery_future = executor.submit(service.recover, run_id)
        assert reached_completion_cas.wait(timeout=10), "recovery did not reach completion CAS"
        paused = service.pause(run_id, reason="C4 human pause wins completion race")
        assert paused["status"] == "PAUSED_HITL"
        release_completion_cas.set()
        recovered = recovery_future.result(timeout=10)

    assert recovered["run"]["status"] == "PAUSED_HITL"
    assert recovered["recovery"]["advanced_completed_job"] is False
    assert recovered["recovery"]["completion_deferred_by_pause_job_id"] == completed_job_id
    paused_automation = service.automation.get_run(run_id)
    assert paused_automation["task_count"] == 1
    assert len(paused_automation["tasks"]) == 1
    with database.connect() as connection:
        pause_events = connection.execute(
            "SELECT COUNT(*) FROM automation_workflow_run_events WHERE run_id=? AND event_type='MANUAL_PAUSE'", (run_id,),
        ).fetchone()[0]
        assert pause_events == 1

    # Remove the artificial barrier. Resume invokes recovery and consumes the
    # already-successful Job exactly once; its immutable task/history remains.
    monkeypatch.setattr(service.automation, "step_run", original_step)
    resumed = service.resume(run_id, note="consume persisted completion after C4 pause")
    assert resumed["status"] == "RUNNING"
    final = service.automation.get_run(run_id)
    assert final["task_count"] == 2
    assert len(final["tasks"]) == 2
    assert len({int(task["ordinal"]) for task in final["tasks"]}) == 2
    assert str(final["tasks"][0]["job_id"]) == completed_job_id
