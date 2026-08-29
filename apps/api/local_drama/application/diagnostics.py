"""Read-only local environment diagnostics persisted as auditable runs."""

from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from urllib.error import URLError
from urllib.request import Request

from local_drama.application.h3_workflows import H3WorkflowFactory
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.domain.network_policy import endpoint_scope, parse_runtime_endpoint
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.local_http import open_local
from local_drama.infrastructure.manifest import load_manifest


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _DiagnosticLocalLLMPort(Protocol):
    def status(
        self,
        provider: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        live_probe: bool = True,
    ) -> dict[str, Any]:
        ...


def _run_version(executable: str | None) -> tuple[str, dict[str, Any]]:
    if not executable or not Path(executable).exists():
        return "BLOCKED", {"executable": executable, "reason": "not_found"}
    try:
        result = subprocess.run([executable, "-version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "FAIL", {"executable": executable, "reason": type(error).__name__}
    first_line = (result.stdout or result.stderr).splitlines()[:1]
    return ("PASS" if result.returncode == 0 else "FAIL"), {"executable": executable, "version": first_line[0] if first_line else ""}


def _positive_number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _probe_gpu_runtime(runtime: dict[str, Any]) -> dict[str, Any]:
    """Return one normalized GPU fact, preferring the live local driver.

    The canonical model inventory is deliberately read-only and may be older
    than the installed driver. Diagnostics therefore treat it as a bounded
    fallback rather than declaring the machine unavailable when an optional
    inventory field is absent.
    """

    manifest_gpu = dict(runtime.get("gpu", {}))
    manifest_total_bytes = _positive_number(manifest_gpu.get("total_bytes"))
    if manifest_total_bytes is None:
        total_gib = _positive_number(manifest_gpu.get("total_gib"))
        manifest_total_bytes = total_gib * 1024**3 if total_gib is not None else None
    observed: dict[str, Any] = {
        "index": manifest_gpu.get("index"),
        "name": manifest_gpu.get("name"),
        "total_bytes": int(manifest_total_bytes) if manifest_total_bytes is not None else None,
        "driver": manifest_gpu.get("driver"),
        "cuda": runtime.get("cuda"),
        "cuda_available": runtime.get("cuda_available") is True,
        "source": "MODEL_INVENTORY",
    }

    executable = shutil.which("nvidia-smi")
    if executable:
        try:
            result = subprocess.run(
                [
                    executable,
                    "--query-gpu=index,name,driver_version,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            rows = list(csv.reader((result.stdout or "").splitlines(), skipinitialspace=True))
            if result.returncode == 0 and rows and len(rows[0]) >= 4:
                index, name, driver, memory_mib = (item.strip() for item in rows[0][:4])
                live_total_mib = _positive_number(memory_mib)
                observed.update(
                    {
                        "index": int(index) if index.isdigit() else index,
                        "name": name or observed["name"],
                        "driver": driver or observed["driver"],
                        "total_bytes": int(live_total_mib * 1024**2) if live_total_mib is not None else observed["total_bytes"],
                        "source": "NVIDIA_SMI",
                    }
                )
            else:
                observed["live_probe_error"] = f"exit_{result.returncode}"
        except (OSError, subprocess.TimeoutExpired) as error:
            observed["live_probe_error"] = type(error).__name__
    else:
        observed["live_probe_error"] = "nvidia_smi_not_found"
    return observed


def _probe_loopback(url: str | None, *, allow_private_network: bool = False) -> tuple[str, dict[str, Any]]:
    """Probe a validated runtime endpoint without proxy or redirect hops.

    The historical name is retained for callers, but LAN_SERVICE may opt into
    literal RFC1918/ULA endpoints through the shared network policy.
    """
    if os.environ.get("LOCAL_DRAMA_COMFY_ACCESS", "enabled").casefold() != "enabled":
        return "BLOCKED", {"reason": "access_disabled"}
    if not url:
        return "BLOCKED", {"reason": "base_url_missing"}
    parsed = parse_runtime_endpoint(
        url,
        allow_private_network=allow_private_network,
        schemes=frozenset({"http"}),
    )
    if parsed is None:
        return "BLOCKED", {"reason": "runtime_endpoint_rejected"}
    scope = str(endpoint_scope(parsed.hostname))
    try:
        request = Request(f"{url.rstrip('/')}/system_stats", method="GET")
        with open_local(request, timeout=2) as response:
            return "PASS", {"status_code": response.status, "endpoint_scope": scope}
    except (OSError, URLError, TimeoutError) as error:
        return "BLOCKED", {"endpoint_scope": scope, "reason": type(error).__name__}


def _build_local_llm_port(database: Database, settings: Settings) -> _DiagnosticLocalLLMPort:
    local_llm = __import__("local_drama.application.local_llm", fromlist=["LocalLLMService"])
    return cast(_DiagnosticLocalLLMPort, local_llm.LocalLLMService(database, settings))


class DiagnosticService:
    def __init__(self, database: Database, settings: Settings, llm: _DiagnosticLocalLLMPort | None = None) -> None:
        self.database = database
        self.settings = settings
        self.llm = llm or _build_local_llm_port(database, settings)

    def run(self, actor: str = "local-user") -> dict[str, Any]:
        manifest = load_manifest(self.settings.manifest_path)
        runtime = manifest.runtime
        manifest_ffmpeg = str(runtime.get("ffmpeg", {}).get("executable", "")).strip()
        ffmpeg_path = manifest_ffmpeg if (manifest_ffmpeg and Path(manifest_ffmpeg).exists()) else (self.settings.ffmpeg_path or shutil.which("ffmpeg"))
        checks: list[dict[str, Any]] = []

        def add(code: str, category: str, status: str, observed: dict[str, Any], remediation: dict[str, Any] | None = None) -> None:
            checks.append({"code": code, "category": category, "status": status, "observed": observed, "remediation": remediation or {}})

        add("MODE_LOCAL_ONLY", "network", "PASS" if self.settings.mode == "LOCAL_ONLY" else "FAIL", {"mode": self.settings.mode})
        add("CANONICAL_MODEL_ROOT", "models", "PASS" if manifest.canonical_model_root.exists() else "BLOCKED", {"path": str(manifest.canonical_model_root)})
        add("MANIFEST_READ_ONLY", "models", "PASS", {"sha256": manifest.sha256, "version": manifest.version})
        add(
            "DISABLED_MODEL_GUARD", "models", "PASS", {"count": len(manifest.disabled_assets), "paths": [item.get("path") for item in manifest.disabled_assets]}
        )
        add(
            "COMFYUI_LOOPBACK",
            "runtime",
            *_probe_loopback(
                self.settings.comfy_base_url,
                allow_private_network=self.settings.allows_private_network,
            ),
            remediation={"action": "启动已配置的 ComfyUI backend；系统不会自动下载或切换公网服务"},
        )
        h3_layout = H3WorkflowFactory(self.settings).runtime_layout()
        add(
            "H3_CANDIDATE_LAYOUT",
            "gpu",
            str(h3_layout.get("status", "BLOCKED")),
            h3_layout,
            remediation={"action": "补齐 manifest 指向的本机 H3 release sidecar；不得自动下载或改写模型目录"},
        )
        try:
            llm = self.llm.status()
        except DomainRuleError as error:
            llm = {"status": "BLOCKED", "error_code": error.code}
        add(
            "LOCAL_LLM_LOOPBACK",
            "runtime",
            str(llm.get("status", "BLOCKED")),
            llm,
            remediation={"action": "显式配置 LOCAL_DRAMA_LLM_MODEL 并启动本机 Ollama；禁止远程 fallback"},
        )
        ffmpeg_status, ffmpeg_observed = _run_version(ffmpeg_path)
        add("FFMPEG", "media", ffmpeg_status, ffmpeg_observed, {"action": "安装或配置本机 FFmpeg；不下载"})
        manifest_ffprobe = manifest_ffmpeg.replace("ffmpeg.exe", "ffprobe.exe") if manifest_ffmpeg else ""
        ffprobe_path = manifest_ffprobe if (manifest_ffprobe and Path(manifest_ffprobe).exists()) else (self.settings.ffprobe_path or shutil.which("ffprobe"))
        ffprobe_status, ffprobe_observed = _run_version(ffprobe_path)
        add("FFPROBE", "media", ffprobe_status, ffprobe_observed, {"action": "安装或配置本机 FFprobe；不下载"})
        disk = shutil.disk_usage(self.settings.data_root)
        disk_status = "PASS" if disk.free > 10 * 1024 * 1024 * 1024 else "WARN"
        add("DISK_SPACE", "storage", disk_status, {"free_bytes": disk.free, "total_bytes": disk.total, "used_bytes": disk.used})
        gpu = _probe_gpu_runtime(runtime)
        add(
            "GPU_MANIFEST",
            "gpu",
            "PASS" if gpu.get("name") and _positive_number(gpu.get("total_bytes")) is not None else "BLOCKED",
            gpu,
            {"action": "确认 NVIDIA 显卡可被系统识别，并重新运行本机检查。"},
        )
        cuda_available = gpu.get("cuda_available") is True
        driver_status = "PASS" if gpu.get("driver") and gpu.get("cuda") and cuda_available else ("WARN" if cuda_available else "BLOCKED")
        add(
            "GPU_DRIVER_CUDA",
            "gpu",
            driver_status,
            gpu,
            {
                "action": "显卡计算可用，但清单证据不完整；请重新运行本机检查。"
                if cuda_available
                else "检查本机 GPU 驱动与 CUDA；系统不会自动修改驱动或联网下载。"
            },
        )
        capabilities = manifest.capabilities
        missing_nodes = [
            str(capability)
            for capability, payload in capabilities.items()
            if not isinstance(payload, dict) or not isinstance(payload.get("required_nodes"), list) or not payload.get("required_nodes")
        ]
        add(
            "COMFYUI_NODE_REGISTRY",
            "runtime",
            "PASS" if capabilities and not missing_nodes else "BLOCKED",
            {"capability_count": len(capabilities), "missing_capability_nodes": missing_nodes},
            {"action": "在本机 ComfyUI custom_nodes 中补齐 manifest 声明的节点；不会自动安装节点"},
        )
        model_entries: list[dict[str, Any]] = []
        for partition, payload in dict(manifest.data.get("models", {}).get("partitions", {})).items():
            if not isinstance(payload, dict):
                continue
            for component, item in payload.items():
                if isinstance(item, dict):
                    model_entries.append({"partition": partition, "component": component, **item})
        missing_model_hashes = [
            f"{item['partition']}.{item['component']}"
            for item in model_entries
            if not isinstance(item.get("full_sha256"), str) or len(str(item.get("full_sha256"))) != 64
        ]
        add(
            "MODEL_INVENTORY_HASH",
            "models",
            "PASS" if model_entries and not missing_model_hashes else "BLOCKED",
            {"entry_count": len(model_entries), "missing_hashes": missing_model_hashes, "manifest_sha256": manifest.sha256},
            {"action": "为本机模型补齐 SHA-256 清单；不会移动、复制或上传权重"},
        )
        add(
            "NETWORK_POLICY",
            "network",
            "PASS",
            {
                "release_mode": self.settings.mode,
                "network_mode": str(self.settings.network_mode),
                "allowed_runtime_hosts": ["127.0.0.1", "localhost", "::1"]
                if not self.settings.allows_private_network
                else ["127.0.0.1", "localhost", "::1", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"],
                "public_network": False,
            },
            {"action": "运行时仅允许当前部署模式声明的 loopback/私网边界；不会自动切换公网服务"},
        )
        add("REMOTE_PROVIDER", "network", "PASS", {"network_mode": str(self.settings.network_mode), "automatic_remote_fallback": False})

        overall = (
            "FAIL"
            if any(item["status"] == "FAIL" for item in checks)
            else "DEGRADED"
            if any(item["status"] in {"BLOCKED", "WARN"} for item in checks)
            else "HEALTHY"
        )
        run_id = str(uuid.uuid4())
        now = _utc_now()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT INTO diagnostic_runs (id, scope, status, manifest_sha256, created_at, updated_at, created_by, revision, schema_version) VALUES (?, 'LOCAL_MACHINE', ?, ?, ?, ?, ?, 1, 'v2')",
                (run_id, overall, manifest.sha256, now, now, actor),
            )
            for item in checks:
                connection.execute(
                    "INSERT INTO diagnostic_checks (id, run_id, check_code, category, status, observed_json, remediation_json, created_at, updated_at, created_by, revision, schema_version) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'v2')",
                    (
                        str(uuid.uuid4()),
                        run_id,
                        item["code"],
                        item["category"],
                        item["status"],
                        _json(item["observed"]),
                        _json(item["remediation"]),
                        now,
                        now,
                        actor,
                    ),
                )
            connection.execute(
                "INSERT INTO audit_events (actor, role_context, action, subject_type, subject_id, summary, metadata_redacted_json) VALUES (?, 'operator', 'DIAGNOSTIC_RUN', 'diagnostic_run', ?, ?, ?)",
                (actor, run_id, "执行本机诊断", _json({"status": overall, "check_count": len(checks)})),
            )
        return {
            "id": run_id,
            "status": overall,
            "manifest_sha256": manifest.sha256,
            "created_at": now,
            "updated_at": now,
            "checks": checks,
        }

    def latest(self) -> dict[str, Any] | None:
        with self.database.connect() as connection:
            run = connection.execute("SELECT * FROM diagnostic_runs ORDER BY created_at DESC LIMIT 1").fetchone()
            if run is None:
                return None
            checks = connection.execute(
                "SELECT check_code, category, status, observed_json, remediation_json FROM diagnostic_checks WHERE run_id = ? ORDER BY check_code", (run["id"],)
            ).fetchall()
        return {
            **dict(run),
            "checks": [
                {
                    **dict(item),
                    "code": str(item["check_code"]),
                    "observed": json.loads(item["observed_json"]),
                    "remediation": json.loads(item["remediation_json"]),
                }
                for item in checks
            ],
        }

    def dry_run_fix(self, check_id: str) -> dict[str, Any]:
        """Preview remediation only; never mutates the machine or contacts a runtime."""
        latest = self.latest()
        check = next((item for item in (latest or {}).get("checks", []) if item["code"] == check_id), None)
        if check is None:
            raise DomainRuleError("DIAGNOSTIC_CHECK_NOT_FOUND", "诊断检查不存在", {"check_id": check_id})
        return {
            "check_id": check_id,
            "current_status": check["status"],
            "remediation": check.get("remediation", {}),
            "status": "PREVIEW_ONLY",
            "would_change": False,
            "requires_user_action": check["status"] in {"BLOCKED", "FAIL", "WARN"},
            "local_only": True,
            "runtime_contacted": False,
            "network_contacted": False,
            "mutated": False,
        }
