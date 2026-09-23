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
        # ``load_manifest`` already refuses a manifest without a non-empty version;
        # this stays defensive so a hand-built snapshot cannot raise a KeyError.
        return str(self.data.get("manifest_version") or "")

    @property
    def canonical_model_root(self) -> Path:
        canonical = self.data.get("canonical_model_root")
        if not isinstance(canonical, dict):
            return Path("")
        return Path(str(canonical.get("path") or ""))

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


def _require_mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestValidationError(
            f"model_manifest.json 的 {field} 必须是对象", 
        )
    return value


def _validate_manifest_structure(data: dict[str, Any]) -> None:
    """Prove every field this module reads actually has the declared shape.

    ``load_manifest`` used to check only the outer type, ``manifest_type``,
    ``read_only_inventory`` and ``canonical_model_root.path``; everything else was
    assumed to exist.  A manifest that is *valid JSON* but semantically wrong — a
    missing ``manifest_version``, ``runtime: null``, ``models: null`` — therefore
    raised ``KeyError``/``TypeError``/``AttributeError`` straight through the
    optional startup step and took the whole API down, even though local import,
    review and CPU post-production do not need the model manifest at all.

    Validation happens here, before any database write, so a rejected manifest can
    never leave a half-synced state.
    """

    version = data.get("manifest_version")
    if not isinstance(version, str) or not version.strip():
        raise ManifestValidationError("model_manifest.json 的 manifest_version 必须是非空字符串")
    for field in ("runtime", "h3_capabilities", "authoritative_current_state"):
        if field in data:
            _require_mapping(data[field], field=field)
    runtime = data.get("runtime")
    if runtime is not None:
        comfy = runtime.get("comfyui_api")
        if comfy is not None and not isinstance(comfy, dict):
            raise ManifestValidationError("model_manifest.json 的 runtime.comfyui_api 必须是对象")
    current = data.get("authoritative_current_state")
    if current is not None:
        for field in ("route_status", "generation_boundary"):
            value = current.get(field)
            if value is not None and not isinstance(value, (str, dict)):
                raise ManifestValidationError(
                    f"model_manifest.json 的 authoritative_current_state.{field} 类型不合法"
                )
        forbidden = current.get("forbidden_assets")
        if forbidden is not None and not isinstance(forbidden, list):
            raise ManifestValidationError(
                "model_manifest.json 的 authoritative_current_state.forbidden_assets 必须是数组"
            )
    models = data.get("models")
    if models is not None:
        _require_mapping(models, field="models")
        partitions = models.get("partitions")
        # ``ProfileService.sync_manifest`` iterates ``partitions.items()``, so a list
        # here is a semantic error: it used to reach the sync loop and raise a raw
        # ``AttributeError`` out of the optional startup step.
        if partitions is not None and not isinstance(partitions, dict):
            raise ManifestValidationError(
                "model_manifest.json 的 models.partitions 必须是对象（分区名 -> 组件）"
            )


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
    _validate_manifest_structure(data)
    return ManifestSnapshot(resolved, hashlib.sha256(raw).hexdigest(), data)
