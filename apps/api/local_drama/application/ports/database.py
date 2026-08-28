"""Persistence boundary shared by application services.

The application layer only needs a SQLite-compatible unit of work.  The
concrete database lifecycle remains owned by the infrastructure/bootstrap
layers.
"""

from __future__ import annotations

import sqlite3
from contextlib import AbstractContextManager
from typing import Protocol


class DatabaseUnitOfWork(Protocol):
    def connect(self) -> sqlite3.Connection: ...

    def transaction(self, *, immediate: bool = True) -> AbstractContextManager[sqlite3.Connection]: ...
