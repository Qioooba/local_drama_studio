"""Read-only local environment diagnostics persisted as auditable runs."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from local_drama.application.h3_workflows import H3WorkflowFactory
from local_drama.application.local_llm import LocalLLMService
from local_drama.config import Settings
from local_drama.domain.errors import DomainRuleError
from local_drama.infrastructure.database.sqlite import Database
from local_drama.infrastructure.manifest import load_manifest


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _run_version(executable: str | None) -> tuple[str, dict[str, Any]]:
    if not executable or not Path(executable).exists():
        return "BLOCKED", {"executable": executable, "reason": "not_found"}
    try:
        result = subprocess.run([executable, "-version"], capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        return "FAIL", {"executable": executable, "reason": type(error).__name__}
    first_line = (result.stdout or result.stderr).splitlines()[:1]
    return ("PASS" if result.returncode == 0 else "FAIL"), {"executable": executable, "version": first_line[0] if first_line else ""}


def _probe_loopback(url: str | None) -> tuple[str, dict[str, Any]]:
    if os.environ.get("LOCAL_DRAMA_COMFY_ACCESS", "enabled").casefold() != "enabled":
        return "BLOCKED", {"reason": "access_disabled"}
    if not url:
        return "BLOCKED", {"reason": "base_url_missing"}
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        return "BLOCKED", {"reason": "non_loopback_url_rejected"}
    try:
        request = Request(f"{url.rstrip('/')}/system_stats", method="GET")
        with urlopen(request, timeout=2) as response:  # noqa: S310 - host is checked as loopback above
            return "PASS", {"status_code": response.status, "loopback": True}
    except (OSError, URLError, TimeoutError) as error:
        return "BLOCKED", {"loopback": True, "reason": type(error).__name__}


class DiagnosticService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def run(self, actor: str = "local-user") -> dict[str, Any]:
        manifest = load_manifest(self.settings.manifest_path)
        runtime = manifest.runtime
        comfy_api = dict(runtime.get("comfyui_api", {}))
        ffmpeg_path = str(runtime.get("ffmpeg", {}).get("executable", "")) or shutil.which("ffmpeg")
        checks: list[dict[str, Any]] = []

        def add(code: str, category: str, status: str, observed: dict[str, Any], remediation: dict[str, Any] | None = None) -> None:
            checks.append({"code": code, "category": category, "status": status, "observed": observed, "remediation": remediation or {}})

        add("MODE_LOCAL_ONLY", "network", "PASS" if self.settings.mode == "LOCAL_ONLY" else "FAIL", {"mode": self.settings.mode})
        add("CANONICAL_MODEL_ROOT", "models", "PASS" if manifest.canonical_model_root.exists() else "BLOCKED", {"path": str(manifest.canonical_model_root)})
        add("MANIFEST_READ_ONLY", "models", "PASS", {"sha256": manifest.sha256, "version": manifest.version})
        add(
            "DISABLED_MODEL_GUARD", "models", "PASS", {"count": len(manifest.disabled_assets), "paths": [item.get("path") for item in manifest.disabled_assets]}
        )
        add("COMFYUI_LOOPBACK", "runtime", *_probe_loopback(comfy_api.get("base_url")), remediation={"action": "启动本机 ComfyUI backend；禁止联网自动修复"})
        h3_layout = H3WorkflowFactory(self.settings).runtime_layout()
        add(
            "H3_CANDIDATE_LAYOUT",
            "gpu",
            str(h3_layout.get("status", "BLOCKED")),
            h3_layout,
            remediation={"action": "补齐 manifest 指向的本机 H3 release sidecar；不得自动下载或改写模型目录"},
        )
        try:
            llm = LocalLLMService(self.database, self.settings).status()
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
        ffprobe_path = str(runtime.get("ffmpeg", {}).get("executable", "")).replace("ffmpeg.exe", "ffprobe.exe") or shutil.which("ffprobe")
        ffprobe_status, ffprobe_observed = _run_version(ffprobe_path)
        add("FFPROBE", "media", ffprobe_status, ffprobe_observed, {"action": "安装或配置本机 FFprobe；不下载"})
        disk = shutil.disk_usage(self.settings.data_root)
        disk_status = "PASS" if disk.free > 10 * 1024 * 1024 * 1024 else "WARN"
        add("DISK_SPACE", "storage", disk_status, {"free_bytes": disk.free, "total_bytes": disk.total, "used_bytes": disk.used})
        gpu = dict(runtime.get("gpu", {}))
        add(
            "GPU_MANIFEST",
            "gpu",
            "PASS" if gpu.get("name") and int(gpu.get("total_bytes", 0)) > 0 else "BLOCKED",
            {"name": gpu.get("name"), "total_bytes": gpu.get("total_bytes"), "cuda": runtime.get("cuda")},
        )
        add("REMOTE_PROVIDER", "network", "PASS", {"mode": "LOCAL_ONLY", "remote_provider": "disabled"})

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
        return {"id": run_id, "status": overall, "manifest_sha256": manifest.sha256, "checks": checks}

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
            "checks": [{**dict(item), "observed": json.loads(item["observed_json"]), "remediation": json.loads(item["remediation_json"])} for item in checks],
        }
