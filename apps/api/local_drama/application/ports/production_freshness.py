"""Read port for production freshness facts.

The application layer owns freshness semantics.  Adapters only return the
bounded, immutable/current facts needed to evaluate them.
"""

from __future__ import annotations

from typing import Any, Protocol


class ProductionFreshnessPort(Protocol):
    def load_facts(self, *, scope_type: str, scope_id: str, limit: int) -> dict[str, Any]: ...
