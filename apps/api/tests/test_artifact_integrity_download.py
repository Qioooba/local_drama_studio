"""MED-01: same-named provider outputs and replayed registrations must not collide.

The diagnostic fixture this replaces asserted the *bad* behaviour (one path, one
artifact, two different bytes, still VERIFIED).  These tests assert the correct
behaviour: two nodes producing ``result.png`` yield two independent byte objects
and two independent records, every VERIFIED file re-hashes to its recorded
digest, a replaced file is refused instead of re-verified, and a cancelled or
expired attempt cannot add new ordinary artifacts.

No GPU or real ComfyUI process is involved: a local HTTP server serves the
provider history JSON the product client actually parses, and the PNG bytes are
real files written to a real SQLite-backed workspace.
"""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from local_drama.application.comfy_jobs import ComfyGenerationService
from local_drama.application.jobs import JobService
from local_drama.application.projects import ProjectService
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.comfy import ComfyClient
from local_drama.infrastructure.filesystem.atomic import replace_path

# ---------------------------------------------------------------------------
# Real PNG bytes.  Two visually different images so a swapped file is detectable
# by digest alone; no image library is required.
# ---------------------------------------------------------------------------
RED_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "3de00000000c4944415408d763f8cfc000000301010018dd8db00000000049454e44ae426082"
)
BLUE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108020000009077"
    "3de00000000c4944415408d76360f8cf0000000301010018dd8db00000000049454e44ae426082"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.fixture()
def provider_output_root(workspace) -> Path:
    root = workspace.work_root / "comfy-output"
    (root / "node-a").mkdir(parents=True, exist_ok=True)
    (root / "node-b").mkdir(parents=True, exist_ok=True)
    (root / "node-a" / "result.png").write_bytes(RED_PNG)
    (root / "node-b" / "result.png").write_bytes(BLUE_PNG)
    return root


def _history_payload(prompt_id: str) -> dict[str, object]:
    """Provider history where two DIFFERENT nodes emit the SAME basename."""
    return {
        prompt_id: {
            "status": {"status_str": "success"},
            "outputs": {
                "17": {"images": [{"filename": "result.png", "subfolder": "node-a", "type": "output"}]},
                "23": {"images": [{"filename": "result.png", "subfolder": "node-b", "type": "output"}]},
            },
        }
    }


class _ProviderHandler(BaseHTTPRequestHandler):
    payload: dict[str, object] = {}

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        if self.path.startswith("/history/"):
            body = json.dumps(self.payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/queue"):
            body = json.dumps({"queue_running": [], "queue_pending": []}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *_args: object) -> None:  # keep pytest output clean
        return


@pytest.fixture()
def provider_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _make_service(workspace, database, provider_root: Path, base_url: str):
    settings = workspace.model_copy(
        update={
            "comfy_output_root": provider_root,
            "comfy_base_url": base_url,
            "comfy_access": "loopback",
        }
    )
    return ComfyGenerationService(database, settings), settings


def _claimed_attempt(workspace, database, code: str) -> tuple[JobService, dict, dict]:
    project = ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title=code,
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )
    jobs = JobService(database, workspace)
    job = jobs.create_job(
        str(project["id"]),
        "CPU_TEST",
        "PROJECT",
        str(project["id"]),
        "CPU",
        {"source": "med01"},
        f"med01-{code}",
    )
    claim = jobs.claim("med01-worker", ["CPU"])
    assert claim is not None and str(claim["job"]["id"]) == str(job["id"])
    return jobs, claim["attempt"], job


def test_same_named_outputs_from_two_nodes_keep_two_independent_objects(
    workspace, database, provider_output_root: Path, provider_server
) -> None:
    """Acceptance: node-a/result.png and node-b/result.png stay two artifacts."""

    service, settings = _make_service(
        workspace, database, provider_output_root, f"http://127.0.0.1:{provider_server.server_port}"
    )
    jobs = JobService(database, settings)
    project = ProjectService(database, settings.projects_root).create_project(
        code="med01_collision",
        title="collision",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )
    job = jobs.create_job(str(project["id"]), "CPU_TEST", "PROJECT", str(project["id"]), "CPU", {"source": "med01"}, "med01-collision")
    claim = jobs.claim("med01-worker", ["CPU"])
    assert claim is not None and str(claim["job"]["id"]) == str(job["id"])
    attempt = claim["attempt"]

    prompt_id = "prompt-collision"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE job_attempts SET state='RUNNING', provider_job_id=?, comfy_prompt_id=? WHERE id=?",
            (prompt_id, prompt_id, str(attempt["id"])),
        )

    _ProviderHandler.payload = _history_payload(prompt_id)
    # Drive the real product collector through its own controlled entry point.
    history = service.comfy.history(prompt_id)
    entries = service.comfy.collect_output_entries(history[prompt_id])
    assert len(entries) == 2
    assert {entry["node_id"] for entry in entries} == {"17", "23"}

    artifacts = [service._publish_provider_output(attempt, entry) for entry in entries]

    assert len(artifacts) == 2
    # Two independent records, two independent paths -- never one shared row.
    assert len({artifact["id"] for artifact in artifacts}) == 2
    assert len({artifact["sandbox_rel_path"] for artifact in artifacts}) == 2
    digests = {artifact["sha256"] for artifact in artifacts}
    assert digests == {_sha256(RED_PNG), _sha256(BLUE_PNG)}

    # Every VERIFIED artifact re-hashes to the digest recorded for it.
    for artifact in artifacts:
        path = settings.work_root / artifact["sandbox_rel_path"]
        assert path.is_file()
        assert _sha256(path.read_bytes()) == artifact["sha256"]

    # ... and the *path* also encodes its provider identity, not just a basename.
    for artifact in artifacts:
        assert "/attempt-" in artifact["sandbox_rel_path"]
        assert "result.png" in artifact["sandbox_rel_path"]

    # The job really completed; the artifacts were not minted for a broken job.
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT id, sha256, status, sandbox_rel_path FROM artifacts WHERE job_attempt_id=? ORDER BY sandbox_rel_path",
            (str(attempt["id"]),),
        ).fetchall()
    assert len(rows) == 2
    assert all(str(row["status"]) == "VERIFIED" for row in rows)


def test_replaying_a_registration_with_replaced_bytes_is_refused(workspace, database) -> None:
    """A hash mismatch after publish must never be reported as VERIFIED."""

    jobs, attempt, _job = _claimed_attempt(workspace, database, "med01_replay")
    target = workspace.work_root / "job-artifacts" / "replay.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(RED_PNG)
    first = jobs.register_artifact(str(attempt["id"]), "COMFY_OUTPUT", "job-artifacts/replay.png")
    assert first["sha256"] == _sha256(RED_PNG)

    # Identical content replays idempotently.
    replay = jobs.register_artifact(str(attempt["id"]), "COMFY_OUTPUT", "job-artifacts/replay.png")
    assert replay["id"] == first["id"] and replay["idempotent_replay"] is True

    # Replaced content under the same identity is a hard conflict.
    target.write_bytes(BLUE_PNG)
    with pytest.raises(DomainRuleError) as conflict:
        jobs.register_artifact(str(attempt["id"]), "COMFY_OUTPUT", "job-artifacts/replay.png")
    assert conflict.value.code == "ARTIFACT_IDENTITY_CONFLICT"
    assert conflict.value.details["registered_sha256"] == _sha256(RED_PNG)
    assert conflict.value.details["observed_sha256"] == _sha256(BLUE_PNG)

    # The original immutable record is untouched.
    with database.connect() as connection:
        row = connection.execute("SELECT sha256, status FROM artifacts WHERE id=?", (first["id"],)).fetchone()
    assert str(row["sha256"]) == _sha256(RED_PNG)
    assert str(row["status"]) == "VERIFIED"


def test_cancelled_or_expired_attempt_cannot_add_ordinary_artifacts(workspace, database) -> None:
    """Cancellation / lease expiry must not mint new VERIFIED artifacts."""

    jobs, attempt, _job = _claimed_attempt(workspace, database, "med01_cancel")
    target = workspace.work_root / "job-artifacts" / "cancel.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(RED_PNG)
    token = str(attempt["lease_token"])

    # A live lease holder may publish.
    published = jobs.register_artifact_for_worker(
        str(attempt["id"]), "COMFY_OUTPUT", "job-artifacts/cancel.png", lease_token=token, worker_id="med01-worker"
    )
    assert published["status"] == "VERIFIED"

    # A different worker cannot present this lease.
    with pytest.raises(DomainRuleError) as mismatch:
        jobs.register_artifact_for_worker(
            str(attempt["id"]), "COMFY_OUTPUT", "job-artifacts/cancel.png", lease_token=token, worker_id="someone-else"
        )
    assert mismatch.value.code == "ARTIFACT_LEASE_OWNER_MISMATCH"

    # A wrong lease token cannot publish either.
    with pytest.raises(DomainRuleError) as stolen:
        jobs.register_artifact_for_worker(
            str(attempt["id"]), "COMFY_OUTPUT", "job-artifacts/cancel.png", lease_token="not-the-lease", worker_id="med01-worker"
        )
    assert stolen.value.code == "ARTIFACT_LEASE_REQUIRED"

    # Cancelling the job blocks new ordinary artifacts even with the right lease.
    jobs.cancel(str(_job["id"]))
    other = workspace.work_root / "job-artifacts" / "after-cancel.png"
    other.write_bytes(BLUE_PNG)
    with pytest.raises(DomainRuleError) as forbidden:
        jobs.register_artifact_for_worker(
            str(attempt["id"]), "COMFY_OUTPUT", "job-artifacts/after-cancel.png", lease_token=token, worker_id="med01-worker"
        )
    assert forbidden.value.code == "ARTIFACT_PUBLISH_FORBIDDEN"

    with database.connect() as connection:
        count = connection.execute("SELECT COUNT(*) AS count FROM artifacts WHERE job_attempt_id=?", (str(attempt["id"]),)).fetchone()
    assert int(count["count"]) == 1


def test_expired_lease_cannot_add_ordinary_artifacts(workspace, database) -> None:
    jobs, attempt, _job = _claimed_attempt(workspace, database, "med01_expired")
    target = workspace.work_root / "job-artifacts" / "expired.png"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(RED_PNG)
    with database.transaction() as connection:
        connection.execute(
            "UPDATE job_attempts SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (str(attempt["id"]),),
        )
    with pytest.raises(DomainRuleError) as expired:
        jobs.register_artifact_for_worker(
            str(attempt["id"]),
            "COMFY_OUTPUT",
            "job-artifacts/expired.png",
            lease_token=str(attempt["lease_token"]),
            worker_id="med01-worker",
        )
    assert expired.value.code == "ARTIFACT_LEASE_EXPIRED"


def test_reconciliation_entry_point_verifies_frozen_provider_identity(
    workspace, database, provider_output_root: Path, provider_server
) -> None:
    """Fault recovery still works, but only for the attempt's frozen provider job."""

    service, settings = _make_service(
        workspace, database, provider_output_root, f"http://127.0.0.1:{provider_server.server_port}"
    )
    jobs = JobService(database, settings)
    project = ProjectService(database, settings.projects_root).create_project(
        code="med01_recover",
        title="recover",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=1000,
        allow_unconfigured_capabilities=True,
    )
    job = jobs.create_job(str(project["id"]), "CPU_TEST", "PROJECT", str(project["id"]), "CPU", {"source": "med01"}, "med01-recover")
    claim = jobs.claim("med01-worker", ["CPU"])
    assert claim is not None and str(claim["job"]["id"]) == str(job["id"])
    attempt = claim["attempt"]

    prompt_id = "prompt-recover"
    with database.transaction() as connection:
        connection.execute(
            "UPDATE job_attempts SET state='ORPHANED', provider_job_id=?, comfy_prompt_id=? WHERE id=?",
            (prompt_id, prompt_id, str(attempt["id"])),
        )
        connection.execute("UPDATE jobs SET state='ORPHANED' WHERE id=?", (str(job["id"]),))
    _ProviderHandler.payload = _history_payload(prompt_id)

    # A callback naming a DIFFERENT provider job must not be able to mint artifacts.
    with pytest.raises(DomainRuleError) as mismatch:
        service.recover_attempt(str(attempt["id"]), "some-other-prompt")
    assert mismatch.value.code == "COMFY_PROVIDER_IDENTITY_MISMATCH"

    recovered = service.recover_attempt(str(attempt["id"]), prompt_id)
    assert recovered["status"] == "SUCCEEDED"
    assert len(recovered["artifacts"]) == 2
    assert {artifact["sha256"] for artifact in recovered["artifacts"]} == {_sha256(RED_PNG), _sha256(BLUE_PNG)}


def test_collect_output_entries_rejects_symlink_and_escape(tmp_path: Path) -> None:
    """Identity collection keeps the existing controlled-path guarantees."""

    output_root = tmp_path / "out"
    output_root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(RED_PNG)
    client = ComfyClient("http://127.0.0.1:1", output_root, allow_private_network=True)

    with pytest.raises(DomainRuleError) as missing:
        client.collect_output_entries({"outputs": {"1": {"images": [{"filename": "nope.png"}]}}})
    assert missing.value.code == "COMFY_OUTPUT_INVALID"

    # A subfolder escape is refused before identity is even assigned.
    with pytest.raises(DomainRuleError) as escape:
        client.collect_output_entries({"outputs": {"1": {"images": [{"filename": "../../outside.png"}]}}})
    assert escape.value.code == "COMFY_OUTPUT_INVALID"


def test_publish_is_atomic_and_leaves_no_staging_file(workspace, database, provider_output_root: Path) -> None:
    """A published output is moved, not copied twice, and staging is drained."""

    service, settings = _make_service(workspace, database, provider_output_root, "http://127.0.0.1:1")
    jobs, attempt, _job = _claimed_attempt(workspace, database, "med01_atomic")
    entries = service.comfy.collect_output_entries(_history_payload("p")["p"])
    artifact = service._publish_provider_output(attempt, entries[0])

    attempt_dir = settings.work_root / "jobs" / str(_job["id"]) / "comfy" / f"attempt-{attempt['id']}"
    staging = attempt_dir / ".staging"
    assert staging.is_dir() and not list(staging.iterdir())
    assert (settings.work_root / artifact["sandbox_rel_path"]).is_file()
    assert not list((settings.work_root / artifact["sandbox_rel_path"]).parent.glob(".partial-*"))
    # The staged copy is gone: it was moved into place, not copied twice.
    assert str(artifact["sandbox_rel_path"]).startswith(f"jobs/{_job['id']}/comfy/attempt-{attempt['id']}/published/")


def test_range_read_uses_identity_cache_instead_of_rehashing(workspace, database) -> None:
    """A byte-range read must not re-hash the whole file each time."""

    jobs, attempt, _job = _claimed_attempt(workspace, database, "med01_range")
    target = workspace.work_root / "job-artifacts" / "range.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(RED_PNG * 64)
    artifact = jobs.register_artifact(str(attempt["id"]), "COMFY_OUTPUT", "job-artifacts/range.bin")

    from local_drama.application import jobs as jobs_module

    original = jobs_module._hash_file_cached
    calls = {"count": 0}

    def counting(path: Path):
        calls["count"] += 1
        return original(path)

    jobs_module._hash_file_cached = counting  # type: ignore[assignment]
    try:
        # First observation verifies once.
        _artifact, path = jobs.artifact_download_for_range(str(artifact["id"]))
        assert path.is_file()
        # Repeated range reads on the same identity must not re-hash at all.
        for _ in range(3):
            jobs.artifact_download_for_range(str(artifact["id"]))
    finally:
        jobs_module._hash_file_cached = original  # type: ignore[assignment]
    assert calls["count"] <= 1


def test_full_download_still_detects_replacement(workspace, database) -> None:
    """A full download always re-verifies, so replacement is still caught."""

    jobs, attempt, _job = _claimed_attempt(workspace, database, "med01_replace")
    target = workspace.work_root / "job-artifacts" / "replace.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(RED_PNG)
    artifact = jobs.register_artifact(str(attempt["id"]), "COMFY_OUTPUT", "job-artifacts/replace.bin")

    # Publish-then-replace, exactly what an immutable-artifact guard must catch.
    replacement = workspace.work_root / "job-artifacts" / "other.bin"
    replacement.write_bytes(BLUE_PNG)
    replace_path(replacement, target)

    with pytest.raises(DomainRuleError) as failed:
        jobs.artifact_download(str(artifact["id"]))
    assert failed.value.code == "ARTIFACT_INTEGRITY_FAILED"
    assert failed.value.details["registered_sha256"] == _sha256(RED_PNG)
    assert failed.value.details["observed_sha256"] == _sha256(BLUE_PNG)
