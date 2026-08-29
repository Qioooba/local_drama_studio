"""Canonical scheduler resource requirements for durable Jobs.

Channels describe which worker can execute a Job.  They do not describe the
physical resources consumed by the provider behind that Job.  In particular,
an Ollama request is executed by the CPU worker process but normally consumes
the same single CUDA device as ComfyUI.  This module is the single authority
that maps immutable Job facts to scheduler resources and runtime ownership.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any, Mapping
from urllib.parse import urlparse

from local_drama.domain.errors import DomainRuleError

GPU_EXCLUSIVE_RESOURCE = "GPU:0:EXCLUSIVE"
SCHEDULER_GPU_EXCLUSIVE_RESOURCE = "GPU_H3_HEAVY"
GPU_CHANNELS = frozenset({"GPU_H3", "GPU", "VIDEO_GPU"})
OLLAMA_PROVIDERS = frozenset({"OLLAMA", "OLLAMA_LOOPBACK"})
PYTORCH_JOB_TYPES = frozenset(
    {
        "ASR_ALIGNMENT",
        "EMBEDDING_INDEX",
        "LIPSYNC_GENERATION",
        "RAG_RETRIEVAL",
        "VOICE_CLONE",
    }
)


class GpuRuntime(StrEnum):
    COMFY = "COMFY"
    OLLAMA = "OLLAMA"
    PYTORCH = "PYTORCH"


def _snapshot(job: Mapping[str, Any]) -> dict[str, Any]:
    value = job.get("input_snapshot")
    if isinstance(value, dict):
        return value
    raw = job.get("input_snapshot_json")
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def gpu_runtime_for_job(job: Mapping[str, Any]) -> GpuRuntime | None:
    """Return the actual local GPU owner required by a Job, if any."""

    job_type = str(job.get("type") or "").strip().upper()
    snapshot = _snapshot(job)
    if job_type == "MODEL_PLATFORM_EXECUTION":
        runtime = str(snapshot.get("scheduler_runtime") or "").strip().upper()
        if not runtime:
            return None
        try:
            return GpuRuntime(runtime)
        except ValueError as error:
            raise DomainRuleError(
                "MP_EXECUTION_SCHEDULER_RUNTIME_INVALID",
                "V2 执行 Job 的冻结调度运行时无效。",
                {"scheduler_runtime": runtime},
            ) from error
    if job_type in PYTORCH_JOB_TYPES:
        return GpuRuntime.PYTORCH
    if job_type == "TTS_GENERATION" and str(snapshot.get("provider_kind") or "").upper() == "VOXCPM2_LOCAL":
        return GpuRuntime.PYTORCH

    channel = str(job.get("channel") or "").strip().upper()
    if channel in GPU_CHANNELS:
        return GpuRuntime.COMFY

    if job_type not in {"SCRIPT_BREAKDOWN_LOCAL_LLM", "LOCAL_LLM_PROBE"}:
        return None
    provider = str(snapshot.get("provider") or "OLLAMA_LOOPBACK").strip().upper()
    if provider not in OLLAMA_PROVIDERS:
        return None
    endpoint = urlparse(str(snapshot.get("base_url") or "http://127.0.0.1:11434"))
    if (endpoint.hostname or "").casefold() not in {"127.0.0.1", "localhost", "::1"}:
        # A private-LAN Ollama runtime owns another machine's GPU.
        return None
    if job_type == "LOCAL_LLM_PROBE" and not bool(snapshot.get("load_test", True)):
        return None
    return GpuRuntime.OLLAMA


def scheduler_resource_key(job: Mapping[str, Any], worker_id: str) -> str:
    """Map a Job to its mutually-exclusive scheduler resource."""

    if gpu_runtime_for_job(job) is not None:
        return SCHEDULER_GPU_EXCLUSIVE_RESOURCE
    channel = str(job.get("channel") or "").strip().upper()
    return f"CHANNEL:{channel}:{worker_id}"
