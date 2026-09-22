"""SS-09: a resource-blocked candidate prefix must not starve later CPU work.

The defect was a SQL ``LIMIT 128`` applied *before* the Python resource filter,
so when the first 128 queued Jobs all needed the one exclusive GPU the mixed
worker received ``None`` even though a plain CPU Job existed further down the
same priority/FIFO order.  Single-GPU mutual exclusion must stay exactly as it
was.
"""

from __future__ import annotations

import pytest

from local_drama.application.job_resources import SCHEDULER_GPU_EXCLUSIVE_RESOURCE
from local_drama.application.jobs import JobService

_GPU_BLOCKED_CANDIDATES = 128


def _cpu_job(service: JobService, key: str, *, priority: int = 100) -> dict[str, object]:
    return service.create_job(None, "CPU_TEST", "PROJECT", f"cpu-{key}", "CPU", {"key": key}, key, priority=priority)


def _gpu_job(service: JobService, key: str, *, priority: int = 1) -> dict[str, object]:
    """A heavy-GPU Job: channel ``CPU`` with a loopback Ollama runtime.

    The scheduler maps it to the single exclusive GPU resource key, exactly as
    the report's reproduction did.
    """

    return service.create_job(
        None,
        "SCRIPT_BREAKDOWN_LOCAL_LLM",
        "PROJECT",
        f"gpu-{key}",
        "CPU",
        {"provider": "OLLAMA_LOOPBACK", "base_url": "http://127.0.0.1:11434", "scheduler_resource_policy": {"gpu_heavy_concurrency": 1}},
        key,
        priority=priority,
    )


def _release(database, attempt_id: str) -> None:
    with database.transaction() as connection:
        connection.execute(
            "UPDATE job_resource_leases SET released_at=? WHERE attempt_id=? AND released_at IS NULL",
            ("2000-01-01T00:00:00+00:00", attempt_id),
        )


def test_cpu_candidate_behind_a_gpu_blocked_prefix_is_still_claimed(workspace, database) -> None:
    service = JobService(database, workspace)
    holder = _gpu_job(service, "gpu-holder", priority=0)
    blocked = [_gpu_job(service, f"gpu-blocked-{index}", priority=10) for index in range(_GPU_BLOCKED_CANDIDATES)]
    cpu = _cpu_job(service, "cpu-behind-gpu-prefix", priority=50)

    # One worker already holds the single GPU resource.
    held = service.claim("gpu-holder-worker", ["CPU"])
    assert held is not None and held["job"]["id"] == holder["id"]
    with database.connect() as connection:
        assert connection.execute(
            "SELECT resource_key FROM job_resource_leases WHERE released_at IS NULL"
        ).fetchone()["resource_key"] == SCHEDULER_GPU_EXCLUSIVE_RESOURCE

    # The mixed worker declares both channels and must still find the CPU Job
    # behind the 128 GPU-blocked candidates.
    mixed = service.claim("mixed-worker", ["CPU", "GPU_H3"])
    assert mixed is not None
    assert mixed["job"]["id"] == cpu["id"]
    assert mixed["job"]["id"] not in {job["id"] for job in blocked}


def test_a_gpu_blocked_prefix_alone_still_returns_none(workspace, database) -> None:
    """No runnable candidate at all must stay ``None`` (no false positive)."""

    service = JobService(database, workspace)
    holder = _gpu_job(service, "gpu-only-holder", priority=0)
    for index in range(_GPU_BLOCKED_CANDIDATES):
        _gpu_job(service, f"gpu-only-blocked-{index}", priority=10)

    held = service.claim("gpu-holder-worker", ["CPU"])
    assert held is not None and held["job"]["id"] == holder["id"]
    assert service.claim("mixed-worker", ["CPU", "GPU_H3"]) is None


def test_priority_and_fifo_order_survive_the_bounded_page_scan(workspace, database) -> None:
    """Global ordering stays priority-first, FIFO-second across page boundaries."""

    service = JobService(database, workspace)
    # Ten same-priority Jobs are created first, six higher-priority Jobs after
    # them: a bounded page scan must still respect priority over creation time.
    for index in range(10):
        _cpu_job(service, f"fifo-second-{index}", priority=50)
    for rank, priority in enumerate([10, 20, 30, 40, 50, 100]):
        service.create_job(
            None,
            "CPU_TEST",
            "PROJECT",
            f"priority-subject-{rank}",
            "CPU",
            {"key": f"priority-{rank}"},
            f"priority-{rank}",
            priority=priority,
        )

    order: list[int] = []
    for index in range(16):
        claimed = service.claim(f"ordered-worker-{index}", ["CPU"])
        if claimed is None:
            break
        order.append(int(claimed["job"]["priority"]))

    assert order == [10, 20, 30, 40, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 50, 100]


def test_twenty_four_concurrent_claims_still_get_distinct_jobs(workspace, database) -> None:
    """Regression guard for the report's positive control."""

    import threading

    service = JobService(database, workspace)
    created = {str(_cpu_job(service, f"concurrent-{index}")["id"]) for index in range(24)}

    claimed: list[str] = []
    lock = threading.Lock()

    def _claim(index: int) -> None:
        worker = JobService(database, workspace)
        result = worker.claim(f"concurrent-worker-{index}", ["CPU"])
        if result is not None:
            with lock:
                claimed.append(str(result["job"]["id"]))

    threads = [threading.Thread(target=_claim, args=(index,)) for index in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
        assert not thread.is_alive()

    assert len(claimed) == 24
    assert len(set(claimed)) == 24
    assert set(claimed) == created
    assert service.claim("late-worker", ["CPU"]) is None


def test_twelve_workers_still_contend_for_one_gpu_without_regression(workspace, database) -> None:
    service = JobService(database, workspace)
    gpu_jobs = [_gpu_job(service, f"contended-gpu-{index}") for index in range(12)]

    first = service.claim("gpu-contender-0", ["CPU"])
    assert first is not None and first["job"]["id"] == gpu_jobs[0]["id"]
    for index in range(1, 12):
        assert service.claim(f"gpu-contender-{index}", ["CPU"]) is None

    with database.connect() as connection:
        active = connection.execute("SELECT COUNT(*) FROM job_resource_leases WHERE released_at IS NULL").fetchone()[0]
        active_attempts = connection.execute("SELECT COUNT(*) FROM job_attempts WHERE state IN ('CLAIMED','RUNNING')").fetchone()[0]
    assert active == 1
    assert active_attempts == 1


@pytest.mark.parametrize("candidate_count", [128, 512])
def test_large_blocked_queue_does_not_degrade_into_a_full_table_scan(workspace, database, candidate_count: int) -> None:
    """A 512-deep blocked queue still resolves the CPU Job promptly."""

    import time

    service = JobService(database, workspace)
    holder = _gpu_job(service, "degrade-holder", priority=0)
    for index in range(candidate_count):
        _gpu_job(service, f"degrade-blocked-{index}", priority=10)
    cpu = _cpu_job(service, "degrade-cpu", priority=50)

    held = service.claim("gpu-holder-worker", ["CPU"])
    assert held is not None and held["job"]["id"] == holder["id"]

    started = time.monotonic()
    mixed = service.claim("mixed-worker", ["CPU", "GPU_H3"])
    elapsed = time.monotonic() - started
    assert mixed is not None and mixed["job"]["id"] == cpu["id"]
    # The bounded page scan must stay well inside an interactive budget even for
    # a 512-entry blocked prefix (the old cap silently failed here instead).
    assert elapsed < 20.0, f"claim took {elapsed:.2f}s for {candidate_count} blocked candidates"
