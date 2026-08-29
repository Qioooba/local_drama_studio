from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app
from local_drama.model_platform.application.installation_plans import (
    InstallationPlanService,
    TrustedDownloadArtifact,
    TrustedDownloadPlanRequest,
)
from local_drama.model_platform.application.trusted_download_plan_execution import HostTrustedDownloadPlanExecutor


def _configured_library(database, root) -> str:
    now = datetime.now(UTC).isoformat()
    node_id = "node-trusted-download-plan"
    library_id = "library-trusted-download-plan"
    with database.transaction() as connection:
        connection.execute(
            """INSERT INTO mp_compute_nodes
            (id,code,display_name,fingerprint,host_json,last_seen_at,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (node_id, "local-trusted-download-plan", "本机服务节点", "trusted-download-plan-node", "{}", now, now, now),
        )
        connection.execute(
            """INSERT INTO mp_model_libraries
            (id,node_id,code,kind,root_path_local,managed,read_only,scan_policy_json,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (library_id, node_id, "model-library-pytorch", "MODEL_ROOT", str(root.resolve()), False, False, "{}", now, now),
        )
    return library_id


class _FakeDownloader:
    def __init__(self, model_root, payload: bytes, *, fail: bool = False) -> None:
        self.model_root = model_root
        self.payload = payload
        self.fail = fail
        self.requests = []

    def download(self, request) -> None:
        self.requests.append(request)
        if self.fail:
            raise DomainRuleError("MP_DOWNLOAD_TRANSPORT_FAILED", "transport unavailable")
        target = self.model_root / "downloads" / request.bundle_reference / request.relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.payload)

    def promote_bundle_to_staging(self, bundle_reference: str) -> None:
        os.replace(self.model_root / "downloads" / bundle_reference, self.model_root / "staging" / bundle_reference)


def _plan(database, settings, library_id: str, payload: bytes) -> str:
    return InstallationPlanService(database, settings).create_trusted_download(
        TrustedDownloadPlanRequest(
            target_library_id=library_id,
            release_code="qwen3-embedding-8b",
            bundle_reference="qwen3-trusted-download",
            license_id="apache-2.0",
            artifacts=(
                TrustedDownloadArtifact(
                    relative_path="Embedding/model.safetensors",
                    sha256=hashlib.sha256(payload).hexdigest(),
                    size_bytes=len(payload),
                    source_url="https://models.example.test/releases/qwen3/model.safetensors",
                ),
            ),
        )
    ).id


def test_trusted_download_plan_is_durable_but_api_never_returns_source_url(workspace, database, tmp_path) -> None:
    model_root = tmp_path / "models"
    library_root = model_root / "libraries" / "pytorch"
    settings = workspace.model_copy(
        update={"model_root": model_root, "model_library_roots": (library_root,), "model_download_source_hosts": ("models.example.test",)}
    )
    settings.ensure_roots()
    library_id = _configured_library(database, library_root)

    with TestClient(create_app(settings)) as client:
        created = client.post("/api/v2/model-platform/installation-plans/trusted-download", json={
            "target_library_id": library_id,
            "release_code": "qwen3-embedding-8b",
            "bundle_reference": "qwen3-api-download",
            "license_id": "apache-2.0",
            "artifacts": [{
                "relative_path": "Embedding/model.safetensors",
                "sha256": "a" * 64,
                "size_bytes": 1234,
                "source_url": "https://models.example.test/releases/qwen3/model.safetensors",
            }],
        })
        listed = client.get("/api/v2/model-platform/installation-plans")

    assert created.status_code == 201
    assert created.json()["network_operations_started"] is False
    assert created.json()["host_download_available"] is False
    assert created.json()["plan"]["status"] == "AWAITING_TRUSTED_DOWNLOAD"
    assert "models.example.test" not in str(created.json())
    assert "qwen3-api-download" not in str(created.json())
    assert listed.status_code == 200
    assert listed.json()["items"][0]["source_kind"] == "TRUSTED_HTTPS"
    assert "models.example.test" not in str(listed.json())


def test_host_trusted_download_plan_stages_verified_artifacts_before_offline_import(workspace, database, tmp_path) -> None:
    payload = b"verified-model"
    model_root = tmp_path / "models"
    library_root = model_root / "libraries" / "pytorch"
    settings = workspace.model_copy(
        update={"model_root": model_root, "model_library_roots": (library_root,), "model_download_source_hosts": ("models.example.test",)}
    )
    settings.ensure_roots()
    plan_id = _plan(database, settings, _configured_library(database, library_root), payload)
    downloader = _FakeDownloader(model_root, payload)

    result = HostTrustedDownloadPlanExecutor(database, settings, downloader=downloader).execute(plan_id)

    assert result.status == "STAGED"
    assert result.downloaded_artifact_count == 1
    assert len(downloader.requests) == 1
    assert (model_root / "staging" / "qwen3-trusted-download" / "Embedding" / "model.safetensors").read_bytes() == payload
    assert not (library_root / "Embedding" / "model.safetensors").exists()
    with database.connect() as connection:
        plan_status = connection.execute("SELECT status FROM mp_install_plans WHERE id=?", (plan_id,)).fetchone()[0]
        job = connection.execute("SELECT status,progress_json,error_redacted FROM mp_install_jobs WHERE id=?", (result.install_job_id,)).fetchone()
    assert plan_status == "AWAITING_OFFLINE_IMPORT"
    assert tuple(job) == ("SUCCEEDED", '{"downloaded_artifact_count":1,"expected_artifact_count":1,"staged":true}', None)


def test_trusted_download_plan_marks_failed_job_without_importing_library(workspace, database, tmp_path) -> None:
    payload = b"expected"
    model_root = tmp_path / "models"
    library_root = model_root / "libraries" / "pytorch"
    settings = workspace.model_copy(
        update={"model_root": model_root, "model_library_roots": (library_root,), "model_download_source_hosts": ("models.example.test",)}
    )
    settings.ensure_roots()
    plan_id = _plan(database, settings, _configured_library(database, library_root), payload)

    with pytest.raises(DomainRuleError) as failed:
        HostTrustedDownloadPlanExecutor(database, settings, downloader=_FakeDownloader(model_root, payload, fail=True)).execute(plan_id)

    assert failed.value.code == "MP_TRUSTED_DOWNLOAD_FAILED"
    assert not list((model_root / "staging").iterdir())
    assert not (library_root / "Embedding" / "model.safetensors").exists()
    with database.connect() as connection:
        plan_status = connection.execute("SELECT status FROM mp_install_plans WHERE id=?", (plan_id,)).fetchone()[0]
        job = connection.execute("SELECT status,error_redacted FROM mp_install_jobs WHERE install_plan_id=?", (plan_id,)).fetchone()
    assert plan_status == "DOWNLOAD_FAILED"
    assert tuple(job) == ("FAILED", "MP_DOWNLOAD_TRANSPORT_FAILED")
