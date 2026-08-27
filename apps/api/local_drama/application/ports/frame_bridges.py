from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from typing import Protocol


class FrameBridgeUnitOfWork(Protocol):
    """Transaction boundary required by semantic Frame Bridge commands."""

    def transaction(self) -> AbstractContextManager[sqlite3.Connection]: ...
