"""LOCAL_LLM_PROBE job handler: bounded credential-boundary probe report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Protocol

from local_drama.domain.errors import DomainRuleError


class LLMProbeClientPort(Protocol):
    """Runtime client returned by the local LLM gateway."""

    def probe(self, *, load_test: bool) -> dict[str, Any]:  # pragma: no cover - protocol boundary
        ...


class LocalLLMProbePort(Protocol):
    """Gateway capability required by LOCAL_LLM_PROBE handlers."""

    def client(
        self,
        *,
        model: str,
        provider: str,
        base_url: str,
        provider_connection_id: str | None = None,
    ) -> LLMProbeClientPort:  # pragma: no cover - protocol boundary
        ...


AtomicWriter = Callable[[Path, Callable[[Path], object]], None]
CancelCheck = Callable[[], bool]
ProgressReporter = Callable[..., bool]


def run_local_llm_probe_job(
    job: dict[str, Any],
    output_root: Path,
    *,
    work_root: Path,
    local_llm: LocalLLMProbePort,
    atomic_writer: AtomicWriter,
    cancel_check: CancelCheck,
    report_progress: ProgressReporter,
) -> tuple[str, str]:
    snapshot = job["input_snapshot"]
    credential_source = snapshot.get("credential_source")
    if snapshot.get("secret_persisted") is not False or credential_source not in {"SETTINGS_OR_ENV", "PROVIDER_CONNECTION"}:
        raise DomainRuleError("LOCAL_LLM_PROBE_SNAPSHOT_INVALID", "LLM 测试 Job 的密钥边界无效")
    if cancel_check():
        raise DomainRuleError("JOB_CANCELLED", "LLM 连接测试已取消")
    report_progress({"phase": "PROBING_RUNTIME", "percent": 25}, force=True)
    probe = local_llm.client(
        model=str(snapshot.get("model") or ""),
        provider=str(snapshot.get("provider") or ""),
        base_url=str(snapshot.get("base_url") or ""),
        provider_connection_id=str(snapshot.get("provider_connection_id") or "") or None,
    ).probe(load_test=bool(snapshot.get("load_test", True)))
    report_progress({"phase": "RECORDING_EVIDENCE", "percent": 85}, force=True)
    report = {
        "schema_version": "localdrama.local-llm-probe-report.v1",
        "job_id": str(job["id"]),
        "probe": probe,
        "secret_persisted": False,
        "credential_source": credential_source,
        "provider_connection_id": snapshot.get("provider_connection_id"),
    }
    output = output_root / "local-llm-probe-report.json"
    atomic_writer(output, lambda target: target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"))
    return "LOCAL_LLM_PROBE_REPORT", output.relative_to(work_root).as_posix()
