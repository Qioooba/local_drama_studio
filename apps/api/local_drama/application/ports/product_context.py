"""Read ports for the creator-facing v2 application context.

The application layer owns projection semantics; infrastructure only returns
bounded facts.  This keeps new v2 queries from depending on the concrete
SQLite ``Database`` object.
"""

from __future__ import annotations

from typing import Any, Protocol


class ProductContextReadPort(Protocol):
    def app_context(self, project_id: str | None, episode_id: str | None) -> dict[str, Any]: ...

    def project_overview_facts(self, project_id: str) -> dict[str, Any]: ...
