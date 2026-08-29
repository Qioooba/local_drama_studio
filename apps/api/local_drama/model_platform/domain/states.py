"""Independent lifecycle states used by the Model Platform catalog."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PresenceStatus(str, Enum):
    DISCOVERED = "DISCOVERED"
    PRESENT = "PRESENT"
    MISSING = "MISSING"
    STALE = "STALE"


class IntegrityStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    HASHING = "HASHING"
    VERIFIED = "VERIFIED"
    INCOMPLETE = "INCOMPLETE"
    CORRUPT = "CORRUPT"
    QUARANTINED = "QUARANTINED"


class RuntimeStatus(str, Enum):
    UNKNOWN = "UNKNOWN"
    READY = "READY"
    BUSY = "BUSY"
    DEGRADED = "DEGRADED"
    UNREACHABLE = "UNREACHABLE"


class ValidationStatus(str, Enum):
    NOT_RUN = "NOT_RUN"
    COMPATIBLE = "COMPATIBLE"
    SMOKE_PASSED = "SMOKE_PASSED"
    FAILED = "FAILED"


class PublicationStatus(str, Enum):
    NONE = "NONE"
    DRAFT = "DRAFT"
    CANDIDATE = "CANDIDATE"
    PUBLISHED = "PUBLISHED"
    DEPRECATED = "DEPRECATED"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class AvailabilitySnapshot:
    """The five independent states required to decide a route is executable.

    A catalog card must expose these inputs instead of collapsing them into a
    misleading single green badge.  ``route_available`` comes from the active
    runtime adapter/worker capability registry.
    """

    presence: PresenceStatus
    integrity: IntegrityStatus
    runtime: RuntimeStatus
    validation: ValidationStatus
    publication: PublicationStatus
    route_available: bool

    @property
    def executable(self) -> bool:
        return (
            self.presence is PresenceStatus.PRESENT
            and self.integrity is IntegrityStatus.VERIFIED
            and self.runtime in (RuntimeStatus.READY, RuntimeStatus.BUSY)
            and self.validation is ValidationStatus.SMOKE_PASSED
            and self.publication is PublicationStatus.PUBLISHED
            and self.route_available
        )

    @property
    def blockers(self) -> tuple[str, ...]:
        blockers: list[str] = []
        if self.presence is not PresenceStatus.PRESENT:
            blockers.append(f"presence:{self.presence.value}")
        if self.integrity is not IntegrityStatus.VERIFIED:
            blockers.append(f"integrity:{self.integrity.value}")
        if self.runtime not in (RuntimeStatus.READY, RuntimeStatus.BUSY):
            blockers.append(f"runtime:{self.runtime.value}")
        if self.validation is not ValidationStatus.SMOKE_PASSED:
            blockers.append(f"validation:{self.validation.value}")
        if self.publication is not PublicationStatus.PUBLISHED:
            blockers.append(f"publication:{self.publication.value}")
        if not self.route_available:
            blockers.append("route:UNAVAILABLE")
        return tuple(blockers)
