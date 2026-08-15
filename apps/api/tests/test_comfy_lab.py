from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from local_drama.application.comfy_lab import ComfyLabService
from local_drama.domain.errors import DomainRuleError
from local_drama.main import create_app


def test_comfy_lab_status_is_truthful_and_local_only_when_not_configured(workspace, monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_DRAMA_COMFY_DESIGNER_PYTHON", raising=False)
    monkeypatch.delenv("LOCAL_DRAMA_COMFY_DESIGNER_ROOT", raising=False)
    status = ComfyLabService(workspace).status()
    assert status["status"] == "STOPPED"
    assert status["launch_configured"] is False
    assert status["formal_project_write"] is False
    assert status["network_contacted"] is False


def test_comfy_lab_capture_is_sandboxed_and_rejects_absolute_paths(workspace) -> None:
    service = ComfyLabService(workspace)
    captured = service.capture({"1": {"class_type": "SaveImage", "inputs": {"filename_prefix": "lab"}}}, "test")
    target = workspace.work_root / str(captured["sandbox_rel_path"])
    assert captured["status"] == "CAPTURED"
    assert target.is_file()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["formal_project_write"] is False
    assert "workflow_packages" not in target.parts
    with pytest.raises(DomainRuleError, match="绝对路径"):
        service.capture({"1": {"inputs": {"image": str(workspace.projects_root / "project.png")}}}, "bad")


def test_comfy_lab_test_plan_never_contacts_runtime_without_explicit_execute(workspace) -> None:
    service = ComfyLabService(workspace)
    result = service.test_run({"1": {"class_type": "SaveImage", "inputs": {}}}, execute=False)
    assert result["status"] == "BLOCKED"
    assert result["would_contact_comfyui"] is False
    assert result["network_contacted"] is False
    assert result["plan"]["designer_endpoint"] == "http://127.0.0.1:8188"


def test_comfy_lab_uses_configured_designer_endpoint_not_production_endpoint(workspace, tmp_path, monkeypatch) -> None:
    designer_root = tmp_path / "designer"
    designer_root.mkdir()
    (designer_root / "main.py").write_text("# local fixture", encoding="utf-8")
    python = tmp_path / "python.exe"
    python.write_bytes(b"fixture")
    monkeypatch.setenv("LOCAL_DRAMA_COMFY_DESIGNER_PYTHON", str(python))
    monkeypatch.setenv("LOCAL_DRAMA_COMFY_DESIGNER_ROOT", str(designer_root))
    monkeypatch.setenv("LOCAL_DRAMA_COMFY_DESIGNER_PORT", "8199")
    service = ComfyLabService(workspace)
    assert service.status()["endpoint"] == "http://127.0.0.1:8199"
    result = service.test_run({"1": {"class_type": "SaveImage", "inputs": {}}}, execute=False)
    assert result["plan"]["designer_endpoint"] == "http://127.0.0.1:8199"


def test_comfy_lab_api_exposes_session_and_blocks_unconfigured_start(workspace, monkeypatch) -> None:
    monkeypatch.delenv("LOCAL_DRAMA_COMFY_DESIGNER_PYTHON", raising=False)
    monkeypatch.delenv("LOCAL_DRAMA_COMFY_DESIGNER_ROOT", raising=False)
    with TestClient(create_app(workspace)) as client:
        status = client.get("/api/v1/comfy-lab/status")
        assert status.status_code == 200
        assert status.json()["status"]["formal_project_write"] is False
        session = client.get("/api/v1/comfy-lab/session")
        assert session.status_code == 200
        assert session.json()["designer"]["production_isolation"] is True
        blocked = client.post("/api/v1/comfy-lab:start")
        assert blocked.status_code == 422
        assert blocked.json()["error"]["code"] == "COMFY_LAB_LAUNCH_NOT_CONFIGURED"
