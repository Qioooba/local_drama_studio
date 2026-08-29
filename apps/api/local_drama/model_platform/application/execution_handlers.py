"""Declared V2 capability handlers; publishing is not execution support."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from local_drama.domain.errors import DomainRuleError


@dataclass(frozen=True, slots=True)
class ExecutionHandlerDescriptor:
    code: str
    version: str
    capability_code: str
    adapter_codes: frozenset[str]
    worker_channel: str
    gpu_runtime: str | None = None


class ExecutionHandlerRegistry:
    """Single source of truth for capability/adapter worker support.

    A Profile may be published after a smoke test, but submission remains
    blocked until its exact capability and adapter have a declared handler.
    """

    def __init__(self, descriptors: Iterable[ExecutionHandlerDescriptor] = ()) -> None:
        items = tuple(descriptors)
        keys = [(item.capability_code.upper(), code) for item in items for code in item.adapter_codes]
        if len(keys) != len(set(keys)):
            raise ValueError("A capability/adapter pair may have only one V2 worker handler")
        self._items = items

    def resolve(self, capability_code: str, adapter_code: str) -> ExecutionHandlerDescriptor:
        capability = capability_code.strip().upper()
        adapter = adapter_code.strip()
        for item in self._items:
            if item.capability_code.upper() == capability and adapter in item.adapter_codes:
                return item
        raise DomainRuleError(
            "MP_EXECUTION_HANDLER_UNAVAILABLE",
            "该已发布 Profile 尚无与当前 adapter 匹配的 V2 Worker handler，不能提交执行。",
            {"capability_code": capability, "adapter_code": adapter},
        )

    def supported_capabilities(self) -> frozenset[str]:
        return frozenset(item.capability_code.upper() for item in self._items)
