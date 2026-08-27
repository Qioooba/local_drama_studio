"""Durable filesystem replacement for Windows hosts.

``os.replace`` is atomic on Windows, but immediately after heavy filesystem
activity (project scaffolding storms, freshly written ComfyUI artifacts) an
external handle — antivirus real-time scanning, the search indexer, or a
lingering descriptor from the writer itself — can hold the target name for a
few hundred milliseconds. The rename then fails with ``PermissionError``
(WinError 5) or sharing-violation (WinError 32) even though nothing is
permanently wrong. Callers must retry within a bounded window instead of
failing the whole operation, which is what happened to one-sentence project
creation in the field.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

# WinError 5 (ERROR_ACCESS_DENIED) and WinError 32 (ERROR_SHARING_VIOLATION)
# are the two transient rename failures Windows reports while another handle
# exists. Everything else (e.g. WinError 3 path not found) is a real bug.
_TRANSIENT_WINERRORS = frozenset({5, 32})
_ATTEMPTS = 5
_BACKOFF_SECONDS = (0.05, 0.15, 0.4, 1.0)


def _is_transient_rename_error(error: OSError) -> bool:
    if isinstance(error, PermissionError):
        return True
    return getattr(error, "winerror", None) in _TRANSIENT_WINERRORS


def replace_path(source: Path, destination: Path) -> None:
    """Atomically move *source* onto *destination*, tolerating transient
    Windows handle contention with bounded linear backoff.

    Retries only the rename — the source content is already durable at this
    point — and re-raises any non-transient error immediately.
    """
    for attempt in range(_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if attempt >= _ATTEMPTS - 1 or not _is_transient_rename_error(error):
                raise
            time.sleep(_BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS) - 1)])
