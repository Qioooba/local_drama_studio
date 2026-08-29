from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.diagnostics import _probe_gpu_runtime
from local_drama.application.model_compatibility import ModelCompatibilityService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


def test_gpu_diagnostics_prefers_live_driver_facts(monkeypatch) -> None:
    monkeypatch.setattr("local_drama.application.diagnostics.shutil.which", lambda executable: "nvidia-smi" if executable == "nvidia-smi" else None)
    monkeypatch.setattr(
        "local_drama.application.diagnostics.subprocess.run",
        lambda *args, **kwargs: type("Result", (), {"returncode": 0, "stdout": "0, NVIDIA GeForce RTX 3090 Ti, 610.62, 24564\n", "stderr": ""})(),
    )

    observed = _probe_gpu_runtime(
        {
            "cuda": "13.0",
            "cuda_available": True,
            "gpu": {"index": 0, "name": "stale inventory name", "total_gib": 23.99},
        }
    )

    assert observed["source"] == "NVIDIA_SMI"
    assert observed["name"] == "NVIDIA GeForce RTX 3090 Ti"
    assert observed["driver"] == "610.62"
    assert observed["total_bytes"] == 24564 * 1024**2


def test_gpu_diagnostics_normalizes_inventory_gib_without_faking_zero(monkeypatch) -> None:
    monkeypatch.setattr("local_drama.application.diagnostics.shutil.which", lambda executable: None)

    observed = _probe_gpu_runtime(
        {
            "cuda": "13.0",
            "cuda_available": True,
            "gpu": {"index": 0, "name": "NVIDIA GeForce RTX 3090 Ti", "total_gib": 23.99},
        }
    )

    assert observed["source"] == "MODEL_INVENTORY"
    assert observed["total_bytes"] == int(23.99 * 1024**3)
    assert observed["driver"] is None
    assert observed["live_probe_error"] == "nvidia_smi_not_found"


def test_diagnostics_exposes_blueprint_checks_and_dry_run_fix(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        run_response = client.post("/api/v1/diagnostics/runs")
        assert run_response.status_code == 200
        checks = {item["code"]: item for item in run_response.json()["run"]["checks"]}
        assert {"GPU_DRIVER_CUDA", "COMFYUI_NODE_REGISTRY", "MODEL_INVENTORY_HASH", "NETWORK_POLICY"} <= set(checks)
        latest = client.get("/api/v1/diagnostics")
        assert latest.status_code == 200
        preview = client.post("/api/v1/diagnostics/MODEL_INVENTORY_HASH:dry-run-fix")
        assert preview.status_code == 200
        assert preview.json()["preview"]["status"] == "PREVIEW_ONLY"
        assert preview.json()["preview"]["would_change"] is False
        assert preview.json()["preview"]["network_contacted"] is False


def test_offline_model_scan_hashes_candidates_without_registering_or_copying(workspace, database) -> None:
    root = workspace.work_root / "offline-model-scan"
    root.mkdir(parents=True)
    (root / "demo_fp16.safetensors").write_bytes(b"local-model")
    (root / "ignored.txt").write_text("not a model", encoding="utf-8")
    before = int(database.connect().execute("SELECT COUNT(*) FROM model_artifacts").fetchone()[0])
    scan = ModelCompatibilityService.scan_local_directory(str(root))
    assert scan["scanned_count"] == 1
    assert scan["items"][0]["quantization_hint"] == "FP16"
    assert scan["items"][0]["copied"] is False
    assert scan["items"][0]["uploaded"] is False
    after = int(database.connect().execute("SELECT COUNT(*) FROM model_artifacts").fetchone()[0])
    assert after == before

    project = ProjectService(database, workspace.projects_root).create_project(
        code="ops_registry", title="Ops registry", episode_count=1, aspect_ratio="16:9",
        fps_num=24, fps_den=1, target_duration_ms=60_000, allow_unconfigured_capabilities=True,
    )
    with TestClient(create_app(workspace)) as client:
        response = client.post("/api/v1/model-registry:scan", json={"root_path": str(root), "max_files": 10})
    assert response.status_code == 200
    assert response.json()["scan"]["read_only"] is True
    assert str(project["id"])


def test_capacity_snapshot_includes_local_metrics_without_mutation(workspace, database) -> None:
    with TestClient(create_app(workspace)) as client:
        response = client.get("/api/v1/capacity/snapshot")
    assert response.status_code == 200
    snapshot = response.json()["snapshot"]
    assert {"gpu", "disk", "duration_seconds", "failure_rate", "retry_rate", "review"} <= set(snapshot)
    assert snapshot["observation_status"] == "OBSERVED_NOT_BENCHMARKED"
    assert snapshot["network_contacted"] is False
    assert snapshot["mutated"] is False
