from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from local_drama.application.local_llm import LocalLLMService
from local_drama.application.projects import ProjectService
from local_drama.application.worker import LocalMediaWorker
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def _project(workspace, database) -> dict[str, object]:
    return ProjectService(database, workspace.projects_root).create_project(
        code="llm_probe_jobs",
        title="LLM Probe Jobs",
        episode_count=1,
        aspect_ratio="16:9",
        fps_num=24,
        fps_den=1,
        target_duration_ms=60_000,
        allow_unconfigured_capabilities=True,
    )


def test_api_startup_never_synchronizes_or_probes_llm(workspace, database, monkeypatch) -> None:
    def unexpected_sync(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("API startup must not contact or synchronize an LLM")

    monkeypatch.setattr(LocalLLMService, "sync_candidate", unexpected_sync)

    with TestClient(create_app(workspace)) as client:
        assert client.app.state.llm_sync is None


def test_local_llm_probe_runs_as_durable_job_without_persisting_secret(workspace, database, monkeypatch) -> None:
    project = _project(workspace, database)
    monkeypatch.setattr(
        "local_drama.infrastructure.local_llm.LocalLLMClient.probe",
        lambda self, load_test=False: {
            "status": "PASS",
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "probe_level_passed": 4,
            "probe_levels": {},
            "load_test": load_test,
        },
    )
    payload = {
        "provider": "OLLAMA_LOOPBACK",
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3:8b",
        "load_test": True,
    }
    with TestClient(create_app(workspace)) as client:
        submitted = client.post(f"/api/v1/projects/{project['id']}/local-llm/probe:submit", json=payload)
        assert submitted.status_code == 202
        job = submitted.json()["job"]
        assert job["state"] == "QUEUED"
        assert job["input_snapshot"]["secret_persisted"] is False
        assert "api_key" not in job["input_snapshot"]
        pending = client.get(f"/api/v1/local-llm/probes/{job['id']}")
        assert pending.status_code == 200 and pending.json()["probe"] is None

    outcome = LocalMediaWorker(database, workspace).run_once("llm-probe-worker")
    assert outcome is not None
    assert outcome["artifact"]["kind"] == "LOCAL_LLM_PROBE_REPORT"
    with TestClient(create_app(workspace)) as client:
        ready = client.get(f"/api/v1/local-llm/probes/{job['id']}")
    assert ready.status_code == 200
    assert ready.json()["job"]["state"] == "SUCCEEDED"
    assert ready.json()["probe"]["probe_level_passed"] == 4

    def unexpected_probe(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("sync/publish must reuse the verified durable probe")

    monkeypatch.setattr("local_drama.infrastructure.local_llm.LocalLLMClient.probe", unexpected_probe)
    llm = LocalLLMService(database, workspace)
    configured = llm.status(model="qwen3:8b", live_probe=False)
    assert configured["status"] == "CONFIGURED"
    candidate = llm.sync_candidate(
        model="qwen3:8b",
        provider="OLLAMA_LOOPBACK",
        base_url="http://127.0.0.1:11434",
        probe_job_id=str(job["id"]),
    )
    published = llm.publish(str(candidate["profile_version_id"]), probe_job_id=str(job["id"]))
    assert published["status"] == "PUBLISHED"
    with pytest.raises(DomainRuleError) as mismatch:
        llm.verified_probe_evidence(
            str(job["id"]),
            provider="OLLAMA_LOOPBACK",
            base_url="http://127.0.0.1:11434",
            model="different-model",
        )
    assert mismatch.value.code == "LOCAL_LLM_PROBE_CONFIG_MISMATCH"


def test_async_probe_rejects_inline_api_key_and_unconfirmed_remote_outbound(workspace, database) -> None:
    project = _project(workspace, database)
    endpoint = f"/api/v1/projects/{project['id']}/local-llm/probe:submit"
    with TestClient(create_app(workspace)) as client:
        secret = client.post(endpoint, json={
            "provider": "OPENAI_COMPAT", "base_url": "https://example.com", "model": "model", "api_key": "secret", "allow_remote_outbound": True,
        })
        outbound = client.post(endpoint, json={
            "provider": "OPENAI_COMPAT", "base_url": "https://example.com", "model": "model", "allow_remote_outbound": False,
        })
    assert secret.status_code == 422
    assert secret.json()["error"]["code"] == "LOCAL_LLM_ASYNC_KEY_ENV_REQUIRED"
    assert outbound.status_code == 422
    assert outbound.json()["error"]["code"] == "OUTBOUND_CONFIRMATION_REQUIRED"
