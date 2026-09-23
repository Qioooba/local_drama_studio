"""Canonical scheduler resource requirements for durable Jobs.

Channels describe which worker can execute a Job.  They do not describe the
physical resources consumed by the provider behind that Job.  In particular,
an Ollama request is executed by the CPU worker process but normally consumes
the same single CUDA device as ComfyUI.  This module is the single authority
that maps immutable Job facts to scheduler resources and runtime ownership.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping
from urllib.parse import urlparse

from local_drama.domain.errors import DomainRuleError

GPU_EXCLUSIVE_RESOURCE = "GPU:0:EXCLUSIVE"
SCHEDULER_GPU_EXCLUSIVE_RESOURCE = "GPU_H3_HEAVY"
SCHEDULER_GPU_POOL_RESOURCE = "GPU_H3_HEAVY"
SCHEDULER_GPU_SLOT_SEPARATOR = "#"
# One physical CUDA device is the documented product invariant: a policy that
# does not declare a wider pool still gets exactly one mutually-exclusive slot.
SCHEDULER_GPU_DEFAULT_CONCURRENCY = 1
GPU_CHANNELS = frozenset({"GPU_H3", "GPU", "VIDEO_GPU"})
OLLAMA_PROVIDERS = frozenset({"OLLAMA", "OLLAMA_LOOPBACK"})
LLAMA_CPP_PROVIDERS = frozenset({"LLAMA_CPP_MANAGED"})
PYTORCH_JOB_TYPES = frozenset(
    {
        "ASR_ALIGNMENT",
        "EMBEDDING_INDEX",
        "LIPSYNC_GENERATION",
        "RAG_RETRIEVAL",
        "VOICE_CLONE",
        # The explainer narration stages run the offline VoxCPM2 / Qwen3 subprocess
        # runtimes: CPU-channel jobs that own the same single CUDA device, so they
        # must take the PYTORCH lease exactly like LIPSYNC_GENERATION does.
        "NARRATION_TTS",
        "NARRATION_ALIGN",
    }
)


class GpuRuntime(StrEnum):
    COMFY = "COMFY"
    OLLAMA = "OLLAMA"
    PYTORCH = "PYTORCH"
    # A llama-server child process owned by this application's GPU runtime
    # coordinator; distinct from OLLAMA because eviction is process exit.
    LLAMA_CPP = "LLAMA_CPP"


@dataclass(frozen=True, slots=True)
class ExecutionGpuPolicy:
    """The GPU ownership a published Profile resource policy declares.

    ``gpu_runtime`` is the declared local CUDA owner (``None`` means the policy
    is silent and the execution handler descriptor stays authoritative).
    ``exclusive_gpu`` is an explicit single-device claim; ``gpu_concurrency``
    is the declared number of concurrent heavy-GPU slots.
    """

    gpu_runtime: GpuRuntime | None = None
    exclusive_gpu: bool = False
    gpu_concurrency: int | None = None


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
    """Return the actual local GPU owner required by a Job, if any.

    The frozen Profile resource policy is authoritative; the execution handler
    descriptor (frozen as ``scheduler_runtime``) is the fallback used when the
    policy is absent or silent.  A contradictory policy fails closed.
    """

    job_type = str(job.get("type") or "").strip().upper()
    snapshot = _snapshot(job)
    if job_type == "MODEL_PLATFORM_EXECUTION":
        runtime = _scheduler_runtime_from_snapshot(snapshot)
        job_id = _optional_job_identity(job)
        policy = _read_policy(snapshot.get("scheduler_resource_policy"), context={"job_id": job_id, "source": "SCHEDULER_RESOURCE_POLICY"})
        return resolve_gpu_runtime(policy, declared_runtime=runtime, job_id=job_id)
    if job_type in PYTORCH_JOB_TYPES:
        return GpuRuntime.PYTORCH
    if job_type == "TTS_GENERATION" and str(snapshot.get("provider_kind") or "").upper() == "VOXCPM2_LOCAL":
        return GpuRuntime.PYTORCH

    channel = str(job.get("channel") or "").strip().upper()
    if channel in GPU_CHANNELS:
        return GpuRuntime.COMFY

    if job_type not in {"SCRIPT_BREAKDOWN_LOCAL_LLM", "LOCAL_LLM_PROBE"}:
        return None
    provider = str(snapshot.get("provider") or "LLAMA_CPP_MANAGED").strip().upper()
    if provider in LLAMA_CPP_PROVIDERS:
        # The managed llama-server endpoint is spawned by this machine's GPU
        # runtime coordinator on a settings-derived loopback port; the lease
        # owns its lifecycle. A non-loading probe never starts the server.
        if job_type == "LOCAL_LLM_PROBE" and not bool(snapshot.get("load_test", True)):
            return None
        return GpuRuntime.LLAMA_CPP
    if provider not in OLLAMA_PROVIDERS:
        return None
    endpoint = urlparse(str(snapshot.get("base_url") or "http://127.0.0.1:11434"))
    if (endpoint.hostname or "").casefold() not in {"127.0.0.1", "localhost", "::1"}:
        # A private-LAN Ollama runtime owns another machine's GPU.
        return None
    if job_type == "LOCAL_LLM_PROBE" and not bool(snapshot.get("load_test", True)):
        return None
    return GpuRuntime.OLLAMA


def resolve_gpu_runtime(
    policy: ExecutionGpuPolicy | None,
    *,
    declared_runtime: GpuRuntime | None,
    job_id: str | None = None,
) -> GpuRuntime | None:
    """Resolve the physical GPU owner from policy first and handler fallback.

    ``DomainRuleError`` with a stable code is raised instead of guessing when
    the policy contradicts the handler descriptor, so a mis-declared Profile
    can never silently acquire a different runtime than it announced.
    """

    policy = policy or ExecutionGpuPolicy()
    policy_runtime = policy.gpu_runtime
    if policy_runtime is not None and declared_runtime is not None and policy_runtime is not declared_runtime:
        raise DomainRuleError(
            "MP_EXECUTION_GPU_POLICY_CONFLICT",
            "资源策略声明的显卡运行时与执行 handler 描述符不一致，禁止按未声明的运行时占用显卡。",
            {
                "job_id": job_id,
                "policy_gpu_runtime": policy_runtime.value,
                "scheduler_runtime": declared_runtime.value,
            },
        )
    effective = policy_runtime if policy_runtime is not None else declared_runtime
    if policy.exclusive_gpu and effective is None:
        raise DomainRuleError(
            "MP_EXECUTION_GPU_POLICY_EXCLUSIVE_WITHOUT_RUNTIME",
            "资源策略声明 exclusive_gpu=true，但没有声明任何本机显卡运行时。",
            {"job_id": job_id, "policy_gpu_runtime": None, "scheduler_runtime": None},
        )
    return effective


def resolve_profile_gpu_policy(
    resource_policy: Mapping[str, Any] | None,
    *,
    handler_gpu_runtime: str | None,
    job_id: str | None = None,
) -> tuple[GpuRuntime | None, ExecutionGpuPolicy]:
    """Fail-closed resolution of a Profile policy against its handler descriptor.

    Submissions call this before a Job becomes visible so a contradictory
    ``resource_policy`` is refused at the boundary instead of silently
    scheduling the wrong physical runtime.
    """

    policy = policy_from_resource_policy(resource_policy or {}, context={"job_id": job_id, "source": "PROFILE_RESOURCE_POLICY"})
    declared = None
    raw = str(handler_gpu_runtime or "").strip().upper()
    if raw:
        try:
            declared = GpuRuntime(raw)
        except ValueError as error:
            raise DomainRuleError(
                "MP_EXECUTION_SCHEDULER_RUNTIME_INVALID",
                "执行 handler 声明的显卡运行时无效。",
                {"job_id": job_id, "scheduler_runtime": raw},
            ) from error
    runtime = resolve_gpu_runtime(policy, declared_runtime=declared, job_id=job_id)
    # Validate the concurrency declaration here as well so a widened or
    # contradictory pool is refused before the Job is queued.
    gpu_runtime_concurrency(policy)
    return runtime, policy


def gpu_policy_for_job(job: Mapping[str, Any]) -> ExecutionGpuPolicy:
    """Return the GPU resource policy frozen into a Job snapshot, if any."""

    snapshot = _snapshot(job)
    policy: object = snapshot.get("scheduler_resource_policy")
    if policy is None:
        # V2 submission freezes the resolved Profile policy inside the linked
        # execution snapshot as well; read it without changing job payloads
        # created before the scheduler copy existed.
        candidate = snapshot.get("execution_snapshot")
        if isinstance(candidate, Mapping):
            policy = candidate.get("resource_policy")
    return _read_policy(policy, context={"job_id": _optional_job_identity(job), "source": "JOB_SNAPSHOT"}) or ExecutionGpuPolicy()


def gpu_runtime_concurrency(policy: ExecutionGpuPolicy | None) -> int:
    """Return the number of concurrent heavy-GPU slots a policy permits."""

    policy = policy or ExecutionGpuPolicy()
    declared = policy.gpu_concurrency
    if policy.exclusive_gpu:
        # ``exclusive_gpu`` is an explicit single-device claim; it can only
        # narrow a pool and is refused if the policy asks for a wider one.
        if declared is not None and declared > 1:
            raise _invalid_policy_error(
                policy.gpu_concurrency,
                {"field": "gpu_heavy_concurrency", "exclusive_gpu": True},
            )
        return 1
    if declared is None:
        return SCHEDULER_GPU_DEFAULT_CONCURRENCY
    return declared


def scheduler_resource_key(job: Mapping[str, Any], worker_id: str) -> str:
    """Map a Job to its mutually-exclusive scheduler resource.

    Heavy-GPU jobs share policy-derived slots so the number of concurrently
    running heavy tasks is bounded by the frozen resource policy rather than by
    a hard-coded constant.  With the default (one) slot the key stays exactly
    ``GPU_H3_HEAVY`` for every heavy-GPU runtime, preserving the single global
    GPU gate.
    """

    if gpu_runtime_for_job(job) is not None:
        concurrency = gpu_runtime_concurrency(gpu_policy_for_job(job))
        if concurrency <= 1:
            return SCHEDULER_GPU_EXCLUSIVE_RESOURCE
        return scheduler_gpu_slot_resource_key(
            SCHEDULER_GPU_POOL_RESOURCE,
            gpu_slot_index(_job_identity(job), concurrency),
            concurrency,
        )
    channel = str(job.get("channel") or "").strip().upper()
    return f"CHANNEL:{channel}:{worker_id}"


def scheduler_gpu_slot_resource_key(pool: str, slot: int, concurrency: int) -> str:
    """Return the scheduler resource key of one slot inside a GPU pool."""

    if slot < 1 or slot > concurrency:
        raise DomainRuleError(
            "SCHEDULER_GPU_SLOT_OUT_OF_RANGE",
            "显卡调度槽位超出资源策略声明的并发上限。",
            {"pool": pool, "slot": slot, "concurrency": concurrency},
        )
    return f"{pool}{SCHEDULER_GPU_SLOT_SEPARATOR}{slot}"


def gpu_slot_index(identifier: str, concurrency: int) -> int:
    """Deterministically and stably assign one Job to a bounded GPU slot."""

    if concurrency <= 1:
        return 1
    # crc32 is stable across processes and Python versions, unlike hash().
    return zlib.crc32(identifier.encode("utf-8")) % concurrency + 1


def _optional_job_identity(job: Mapping[str, Any]) -> str | None:
    identity = str(job.get("id") or job.get("job_id") or job.get("idempotency_key") or "").strip()
    return identity or None


def _job_identity(job: Mapping[str, Any]) -> str:
    identity = _optional_job_identity(job)
    if identity is None:
        raise DomainRuleError(
            "SCHEDULER_JOB_IDENTITY_REQUIRED",
            "显卡调度需要 Job 身份才能稳定分配资源槽位。",
        )
    return identity


def _scheduler_runtime_from_snapshot(snapshot: Mapping[str, Any]) -> GpuRuntime | None:
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


def _read_policy(value: object, *, context: Mapping[str, Any]) -> ExecutionGpuPolicy | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as error:
            raise DomainRuleError(
                "MP_EXECUTION_RESOURCE_POLICY_INVALID",
                "冻结的资源策略不是合法 JSON 对象。",
                dict(context),
            ) from error
        value = decoded
    if not isinstance(value, Mapping):
        raise DomainRuleError(
            "MP_EXECUTION_RESOURCE_POLICY_INVALID",
            "冻结的资源策略必须是 JSON 对象。",
            {**context, "type": type(value).__name__},
        )
    return policy_from_resource_policy(value, context=context)


def policy_from_resource_policy(
    resource_policy: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
) -> ExecutionGpuPolicy:
    """Parse ``gpu_runtime`` / ``exclusive_gpu`` / ``gpu_heavy_concurrency``."""

    details = dict(context or {})
    if not resource_policy:
        return ExecutionGpuPolicy()
    raw_runtime = resource_policy.get("gpu_runtime")
    runtime: GpuRuntime | None = None
    if raw_runtime is not None and str(raw_runtime).strip():
        try:
            runtime = GpuRuntime(str(raw_runtime).strip().upper())
        except ValueError as error:
            raise DomainRuleError(
                "MP_EXECUTION_RESOURCE_POLICY_INVALID",
                "资源策略声明的 gpu_runtime 不是本机已知的显卡运行时。",
                {**details, "gpu_runtime": str(raw_runtime)},
            ) from error
    exclusive_raw = resource_policy.get("exclusive_gpu", False)
    if not isinstance(exclusive_raw, bool):
        raise DomainRuleError(
            "MP_EXECUTION_RESOURCE_POLICY_INVALID",
            "资源策略的 exclusive_gpu 必须是布尔值。",
            {**details, "exclusive_gpu": exclusive_raw},
        )
    concurrency = _read_concurrency(resource_policy, details)
    return ExecutionGpuPolicy(gpu_runtime=runtime, exclusive_gpu=exclusive_raw, gpu_concurrency=concurrency)


def _read_concurrency(resource_policy: Mapping[str, Any], details: Mapping[str, Any]) -> int | None:
    for key in ("gpu_heavy_concurrency", "gpu_concurrency"):
        value = resource_policy.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise _invalid_policy_error(value, {**details, "field": key})
        return value
    return None


def _invalid_policy_error(value: object, details: Mapping[str, Any]) -> DomainRuleError:
    return DomainRuleError(
        "MP_EXECUTION_RESOURCE_POLICY_INVALID",
        "资源策略的显卡并发必须是大于等于 1 的整数，且不能与 exclusive_gpu=true 冲突。",
        {**details, "value": value},
    )
