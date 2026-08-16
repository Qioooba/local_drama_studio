from __future__ import annotations

from pathlib import Path

from scripts.runtime_smoke_uat import _comfy_inventory, _model_inventory


def test_runtime_smoke_rejects_non_loopback_comfy_endpoint_without_contact() -> None:
    result = _comfy_inventory(
        {
            "runtime": {
                "comfyui_root": "C:/does-not-exist",
                "comfyui_python_executable": "C:/does-not-exist/python.exe",
                "comfyui_api": {"base_url": "https://example.invalid:8188"},
            }
        },
        execute_smoke=False,
        root=Path(".").resolve(),
    )
    assert result["status"] == "BLOCKED"
    assert result["endpoint_local"] is False
    assert result["runtime_contacted"] is False
    assert result["network_contacted"] is False


def test_runtime_smoke_model_inventory_is_reference_only(tmp_path: Path) -> None:
    model_root = tmp_path / "models"
    model_root.mkdir()
    (model_root / "user-model.safetensors").write_bytes(b"fixture")
    result = _model_inventory({"canonical_model_root": {"path": str(model_root)}})
    assert result["status"] == "PASS"
    assert result["file_count"] == 1
    assert result["reference_only"] is True
    assert result["bundled"] is False
    assert result["uploaded"] is False
    assert result["runtime_contacted"] is False
    assert result["network_contacted"] is False
