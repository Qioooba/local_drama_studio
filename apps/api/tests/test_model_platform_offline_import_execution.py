from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from local_drama.entrypoints.maintenance import _parser, download_trusted_model, import_offline_model, stage_downloaded_model
from local_drama.model_platform.application.installation_plans import (
    ExpectedInstallArtifact,
    InstallationPlanService,
    OfflineImportPlanRequest,
)
from local_drama.model_platform.application.offline_import_execution import HostOfflineImportExecutor


def _configured_library(database, root) -> str:
    now = datetime.now(UTC).isoformat()
    node_id = "node-offline-executor"
    library_id = "library-offline-executor"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO mp_compute_nodes
            (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (node_id, "local-offline-executor", "本机服务节点", "offline-executor-node", "{}", now, now, now),
        )
        connection.execute(
            """INSERT INTO mp_model_libraries
            (id,node_id,code,kind,root_path_local,managed,read_only,scan_policy_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (library_id, node_id, "model-library-pytorch", "MODEL_ROOT", str(root.resolve()), False, False, "{}", now, now),
        )
    return library_id


def _plan(database, settings, library_id: str, bundle_reference: str, expected_bytes: bytes) -> str:
    return InstallationPlanService(database, settings).create_offline_import(
        OfflineImportPlanRequest(
            target_library_id=library_id,
            release_code="qwen3-embedding-8b",
            bundle_reference=bundle_reference,
            license_id="apache-2.0",
            expected_artifacts=(
                ExpectedInstallArtifact(
                    "Embedding/model.safetensors",
                    hashlib.sha256(expected_bytes).hexdigest(),
                    len(expected_bytes),
                ),
            ),
        )
    ).id


def test_host_offline_import_copies_only_verified_staging_bundle_into_registered_library(workspace, database, tmp_path) -> None:
    model_root = tmp_path / "model-root"
    library_root = model_root / "libraries" / "pytorch"
    settings = workspace.model_copy(update={"model_root": model_root, "model_library_roots": (library_root,)})
    settings.ensure_roots()
    library_id = _configured_library(database, library_root)
    expected_bytes = b"verified-embedding-model"
    plan_id = _plan(database, settings, library_id, "qwen3-offline", expected_bytes)
    staged = model_root / "staging" / "qwen3-offline" / "Embedding" / "model.safetensors"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(expected_bytes)

    result = HostOfflineImportExecutor(database, settings).execute(plan_id)

    assert result.status == "IMPORTED"
    assert result.verified_artifact_count == 1
    assert result.imported_artifact_count == 1
    assert not result.quarantined
    assert (library_root / "Embedding" / "model.safetensors").read_bytes() == expected_bytes
    assert staged.exists()
    with database.connect() as connection:
        plan_status = connection.execute("SELECT status FROM mp_install_plans WHERE id=?", (plan_id,)).fetchone()[0]
        job = connection.execute("SELECT status,error_redacted FROM mp_install_jobs WHERE id=?", (result.install_job_id,)).fetchone()
    assert plan_status == "IMPORTED"
    assert tuple(job) == ("SUCCEEDED", None)


def test_host_offline_import_quarantines_bundle_that_fails_declared_hash(workspace, database, tmp_path) -> None:
    model_root = tmp_path / "model-root"
    library_root = model_root / "libraries" / "pytorch"
    settings = workspace.model_copy(update={"model_root": model_root, "model_library_roots": (library_root,)})
    settings.ensure_roots()
    library_id = _configured_library(database, library_root)
    plan_id = _plan(database, settings, library_id, "bad-qwen3", b"expected")
    staged_root = model_root / "staging" / "bad-qwen3"
    staged = staged_root / "Embedding" / "model.safetensors"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"tampered")

    result = HostOfflineImportExecutor(database, settings).execute(plan_id)

    assert result.status == "QUARANTINED"
    assert result.quarantined
    assert not staged_root.exists()
    quarantined = list((model_root / "quarantine").iterdir())
    assert len(quarantined) == 1
    assert (quarantined[0] / "Embedding" / "model.safetensors").read_bytes() == b"tampered"
    assert not (library_root / "Embedding" / "model.safetensors").exists()
    with database.connect() as connection:
        plan_status = connection.execute("SELECT status FROM mp_install_plans WHERE id=?", (plan_id,)).fetchone()[0]
        job = connection.execute("SELECT status,error_redacted FROM mp_install_jobs WHERE id=?", (result.install_job_id,)).fetchone()
    assert plan_status == "QUARANTINED"
    assert tuple(job) == ("QUARANTINED", "MP_OFFLINE_BUNDLE_INTEGRITY_FAILED")


def test_host_offline_import_rechecks_destination_after_copy_before_marking_imported(workspace, database, tmp_path, monkeypatch) -> None:
    model_root = tmp_path / "model-root"
    library_root = model_root / "libraries" / "pytorch"
    settings = workspace.model_copy(update={"model_root": model_root, "model_library_roots": (library_root,)})
    settings.ensure_roots()
    library_id = _configured_library(database, library_root)
    expected_bytes = b"expected"
    plan_id = _plan(database, settings, library_id, "post-copy-change", expected_bytes)
    staged_root = model_root / "staging" / "post-copy-change"
    staged = staged_root / "Embedding" / "model.safetensors"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(expected_bytes)

    def write_wrong_bytes(_source, destination) -> None:
        destination.write_bytes(b"tampered-after-verification")

    monkeypatch.setattr(HostOfflineImportExecutor, "_copy_new_file", staticmethod(write_wrong_bytes))
    result = HostOfflineImportExecutor(database, settings).execute(plan_id)

    assert result.status == "QUARANTINED"
    assert not staged_root.exists()
    assert not (library_root / "Embedding" / "model.safetensors").exists()
    with database.connect() as connection:
        job = connection.execute("SELECT status,error_redacted FROM mp_install_jobs WHERE id=?", (result.install_job_id,)).fetchone()
    assert tuple(job) == ("QUARANTINED", "MP_OFFLINE_IMPORT_DESTINATION_INTEGRITY_FAILED")


def test_offline_import_maintenance_command_requires_explicit_stopped_host_confirmation(workspace) -> None:
    args = _parser().parse_args(["offline-model-import", "plan-1", "--confirm-host-stopped"])

    assert args.command == "offline-model-import"
    assert args.install_plan_id == "plan-1"
    assert args.confirm_host_stopped is True
    with pytest.raises(ValueError, match="confirm-host-stopped"):
        import_offline_model(workspace, "plan-1", host_stopped_confirmed=False)
    staged = _parser().parse_args(["stage-downloaded-model", "bundle-1", "--confirm-host-stopped"])
    assert staged.bundle_reference == "bundle-1"
    with pytest.raises(ValueError, match="confirm-host-stopped"):
        stage_downloaded_model(workspace, "bundle-1", host_stopped_confirmed=False)
    trusted = _parser().parse_args(["trusted-model-download", "plan-2", "--confirm-host-stopped"])
    assert trusted.install_plan_id == "plan-2"
    with pytest.raises(ValueError, match="confirm-host-stopped"):
        download_trusted_model(workspace, "plan-2", host_stopped_confirmed=False)
