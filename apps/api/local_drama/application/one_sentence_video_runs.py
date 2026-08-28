"""Compatibility adapter for the pre-0064 service name.

New code imports :mod:`local_drama.application.quick_generations` directly.
"""

from __future__ import annotations

from typing import Any, Callable

from local_drama.application.jobs import JobService
from local_drama.application.local_llm import LocalLLMService
from local_drama.application.media import MediaService
from local_drama.application.profiles import ProfileService
from local_drama.application.quick_generations import QuickGenerationService
from local_drama.application.workflows import WorkflowService
from local_drama.config import Settings
from local_drama.infrastructure.database.sqlite import Database


class OneSentenceVideoRunService(QuickGenerationService):
    """Preserve the legacy constructor while delegating to the new aggregate."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        runtime_probe: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(
            database,
            settings,
            profiles=ProfileService(database, settings.manifest_path),
            workflows=WorkflowService(database, settings),
            jobs=JobService(database, settings),
            media=MediaService(database, settings),
            llm=LocalLLMService(database, settings),
            runtime_probe=runtime_probe,
        )


__all__ = ["OneSentenceVideoRunService", "QuickGenerationService"]
