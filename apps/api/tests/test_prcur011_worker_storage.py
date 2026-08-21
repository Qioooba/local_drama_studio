from __future__ import annotations

import errno
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from local_drama.application.jobs import JobService
from local_drama.application.media import MediaService
from local_drama.application.projects import ProjectService
from local_drama.application.storage_operations import StorageOperationService
from local_drama.application.worker import LocalMediaWorker
from local_drama.application.worker_sessions import WORKER_PROTOCOL_VERSION, WorkerSessionService, WorkerSupervisor
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database

ROOT = Path(__file__).resolve().parents[3]


def _project(database, workspace, code: str = "prcur011") -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code=code,
        title="PR-CUR-011",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def _stage(service: StorageOperationService, project_id: str, source: Path, *, key: str, **kwargs):
    return service.stage_media_ingest(
        project_id,
        source,
        media_kind="DOCUMENT",
        mime_type="text/plain",
        idempotency_key=key,
        **kwargs,
    )


def test_worker_session_version_handshake_heartbeat_and_attempt_binding(workspace, database) -> None:
    sessions = WorkerSessionService(database, workspace)
    incompatible = sessions.start_session(
        "version-mismatch",
        worker_version="old-worker",
        api_version=workspace.app_version,
        protocol_version=WORKER_PROTOCOL_VERSION,
    )
    assert incompatible["status"] == "INCOMPATIBLE"
    assert incompatible["compatible"] is False
    with pytest.raises(DomainRuleError) as inactive:
        sessions.heartbeat(str(incompatible["id"]))
    assert inactive.value.code == "WORKER_SESSION_NOT_ACTIVE"

    session = sessions.start_session(
        "bound-worker",
        worker_version=workspace.app_version,
        api_version=workspace.app_version,
        channels=["CPU"],
    )
    heartbeat = sessions.heartbeat(str(session["id"]))
    assert heartbeat["status"] == "RUNNING"
    project = _project(database, workspace, "worker_binding")
    job = JobService(database, workspace).create_job(
        str(project["id"]), "CPU_TEST", "PROJECT", str(project["id"]), "CPU", {}, "worker-session-binding",
    )
    claim = JobService(database, workspace).claim(
        "bound-worker", ["CPU"], worker_session_id=str(session["id"]),
    )
    assert claim is not None and claim["job"]["id"] == job["id"]
    assert claim["attempt"]["worker_session_id"] == session["id"]


def test_worker_kill_expires_session_requeues_job_and_releases_gpu_lease(workspace, database) -> None:
    project = _project(database, workspace, "worker_kill")
    sessions = WorkerSessionService(database, workspace)
    session = sessions.start_session(
        "killed-worker",
        worker_version=workspace.app_version,
        api_version=workspace.app_version,
        channels=["GPU_H3"],
        lease_seconds=5,
    )
    jobs = JobService(database, workspace)
    job = jobs.create_job(
        str(project["id"]), "CPU_TEST", "PROJECT", str(project["id"]), "GPU_H3", {}, "worker-kill-job",
    )
    claim = jobs.claim(
        "killed-worker", ["GPU_H3"], lease_seconds=3600, worker_session_id=str(session["id"]),
    )
    assert claim is not None and claim["job"]["id"] == job["id"]
    observed = datetime.fromisoformat(str(session["lease_expires_at"])) + timedelta(seconds=1)
    result = sessions.reconcile(current=observed)
    assert str(session["id"]) in result["stale_session_ids"]
    recovered = jobs.get_job(str(job["id"]))
    assert recovered["state"] == "QUEUED"
    assert recovered["attempts"][0]["state"] == "ORPHANED"
    with database.connect() as connection:
        lease = connection.execute(
            "SELECT released_at FROM job_resource_leases WHERE attempt_id=?", (claim["attempt"]["id"],),
        ).fetchone()
    assert lease is not None and lease["released_at"] is not None


def test_supervisor_persists_restart_backoff_and_recovers(workspace, database, monkeypatch) -> None:
    calls = 0

    def unstable_run_once(self, worker_id, channels=None, *, worker_session_id=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated worker child exit")
        return None

    monkeypatch.setattr(LocalMediaWorker, "run_once", unstable_run_once)
    result = WorkerSupervisor(database, workspace, sleep=lambda _seconds: None).run_until_idle(
        "restart-worker", max_restarts=1,
    )
    assert result["status"] == "STOPPED"
    assert result["session"]["restart_count"] == 1
    assert result["session"]["consecutive_failure_count"] == 0
    assert calls == 2


def test_storage_finalize_is_idempotent_and_streaming(workspace, database, monkeypatch) -> None:
    project = _project(database, workspace, "storage_happy")
    source = workspace.work_root / "source.txt"
    source.write_bytes(b"staged-media" * 300_000)

    def reject_whole_file_read(_path):
        raise AssertionError("storage ingest must stream instead of Path.read_bytes")

    monkeypatch.setattr(Path, "read_bytes", reject_whole_file_read)

    # The implementation contract is bounded chunks; the persisted hash/size
    # and replay identity prove that the same bytes reached immutable media.
    service = StorageOperationService(database, workspace)
    operation = _stage(service, str(project["id"]), source, key="storage-happy")
    service.set_media_probe(str(operation["id"]), {"probe_status": "NOT_APPLICABLE"}, duration_ms=None, fps_num=None, fps_den=None)
    first = service.finalize_media_ingest(str(operation["id"]))
    replay = service.finalize_media_ingest(str(operation["id"]))
    assert first["media_version_id"] == replay["media_version_id"]
    assert first["sha256"] == replay["sha256"]
    assert replay["idempotent_replay"] is True
    assert first["storage_operation_id"] == operation["id"]
    staged_replay = _stage(service, str(project["id"]), source, key="storage-happy")
    assert staged_replay["idempotent_replay"] is True
    source.write_bytes(b"different bytes")
    with pytest.raises(DomainRuleError) as mismatched_replay:
        _stage(service, str(project["id"]), source, key="storage-happy")
    assert mismatched_replay.value.code == "STORAGE_IDEMPOTENCY_PAYLOAD_MISMATCH"


def test_media_service_import_is_backed_by_committed_storage_ledger(workspace, database) -> None:
    project = _project(database, workspace, "storage_media_service")
    source = workspace.work_root / "media-service.txt"
    source.write_text("registered through staged ledger", encoding="utf-8")
    imported = MediaService(database, workspace).import_file(
        str(project["id"]), source, media_kind="DOCUMENT",
    )
    with database.connect() as connection:
        operation = connection.execute(
            """SELECT status,media_asset_id,media_version_id,destination_rel_path
            FROM storage_operations WHERE media_version_id=?""",
            (imported["media_version_id"],),
        ).fetchone()
    assert operation is not None and operation["status"] == "COMMITTED"
    assert operation["media_asset_id"] == imported["media_asset_id"]
    assert operation["destination_rel_path"] == imported["rel_path"]


def test_copy_then_database_failure_is_resumed_by_reconciler(workspace, database) -> None:
    project = _project(database, workspace, "storage_db_fail")
    source = workspace.work_root / "db-fail.txt"
    source.write_text("copy survived database failure", encoding="utf-8")

    def fail_before_commit(name, _operation):
        if name == "before_database_commit":
            raise RuntimeError("injected sqlite commit failure")

    failing = StorageOperationService(database, workspace, fault_injector=fail_before_commit)
    operation = _stage(failing, str(project["id"]), source, key="storage-db-fail")
    with pytest.raises(DomainRuleError) as failed:
        failing.finalize_media_ingest(str(operation["id"]))
    assert failed.value.code == "STORAGE_DATABASE_FINALIZE_FAILED"
    pending = failing.get_operation(str(operation["id"]))
    assert pending["status"] == "NEEDS_ATTENTION"
    destination = workspace.projects_root / str(project["root_rel"]) / str(pending["destination_rel_path"])
    assert destination.is_file()

    recovered = StorageOperationService(database, workspace).reconcile()
    assert str(operation["id"]) in recovered["recovered_operation_ids"]
    committed = StorageOperationService(database, workspace).get_operation(str(operation["id"]))
    assert committed["status"] == "COMMITTED"
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM media_versions WHERE id=?", (committed["media_version_id"],),
        ).fetchone()[0] == 1


def test_database_commit_then_process_crash_replays_without_duplicate(workspace, database) -> None:
    class SimulatedProcessCrash(BaseException):
        pass

    project = _project(database, workspace, "storage_commit_crash")
    source = workspace.work_root / "commit-crash.txt"
    source.write_text("database commit is authoritative", encoding="utf-8")

    def crash_after_commit(name, _operation):
        if name == "after_database_commit":
            raise SimulatedProcessCrash()

    crashing = StorageOperationService(database, workspace, fault_injector=crash_after_commit)
    operation = _stage(crashing, str(project["id"]), source, key="storage-commit-crash")
    with pytest.raises(SimulatedProcessCrash):
        crashing.finalize_media_ingest(str(operation["id"]))
    persisted = StorageOperationService(database, workspace).get_operation(str(operation["id"]))
    assert persisted["status"] == "COMMITTED"
    replay = StorageOperationService(database, workspace).finalize_media_ingest(str(operation["id"]))
    assert replay["media_version_id"] == persisted["media_version_id"]
    with database.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM media_versions WHERE media_asset_id=?", (persisted["media_asset_id"],),
        ).fetchone()[0] == 1


def test_storage_disk_full_hash_mismatch_path_and_symlink_fail_closed(workspace, database, monkeypatch) -> None:
    project = _project(database, workspace, "storage_fail_closed")
    source = workspace.work_root / "source-safe.txt"
    source.write_text("safe bytes", encoding="utf-8")

    import local_drama.application.storage_operations as storage_module

    original_stream_copy = storage_module._stream_copy

    def disk_full(_source, _destination):
        raise OSError(errno.ENOSPC, "injected disk full")

    monkeypatch.setattr(storage_module, "_stream_copy", disk_full)
    with pytest.raises(DomainRuleError) as full:
        _stage(StorageOperationService(database, workspace), str(project["id"]), source, key="storage-disk-full")
    assert full.value.code == "DISK_FULL"
    with database.connect() as connection:
        failed = connection.execute(
            "SELECT status,last_error_code FROM storage_operations WHERE idempotency_key='storage-disk-full'",
        ).fetchone()
    assert failed is not None and (failed["status"], failed["last_error_code"]) == ("FAILED", "DISK_FULL")

    monkeypatch.setattr(storage_module, "_stream_copy", original_stream_copy)
    with pytest.raises(DomainRuleError) as mismatch:
        _stage(
            StorageOperationService(database, workspace),
            str(project["id"]),
            source,
            key="storage-hash-mismatch",
            expected_sha256="0" * 64,
        )
    assert mismatch.value.code == "STORAGE_HASH_MISMATCH"
    with database.connect() as connection:
        quarantined = connection.execute(
            "SELECT status,quarantine_rel_path FROM storage_operations WHERE idempotency_key='storage-hash-mismatch'",
        ).fetchone()
    assert quarantined is not None and quarantined["status"] == "QUARANTINED"
    assert (workspace.work_root / str(quarantined["quarantine_rel_path"])).is_file()

    traversal = workspace.work_root / "folder" / ".." / "source-safe.txt"
    with pytest.raises(DomainRuleError) as escaped:
        _stage(StorageOperationService(database, workspace), str(project["id"]), traversal, key="storage-traversal")
    assert escaped.value.code == "PATH_TRAVERSAL_REJECTED"

    symlink = workspace.work_root / "source-link.txt"
    try:
        symlink.symlink_to(source)
    except (OSError, NotImplementedError):
        return
    with pytest.raises(DomainRuleError) as linked:
        _stage(StorageOperationService(database, workspace), str(project["id"]), symlink, key="storage-symlink")
    assert linked.value.code == "SYMLINK_REJECTED"


def test_storage_reconciler_quarantines_unclassified_orphan_without_deleting(workspace, database) -> None:
    orphan = workspace.work_root / "storage-staging" / "unknown" / "orphan.partial"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"unclassified")
    result = StorageOperationService(database, workspace).reconcile()
    assert result["quarantined_operation_ids"]
    assert not orphan.exists()
    with database.connect() as connection:
        row = connection.execute(
            "SELECT status,quarantine_rel_path FROM storage_operations WHERE operation_type='ORPHAN_QUARANTINE'",
        ).fetchone()
    assert row is not None and row["status"] == "QUARANTINED"
    quarantined = workspace.work_root / str(row["quarantine_rel_path"])
    assert quarantined.read_bytes() == b"unclassified"


def test_storage_reconciler_quarantines_tampered_committed_media_and_marks_corrupt(workspace, database) -> None:
    project = _project(database, workspace, "storage_committed_tamper")
    source = workspace.work_root / "committed.txt"
    source.write_text("immutable committed bytes", encoding="utf-8")
    service = StorageOperationService(database, workspace)
    operation = _stage(service, str(project["id"]), source, key="storage-committed-tamper")
    committed = service.finalize_media_ingest(str(operation["id"]))
    destination = workspace.projects_root / str(project["root_rel"]) / str(committed["rel_path"])
    destination.write_bytes(b"tampered")

    result = service.reconcile()
    assert str(operation["id"]) in result["quarantined_operation_ids"]
    quarantined = service.get_operation(str(operation["id"]))
    assert quarantined["status"] == "QUARANTINED"
    assert (workspace.work_root / str(quarantined["quarantine_rel_path"])).read_bytes() == b"tampered"
    with database.connect() as connection:
        integrity = connection.execute(
            "SELECT integrity_status FROM media_versions WHERE id=?", (committed["media_version_id"],),
        ).fetchone()["integrity_status"]
    assert integrity == "CORRUPT"


def test_0050_through_0052_upgrade_downgrade_reupgrade(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "migration-cycle.sqlite3"
    monkeypatch.setenv("LOCAL_DRAMA_DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "apps" / "api" / "alembic"))
    config.set_main_option("prepend_sys_path", str(ROOT / "apps" / "api"))
    command.upgrade(config, "0052_storage_operations")
    with Database(database_path).connect() as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0052_storage_operations"
    command.downgrade(config, "0050_character_identity_packs")
    with Database(database_path).connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "worker_sessions" not in tables and "storage_operations" not in tables
        assert "character_identity_packs" in tables
    command.downgrade(config, "0049_canonical_capabilities")
    with Database(database_path).connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(shot_asset_bindings)")}
        assert "character_identity_packs" not in tables
        assert "identity_pack_version_id" not in columns
    command.upgrade(config, "0052_storage_operations")
    with Database(database_path).connect() as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0052_storage_operations"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
