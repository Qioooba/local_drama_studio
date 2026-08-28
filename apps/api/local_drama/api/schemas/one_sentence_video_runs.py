"""Deprecated request-model aliases for the legacy API route."""

from __future__ import annotations

from local_drama.api.schemas.quick_generations import (
    QuickGenerationImageRerollRequest,
    QuickGenerationImageSelectRequest,
    QuickGenerationPlanRequest,
    QuickGenerationPromptRegenerateRequest,
    QuickGenerationRetryRequest,
)

OneSentenceVideoPlanRequest = QuickGenerationPlanRequest
OneSentenceVideoRetryRequest = QuickGenerationRetryRequest
OneSentencePromptRegenerateRequest = QuickGenerationPromptRegenerateRequest
OneSentenceImageRerollRequest = QuickGenerationImageRerollRequest
OneSentenceImageSelectRequest = QuickGenerationImageSelectRequest

__all__ = [
    "OneSentenceImageRerollRequest",
    "OneSentenceImageSelectRequest",
    "OneSentencePromptRegenerateRequest",
    "OneSentenceVideoPlanRequest",
    "OneSentenceVideoRetryRequest",
]
