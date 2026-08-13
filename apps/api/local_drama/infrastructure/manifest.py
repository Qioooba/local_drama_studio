"""Read-only access to the authoritative local model manifest."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ManifestValidationError(RuntimeError):
    """Raised when the local manifest is missing or violates its contract."""


@dataclass(frozen=True)
class ManifestSnapshot:
    path: Path
    sha256: str
    data: dict[str, Any]

    @property
    def version(self) -> str:
        return str(self.data["manifest_version"])

    @property
    def canonical_model_root(self) -> Path:
        return Path(str(self.data["canonical_model_root"]["path"]))

    @property
    def worker_policy(self) -> str:
        current = self.data.get("authoritative_current_state", {})
        return str(current.get("worker_policy", ""))

    @property
    def route_status(self) -> dict[str, str]:
        current = self.data.get("authoritative_current_state", {})
        return {str(key): str(value) for key, value in current.get("route_status", {}).items()}

    @property
    def runtime(self) -> dict[str, Any]:
        return dict(self.data.get("runtime", {}))

    @property
    def capabilities(self) -> dict[str, Any]:
        return dict(self.data.get("h3_capabilities", {}))

    @property
    def disabled_assets(self) -> list[dict[str, Any]]:
        current = self.data.get("authoritative_current_state", {})
        return [dict(item) for item in current.get("forbidden_assets", [])]

    def as_public_dict(self) -> dict[str, Any]:
        runtime = self.runtime
        comfy = dict(runtime.get("comfyui_api", {}))
        return {
            "manifest_version": self.version,
            "manifest_sha256": self.sha256,
            "read_only_inventory": bool(self.data.get("read_only_inventory", False)),
            "canonical_model_root": str(self.canonical_model_root),
            "worker_policy": self.worker_policy,
            "route_status": self.route_status,
            "comfyui": {
                "base_url": comfy.get("base_url"),
                "port_8188_listening": bool(comfy.get("port_8188_listening", False)),
                "runtime_health_status": comfy.get("runtime_health_status"),
            },
            "disabled_assets": self.disabled_assets,
            "generation_boundary": self.data.get("authoritative_current_state", {}).get("generation_boundary"),
        }


def load_manifest(path: Path) -> ManifestSnapshot:
    resolved = path.resolve()
    try:
        raw = resolved.read_bytes()
        data = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestValidationError(f"无法读取本机 model_manifest.json: {resolved}") from error
    if not isinstance(data, dict) or data.get("manifest_type") != "canonical_model_inventory":
        raise ManifestValidationError("model_manifest.json 不是 canonical_model_inventory")
    if data.get("read_only_inventory") is not True:
        raise ManifestValidationError("模型 manifest 必须声明 read_only_inventory=true")
    canonical = data.get("canonical_model_root")
    if not isinstance(canonical, dict) or not canonical.get("path"):
        raise ManifestValidationError("模型 manifest 缺少 canonical_model_root.path")
    return ManifestSnapshot(resolved, hashlib.sha256(raw).hexdigest(), data)
