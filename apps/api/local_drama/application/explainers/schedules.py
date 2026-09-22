"""Scheduled production for the explainer factory (local Runtime Host / Worker).

This module owns the schedule half of the 解说工厂 (Explainer Factory): trigger
expansion into UTC instants, idempotent materialisation of trigger points, the
database lease + fencing token that lets exactly one worker produce a point, the
catch-up / concurrency / budget gates, topic de-duplication and step-failure
classification.

What this module deliberately does **NOT** do:

* It is **not a second job queue**.  It never inserts ``jobs`` /
  ``job_attempts`` rows, never runs a step and never waits on a GPU.  ``tick``
  only *decides* which trigger point may be produced next and reserves it with a
  database lease; the production itself stays with the existing automation
  workflow (:meth:`AutomationWorkflowService.start_run`, see
  :meth:`ExplainerScheduleService.build_workflow_handoff`).
* It is **not a browser timer** and **not an external reminder service**.  No
  HTTP handler, no ``setTimeout``, no cron daemon and no cloud scheduler
  participates: the Runtime Host/Worker calls :meth:`ExplainerScheduleService.tick`
  and the truth lives in the ``explainer_schedules`` / ``schedule_occurrences``
  tables only.
* It is **not a topic generator**.  When sources are insufficient the occurrence
  is skipped with a reason; nothing here ever invents facts to fill a quota.

Three invariants the rest of the system must not re-implement:

1. **The only de-duplication key is ``(schedule_id, scheduled_for)``.**
   ``config_revision`` is a frozen snapshot recorded at materialisation time and
   is deliberately absent from the key, so editing a schedule can never re-run a
   trigger point that already exists.
2. **Every trigger point is stored in UTC** while the rule is expressed in an
   IANA zone.  A repeated local time (DST fall-back) executes only its *first*
   UTC instant; a non-existent local time (DST spring-forward) is a misfire and
   is stored as ``MISSED`` with ``DST_NONEXISTENT_LOCAL_TIME``.
3. **Claiming is a conditional ``UPDATE`` with a fencing token.**  An in-process
   lock is never sufficient, and a writer whose fencing token is stale can never
   mutate the row.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import shutil
import sqlite3
import sys
from datetime import datetime, time, timedelta, timezone, tzinfo
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from local_drama.domain.explainers.contracts import (
    AutomationMode,
    ExplainerContractError,
    ScheduleOccurrenceKind,
    ScheduleOccurrenceStatus,
    content_hash,
    schedule_trigger_key,
    utc_now_iso,
)
from local_drama.infrastructure.database.explainer_repository import ExplainerRepository

__all__ = [
    "FIXED_OFFSET_FALLBACK_ZONES",
    "ExplainerScheduleService",
    "expand_rule_trigger_points",
    "resolve_local_time",
    "resolve_timezone",
    "timezone_status",
    "to_utc_iso",
]

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #
_UTC_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

#: ``explainer_schedules.status`` values (migration 0102 check constraint).
SCHEDULE_STATUSES: tuple[str, ...] = ("ACTIVE", "PAUSED", "ARCHIVED")

#: ``explainer_schedules.insufficient_topic_policy`` values.
INSUFFICIENT_TOPIC_POLICIES: tuple[str, ...] = ("SKIP_WITH_REASON", "WAIT_FOR_INPUT", "FAIL")

#: Supported ``rule_json`` kinds.  A schedule version is frozen on one of these.
RULE_KINDS: tuple[str, ...] = ("DAILY", "WEEKLY", "INTERVAL", "ONCE")

#: Keys this module itself freezes into ``rule_json``; they are accepted when a
#: stored rule is re-validated (e.g. before materialising an occurrence).
_RULE_FROZEN_KEYS: frozenset[str] = frozenset(
    {"dst_policy", "semantic_duplicate_threshold", "timezone_at_version"}
)

#: The DST rule is part of the schedule version and cannot be switched in place.
DST_POLICY_FIRST_INSTANT_ONLY = "FIRST_INSTANT_ONLY"

#: Catch-up policy name: only the most recent missed point is ever recovered.
CATCH_UP_POLICY = "MOST_RECENT_ONLY"

#: Skip reasons this module writes.  ``CLOSED_WINDOW`` / ``CATCH_UP_BACKLOG_CAP``
#: are terminal policy misses: they are never caught up, so a restart cannot
#: resurrect a month of skipped windows.
SKIP_REASON_CLOSED_WINDOW = "CLOSED_WINDOW"
SKIP_REASON_DST_NONEXISTENT = "DST_NONEXISTENT_LOCAL_TIME"
SKIP_REASON_BACKLOG_CAP = "CATCH_UP_BACKLOG_CAP"
NON_CATCHABLE_SKIP_REASONS: frozenset[str] = frozenset({SKIP_REASON_CLOSED_WINDOW, SKIP_REASON_BACKLOG_CAP})

#: Statuses a claim may start from (design rule: PENDING or MISSED only).
CLAIMABLE_STATUSES: tuple[str, ...] = (
    ScheduleOccurrenceStatus.PENDING.value,
    ScheduleOccurrenceStatus.MISSED.value,
)

#: Statuses that occupy a production slot for the concurrency gate.
ACTIVE_OCCURRENCE_STATUSES: tuple[str, ...] = (
    ScheduleOccurrenceStatus.CLAIMED.value,
    ScheduleOccurrenceStatus.RUNNING.value,
)

#: Statuses a lease holder may release an occurrence into.
RELEASABLE_STATUSES: tuple[str, ...] = (
    ScheduleOccurrenceStatus.COMPLETED.value,
    ScheduleOccurrenceStatus.SKIPPED_WITH_REASON.value,
    ScheduleOccurrenceStatus.FAILED.value,
)

MAX_CONCURRENT_RUNS_RANGE = (1, 8)
BACKLOG_CAP_RANGE = (0, 7)
LEASE_SECONDS_RANGE = (5, 3600)

#: Hard bounds that keep one bad schedule from scanning forever.
MAX_SCAN_DAYS = 400
MAX_TRIGGER_POINTS = 512
MAX_HORIZON_OCCURRENCES = 512
NEXT_OCCURRENCE_LOOKAHEAD = 32
OCCURRENCE_HISTORY_LIMIT = 200
PROJECT_VIDEO_HISTORY_LIMIT = 200

#: Default semantic-similarity threshold above which a candidate is treated as
#: tracking an already covered topic.  Configurable per schedule through
#: ``rule_json["semantic_duplicate_threshold"]``.
DEFAULT_SEMANTIC_DUPLICATE_THRESHOLD = 0.92
SEMANTIC_THRESHOLD_RANGE = (0.50, 1.0)

_CODE_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")
_HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
_LOCAL_MINUTE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::[0-5][0-9])?$")
#: Punctuation removed during topic normalisation.  Spaces are preserved so
#: "战 长沙" and "战长沙" normalise differently only when the source differs.
_PUNCTUATION_RE = re.compile(r"[^\w ]+", re.UNICODE)

_SCHEDULE_COLUMNS = {
    "title": "title",
    "timezone": "timezone",
    "rule": "rule_json",
    "topic_scope": "topic_scope",
    "source_allowlist": "source_allowlist_json",
    "daily_budget": "daily_budget_json",
    "max_concurrent_runs": "max_concurrent_runs",
    "duplicate_window_hours": "duplicate_window_hours",
    "insufficient_topic_policy": "insufficient_topic_policy",
    "closed_window": "closed_window_json",
    "failure_notification": "failure_notification_json",
    "durations": "durations_json",
    "outputs": "outputs_json",
    "automation_mode": "automation_mode",
    "status": "status",
}

# --------------------------------------------------------------------------- #
# timezone resolution
# --------------------------------------------------------------------------- #
#: Zones accepted when **no** IANA TZif database can be discovered at all
#: (Windows without the ``tzdata`` wheel, without Git-for-Windows and without a
#: Unix ``/usr/share/zoneinfo``).  Every entry is a zone whose current UTC offset
#: is stable and which observes no DST, so a fixed offset is exactly right rather
#: than a guess.  A genuine DST zone (``America/New_York``, ``Europe/Berlin``,
#: ``Australia/Sydney``, ...) is *refused* instead of being silently pinned to
#: one offset, because a wrong DST decision moves a production by an hour.
FIXED_OFFSET_FALLBACK_ZONES: dict[str, int] = {
    "UTC": 0,
    "Etc/UTC": 0,
    "Etc/GMT": 0,
    "GMT": 0,
    "Zulu": 0,
    "Asia/Shanghai": 480,
    "Asia/Chongqing": 480,
    "Asia/Harbin": 480,
    "Asia/Urumqi": 360,
    "Asia/Hong_Kong": 480,
    "Asia/Macau": 480,
    "Asia/Taipei": 480,
    "Asia/Singapore": 480,
    "Asia/Kuala_Lumpur": 480,
    "Asia/Manila": 480,
    "Asia/Brunei": 480,
    "Asia/Tokyo": 540,
    "Asia/Seoul": 540,
    "Asia/Pyongyang": 540,
    "Asia/Bangkok": 420,
    "Asia/Jakarta": 420,
    "Asia/Ho_Chi_Minh": 420,
    "Asia/Phnom_Penh": 420,
    "Asia/Yangon": 390,
    "Asia/Kathmandu": 345,
    "Asia/Kolkata": 330,
    "Asia/Colombo": 330,
    "Asia/Dhaka": 360,
    "Asia/Almaty": 300,
    "Asia/Tashkent": 300,
    "Asia/Karachi": 300,
    "Asia/Dubai": 240,
    "Asia/Muscat": 240,
    "Asia/Baku": 240,
    "Asia/Tbilisi": 240,
    "Asia/Riyadh": 180,
    "Asia/Kuwait": 180,
    "Asia/Baghdad": 180,
    "Asia/Qatar": 180,
    "Africa/Nairobi": 180,
    "Africa/Addis_Ababa": 180,
    "Africa/Johannesburg": 120,
    "Africa/Lagos": 60,
    "Europe/Moscow": 180,
    "Europe/Istanbul": 180,
    "Europe/Minsk": 180,
    "America/Phoenix": -420,
    "America/Bogota": -300,
    "America/Lima": -300,
    "America/Caracas": -240,
    "America/Sao_Paulo": -180,
    "America/Argentina/Buenos_Aires": -180,
    "America/Mexico_City": -360,
    "Australia/Brisbane": 600,
    "Australia/Perth": 480,
    "Pacific/Guam": 600,
    "Pacific/Port_Moresby": 600,
}


class _FixedOffsetZone(tzinfo):
    """Last-resort fixed-offset zone (see :data:`FIXED_OFFSET_FALLBACK_ZONES`)."""

    def __init__(self, key: str, offset_minutes: int) -> None:
        self.key = key
        self._offset = timedelta(minutes=offset_minutes)

    def utcoffset(self, dt: datetime | None) -> timedelta:
        return self._offset

    def dst(self, dt: datetime | None) -> timedelta:
        return timedelta(0)

    def tzname(self, dt: datetime | None) -> str:
        minutes = int(self._offset.total_seconds() // 60)
        sign = "+" if minutes >= 0 else "-"
        return f"{self.key}{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"_FixedOffsetZone({self.key!r}, {int(self._offset.total_seconds() // 60)})"


@lru_cache(maxsize=1)
def discover_tzdata_roots() -> tuple[Path, ...]:
    """Return the bounded set of directories searched for IANA TZif files.

    ``zoneinfo`` on Windows has an empty ``TZPATH``: it can only work through the
    ``tzdata`` wheel.  A virtual environment with ``include-system-site-packages
    = false`` (this repository's ``.venv``) cannot even see a ``tzdata`` package
    installed for the base interpreter, so the interpreter prefixes and a
    Git-for-Windows copy are probed as well.  The list is deterministic and
    documented; nothing here downloads or installs anything.
    """

    candidates: list[Path] = []
    override = os.environ.get("LOCAL_DRAMA_TZDATA_DIR")
    if override:
        candidates.append(Path(override))
    prefixes = {
        Path(sys.prefix),
        Path(sys.base_prefix),
        Path(sys.exec_prefix),
        Path(sys.base_exec_prefix),
    }
    for prefix in sorted(prefixes, key=str):
        candidates.append(prefix / "Lib" / "site-packages" / "tzdata" / "zoneinfo")
        candidates.append(prefix / "lib" / "python3.12" / "site-packages" / "tzdata" / "zoneinfo")
    git_executable = shutil.which("git")
    if git_executable:
        git_root = Path(git_executable).resolve().parent.parent
        candidates.append(git_root / "mingw64" / "share" / "zoneinfo")
        candidates.append(git_root / "usr" / "share" / "zoneinfo")
    candidates.extend((Path("/usr/share/zoneinfo"), Path("/etc/zoneinfo"), Path("/usr/lib/zoneinfo")))

    roots: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = str(candidate)
        if text in seen:
            continue
        seen.add(text)
        try:
            if candidate.is_dir():
                roots.append(candidate)
        except OSError:  # pragma: no cover - unreadable path
            continue
    return tuple(roots)


def _safe_relative_zone_path(name: str) -> Path | None:
    if name.startswith(("/", "\\")) or "\\" in name:
        return None
    parts = [part for part in name.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    return Path(*parts)


@lru_cache(maxsize=128)
def resolve_timezone(key: str) -> tzinfo:
    """Resolve an IANA zone key to a ``tzinfo``.

    Order: real ``zoneinfo`` data, then the bounded TZif roots of
    :func:`discover_tzdata_roots`, then the documented fixed-offset fallback.
    An unknown zone raises ``SCHEMA_INVALID`` instead of silently defaulting.
    """

    name = (key or "").strip()
    if not name:
        raise ExplainerContractError("SCHEMA_INVALID", "时区不能为空", {"timezone": key})
    try:
        return ZoneInfo(name)
    except (OSError, ValueError, KeyError):
        pass
    relative = _safe_relative_zone_path(name)
    if relative is not None:
        for root in discover_tzdata_roots():
            path = root / relative
            try:
                if path.is_file():
                    with path.open("rb") as handle:
                        return ZoneInfo.from_file(handle, key=name)
            except OSError:  # pragma: no cover - unreadable tz file
                continue
    if name in FIXED_OFFSET_FALLBACK_ZONES:
        return _FixedOffsetZone(name, FIXED_OFFSET_FALLBACK_ZONES[name])
    raise ExplainerContractError(
        "SCHEMA_INVALID",
        f"未知的时区：{key!r}（本机未找到该 IANA 时区数据）",
        {
            "timezone": key,
            "tzdata_roots": [str(root) for root in discover_tzdata_roots()],
            "fixed_offset_fallback_zone_count": len(FIXED_OFFSET_FALLBACK_ZONES),
        },
    )


def timezone_status(key: str = "Asia/Shanghai") -> dict[str, Any]:
    """Diagnostic: report how ``key`` resolves on this machine.

    Exists so the honest limitation of the fixed-offset fallback is observable
    instead of implied: a schedule that had to use it is producing on a fixed
    offset, not on real DST rules.
    """

    roots = [str(root) for root in discover_tzdata_roots()]
    try:
        zone = resolve_timezone(key)
    except ExplainerContractError as error:
        return {
            "timezone": key,
            "resolved": False,
            "backend": "unresolved",
            "error": error.message,
            "details": dict(error.details),
            "tzdata_roots": roots,
        }
    if isinstance(zone, _FixedOffsetZone):
        backend = "fixed-offset-fallback"
    else:
        try:
            ZoneInfo(key)
            backend = "system-zoneinfo"
        except (OSError, ValueError, KeyError):
            backend = "discovered-tzif"
    probe = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
    return {
        "timezone": key,
        "resolved": True,
        "backend": backend,
        "utcoffset_at_2026_06_01T12:00Z": str(probe.astimezone(zone).utcoffset()),
        "has_dst_rules": backend != "fixed-offset-fallback",
        "tzdata_roots": roots,
        "fixed_offset_fallback_zone_count": len(FIXED_OFFSET_FALLBACK_ZONES),
    }


# --------------------------------------------------------------------------- #
# time helpers
# --------------------------------------------------------------------------- #
def to_utc_iso(value: str | datetime) -> str:
    """Normalise an instant to the storage form ``YYYY-MM-DDTHH:MM:SSZ``."""

    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).strftime(_UTC_ISO_FORMAT)
    text = (value or "").strip()
    if not text:
        raise ExplainerContractError("SCHEMA_INVALID", "时间不能为空", {"value": value})
    normalised = f"{text[:-1]}+00:00" if text[-1] in "Zz" else text
    try:
        moment = datetime.fromisoformat(normalised)
    except ValueError as error:
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"时间必须是 ISO-8601：{value!r}", {"value": value}
        ) from error
    if moment.tzinfo is None:
        raise ExplainerContractError(
            "SCHEMA_INVALID", "时间必须带时区偏移（UTC 存储使用 ...Z）", {"value": value}
        )
    return moment.astimezone(timezone.utc).strftime(_UTC_ISO_FORMAT)


def _parse_utc(value: str | datetime) -> datetime:
    return datetime.strptime(to_utc_iso(value), _UTC_ISO_FORMAT).replace(tzinfo=timezone.utc)


def _parse_hhmm(value: Any, *, field: str) -> time:
    text = str(value or "").strip()
    match = _HHMM_RE.match(text)
    if match is None:
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"{field} 必须是 24 小时制的 HH:MM", {field: value}
        )
    return time(int(match.group(1)), int(match.group(2)))


def resolve_local_time(local_dt: datetime, tz: tzinfo) -> dict[str, Any]:
    """Resolve a local wall-clock time in ``tz`` to UTC instant(s).

    Returns ``{"utc": [...], "kind": "NORMAL"|"AMBIGUOUS"|"NONEXISTENT", ...}``
    with ISO-8601 ``...Z`` strings in ``utc``.

    * ``NORMAL``      - one instant, ``utc`` has one entry.
    * ``AMBIGUOUS``   - the local time repeats (DST fall-back).  Only the **first**
      corresponding UTC instant is kept, so a repeated local time cannot produce
      two productions.
    * ``NONEXISTENT`` - the local time never occurs (DST spring-forward).
      ``utc`` is empty; ``nominal_utc`` is the pre-transition interpretation of
      the requested local time and is what the misfire row is keyed on.
    """

    if not isinstance(local_dt, datetime):
        raise ExplainerContractError(
            "SCHEMA_INVALID", "local_dt 必须是 datetime", {"type": type(local_dt).__name__}
        )
    naive = local_dt.astimezone(tz).replace(tzinfo=None) if local_dt.tzinfo is not None else local_dt
    first_utc = naive.replace(tzinfo=tz, fold=0).astimezone(timezone.utc)
    second_utc = naive.replace(tzinfo=tz, fold=1).astimezone(timezone.utc)
    # A repeated local time (fall-back) has TWO instants that both convert back to
    # the requested wall clock; a non-existent local time (spring-forward gap) has
    # two candidate instants of which NEITHER converts back, because the clock
    # jumped over the requested time.
    first_round_trip = first_utc.astimezone(tz).replace(tzinfo=None)
    second_round_trip = second_utc.astimezone(tz).replace(tzinfo=None)
    if first_utc == second_utc:
        if first_round_trip == naive:
            kind = "NORMAL"
            instants = [first_utc]
        else:
            kind = "NONEXISTENT"
            instants = []
    elif first_round_trip == naive and second_round_trip == naive:
        kind = "AMBIGUOUS"
        instants = [min(first_utc, second_utc)]
    else:
        kind = "NONEXISTENT"
        instants = []
    nominal = instants[0] if instants else first_utc
    return {
        "local": naive.strftime("%Y-%m-%dT%H:%M:%S"),
        "utc": [instant.strftime(_UTC_ISO_FORMAT) for instant in instants],
        "utc_candidates": [instant.strftime(_UTC_ISO_FORMAT) for instant in (first_utc, second_utc)],
        "kind": kind,
        "nominal_utc": nominal.strftime(_UTC_ISO_FORMAT),
    }


# --------------------------------------------------------------------------- #
# rule validation and expansion
# --------------------------------------------------------------------------- #
def normalise_rule(rule: Mapping[str, Any] | None, *, timezone_key: str) -> dict[str, Any]:
    """Validate a schedule rule and freeze the version-level DST policy.

    Unknown keys are rejected: a typo such as ``tim`` instead of ``time`` must
    fail loudly rather than silently schedule at midnight.
    """

    if not isinstance(rule, Mapping):
        raise ExplainerContractError("SCHEMA_INVALID", "rule 必须是对象", {"rule": rule})
    payload = {str(key): value for key, value in rule.items()}
    kind = str(payload.get("kind") or "").strip().upper()
    if kind not in RULE_KINDS:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"rule.kind 必须是 {'/'.join(RULE_KINDS)} 之一",
            {"kind": payload.get("kind")},
        )
    dst_policy = str(payload.get("dst_policy") or DST_POLICY_FIRST_INSTANT_ONLY)
    if dst_policy != DST_POLICY_FIRST_INSTANT_ONLY:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "DST 规则在排期版本内冻结，不能切换为其他取值",
            {"dst_policy": dst_policy, "frozen": DST_POLICY_FIRST_INSTANT_ONLY},
        )
    threshold = payload.get("semantic_duplicate_threshold")
    if threshold is not None:
        low, high = SEMANTIC_THRESHOLD_RANGE
        try:
            threshold_value = float(threshold)
        except (TypeError, ValueError) as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "semantic_duplicate_threshold 必须是数值", {"value": threshold}
            ) from error
        if not low <= threshold_value <= high:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"semantic_duplicate_threshold 必须在 {low}–{high} 之间",
                {"value": threshold_value},
            )
    else:
        threshold_value = DEFAULT_SEMANTIC_DUPLICATE_THRESHOLD

    if kind == "ONCE":
        at = payload.get("at")
        if not isinstance(at, str) or _LOCAL_MINUTE_RE.match(at.strip()) is None:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "ONCE 规则必须提供本地时间 at（YYYY-MM-DDTHH:MM，不带时区偏移）",
                {"at": at},
            )
        allowed = {"kind", "at"} | _RULE_FROZEN_KEYS
        _reject_unknown_keys(payload, allowed, kind="ONCE")
        return {
            "kind": kind,
            "at": at.strip().replace(" ", "T"),
            "dst_policy": DST_POLICY_FIRST_INSTANT_ONLY,
            "semantic_duplicate_threshold": threshold_value,
            "timezone_at_version": timezone_key,
        }

    if kind == "INTERVAL":
        every = payload.get("every_minutes")
        if isinstance(every, bool) or not isinstance(every, (int, float, str)):
            raise ExplainerContractError(
                "SCHEMA_INVALID", "INTERVAL 规则必须提供整数 every_minutes", {"every_minutes": every}
            )
        try:
            every_minutes = int(every)
        except (TypeError, ValueError) as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "INTERVAL 规则必须提供整数 every_minutes", {"every_minutes": every}
            ) from error
        if not 5 <= every_minutes <= 1440:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "every_minutes 必须在 5–1440 之间", {"every_minutes": every_minutes}
            )
        anchor = _parse_hhmm(payload.get("anchor_time", "00:00"), field="anchor_time")
        allowed = {"kind", "every_minutes", "anchor_time"} | _RULE_FROZEN_KEYS
        _reject_unknown_keys(payload, allowed, kind="INTERVAL")
        return {
            "kind": kind,
            "every_minutes": every_minutes,
            "anchor_time": anchor.strftime("%H:%M"),
            "dst_policy": DST_POLICY_FIRST_INSTANT_ONLY,
            "semantic_duplicate_threshold": threshold_value,
            "timezone_at_version": timezone_key,
        }

    at_time = _parse_hhmm(payload.get("time"), field="time")
    if kind == "WEEKLY":
        raw_weekdays = payload.get("weekdays")
        if not isinstance(raw_weekdays, (list, tuple)) or not raw_weekdays:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "WEEKLY 规则必须提供非空 weekdays（0=周一 … 6=周日）", {"weekdays": raw_weekdays}
            )
        weekdays: list[int] = []
        for item in raw_weekdays:
            try:
                value = int(item)
            except (TypeError, ValueError) as error:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "weekdays 只能包含 0–6 的整数", {"weekdays": raw_weekdays}
                ) from error
            if not 0 <= value <= 6:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "weekdays 只能包含 0–6 的整数", {"weekdays": raw_weekdays}
                )
            weekdays.append(value)
        allowed = {"kind", "weekdays", "time"} | _RULE_FROZEN_KEYS
        _reject_unknown_keys(payload, allowed, kind="WEEKLY")
        return {
            "kind": kind,
            "weekdays": sorted(set(weekdays)),
            "time": at_time.strftime("%H:%M"),
            "dst_policy": DST_POLICY_FIRST_INSTANT_ONLY,
            "semantic_duplicate_threshold": threshold_value,
            "timezone_at_version": timezone_key,
        }

    allowed = {"kind", "time"} | _RULE_FROZEN_KEYS
    _reject_unknown_keys(payload, allowed, kind="DAILY")
    return {
        "kind": "DAILY",
        "time": at_time.strftime("%H:%M"),
        "dst_policy": DST_POLICY_FIRST_INSTANT_ONLY,
        "semantic_duplicate_threshold": threshold_value,
        "timezone_at_version": timezone_key,
    }


def _reject_unknown_keys(payload: Mapping[str, Any], allowed: set[str], *, kind: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"{kind} 规则包含不支持的字段：{', '.join(unknown)}",
            {"unknown_fields": unknown, "allowed_fields": sorted(allowed)},
        )


def _local_candidates(rule: Mapping[str, Any], day: datetime) -> list[datetime]:
    kind = str(rule["kind"])
    if kind == "DAILY":
        hour, minute = (int(part) for part in str(rule["time"]).split(":"))
        return [datetime.combine(day.date(), time(hour, minute))]
    if kind == "WEEKLY":
        if day.weekday() not in set(rule["weekdays"]):
            return []
        hour, minute = (int(part) for part in str(rule["time"]).split(":"))
        return [datetime.combine(day.date(), time(hour, minute))]
    if kind == "INTERVAL":
        anchor_hour, anchor_minute = (int(part) for part in str(rule["anchor_time"]).split(":"))
        cursor = datetime.combine(day.date(), time(anchor_hour, anchor_minute))
        step = timedelta(minutes=int(rule["every_minutes"]))
        points: list[datetime] = []
        while cursor.date() == day.date():
            points.append(cursor)
            cursor = cursor + step
        return points
    if kind == "ONCE":
        at = datetime.fromisoformat(str(rule["at"]).replace(" ", "T"))
        return [at] if at.date() == day.date() else []
    raise ExplainerContractError("SCHEMA_INVALID", f"未知的规则类型：{kind}", {"kind": kind})


def expand_rule_trigger_points(
    rule: Mapping[str, Any],
    zone: tzinfo,
    *,
    start_utc: str | datetime,
    count: int = 8,
    horizon_utc: str | datetime | None = None,
) -> list[dict[str, Any]]:
    """Expand a normalised rule into UTC trigger points strictly after ``start_utc``.

    With ``horizon_utc`` every point up to the horizon is returned (bounded by
    :data:`MAX_TRIGGER_POINTS`); otherwise exactly ``count`` points are returned.
    The scan is bounded by :data:`MAX_SCAN_DAYS` so a schedule cannot stall the
    worker.
    """

    start = _parse_utc(start_utc)
    horizon = _parse_utc(horizon_utc) if horizon_utc is not None else None
    wanted = max(1, min(int(count), MAX_TRIGGER_POINTS))
    scan_start = start.astimezone(zone).date() - timedelta(days=1)
    collected: list[dict[str, Any]] = []
    for offset in range(MAX_SCAN_DAYS):
        day = datetime.combine(scan_start + timedelta(days=offset), time(0, 0))
        resolved_midnight = resolve_local_time(day, zone)
        if horizon is not None and _parse_utc(resolved_midnight["nominal_utc"]) > horizon:
            break
        for local_dt in _local_candidates(rule, day):
            resolved = resolve_local_time(local_dt, zone)
            instant = _parse_utc(resolved["nominal_utc"])
            if instant <= start:
                continue
            if horizon is not None and instant > horizon:
                continue
            collected.append(
                {
                    "scheduled_for": resolved["nominal_utc"],
                    "local": resolved["local"],
                    "kind": resolved["kind"],
                    "utc_candidates": resolved["utc_candidates"],
                }
            )
        if len(collected) >= (MAX_TRIGGER_POINTS if horizon is not None else wanted):
            break
    collected.sort(key=lambda item: str(item["scheduled_for"]))
    if horizon is None:
        return collected[:wanted]
    return collected[:MAX_TRIGGER_POINTS]


# --------------------------------------------------------------------------- #
# closed window / budgets
# --------------------------------------------------------------------------- #
def normalise_closed_window(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None or (isinstance(value, Mapping) and not value):
        return {}
    if not isinstance(value, Mapping):
        raise ExplainerContractError("SCHEMA_INVALID", "closed_window 必须是对象", {"closed_window": value})
    payload = {str(key): item for key, item in value.items()}
    unknown = sorted(set(payload) - {"start", "end", "weekdays", "note"})
    if unknown:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"closed_window 包含不支持的字段：{', '.join(unknown)}",
            {"unknown_fields": unknown},
        )
    start = _parse_hhmm(payload.get("start"), field="closed_window.start")
    end = _parse_hhmm(payload.get("end"), field="closed_window.end")
    if start == end:
        raise ExplainerContractError(
            "SCHEMA_INVALID", "closed_window 的 start 与 end 不能相同", {"start": str(start), "end": str(end)}
        )
    weekdays: list[int] = []
    raw_weekdays = payload.get("weekdays")
    if raw_weekdays is not None:
        if not isinstance(raw_weekdays, (list, tuple)):
            raise ExplainerContractError("SCHEMA_INVALID", "closed_window.weekdays 必须是数组", {"value": raw_weekdays})
        for item in raw_weekdays:
            try:
                weekday = int(item)
            except (TypeError, ValueError) as error:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "closed_window.weekdays 只能包含 0–6 的整数", {"value": raw_weekdays}
                ) from error
            if not 0 <= weekday <= 6:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "closed_window.weekdays 只能包含 0–6 的整数", {"value": raw_weekdays}
                )
            weekdays.append(weekday)
    result: dict[str, Any] = {"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")}
    if weekdays:
        result["weekdays"] = sorted(set(weekdays))
    if payload.get("note") is not None:
        result["note"] = str(payload["note"])
    return result


def local_time_in_closed_window(closed_window: Mapping[str, Any], local_naive: datetime) -> bool:
    """True when a local wall-clock time falls inside the configured closed window.

    ``start > end`` wraps over midnight (23:00–06:00 closes the night).  When
    ``weekdays`` is present the window is anchored on the local day the window
    *starts*.
    """

    if not closed_window:
        return False
    weekdays = closed_window.get("weekdays")
    if weekdays and local_naive.weekday() not in set(weekdays):
        return False
    start = _parse_hhmm(closed_window["start"], field="closed_window.start")
    end = _parse_hhmm(closed_window["end"], field="closed_window.end")
    start_minutes = start.hour * 60 + start.minute
    end_minutes = end.hour * 60 + end.minute
    current = local_naive.hour * 60 + local_naive.minute
    if start_minutes < end_minutes:
        return start_minutes <= current < end_minutes
    return current >= start_minutes or current < end_minutes


def normalise_daily_budget(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None or (isinstance(value, Mapping) and not value):
        return {}
    if not isinstance(value, Mapping):
        raise ExplainerContractError("SCHEMA_INVALID", "daily_budget 必须是对象", {"daily_budget": value})
    allowed = {"max_runs_per_day", "max_gpu_seconds_per_day", "max_wall_seconds_per_day", "backlog_cap"}
    payload = {str(key): item for key, item in value.items()}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"daily_budget 包含不支持的字段：{', '.join(unknown)}",
            {"unknown_fields": unknown, "allowed_fields": sorted(allowed)},
        )
    result: dict[str, Any] = {}
    for key, item in payload.items():
        if item is None:
            continue
        try:
            number = int(item)
        except (TypeError, ValueError) as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID", f"daily_budget.{key} 必须是非负整数", {key: item}
            ) from error
        if number < 0:
            raise ExplainerContractError("SCHEMA_INVALID", f"daily_budget.{key} 必须是非负整数", {key: item})
        result[key] = number
    low, high = BACKLOG_CAP_RANGE
    if "backlog_cap" in result and not low <= int(result["backlog_cap"]) <= high:
        raise ExplainerContractError(
            "SCHEMA_INVALID", f"daily_budget.backlog_cap 必须在 {low}–{high} 之间", {"backlog_cap": result["backlog_cap"]}
        )
    return result


def normalise_failure_notification(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None or (isinstance(value, Mapping) and not value):
        return {}
    if not isinstance(value, Mapping):
        raise ExplainerContractError(
            "SCHEMA_INVALID", "failure_notification 必须是对象", {"failure_notification": value}
        )
    allowed = {"channel", "min_severity", "events", "webhook_configured"}
    payload = {str(key): item for key, item in value.items()}
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"failure_notification 包含不支持的字段：{', '.join(unknown)}",
            {"unknown_fields": unknown, "allowed_fields": sorted(allowed)},
        )
    channels = {"NONE", "LOCAL_LOG", "DESKTOP_NOTIFICATION"}
    channel = str(payload.get("channel") or "LOCAL_LOG").upper()
    if channel not in channels:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            f"failure_notification.channel 必须是 {'/'.join(sorted(channels))} 之一",
            {"channel": payload.get("channel")},
        )
    events = payload.get("events") or ["FAILED", "MISSED"]
    if not isinstance(events, (list, tuple)):
        raise ExplainerContractError("SCHEMA_INVALID", "failure_notification.events 必须是数组", {"events": events})
    result: dict[str, Any] = {"channel": channel, "events": [str(item) for item in events]}
    if payload.get("min_severity") is not None:
        result["min_severity"] = str(payload["min_severity"])
    if payload.get("webhook_configured") is not None:
        # An external reminder service is explicitly out of scope; the flag is
        # recorded only so a caller cannot pretend it configured one silently.
        result["webhook_configured"] = bool(payload["webhook_configured"])
    return result


def normalise_source_allowlist(value: Iterable[Any] | None) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        raise ExplainerContractError("SCHEMA_INVALID", "source_allowlist 必须是域名数组", {"source_allowlist": value})
    entries: list[str] = []
    for item in value:
        text = str(item or "").strip().lower()
        if not text:
            raise ExplainerContractError("SCHEMA_INVALID", "source_allowlist 不能包含空项", {"item": item})
        if "://" in text or "/" in text:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "source_allowlist 只接受域名（不含协议与路径）",
                {"item": item},
            )
        entries.append(text)
    return sorted(set(entries))


# --------------------------------------------------------------------------- #
# step-failure classification
# --------------------------------------------------------------------------- #
#: Ordered classification table: the first token found in ``error_code`` or
#: ``message`` decides the disposition.  Order matters - an OOM message also
#: contains generic words, and disk exhaustion must not be read as a network
#: error.
STEP_FAILURE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "OOM_FALLBACK",
        (
            "CUDA_OOM",
            "CUDA OUT OF MEMORY",
            "OUT_OF_MEMORY",
            "OUTOFMEMORY",
            "OOM",
            "VRAM",
            "GPU_MEMORY",
            "MEMORY_ALLOCATION",
            "ALLOCATION_FAILED",
        ),
    ),
    (
        "RECOVERABLE_STOP",
        (
            "DISK_FULL",
            "INSUFFICIENT_DISK",
            "ENOSPC",
            "NO_SPACE_LEFT",
            "NO SPACE LEFT",
            "DISK_QUOTA",
            "DISK_EXHAUSTED",
        ),
    ),
    (
        "STOP_PREFLIGHT",
        (
            "MODEL_MISSING",
            "MODEL_NOT_FOUND",
            "CAPABILITY_UNAVAILABLE",
            "UNSUPPORTED_PROTOCOL",
            "PROTOCOL_UNSUPPORTED",
            "PROVIDER_UNSUPPORTED",
            "CHECKPOINT_MISSING",
            "TOKENIZER_MISSING",
            "MODEL_LOAD_FAILED",
            "MODEL_HASH_MISMATCH",
            "NODE_TYPE_MISSING",
        ),
    ),
    (
        "WAIT_FOR_INPUT",
        (
            "SOURCE_CONFLICT",
            "CLAIM_CONFLICT",
            "SOURCE_EVIDENCE_MISSING",
            "LICENSE_SCOPE_UNVERIFIED",
            "RIGHTS_UNCONFIRMED",
            "UNCONFIRMED_RIGHTS",
            "LICENSE_",
            "FACT_KEY_CONFLICT",
            "COPYRIGHT_",
        ),
    ),
    (
        "CREATIVE_REPAIR",
        (
            "CONTENT_INVALID",
            "SCRIPT_INVALID",
            "NARRATION_MISMATCH",
            "QC_BLOCK",
            "QC_FAILED",
            "PROMPT_REJECTED",
            "SAFETY_REFUSAL",
            "CONTENT_POLICY",
            "SCHEMA_INVALID",
            "OUTPUT_VALIDATION_FAILED",
            "TRUNCATED_OUTPUT",
            "EMPTY_OUTPUT",
        ),
    ),
    (
        "RETRY",
        (
            "CONNECTION",
            "TIMEOUT",
            "TIMED_OUT",
            "NETWORK",
            "TRANSIENT",
            "DNS",
            "SOCKET",
            "EAI_AGAIN",
            "RATE_LIMIT",
            "TEMPORARY",
            "BROKEN_PIPE",
            "RESET_BY_PEER",
            "HTTP_5",
        ),
    ),
)

_RETRYABLE_DISPOSITIONS: frozenset[str] = frozenset({"RETRY", "OOM_FALLBACK", "CREATIVE_REPAIR"})

_STEP_FAILURE_REASONS: dict[str, str] = {
    "RETRY": "连接类瞬时故障；在有界退避后重试同一步骤，退避期间保持可取消。",
    "STOP_PREFLIGHT": "缺少模型或协议不受支持；必须在预检阶段停止，不能靠重试绕过。",
    "OOM_FALLBACK": "显存不足；切换到已批准的更低资源档位后重跑该步骤。",
    "CREATIVE_REPAIR": "内容类错误；在有界创作修复次数内重做该内容步骤，人工锁定保持不变。",
    "WAIT_FOR_INPUT": "来源冲突或权利未确认；等待人工处理后继续，不得编造来源。",
    "RECOVERABLE_STOP": "可恢复的停止：清理可再生缓存后可继续，原始素材不得删除。",
}

_STEP_FAILURE_NEXT_STEPS: dict[str, str] = {
    "RETRY": "按 backoff_seconds 退避后重试；若作业被取消则立即停止等待。",
    "STOP_PREFLIGHT": "在模型中心补齐能力并完成 smoke，然后重新预检；不要重复提交相同载荷。",
    "OOM_FALLBACK": "改用已批准的更低显存档位重跑该步骤；不要提高分辨率或并发。",
    "CREATIVE_REPAIR": "在剩余创作修复次数内重做该内容步骤；保持人工锁定的镜头与讲稿不变。",
    "WAIT_FOR_INPUT": "在资料页解决冲突或补齐权利证据后恢复；无证据时保留该触发点的跳过原因。",
    "RECOVERABLE_STOP": "先清理可再生的缓存（模型与原始素材除外），确认磁盘余量后恢复。",
}


def classify_step_failure(
    *, error_code: str, message: str, attempt_count: int, max_attempts: int
) -> dict[str, Any]:
    """Classify a step failure into a bounded, cancellation-responsive disposition.

    Returns ``{"disposition", "retryable", "backoff_seconds", "cancel_responsive",
    "reason", "next_step"}``.  ``backoff_seconds`` is exponential from 5 s and
    capped at 300 s; ``attempt_count >= max_attempts`` always yields a
    non-retryable disposition so a failing step cannot loop forever.
    """

    try:
        attempts = int(attempt_count)
        limit = int(max_attempts)
    except (TypeError, ValueError) as error:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "attempt_count 与 max_attempts 必须是正整数",
            {"attempt_count": attempt_count, "max_attempts": max_attempts},
        ) from error
    if attempts < 1 or limit < 1:
        raise ExplainerContractError(
            "SCHEMA_INVALID",
            "attempt_count 与 max_attempts 必须 >= 1",
            {"attempt_count": attempts, "max_attempts": limit},
        )
    code = str(error_code or "").strip().upper()
    haystack = f"{code} {str(message or '').upper()}"
    disposition = ""
    matched = ""
    for candidate, tokens in STEP_FAILURE_RULES:
        hit = next((token for token in tokens if token in haystack), None)
        if hit is not None:
            disposition = candidate
            matched = hit
            break
    if not disposition:
        disposition = "RETRY"
        matched = "UNCLASSIFIED"
    exhausted = attempts >= limit
    retryable = disposition in _RETRYABLE_DISPOSITIONS
    if exhausted and disposition in {"RETRY", "CREATIVE_REPAIR", "OOM_FALLBACK"}:
        disposition = "RECOVERABLE_STOP"
        retryable = False
    backoff: int | None = None
    if not exhausted:
        if disposition in {"RETRY", "CREATIVE_REPAIR"}:
            backoff = min(300, 5 * (2 ** (attempts - 1)))
        elif disposition == "OOM_FALLBACK":
            backoff = min(300, 15 * attempts)
    reason = _STEP_FAILURE_REASONS[disposition]
    if matched == "UNCLASSIFIED":
        reason = f"{reason}（未识别的错误码 {code or 'N/A'}，按有界重试处理）"
    else:
        reason = f"{reason}（匹配 {matched}）"
    if exhausted:
        reason = f"{reason} 已达到最大尝试次数 {limit}，不再自动重试。"
    return {
        "disposition": disposition,
        "retryable": retryable,
        "backoff_seconds": backoff,
        "cancel_responsive": True,
        "reason": reason,
        "next_step": _STEP_FAILURE_NEXT_STEPS[disposition],
    }


# --------------------------------------------------------------------------- #
# topic de-duplication helpers
# --------------------------------------------------------------------------- #
def _normalise_text(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = re.sub(r"[\s\u3000]+", " ", text)
    text = _PUNCTUATION_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def _normalise_event_date(value: Any) -> str:
    text = _normalise_text(value)
    return text.replace("年", "-").replace("月", "-").replace("日", "").strip("-")


def _normalise_entity_set(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, (str, bytes)):
        values = [values]
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ExplainerContractError("SCHEMA_INVALID", "normalised_entities 必须是数组", {"value": values})
    return tuple(sorted({_normalise_text(item) for item in values if _normalise_text(item)}))


def _event_signature(*, entities: Sequence[str], event_date: str, event_place: str) -> str | None:
    if not entities and not event_date and not event_place:
        return None
    return content_hash({"entities": list(entities), "event_date": event_date, "event_place": event_place})


def _source_hosts_of(source_refs: Sequence[str]) -> tuple[set[str], bool]:
    """Return ``(url_hosts, has_local_evidence)`` for a candidate's source refs.

    A non-URL reference (offline import, local document) is local evidence and is
    not constrained by a network allowlist; only http(s) references are.
    """

    hosts: set[str] = set()
    has_local = False
    for ref in source_refs:
        text = str(ref or "").strip()
        if not text:
            continue
        if "://" not in text:
            has_local = True
            continue
        host = urlsplit(text).hostname or ""
        if host:
            hosts.add(host.lower())
        else:
            has_local = True
    return hosts, has_local


# --------------------------------------------------------------------------- #
# service
# --------------------------------------------------------------------------- #
class ExplainerScheduleService:
    """Scheduling application service over :class:`ExplainerRepository`.

    Every mutation is expressed as a single conditional SQL statement executed
    through the repository, so two workers can never both believe they own a
    trigger point.  Wall-clock time is injected wherever it matters: pass
    ``now_utc`` to get fully deterministic behaviour.
    """

    def __init__(self, repo: ExplainerRepository) -> None:
        self.repo = repo

    #: Convenience alias so both ``schedules.resolve_local_time`` and
    #: ``service.resolve_local_time`` work.
    resolve_local_time = staticmethod(resolve_local_time)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _now(now_utc: str | datetime | None) -> str:
        return utc_now_iso() if now_utc is None else to_utc_iso(now_utc)

    def schedule_for_occurrence(self, occurrence_id: str) -> dict[str, Any]:
        """The schedule a trigger point belongs to.

        Public accessor used by the worker's schedule handoff so it never has to
        reach into the repository for a two-step lookup.
        """

        occurrence = self._occurrence(occurrence_id)
        return self._schedule(str(occurrence["schedule_id"]))

    def _schedule(self, schedule_id: str) -> dict[str, Any]:
        if not schedule_id or not str(schedule_id).strip():
            raise ExplainerContractError("SCHEMA_INVALID", "schedule_id 不能为空", {"schedule_id": schedule_id})
        row = self.repo.find("explainer_schedules", str(schedule_id))
        if row is None:
            raise ExplainerContractError("NOT_FOUND", "排期不存在", {"schedule_id": schedule_id})
        return row

    def _occurrence(self, occurrence_id: str) -> dict[str, Any]:
        if not occurrence_id or not str(occurrence_id).strip():
            raise ExplainerContractError("SCHEMA_INVALID", "occurrence_id 不能为空", {"occurrence_id": occurrence_id})
        row = self.repo.find("schedule_occurrences", str(occurrence_id))
        if row is None:
            raise ExplainerContractError("NOT_FOUND", "触发点不存在", {"occurrence_id": occurrence_id})
        return row

    @staticmethod
    def _normalised_rule(schedule: Mapping[str, Any]) -> dict[str, Any]:
        return normalise_rule(
            schedule.get("rule_json") if isinstance(schedule.get("rule_json"), Mapping) else {},
            timezone_key=str(schedule.get("timezone") or ""),
        )

    @staticmethod
    def _occurrence_summary(row: Mapping[str, Any]) -> dict[str, Any]:
        """Compact, credential-free projection of an occurrence row."""

        return {
            "occurrence_id": row.get("id"),
            "schedule_id": row.get("schedule_id"),
            "scheduled_for": row.get("scheduled_for"),
            "status": row.get("status"),
            "occurrence_kind": row.get("occurrence_kind"),
            "skip_reason": row.get("skip_reason"),
            "claim_count": int(row.get("claim_count") or 0),
            "fencing_token": int(row.get("fencing_token") or 0),
            "config_revision": int(row.get("config_revision") or 0),
            "rule_version": int(row.get("rule_version") or 0),
            "run_id": row.get("run_id"),
            "video_id": row.get("video_id"),
            "project_id": row.get("project_id"),
            "error_code": row.get("error_code"),
        }

    def _occurrences_for(self, schedule_id: str) -> list[dict[str, Any]]:
        return self.repo.list_where(
            "schedule_occurrences",
            {"schedule_id": schedule_id},
            order_by="scheduled_for",
            descending=False,
        )

    def _occurrence_counts(self, schedule_ids: Sequence[str]) -> dict[str, dict[str, int]]:
        if not schedule_ids:
            return {}
        placeholders = ", ".join("?" for _ in schedule_ids)
        rows = self.repo.query_all(
            f"SELECT schedule_id, status, COUNT(*) AS total FROM schedule_occurrences "
            f"WHERE schedule_id IN ({placeholders}) GROUP BY schedule_id, status",
            tuple(schedule_ids),
        )
        counts: dict[str, dict[str, int]] = {}
        for row in rows:
            counts.setdefault(str(row["schedule_id"]), {})[str(row["status"])] = int(row["total"])
        return counts

    def _refresh_next_occurrence(self, schedule: Mapping[str, Any], *, now_utc: str) -> str | None:
        """Recompute the derived ``next_occurrence_at`` projection.

        This does not bump ``revision``: it is a derived projection of the rule,
        not a configuration change, so a creator's optimistic-concurrency token
        stays valid across a materialisation tick.
        """

        zone = resolve_timezone(str(schedule.get("timezone") or ""))
        rule = self._normalised_rule(schedule)
        points = expand_rule_trigger_points(
            rule, zone, start_utc=now_utc, count=NEXT_OCCURRENCE_LOOKAHEAD
        )
        schedule_id = str(schedule.get("id"))
        next_at: str | None = None
        for point in points:
            if self.repo.occurrence_by_trigger(schedule_id, str(point["scheduled_for"])) is None:
                next_at = str(point["scheduled_for"])
                break
        self.repo.execute(
            "UPDATE explainer_schedules SET next_occurrence_at = ?, updated_at = ? WHERE id = ?",
            (next_at, now_utc, schedule_id),
        )
        return next_at

    # ------------------------------------------------------------------ create
    def create_schedule(
        self,
        *,
        project_id: str | None = None,
        channel_profile_id: str,
        channel_profile_version_id: str,
        code: str,
        title: str,
        timezone: str = "Asia/Shanghai",
        rule: Mapping[str, Any],
        topic_scope: str = "",
        source_allowlist: Iterable[Any] = (),
        daily_budget: Mapping[str, Any] | None = None,
        max_concurrent_runs: int = 1,
        duplicate_window_hours: int = 72,
        insufficient_topic_policy: str = "SKIP_WITH_REASON",
        closed_window: Mapping[str, Any] | None = None,
        failure_notification: Mapping[str, Any] | None = None,
        durations: Mapping[str, Any] | Iterable[Any] | None = None,
        outputs: Iterable[Any] = (),
        automation_mode: str = "AUTO_WITH_EXCEPTIONS",
        actor: str = "local-user",
        now_utc: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Create a schedule and compute its first UTC trigger point.

        ``now_utc`` defaults to the wall clock; pass it for deterministic tests.
        """

        now = self._now(now_utc)
        zone_key = str(timezone or "").strip()
        resolve_timezone(zone_key)  # unknown zone -> SCHEMA_INVALID
        schedule_code = str(code or "").strip()
        if _CODE_RE.match(schedule_code) is None:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "排期编码必须为 3–64 位小写字母、数字、下划线或连字符，且以字母或数字开头",
                {"code": code},
            )
        schedule_title = str(title or "").strip()
        if not 1 <= len(schedule_title) <= 200:
            raise ExplainerContractError("SCHEMA_INVALID", "标题必须是 1–200 个字符", {"title": title})
        if project_id is not None:
            self.repo.require_explainer_project(str(project_id))
        profile_id = str(channel_profile_id or "").strip()
        version_id = str(channel_profile_version_id or "").strip()
        if not profile_id or not version_id:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "必须提供 channel_profile_id 与 channel_profile_version_id",
                {"channel_profile_id": channel_profile_id, "channel_profile_version_id": channel_profile_version_id},
            )
        version_row = self.repo.find("channel_profile_versions", version_id)
        if version_row is None:
            raise ExplainerContractError(
                "NOT_FOUND", "渠道档案版本不存在", {"channel_profile_version_id": version_id}
            )
        if str(version_row.get("channel_profile_id")) != profile_id:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "渠道档案版本不属于该渠道档案",
                {
                    "channel_profile_id": profile_id,
                    "channel_profile_version_id": version_id,
                    "version_channel_profile_id": version_row.get("channel_profile_id"),
                },
            )
        concurrency = self._validate_concurrency(max_concurrent_runs)
        duplicate_window = self._validate_duplicate_window(duplicate_window_hours)
        topic_policy = self._validate_topic_policy(insufficient_topic_policy)
        mode = self._validate_automation_mode(automation_mode)
        normalised_rule = normalise_rule(rule, timezone_key=zone_key)
        payload: dict[str, Any] = {
            "project_id": str(project_id) if project_id is not None else None,
            "channel_profile_id": profile_id,
            "channel_profile_version_id": version_id,
            "code": schedule_code,
            "title": schedule_title,
            "status": "ACTIVE",
            "timezone": zone_key,
            "rule_json": normalised_rule,
            "rule_version": 1,
            "config_revision": 1,
            "topic_scope": str(topic_scope or ""),
            "source_allowlist_json": normalise_source_allowlist(source_allowlist),
            "daily_budget_json": normalise_daily_budget(daily_budget),
            "max_concurrent_runs": concurrency,
            "duplicate_window_hours": duplicate_window,
            "insufficient_topic_policy": topic_policy,
            "closed_window_json": normalise_closed_window(closed_window),
            "failure_notification_json": normalise_failure_notification(failure_notification),
            "durations_json": self._normalise_durations(durations),
            "outputs_json": self._normalise_outputs(outputs),
            "automation_mode": mode,
            "next_occurrence_at": None,
            "last_occurrence_at": None,
        }
        zone = resolve_timezone(zone_key)
        first_points = expand_rule_trigger_points(normalised_rule, zone, start_utc=now, count=1)
        payload["next_occurrence_at"] = str(first_points[0]["scheduled_for"]) if first_points else None
        try:
            return self.repo.insert("explainer_schedules", payload, actor=actor)
        except sqlite3.IntegrityError as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"排期编码已存在：{schedule_code}",
                {"code": schedule_code, "constraint": "uq_explainer_schedules_code"},
            ) from error

    def _validate_concurrency(self, value: Any) -> int:
        try:
            concurrency = int(value)
        except (TypeError, ValueError) as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "max_concurrent_runs 必须是整数", {"max_concurrent_runs": value}
            ) from error
        low, high = MAX_CONCURRENT_RUNS_RANGE
        if not low <= concurrency <= high:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"max_concurrent_runs 必须在 {low}–{high} 之间",
                {"max_concurrent_runs": concurrency},
            )
        return concurrency

    def _validate_duplicate_window(self, value: Any) -> int:
        try:
            hours = int(value)
        except (TypeError, ValueError) as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "duplicate_window_hours 必须是整数", {"duplicate_window_hours": value}
            ) from error
        if hours < 0:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "duplicate_window_hours 不能为负", {"duplicate_window_hours": hours}
            )
        return hours

    def _validate_topic_policy(self, value: Any) -> str:
        policy = str(value or "").strip().upper()
        if policy not in INSUFFICIENT_TOPIC_POLICIES:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"insufficient_topic_policy 必须是 {'/'.join(INSUFFICIENT_TOPIC_POLICIES)} 之一",
                {"insufficient_topic_policy": value},
            )
        return policy

    def _validate_automation_mode(self, value: Any) -> str:
        mode = str(value or "").strip().upper()
        allowed = {item.value for item in AutomationMode}
        if mode not in allowed:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"automation_mode 必须是 {'/'.join(sorted(allowed))} 之一",
                {"automation_mode": value},
            )
        return mode

    def _normalise_durations(self, value: Mapping[str, Any] | Iterable[Any] | None) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, Mapping):
            items = dict(value)
        else:
            items = {}
            for item in value:
                if not isinstance(item, Mapping) or not item.get("edition_key"):
                    raise ExplainerContractError(
                        "SCHEMA_INVALID", "durations 数组项必须包含 edition_key", {"item": item}
                    )
                items[str(item["edition_key"])] = dict(item)
        result: dict[str, Any] = {}
        for edition_key, spec in items.items():
            key = str(edition_key).strip()
            if not key:
                raise ExplainerContractError("SCHEMA_INVALID", "durations 的 edition_key 不能为空", {"value": spec})
            if isinstance(spec, Mapping):
                seconds = spec.get("target_seconds")
                if seconds is None:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID", "durations 必须声明 target_seconds", {"edition_key": key, "value": spec}
                    )
                try:
                    target_seconds = int(seconds)
                except (TypeError, ValueError) as error:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID", "target_seconds 必须是整数", {"edition_key": key, "value": seconds}
                    ) from error
                if not 30 <= target_seconds <= 7200:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "目标时长必须在 30–7200 秒之间",
                        {"edition_key": key, "target_seconds": target_seconds},
                    )
                entry: dict[str, Any] = {
                    "edition_key": key,
                    "target_seconds": target_seconds,
                    "mode": str(spec.get("mode") or "TARGET").upper(),
                    "tolerance_percent": float(spec.get("tolerance_percent") or 5.0),
                }
            else:
                try:
                    target_seconds = int(spec)
                except (TypeError, ValueError) as error:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID", "durations 值必须是秒数或对象", {"edition_key": key, "value": spec}
                    ) from error
                if not 30 <= target_seconds <= 7200:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "目标时长必须在 30–7200 秒之间",
                        {"edition_key": key, "target_seconds": target_seconds},
                    )
                entry = {
                    "edition_key": key,
                    "target_seconds": target_seconds,
                    "mode": "TARGET",
                    "tolerance_percent": 5.0,
                }
            result[key] = entry
        return result

    def _normalise_outputs(self, value: Iterable[Any] | None) -> list[dict[str, Any]]:
        if value is None:
            return []
        if isinstance(value, (str, bytes, Mapping)):
            raise ExplainerContractError("SCHEMA_INVALID", "outputs 必须是数组", {"outputs": value})
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in value:
            if not isinstance(item, Mapping):
                raise ExplainerContractError("SCHEMA_INVALID", "outputs 数组项必须是对象", {"item": item})
            edition_key = str(item.get("edition_key") or "").strip()
            if not edition_key:
                raise ExplainerContractError("SCHEMA_INVALID", "outputs 项必须包含 edition_key", {"item": item})
            if edition_key in seen:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "同一作品的 edition_key 必须唯一", {"edition_key": edition_key}
                )
            seen.add(edition_key)
            voice_locale = str(item.get("voice_locale") or "").strip()
            if not voice_locale:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "outputs 项必须包含 voice_locale", {"edition_key": edition_key}
                )
            entry = dict(item)
            entry["edition_key"] = edition_key
            entry["voice_locale"] = voice_locale
            subtitle_locales = entry.get("subtitle_locales") or []
            if not isinstance(subtitle_locales, (list, tuple)):
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "subtitle_locales 必须是数组", {"edition_key": edition_key}
                )
            entry["subtitle_locales"] = [str(locale) for locale in subtitle_locales]
            entry["subtitle_mode"] = str(entry.get("subtitle_mode") or "NONE").upper()
            entry["aspect_ratio"] = str(entry.get("aspect_ratio") or "16:9")
            result.append(entry)
        return result

    # ------------------------------------------------------------------ update
    def update_schedule(
        self, *, schedule_id: str, expected_revision: int, **fields: Any
    ) -> dict[str, Any]:
        """Edit a schedule without ever replaying an already-materialised trigger point.

        ``config_revision`` **and** ``rule_version`` are bumped, the next trigger
        point is recomputed, and no occurrence row is created or re-created: the
        de-duplication key is ``(schedule_id, scheduled_for)`` and does not
        contain ``config_revision``.
        """

        schedule = self._schedule(schedule_id)
        now = self._now(fields.pop("now_utc", None))
        unknown = sorted(set(fields) - set(_SCHEDULE_COLUMNS))
        if unknown:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"不支持更新的字段：{', '.join(unknown)}",
                {"unknown_fields": unknown, "allowed_fields": sorted(_SCHEDULE_COLUMNS)},
            )
        if not fields:
            raise ExplainerContractError("SCHEMA_INVALID", "update 必须至少提供一个字段", {})
        payload: dict[str, Any] = {}
        if "timezone" in fields:
            zone_key = str(fields["timezone"] or "").strip()
            resolve_timezone(zone_key)
            payload["timezone"] = zone_key
        if "title" in fields:
            title = str(fields["title"] or "").strip()
            if not 1 <= len(title) <= 200:
                raise ExplainerContractError("SCHEMA_INVALID", "标题必须是 1–200 个字符", {"title": fields["title"]})
            payload["title"] = title
        if "rule" in fields:
            payload["rule_json"] = normalise_rule(
                fields["rule"], timezone_key=str(payload.get("timezone") or schedule.get("timezone") or "")
            )
        if "topic_scope" in fields:
            payload["topic_scope"] = str(fields["topic_scope"] or "")
        if "source_allowlist" in fields:
            payload["source_allowlist_json"] = normalise_source_allowlist(fields["source_allowlist"])
        if "daily_budget" in fields:
            payload["daily_budget_json"] = normalise_daily_budget(fields["daily_budget"])
        if "max_concurrent_runs" in fields:
            payload["max_concurrent_runs"] = self._validate_concurrency(fields["max_concurrent_runs"])
        if "duplicate_window_hours" in fields:
            payload["duplicate_window_hours"] = self._validate_duplicate_window(fields["duplicate_window_hours"])
        if "insufficient_topic_policy" in fields:
            payload["insufficient_topic_policy"] = self._validate_topic_policy(fields["insufficient_topic_policy"])
        if "closed_window" in fields:
            payload["closed_window_json"] = normalise_closed_window(fields["closed_window"])
        if "failure_notification" in fields:
            payload["failure_notification_json"] = normalise_failure_notification(fields["failure_notification"])
        if "durations" in fields:
            payload["durations_json"] = self._normalise_durations(fields["durations"])
        if "outputs" in fields:
            payload["outputs_json"] = self._normalise_outputs(fields["outputs"])
        if "automation_mode" in fields:
            payload["automation_mode"] = self._validate_automation_mode(fields["automation_mode"])
        if "status" in fields:
            status = str(fields["status"] or "").strip().upper()
            if status not in SCHEDULE_STATUSES:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    f"status 必须是 {'/'.join(SCHEDULE_STATUSES)} 之一",
                    {"status": fields["status"]},
                )
            payload["status"] = status

        materialised_before = len(self._occurrences_for(str(schedule_id)))
        payload["config_revision"] = int(schedule.get("config_revision") or 1) + 1
        payload["rule_version"] = int(schedule.get("rule_version") or 1) + 1
        updated = self.repo.update(
            "explainer_schedules", str(schedule_id), payload, expected_revision=int(expected_revision)
        )
        next_at = self._refresh_next_occurrence(updated, now_utc=now)
        materialised_after = len(self._occurrences_for(str(schedule_id)))
        refreshed = self.repo.get("explainer_schedules", str(schedule_id))
        return {
            "schedule": {**refreshed, "next_occurrence_at": next_at},
            "replayed_occurrences": [],
            "occurrence_replay": {
                "trigger_key": ["schedule_id", "scheduled_for"],
                "config_revision_in_trigger_key": False,
                "materialised_before": materialised_before,
                "materialised_after": materialised_after,
                "note": (
                    "配置修订只影响未来触发点；已物化的触发点保持原样，不会被重建或重放"
                    "（唯一键 (schedule_id, scheduled_for) 不包含 config_revision）。"
                ),
            },
        }

    # ------------------------------------------------------------------ read
    def list_schedules(self, *, project_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
        where: dict[str, Any] = {}
        if project_id is not None:
            where["project_id"] = str(project_id)
        if status is not None:
            wanted = str(status).strip().upper()
            if wanted not in SCHEDULE_STATUSES:
                raise ExplainerContractError(
                    "SCHEMA_INVALID",
                    f"status 必须是 {'/'.join(SCHEDULE_STATUSES)} 之一",
                    {"status": status},
                )
            where["status"] = wanted
        rows = self.repo.list_where(
            "explainer_schedules", where, order_by="created_at", descending=True
        )
        counts = self._occurrence_counts([str(row["id"]) for row in rows])
        return [{**row, "occurrence_counts": counts.get(str(row["id"]), {})} for row in rows]

    # ------------------------------------------------------------------ materialise
    def materialise_occurrences(
        self,
        *,
        schedule_id: str,
        horizon_utc: str | datetime | None = None,
        count: int = 8,
        now_utc: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Insert the missing ``schedule_occurrences`` rows for a schedule's rule.

        Idempotent by construction: the ``(schedule_id, scheduled_for)`` unique
        key decides, and a duplicate insert is reported as ``existing`` rather
        than as a failure.  ``config_revision``/``rule_version`` are copied onto
        the row as the frozen snapshot used at claim time.

        Points that fall in a closed window (``CLOSED_WINDOW``) and points whose
        local time does not exist (``DST_NONEXISTENT_LOCAL_TIME``) are stored as
        ``MISSED`` with an explicit reason.
        """

        schedule = self._schedule(schedule_id)
        if str(schedule.get("status")) == "ARCHIVED":
            raise ExplainerContractError(
                "INVALID_REQUEST", "已归档的排期不再物化触发点", {"schedule_id": schedule_id}
            )
        now = self._now(now_utc)
        wanted = int(count)
        if not 1 <= wanted <= MAX_TRIGGER_POINTS:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"count 必须在 1–{MAX_TRIGGER_POINTS} 之间",
                {"count": count},
            )
        zone_key = str(schedule.get("timezone") or "")
        zone = resolve_timezone(zone_key)
        rule = self._normalised_rule(schedule)
        if horizon_utc is not None:
            horizon = to_utc_iso(horizon_utc)
            points = expand_rule_trigger_points(
                rule, zone, start_utc=now, count=MAX_HORIZON_OCCURRENCES, horizon_utc=horizon
            )
        else:
            points = expand_rule_trigger_points(rule, zone, start_utc=now, count=wanted)
        closed_window = schedule.get("closed_window_json") or {}
        created: list[dict[str, Any]] = []
        existing: list[dict[str, Any]] = []
        missed: list[dict[str, Any]] = []
        config_revision = int(schedule.get("config_revision") or 1)
        rule_version = int(schedule.get("rule_version") or 1)
        for point in points:
            local_naive = datetime.strptime(str(point["local"]), "%Y-%m-%dT%H:%M:%S")
            skip_reason: str | None = None
            if local_time_in_closed_window(closed_window, local_naive):
                skip_reason = SKIP_REASON_CLOSED_WINDOW
            elif str(point["kind"]) == "NONEXISTENT":
                skip_reason = SKIP_REASON_DST_NONEXISTENT
            scheduled_for = str(point["scheduled_for"])
            schedule_trigger_key(str(schedule_id), scheduled_for)
            payload = {
                "schedule_id": str(schedule_id),
                "scheduled_for": scheduled_for,
                "config_revision": config_revision,
                "occurrence_kind": ScheduleOccurrenceKind.SCHEDULED.value,
                "rule_version": rule_version,
                "status": (
                    ScheduleOccurrenceStatus.MISSED.value
                    if skip_reason
                    else ScheduleOccurrenceStatus.PENDING.value
                ),
                "skip_reason": skip_reason,
                "project_id": schedule.get("project_id"),
                "fencing_token": 0,
                "claim_count": 0,
            }
            try:
                row = self.repo.insert("schedule_occurrences", payload)
            except sqlite3.IntegrityError as error:
                duplicate = self.repo.occurrence_by_trigger(str(schedule_id), scheduled_for)
                if duplicate is None:
                    raise ExplainerContractError(
                        "SCHEMA_INVALID",
                        "触发点写入违反数据库约束",
                        {"schedule_id": schedule_id, "scheduled_for": scheduled_for, "error": str(error)},
                    ) from error
                existing.append(self._occurrence_summary(duplicate))
                continue
            summary = self._occurrence_summary(row)
            if skip_reason == SKIP_REASON_DST_NONEXISTENT:
                summary["dst_kind"] = "NONEXISTENT"
                summary["utc_candidates"] = point.get("utc_candidates")
            created.append(summary)
            if skip_reason:
                missed.append({**summary, "reason": skip_reason})
        next_at = self._refresh_next_occurrence(schedule, now_utc=now)
        return {
            "created": created,
            "existing": existing,
            "missed": missed,
            "schedule_id": str(schedule_id),
            "next_occurrence_at": next_at,
            "horizon_utc": to_utc_iso(horizon_utc) if horizon_utc is not None else None,
        }

    # ------------------------------------------------------------------ catch-up
    def catch_up_policy(
        self,
        *,
        schedule_id: str,
        now_utc: str | datetime,
        backlog_cap: int = 1,
    ) -> dict[str, Any]:
        """Catch up only the most recent missed point, bounded by ``backlog_cap``.

        Never returns more than ``backlog_cap`` candidates.  Older due points are
        marked ``MISSED`` with ``CATCH_UP_BACKLOG_CAP`` so a restart can never run
        a month of missed jobs.  Closed-window misses are not catch-up eligible.
        """

        schedule = self._schedule(schedule_id)
        now = self._now(now_utc)
        try:
            cap = int(backlog_cap)
        except (TypeError, ValueError) as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "backlog_cap 必须是整数", {"backlog_cap": backlog_cap}
            ) from error
        low, high = BACKLOG_CAP_RANGE
        if not low <= cap <= high:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"backlog_cap 必须在 {low}–{high} 之间",
                {"backlog_cap": cap},
            )
        due = [
            row
            for row in self._occurrences_for(str(schedule_id))
            if str(row.get("status")) in CLAIMABLE_STATUSES
            and str(row.get("scheduled_for") or "") <= now
            and str(row.get("skip_reason") or "") not in NON_CATCHABLE_SKIP_REASONS
        ]
        due.sort(key=lambda row: str(row.get("scheduled_for")), reverse=True)
        candidates = due[:cap]
        superseded = due[cap:]
        marked: list[dict[str, Any]] = []
        for row in superseded:
            existing_reason = str(row.get("skip_reason") or "").strip()
            if str(row.get("status")) == ScheduleOccurrenceStatus.MISSED.value and existing_reason:
                marked.append(
                    {
                        **self._occurrence_summary(row),
                        "reason": existing_reason,
                        "newly_marked": False,
                    }
                )
                continue
            self.repo.execute(
                "UPDATE schedule_occurrences SET status = ?, skip_reason = ?, updated_at = ?, "
                "revision = revision + 1 WHERE id = ? AND status IN (?, ?)",
                (
                    ScheduleOccurrenceStatus.MISSED.value,
                    SKIP_REASON_BACKLOG_CAP,
                    now,
                    str(row.get("id")),
                    *CLAIMABLE_STATUSES,
                ),
            )
            refreshed = self.repo.get("schedule_occurrences", str(row.get("id")))
            marked.append(
                {
                    **self._occurrence_summary(refreshed),
                    "reason": SKIP_REASON_BACKLOG_CAP,
                    "newly_marked": True,
                }
            )
        return {
            "schedule_id": str(schedule_id),
            "policy": CATCH_UP_POLICY,
            "backlog_cap": cap,
            "now_utc": now,
            "max_concurrent_runs": int(schedule.get("max_concurrent_runs") or 1),
            "catch_up": [self._occurrence_summary(row) for row in candidates],
            "missed": marked,
        }

    # ------------------------------------------------------------------ claim / lease
    def claim_occurrence(
        self,
        *,
        occurrence_id: str,
        owner: str,
        lease_seconds: int = 900,
        now_utc: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Atomically claim a trigger point with a lease and a fencing token.

        Mirrors the JobAttempt lease pattern of
        :meth:`local_drama.application.jobs.JobService.claim` /
        ``_leased_attempt``: the claim is one conditional ``UPDATE`` guarded by
        status and lease expiry, and the fencing token increases monotonically so
        a superseded worker can never write afterwards.
        """

        row = self._occurrence(occurrence_id)
        owner_value = str(owner or "").strip()
        if not owner_value:
            raise ExplainerContractError("SCHEMA_INVALID", "owner 不能为空", {"occurrence_id": occurrence_id})
        lease_low, lease_high = LEASE_SECONDS_RANGE
        try:
            lease = int(lease_seconds)
        except (TypeError, ValueError) as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "lease_seconds 必须是整数", {"lease_seconds": lease_seconds}
            ) from error
        if not lease_low <= lease <= lease_high:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"lease_seconds 必须在 {lease_low}–{lease_high} 之间",
                {"lease_seconds": lease},
            )
        now = self._now(now_utc)
        expires = (datetime.strptime(now, _UTC_ISO_FORMAT) + timedelta(seconds=lease)).strftime(_UTC_ISO_FORMAT)
        token = secrets.token_urlsafe(24)
        cursor = self.repo.execute(
            """
            UPDATE schedule_occurrences
               SET status = ?, lease_owner = ?, lease_token = ?, lease_expires_at = ?,
                   fencing_token = fencing_token + 1, claim_count = claim_count + 1,
                   skip_reason = NULL, started_at = COALESCE(started_at, ?),
                   updated_at = ?, revision = revision + 1
             WHERE id = ? AND status IN (?, ?)
               AND (lease_expires_at IS NULL OR lease_expires_at < ?)
            """,
            (
                ScheduleOccurrenceStatus.CLAIMED.value,
                owner_value,
                token,
                expires,
                now,
                now,
                str(occurrence_id),
                *CLAIMABLE_STATUSES,
                now,
            ),
        )
        if int(cursor.rowcount or 0) != 1:
            current = self._occurrence(occurrence_id)
            status = str(current.get("status"))
            if status in ACTIVE_OCCURRENCE_STATUSES and not self._lease_expired(
                current.get("lease_expires_at"), now
            ):
                raise ExplainerContractError(
                    "INVALID_REQUEST",
                    "该触发点已被其他 Worker 领取",
                    {
                        "occurrence_id": occurrence_id,
                        "owner": current.get("lease_owner"),
                        "status": status,
                        "lease_expires_at": current.get("lease_expires_at"),
                        "fencing_token": int(current.get("fencing_token") or 0),
                    },
                )
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "该触发点当前不可领取",
                {
                    "occurrence_id": occurrence_id,
                    "status": status,
                    "skip_reason": current.get("skip_reason"),
                    "lease_expires_at": current.get("lease_expires_at"),
                },
            )
        claimed = self.repo.get("schedule_occurrences", str(occurrence_id))
        return {
            "occurrence_id": str(occurrence_id),
            "schedule_id": claimed.get("schedule_id"),
            "scheduled_for": claimed.get("scheduled_for"),
            "status": ScheduleOccurrenceStatus.CLAIMED.value,
            "occurrence_kind": claimed.get("occurrence_kind"),
            "owner": owner_value,
            "lease_token": token,
            "lease_expires_at": expires,
            "fencing_token": int(claimed.get("fencing_token") or 0),
            "claim_count": int(claimed.get("claim_count") or 0),
            "config_revision": int(claimed.get("config_revision") or 0),
            "rule_version": int(claimed.get("rule_version") or 0),
            "previous_status": row.get("status"),
            "caught_up_from_skip_reason": row.get("skip_reason"),
            "project_id": claimed.get("project_id"),
        }

    @staticmethod
    def _lease_expired(expires_at: Any, now_iso: str) -> bool:
        text = str(expires_at or "").strip()
        if not text:
            return True
        try:
            return to_utc_iso(text) < now_iso
        except ExplainerContractError:
            return True

    @staticmethod
    def _tokens_equal(left: Any, right: Any) -> bool:
        stored = str(left or "")
        provided = str(right or "")
        if not stored or not provided:
            return False
        return hmac.compare_digest(stored.encode("utf-8"), provided.encode("utf-8"))

    def _require_lease(
        self,
        occurrence_id: str,
        *,
        lease_token: str,
        fencing_token: int,
        now: str,
    ) -> dict[str, Any]:
        """Validate a lease writer; a stale fencing token is rejected first."""

        row = self._occurrence(occurrence_id)
        expected = int(row.get("fencing_token") or 0)
        try:
            provided = int(fencing_token)
        except (TypeError, ValueError) as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "fencing_token 必须是整数", {"fencing_token": fencing_token}
            ) from error
        if provided != expected:
            raise ExplainerContractError(
                "STALE_REVISION",
                "fencing_token 已过期：该触发点已被更新的领取者接管，本次写入被拒绝",
                {
                    "occurrence_id": occurrence_id,
                    "expected_fencing_token": expected,
                    "provided_fencing_token": provided,
                    "owner": row.get("lease_owner"),
                },
            )
        if not self._tokens_equal(row.get("lease_token"), lease_token):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "lease_token 无效或不属于当前领取者",
                {"occurrence_id": occurrence_id, "owner": row.get("lease_owner")},
            )
        if str(row.get("status")) not in ACTIVE_OCCURRENCE_STATUSES:
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "该触发点已结束或未被领取，不能写入",
                {"occurrence_id": occurrence_id, "status": row.get("status")},
            )
        if self._lease_expired(row.get("lease_expires_at"), now):
            raise ExplainerContractError(
                "INVALID_REQUEST",
                "租约已过期，请重新领取该触发点",
                {
                    "occurrence_id": occurrence_id,
                    "lease_expires_at": row.get("lease_expires_at"),
                    "now_utc": now,
                },
            )
        return row

    def heartbeat_occurrence(
        self,
        *,
        occurrence_id: str,
        lease_token: str,
        fencing_token: int,
        lease_seconds: int = 900,
        now_utc: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Extend a lease; refuses a stale fencing token or an expired lease."""

        lease_low, lease_high = LEASE_SECONDS_RANGE
        try:
            lease = int(lease_seconds)
        except (TypeError, ValueError) as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "lease_seconds 必须是整数", {"lease_seconds": lease_seconds}
            ) from error
        if not lease_low <= lease <= lease_high:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"lease_seconds 必须在 {lease_low}–{lease_high} 之间",
                {"lease_seconds": lease},
            )
        now = self._now(now_utc)
        row = self._require_lease(
            occurrence_id, lease_token=lease_token, fencing_token=fencing_token, now=now
        )
        expires = (datetime.strptime(now, _UTC_ISO_FORMAT) + timedelta(seconds=lease)).strftime(_UTC_ISO_FORMAT)
        cursor = self.repo.execute(
            "UPDATE schedule_occurrences SET status = CASE WHEN status = ? THEN ? ELSE status END, "
            "lease_expires_at = ?, updated_at = ?, revision = revision + 1 "
            "WHERE id = ? AND fencing_token = ? AND lease_token = ?",
            (
                ScheduleOccurrenceStatus.CLAIMED.value,
                ScheduleOccurrenceStatus.RUNNING.value,
                expires,
                now,
                str(occurrence_id),
                int(row.get("fencing_token") or 0),
                str(row.get("lease_token") or ""),
            ),
        )
        if int(cursor.rowcount or 0) != 1:
            raise ExplainerContractError(
                "STALE_REVISION",
                "fencing_token 已过期：该触发点已被更新的领取者接管，本次心跳被拒绝",
                {"occurrence_id": occurrence_id, "fencing_token": int(row.get("fencing_token") or 0)},
            )
        refreshed = self.repo.get("schedule_occurrences", str(occurrence_id))
        return {
            "occurrence_id": str(occurrence_id),
            "schedule_id": refreshed.get("schedule_id"),
            "status": refreshed.get("status"),
            "heartbeat_at": now,
            "lease_expires_at": expires,
            "fencing_token": int(refreshed.get("fencing_token") or 0),
            "claim_count": int(refreshed.get("claim_count") or 0),
            "run_id": refreshed.get("run_id"),
        }

    def release_occurrence(
        self,
        *,
        occurrence_id: str,
        lease_token: str,
        fencing_token: int,
        status: str,
        run_id: str | None = None,
        video_id: str | None = None,
        project_id: str | None = None,
        skip_reason: str | None = None,
        error_code: str | None = None,
        error_detail_redacted: str | None = None,
        now_utc: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Settle a claimed occurrence; a stale writer's write is never applied."""

        target = str(status or "").strip().upper()
        if target not in RELEASABLE_STATUSES:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                f"释放状态必须是 {'/'.join(RELEASABLE_STATUSES)} 之一",
                {"status": status},
            )
        reason = str(skip_reason or "").strip()
        if target == ScheduleOccurrenceStatus.SKIPPED_WITH_REASON.value and not reason:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "SKIPPED_WITH_REASON 必须给出非空的跳过原因",
                {"occurrence_id": occurrence_id},
            )
        now = self._now(now_utc)
        row = self._occurrence(occurrence_id)
        if str(row.get("status")) == target and int(row.get("fencing_token") or 0) == int(fencing_token):
            return {
                **self._occurrence_summary(row),
                "released": True,
                "idempotent": True,
            }
        current = self._require_lease(
            occurrence_id, lease_token=lease_token, fencing_token=fencing_token, now=now
        )
        try:
            cursor = self.repo.execute(
                """
                UPDATE schedule_occurrences
                   SET status = ?, lease_token = NULL, lease_expires_at = NULL,
                       run_id = COALESCE(?, run_id), video_id = COALESCE(?, video_id),
                       project_id = COALESCE(?, project_id),
                       skip_reason = ?, error_code = ?, error_detail_redacted = ?,
                       finished_at = ?, updated_at = ?, revision = revision + 1
                 WHERE id = ? AND fencing_token = ? AND lease_token = ?
                """,
                (
                    target,
                    str(run_id) if run_id else None,
                    str(video_id) if video_id else None,
                    str(project_id) if project_id else None,
                    reason or None,
                    str(error_code) if error_code else None,
                    str(error_detail_redacted) if error_detail_redacted else None,
                    now,
                    now,
                    str(occurrence_id),
                    int(current.get("fencing_token") or 0),
                    str(current.get("lease_token") or ""),
                ),
            )
        except sqlite3.IntegrityError as error:
            raise ExplainerContractError(
                "SCHEMA_INVALID",
                "释放载荷违反数据库约束（run_id / video_id / project_id 必须存在）",
                {"occurrence_id": occurrence_id, "error": str(error)},
            ) from error
        if int(cursor.rowcount or 0) != 1:
            raise ExplainerContractError(
                "STALE_REVISION",
                "fencing_token 已过期：该触发点已被更新的领取者接管，本次写入被拒绝",
                {"occurrence_id": occurrence_id, "fencing_token": int(current.get("fencing_token") or 0)},
            )
        refreshed = self.repo.get("schedule_occurrences", str(occurrence_id))
        schedule_id = str(refreshed.get("schedule_id"))
        if schedule_id:
            self.repo.execute(
                "UPDATE explainer_schedules SET last_occurrence_at = ?, updated_at = ? WHERE id = ?",
                (now, now, schedule_id),
            )
        return {
            **self._occurrence_summary(refreshed),
            "released": True,
            "idempotent": False,
            "finished_at": refreshed.get("finished_at"),
            "error_detail_redacted": refreshed.get("error_detail_redacted"),
        }

    # ------------------------------------------------------------------ manual
    def create_manual_occurrence(
        self, *, schedule_id: str, now_utc: str | datetime | None = None
    ) -> dict[str, Any]:
        """Create an explicit "do another one now" trigger point.

        It carries ``occurrence_kind=MANUAL`` and its own ``scheduled_for`` (the
        injected instant, shifted by seconds only if that exact key is already
        taken), so it can never shadow or replay a scheduled trigger point.
        """

        schedule = self._schedule(schedule_id)
        if str(schedule.get("status")) == "ARCHIVED":
            raise ExplainerContractError(
                "INVALID_REQUEST", "已归档的排期不能手动新增触发点", {"schedule_id": schedule_id}
            )
        now = self._now(now_utc)
        cursor = datetime.strptime(now, _UTC_ISO_FORMAT)
        last_error: sqlite3.IntegrityError | None = None
        for shift in range(5):
            scheduled_for = (cursor + timedelta(seconds=shift)).strftime(_UTC_ISO_FORMAT)
            schedule_trigger_key(str(schedule_id), scheduled_for)
            try:
                row = self.repo.insert(
                    "schedule_occurrences",
                    {
                        "schedule_id": str(schedule_id),
                        "scheduled_for": scheduled_for,
                        "config_revision": int(schedule.get("config_revision") or 1),
                        "occurrence_kind": ScheduleOccurrenceKind.MANUAL.value,
                        "rule_version": int(schedule.get("rule_version") or 1),
                        "status": ScheduleOccurrenceStatus.PENDING.value,
                        "project_id": schedule.get("project_id"),
                        "fencing_token": 0,
                        "claim_count": 0,
                    },
                )
            except sqlite3.IntegrityError as error:
                last_error = error
                continue
            return {
                **self._occurrence_summary(row),
                "manual": True,
                "note": (
                    "手动触发点独立于排期规则；它不会占用或重放任何计划触发点，"
                    "配置快照在领取时冻结到该行的 config_revision / rule_version。"
                ),
            }
        raise ExplainerContractError(
            "IDEMPOTENCY_CONFLICT",
            "无法为手动触发点分配唯一的 scheduled_for",
            {"schedule_id": schedule_id, "error": str(last_error)},
        )

    # ------------------------------------------------------------------ gates
    def concurrency_gate(self, *, schedule_id: str) -> dict[str, Any]:
        """Decide whether another production may start for this schedule now."""

        schedule = self._schedule(schedule_id)
        limit = int(schedule.get("max_concurrent_runs") or 1)
        placeholders = ", ".join("?" for _ in ACTIVE_OCCURRENCE_STATUSES)
        row = self.repo.query_one(
            f"SELECT COUNT(*) AS total FROM schedule_occurrences WHERE schedule_id = ? "
            f"AND status IN ({placeholders})",
            (str(schedule_id), *ACTIVE_OCCURRENCE_STATUSES),
        )
        active = int(row["total"]) if row is not None else 0
        allowed = active < limit
        reason = (
            f"当前有 {active} 个进行中的制程，未达到上限 {limit}，可以开始"
            if allowed
            else f"当前有 {active} 个进行中的制程，已达到上限 {limit}，需等待其中一个结束"
        )
        return {
            "active_count": active,
            "max_concurrent_runs": limit,
            "allowed": allowed,
            "reason": reason,
        }

    def _daily_budget_state(self, schedule: Mapping[str, Any], *, now: str) -> dict[str, Any]:
        """Count today's (local) productions against ``daily_budget``."""

        budget = schedule.get("daily_budget_json") or {}
        max_runs = int(budget.get("max_runs_per_day") or 0)
        if max_runs <= 0:
            return {"allowed": True, "used": 0, "max_runs_per_day": 0, "reason": "未设置每日产出上限"}
        zone = resolve_timezone(str(schedule.get("timezone") or ""))
        local_day = datetime.strptime(now, _UTC_ISO_FORMAT).replace(tzinfo=timezone.utc).astimezone(zone).date()
        used = 0
        for row in self._occurrences_for(str(schedule["id"])):
            status = str(row.get("status"))
            if status not in (*ACTIVE_OCCURRENCE_STATUSES, ScheduleOccurrenceStatus.COMPLETED.value):
                continue
            try:
                scheduled = datetime.strptime(str(row.get("scheduled_for")), _UTC_ISO_FORMAT).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                continue
            if scheduled.astimezone(zone).date() == local_day:
                used += 1
        allowed = used < max_runs
        reason = (
            f"本地今日已产出 {used} 个，未达到每日上限 {max_runs}"
            if allowed
            else f"本地今日已产出 {used} 个，达到每日上限 {max_runs}，不再新增"
        )
        return {"allowed": allowed, "used": used, "max_runs_per_day": max_runs, "reason": reason}

    # ------------------------------------------------------------------ dedupe
    def dedupe_topics(
        self,
        *,
        schedule_id: str,
        candidates: Sequence[Mapping[str, Any]],
        now_utc: str | datetime | None = None,
    ) -> dict[str, Any]:
        """De-duplicate a topic batch against the duplicate window.

        Signals, strongest first: published ``content_hash``, then the normalised
        event signature (entity set + event date + event place), then the
        normalised title, then ``semantic_similarity``.  A different title for
        the same event is therefore still a duplicate.  Tracking new evidence for
        a covered event must be declared as ``update_to`` *and* carry
        ``added_value``; otherwise it is rejected with ``NO_ADDED_VALUE``.

        Source sufficiency is evaluated only when the candidate declares its
        sources (``sources`` / ``source_refs``); an undeclared source set means
        "unknown", not "insufficient", and a schedule with a non-empty
        ``source_allowlist`` rejects a candidate whose declared network sources
        are all outside it.  Nothing is ever fabricated to fill a daily quota.
        """

        schedule = self._schedule(schedule_id)
        if isinstance(candidates, (str, bytes, Mapping)) or not isinstance(candidates, (list, tuple)):
            raise ExplainerContractError(
                "SCHEMA_INVALID", "candidates 必须是数组", {"candidates": type(candidates).__name__}
            )
        rule = self._normalised_rule(schedule)
        threshold = float(rule.get("semantic_duplicate_threshold") or DEFAULT_SEMANTIC_DUPLICATE_THRESHOLD)
        allowlist = [str(item).lower() for item in (schedule.get("source_allowlist_json") or [])]
        window_hours = int(schedule.get("duplicate_window_hours") or 0)
        now = self._now(now_utc)
        cutoff: str | None = None
        if window_hours > 0:
            cutoff = (datetime.strptime(now, _UTC_ISO_FORMAT) - timedelta(hours=window_hours)).strftime(
                _UTC_ISO_FORMAT
            )
        history: list[dict[str, Any]] = self._topic_history(schedule, cutoff=cutoff)
        accepted: list[dict[str, Any]] = []
        duplicates: list[dict[str, Any]] = []
        rejected_sources: list[dict[str, Any]] = []
        for raw in candidates:
            if not isinstance(raw, Mapping):
                raise ExplainerContractError("SCHEMA_INVALID", "candidates 的每一项必须是对象", {"item": raw})
            candidate = {str(key): value for key, value in raw.items()}
            signal = self._topic_signal(candidate)
            insufficiency = self._source_insufficiency(candidate, allowlist)
            if insufficiency is not None:
                rejected_sources.append(
                    {
                        "candidate": candidate,
                        "reason": insufficiency[0],
                        "detail": insufficiency[1],
                        "topic_key": signal["topic_key"],
                    }
                )
                continue
            update_to = signal["update_to"]
            if update_to:
                target = next(
                    (
                        entry
                        for entry in history
                        if str(entry.get("topic_key")) == update_to or str(entry.get("ref_id")) == update_to
                    ),
                    None,
                )
                if target is None:
                    duplicates.append(
                        {
                            "candidate": candidate,
                            "duplicate_of": None,
                            "reason": "UNKNOWN_UPDATE_TARGET",
                            "detail": f"update_to={update_to} 不在重复窗口的历史主题中",
                        }
                    )
                    continue
                if not signal["added_value"]:
                    duplicates.append(
                        {
                            "candidate": candidate,
                            "duplicate_of": self._history_ref(target),
                            "reason": "NO_ADDED_VALUE",
                            "detail": "update_to 必须声明新增价值（added_value）才能作为增量证据生产",
                        }
                    )
                    continue
                accepted.append(
                    {
                        "topic_key": signal["topic_key"],
                        "title": signal["title"],
                        "event_signature": signal["event_signature"],
                        "content_hash": signal["content_hash"],
                        "acceptance": "UPDATE_WITH_ADDED_VALUE",
                        "update_to": update_to,
                        "added_value": signal["added_value"],
                    }
                )
                history.insert(0, self._history_entry_from_signal(signal, source="BATCH"))
                continue
            duplicate = self._find_duplicate(signal, history, threshold=threshold)
            if duplicate is not None:
                entry, reason, detail = duplicate
                duplicates.append(
                    {
                        "candidate": candidate,
                        "duplicate_of": self._history_ref(entry),
                        "reason": reason,
                        "detail": detail,
                    }
                )
                continue
            accepted.append(
                {
                    "topic_key": signal["topic_key"],
                    "title": signal["title"],
                    "event_signature": signal["event_signature"],
                    "content_hash": signal["content_hash"],
                    "acceptance": "NEW_TOPIC",
                }
            )
            history.insert(0, self._history_entry_from_signal(signal, source="BATCH"))
        policy = str(schedule.get("insufficient_topic_policy") or "SKIP_WITH_REASON")
        if accepted:
            disposition = "PRODUCE"
            policy_reason = f"本批次接受 {len(accepted)} 个主题"
        elif policy == "WAIT_FOR_INPUT":
            disposition = "WAIT_FOR_INPUT"
            policy_reason = "没有可用主题：按策略等待人工补充来源，不产出、不编造事实"
        elif policy == "FAIL":
            disposition = "FAIL"
            policy_reason = "没有可用主题：按策略标记失败并通知"
        else:
            disposition = "SKIP_WITH_REASON"
            policy_reason = "没有可用主题：按策略跳过该触发点并记录原因，不编造事实凑数"
        return {
            "accepted": accepted,
            "duplicates": duplicates,
            "rejected_insufficient_sources": rejected_sources,
            "schedule_id": str(schedule_id),
            "duplicate_window_hours": window_hours,
            "semantic_duplicate_threshold": threshold,
            "insufficient_topic_policy": policy,
            "policy_disposition": disposition,
            "policy_reason": policy_reason,
            "history_size": len(history),
        }

    def _topic_signal(self, candidate: Mapping[str, Any]) -> dict[str, Any]:
        topic_key = str(candidate.get("topic_key") or "").strip()
        title = str(candidate.get("title") or "").strip()
        if not topic_key:
            raise ExplainerContractError("SCHEMA_INVALID", "topic_key 不能为空", {"candidate": dict(candidate)})
        if not title:
            raise ExplainerContractError(
                "SCHEMA_INVALID", "候选主题必须包含 title", {"topic_key": topic_key}
            )
        entities = _normalise_entity_set(candidate.get("normalised_entities"))
        event_date = _normalise_event_date(candidate.get("event_date"))
        event_place = _normalise_text(candidate.get("event_place"))
        similarity = candidate.get("semantic_similarity")
        if similarity is None:
            similarity_value = 0.0
        else:
            try:
                similarity_value = float(similarity)
            except (TypeError, ValueError) as error:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "semantic_similarity 必须是 0–1 的数值", {"value": similarity}
                ) from error
            if not 0.0 <= similarity_value <= 1.0:
                raise ExplainerContractError(
                    "SCHEMA_INVALID", "semantic_similarity 必须是 0–1 的数值", {"value": similarity}
                )
        digest = candidate.get("content_hash")
        digest_text = str(digest).strip().lower() if digest else ""
        return {
            "topic_key": topic_key,
            "title": title,
            "title_key": _normalise_text(title),
            "entities": entities,
            "event_date": event_date,
            "event_place": event_place,
            "event_signature": _event_signature(
                entities=entities, event_date=event_date, event_place=event_place
            ),
            "content_hash": digest_text,
            "semantic_similarity": similarity_value,
            "update_to": str(candidate.get("update_to") or "").strip() or None,
            "added_value": str(candidate.get("added_value") or "").strip(),
        }

    @staticmethod
    def _history_entry_from_signal(signal: Mapping[str, Any], *, source: str) -> dict[str, Any]:
        return {
            "topic_key": signal.get("topic_key"),
            "title": signal.get("title"),
            "title_key": signal.get("title_key"),
            "entities": signal.get("entities"),
            "event_date": signal.get("event_date"),
            "event_place": signal.get("event_place"),
            "event_signature": signal.get("event_signature"),
            "content_hash": signal.get("content_hash"),
            "source": source,
            "ref_id": None,
        }

    @staticmethod
    def _history_ref(entry: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "topic_key": entry.get("topic_key"),
            "title": entry.get("title"),
            "ref_id": entry.get("ref_id"),
            "source": entry.get("source"),
            "event_signature": entry.get("event_signature"),
        }

    def _topic_history(self, schedule: Mapping[str, Any], *, cutoff: str | None) -> list[dict[str, Any]]:
        """Existing explainer videos of the project inside the duplicate window."""

        project_id = schedule.get("project_id")
        if not project_id:
            return []
        rows = self.repo.list_where(
            "explainer_videos",
            {"project_id": str(project_id)},
            order_by="created_at",
            descending=True,
            limit=PROJECT_VIDEO_HISTORY_LIMIT,
        )
        history: list[dict[str, Any]] = []
        for row in rows:
            created_at = str(row.get("created_at") or "")
            if cutoff is not None and created_at:
                try:
                    created_iso = to_utc_iso(created_at.replace(" ", "T"))
                except ExplainerContractError:
                    created_iso = ""
                if created_iso and created_iso < cutoff:
                    continue
            payload = row.get("input_payload_json")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except ValueError:
                    payload = {}
            payload = payload if isinstance(payload, Mapping) else {}
            entities = _normalise_entity_set(payload.get("normalised_entities") or payload.get("entities"))
            event_date = _normalise_event_date(payload.get("event_date"))
            event_place = _normalise_text(payload.get("event_place"))
            history.append(
                {
                    "topic_key": str(row.get("topic") or row.get("id")),
                    "title": str(row.get("title") or ""),
                    "title_key": _normalise_text(row.get("title")),
                    "entities": entities,
                    "event_date": event_date,
                    "event_place": event_place,
                    "event_signature": _event_signature(
                        entities=entities, event_date=event_date, event_place=event_place
                    ),
                    "content_hash": content_hash(payload) if payload else "",
                    "source": "EXPLAINER_VIDEO",
                    "ref_id": row.get("id"),
                }
            )
        return history

    @staticmethod
    def _find_duplicate(
        signal: Mapping[str, Any],
        history: Sequence[Mapping[str, Any]],
        *,
        threshold: float,
    ) -> tuple[Mapping[str, Any], str, str] | None:
        for entry in history:
            digest = str(signal.get("content_hash") or "")
            if digest and digest == str(entry.get("content_hash") or ""):
                return entry, "SAME_CONTENT_HASH", "发布内容哈希与历史主题一致"
        signature = signal.get("event_signature")
        if signature:
            for entry in history:
                if str(entry.get("event_signature") or "") != str(signature):
                    continue
                if str(entry.get("title_key") or "") == str(signal.get("title_key") or ""):
                    return entry, "SAME_EVENT", "同一事件（实体+日期+地点一致）且标题相同"
                return entry, "DISTINCT_TITLE_SAME_EVENT", "同一事件（实体+日期+地点一致）但标题不同"
        for entry in history:
            if str(entry.get("title_key") or "") and str(entry.get("title_key")) == str(signal.get("title_key")):
                return entry, "SAME_TITLE", "标题归一化后与历史主题相同"
        similarity = float(signal.get("semantic_similarity") or 0.0)
        if similarity >= threshold:
            for entry in history:
                return entry, "SEMANTIC_NEAR_DUPLICATE", f"语义相似度 {similarity} 达到阈值 {threshold}"
        return None

    @staticmethod
    def _source_insufficiency(
        candidate: Mapping[str, Any], allowlist: Sequence[str]
    ) -> tuple[str, str] | None:
        declared_key = next(
            (key for key in ("sources", "source_refs", "source_hosts") if key in candidate), None
        )
        if declared_key is None:
            return None
        raw = candidate.get(declared_key)
        if raw is None:
            return None
        if isinstance(raw, (str, bytes)):
            refs = [str(raw)]
        elif isinstance(raw, (list, tuple, set, frozenset)):
            refs = [str(item) for item in raw]
        else:
            return ("SOURCE_DECLARATION_INVALID", "来源声明必须是引用数组")
        refs = [ref for ref in (item.strip() for item in refs) if ref]
        if not refs:
            return ("NO_SOURCE_PROVIDED", "候选主题声明了空来源集合")
        if not allowlist:
            return None
        hosts, has_local = _source_hosts_of(refs)
        if has_local or not hosts:
            return None
        for host in sorted(hosts):
            for allowed in allowlist:
                if host == allowed or host.endswith(f".{allowed}"):
                    return None
        return (
            "NO_SOURCE_WITHIN_ALLOWLIST",
            f"声明的网络来源 {sorted(hosts)} 均不在允许清单 {list(allowlist)} 内",
        )

    # ------------------------------------------------------------------ classification
    def classify_step_failure(
        self, *, error_code: str, message: str, attempt_count: int, max_attempts: int
    ) -> dict[str, Any]:
        """Delegate to :func:`classify_step_failure` (kept as a method for callers)."""

        return classify_step_failure(
            error_code=error_code,
            message=message,
            attempt_count=attempt_count,
            max_attempts=max_attempts,
        )

    # ------------------------------------------------------------------ handoff
    def build_workflow_handoff(
        self,
        *,
        occurrence_id: str,
        lease_token: str,
        fencing_token: int,
        workflow_id: str,
        plan_hash: str,
        actor: str = "local-user",
        now_utc: str | datetime | None = None,
    ) -> dict[str, Any]:
        """Validate a claim and build the exact handoff payload for the existing runner.

        This module adds **no** executor: the returned mapping is what the worker
        passes to :meth:`local_drama.application.automation_workflows.AutomationWorkflowService.start_run`,
        and the idempotency key is derived from the occurrence id so a retried
        handoff cannot start two runs for one trigger point.
        """

        if not str(workflow_id or "").strip():
            raise ExplainerContractError("SCHEMA_INVALID", "workflow_id 不能为空", {})
        if not str(plan_hash or "").strip():
            raise ExplainerContractError("SCHEMA_INVALID", "plan_hash 不能为空", {})
        now = self._now(now_utc)
        row = self._require_lease(
            occurrence_id, lease_token=lease_token, fencing_token=fencing_token, now=now
        )
        return {
            "invokes": "AutomationWorkflowService.start_run",
            "workflow_id": str(workflow_id),
            "plan_hash": str(plan_hash),
            "idempotency_key": f"schedule-occurrence:{occurrence_id}",
            "actor": actor,
            "occurrence_id": str(occurrence_id),
            "schedule_id": row.get("schedule_id"),
            "scheduled_for": row.get("scheduled_for"),
            "project_id": row.get("project_id"),
            "config_revision": int(row.get("config_revision") or 0),
            "rule_version": int(row.get("rule_version") or 0),
            "fencing_token": int(row.get("fencing_token") or 0),
            "note": "排期只负责领取与冻结快照；生产由既有 automation workflow 执行，不新建执行器。",
        }

    # ------------------------------------------------------------------ tick
    def tick(
        self,
        *,
        now_utc: str | datetime | None = None,
        limit: int = 50,
        owner: str = "local-scheduler",
    ) -> dict[str, Any]:
        """Worker entry point: decide what may be produced next.  Never produces it.

        Idempotent by construction: a trigger point that is already ``CLAIMED`` /
        ``RUNNING`` is not due any more, and the concurrency gate caps the number
        of new claims per schedule at ``max_concurrent_runs``.  Every returned
        disposition is one of ``CLAIMED`` / ``DEFERRED_CONCURRENCY`` /
        ``DEFERRED_DAILY_BUDGET`` / ``MISSED_BACKLOG_CAP`` / ``SKIPPED_CLOSED_WINDOW``.
        """

        now = self._now(now_utc)
        owner_value = str(owner or "").strip()
        if not owner_value:
            raise ExplainerContractError("SCHEMA_INVALID", "owner 不能为空", {})
        try:
            page = int(limit)
        except (TypeError, ValueError) as error:
            raise ExplainerContractError("SCHEMA_INVALID", "limit 必须是整数", {"limit": limit}) from error
        if not 1 <= page <= 500:
            raise ExplainerContractError("SCHEMA_INVALID", "limit 必须在 1–500 之间", {"limit": page})
        due_rows = self.repo.due_occurrences(now_utc=now, limit=page)
        results: list[dict[str, Any]] = []
        by_schedule: dict[str, list[dict[str, Any]]] = {}
        for row in due_rows:
            schedule_id = str(row.get("schedule_id"))
            reason = str(row.get("skip_reason") or "")
            if reason in NON_CATCHABLE_SKIP_REASONS:
                results.append(
                    {
                        "occurrence_id": row.get("id"),
                        "schedule_id": schedule_id,
                        "disposition": (
                            "SKIPPED_CLOSED_WINDOW"
                            if reason == SKIP_REASON_CLOSED_WINDOW
                            else "MISSED_BACKLOG_CAP"
                        ),
                        "reason": (
                            "该触发点落在关闭窗口内，按配置不产出且不补跑"
                            if reason == SKIP_REASON_CLOSED_WINDOW
                            else "该触发点已因积压上限被标记为漏做，不补跑"
                        ),
                        "claim": None,
                    }
                )
                continue
            by_schedule.setdefault(schedule_id, []).append(dict(row))
        claimed_total = 0
        deferred_total = 0
        for schedule_id, rows in sorted(by_schedule.items()):
            schedule = self._schedule(schedule_id)
            budget = schedule.get("daily_budget_json") or {}
            cap = int(budget.get("backlog_cap") or 1)
            policy = self.catch_up_policy(schedule_id=schedule_id, now_utc=now, backlog_cap=cap)
            for missed in policy["missed"]:
                results.append(
                    {
                        "occurrence_id": missed.get("occurrence_id"),
                        "schedule_id": schedule_id,
                        "disposition": "MISSED_BACKLOG_CAP",
                        "reason": (
                            f"只补做最近 {cap} 个漏做触发点，更早的触发点标记为漏做"
                            f"（原因 {SKIP_REASON_BACKLOG_CAP}），避免重启后补跑整月积压"
                        ),
                        "claim": None,
                    }
                )
            scheduled_ids = {str(row.get("id")) for row in rows}
            for candidate in policy["catch_up"]:
                occurrence_id = str(candidate.get("occurrence_id"))
                if occurrence_id not in scheduled_ids:
                    continue
                gate = self.concurrency_gate(schedule_id=schedule_id)
                if not gate["allowed"]:
                    deferred_total += 1
                    results.append(
                        {
                            "occurrence_id": occurrence_id,
                            "schedule_id": schedule_id,
                            "disposition": "DEFERRED_CONCURRENCY",
                            "reason": gate["reason"],
                            "claim": None,
                        }
                    )
                    continue
                daily = self._daily_budget_state(schedule, now=now)
                if not daily["allowed"]:
                    deferred_total += 1
                    results.append(
                        {
                            "occurrence_id": occurrence_id,
                            "schedule_id": schedule_id,
                            "disposition": "DEFERRED_DAILY_BUDGET",
                            "reason": daily["reason"],
                            "claim": None,
                        }
                    )
                    continue
                claim = self.claim_occurrence(
                    occurrence_id=occurrence_id, owner=owner_value, now_utc=now
                )
                claimed_total += 1
                results.append(
                    {
                        "occurrence_id": occurrence_id,
                        "schedule_id": schedule_id,
                        "disposition": "CLAIMED",
                        "reason": (
                            "已领取该触发点；生产由既有 automation workflow 执行，"
                            "本模块不启动执行器"
                        ),
                        "claim": claim,
                    }
                )
        return {
            "now_utc": now,
            "owner": owner_value,
            "considered": len(due_rows),
            "schedule_count": len(by_schedule),
            "claimed_count": claimed_total,
            "deferred_count": deferred_total,
            "results": results,
        }
