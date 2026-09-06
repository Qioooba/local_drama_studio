"""Polymorphic lifecycle contracts for runtimes that can hold the single GPU.

The coordinator owns the durable lease; adapters own the real eviction because
CUDA memory can only be released by the process that allocated it.  Adding a
runtime means adding one adapter here plus a mapping in ``job_resources`` --
the coordinator itself stays free of per-runtime branches.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Mapping, Protocol

from local_drama.application.job_resources import GpuRuntime


class GpuEvictMode(StrEnum):
    """Why an adapter is being asked to release GPU memory."""

    # Another runtime is about to take the GPU.  An unreachable external
    # service owns no VRAM, so adapters tolerate unavailability and report a
    # skip instead of failing the switch.
    SWITCH = "SWITCH"
    # Our own lease finished.  The runtime we used must be verifiably
    # released; adapters raise when ownership cannot be proven.
    RELEASE = "RELEASE"


class GpuLifecycleAdapter(Protocol):
    """Lifecycle surface of one GPU runtime."""

    @property
    def runtime(self) -> GpuRuntime:
        """The runtime this adapter manages."""

    def activate(self, context: Mapping[str, object] | None = None) -> None:
        """Make this runtime ready to serve before its lease is used.

        Daemon runtimes (ComfyUI, Ollama) are started by the operator and need
        no activation.  Managed process runtimes start their child here and
        must fail the lease when the process cannot serve. ``context`` is an
        immutable, worker-resolved activation fact such as a Profile-bound
        model locator; adapters that do not need it ignore it.
        """

    def evict(self, mode: GpuEvictMode) -> bool:
        """Release VRAM held by this runtime.

        Returns ``True`` when eviction work was performed and ``False`` when
        there was nothing to release or the runtime was unreachable in
        ``SWITCH`` mode.  ``RELEASE`` mode raises instead of skipping when
        ownership cannot be proven.
        """


class GpuLifecycleAdapterRegistry:
    """Immutable runtime-to-adapter map with deterministic eviction order.

    Every ``GpuRuntime`` member must have exactly one adapter so the
    coordinator can dispatch without default branches.  Adapter order is the
    cross-eviction order used while switching runtimes.
    """

    def __init__(self, adapters: tuple[GpuLifecycleAdapter, ...]) -> None:
        mapping: dict[GpuRuntime, GpuLifecycleAdapter] = {}
        for adapter in adapters:
            if adapter.runtime in mapping:
                raise ValueError(f"duplicate GPU lifecycle adapter for {adapter.runtime.value}")
            mapping[adapter.runtime] = adapter
        missing = set(GpuRuntime) - set(mapping)
        if missing:
            raise ValueError("missing GPU lifecycle adapters: " + ", ".join(sorted(item.value for item in missing)))
        self._adapters = adapters
        self._mapping = mapping

    def get(self, runtime: GpuRuntime) -> GpuLifecycleAdapter:
        return self._mapping[runtime]

    def others(self, runtime: GpuRuntime) -> tuple[GpuLifecycleAdapter, ...]:
        """All adapters except ``runtime``'s, in eviction order."""

        return tuple(adapter for adapter in self._adapters if adapter.runtime is not runtime)


class GpuMemoryReleaseGate(Protocol):
    """Verify that cross-runtime eviction actually returned GPU memory."""

    def wait_until_released(self) -> bool:
        """Wait for the configured free-memory threshold.

        Returns ``False`` only when the local telemetry service is unavailable;
        a reachable device that stays below the threshold raises.
        """
