from __future__ import annotations

from fastapi.testclient import TestClient

from local_drama.application.model_compatibility import ModelCompatibilityService
from local_drama.application.projects import ProjectService
from local_drama.main import create_app


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
