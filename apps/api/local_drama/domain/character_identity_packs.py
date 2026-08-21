"""Domain rules and value types for Character Identity Packs (PR-CUR-007)."""

from __future__ import annotations

from enum import StrEnum
from typing import Final


class SlotKind(StrEnum):
    FRONT = "FRONT"
    LEFT = "LEFT"
    RIGHT = "RIGHT"
    BACK = "BACK"
    FACE = "FACE"
    HALF_BODY = "HALF_BODY"
    FULL_BODY = "FULL_BODY"
    EXPRESSION = "EXPRESSION"


REQUIRED_THREE_VIEW_SLOTS: Final[tuple[str, ...]] = (
    SlotKind.FRONT.value,
    SlotKind.LEFT.value,
    SlotKind.RIGHT.value,
)

STANDARD_CHARACTER_SLOTS: Final[tuple[str, ...]] = (
    SlotKind.FRONT.value,
    SlotKind.LEFT.value,
    SlotKind.RIGHT.value,
    SlotKind.BACK.value,
    SlotKind.FACE.value,
)


class PackStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    RETIRED = "RETIRED"


class PackVersionStatus(StrEnum):
    DRAFT = "DRAFT"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    SUPERSEDED = "SUPERSEDED"
    RETIRED = "RETIRED"


IMMUTABLE_PACK_VERSION_STATUSES: Final[frozenset[str]] = frozenset(
    {
        PackVersionStatus.APPROVED.value,
        PackVersionStatus.SUPERSEDED.value,
        PackVersionStatus.RETIRED.value,
    }
)
