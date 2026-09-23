from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def sqlite_readonly_uri(path: Path) -> str:
    """Build a *read-only* SQLite URI for a real filesystem path.

    Hand-assembling ``file:{path}?mode=ro`` breaks for any legal path that SQLite's
    URI parser treats specially: a directory called ``work#draft`` made SQLite read
    the file ``work`` (the ``#`` started a URI fragment) and silently dropped
    ``mode=ro`` with it, so readiness reported a complete database as
    ``schema_incomplete``, the server answered 503, and a stray zero-byte ``work``
    file was created.  ``Path.as_uri()`` percent-encodes the path correctly and, on
    Windows, produces the ``file:///C:/...`` form SQLite expects.
    """

    return Path(path).resolve().as_uri() + "?mode=ro"


def connect_readonly(path: Path) -> sqlite3.Connection:
    """Open an existing database read-only, without creating any file."""

    connection = sqlite3.connect(sqlite_readonly_uri(path), uri=True)
    connection.row_factory = sqlite3.Row
    return connection


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def exists(self) -> bool:
        return self.path.exists()

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    @contextmanager
    def transaction(self, *, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield connection
            # A helper inside the block may legitimately have finished the
            # transaction (``connection.commit()`` in this driver ends an
            # explicit ``BEGIN``).  Committing an already-closed transaction is a
            # no-op here rather than an OperationalError that would hide the real
            # failure; the data is already durable either way.
            if connection.in_transaction:
                connection.execute("COMMIT")
            else:
                connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()

    def integrity_check(self) -> str:
        with self.connect() as connection:
            return str(connection.execute("PRAGMA integrity_check").fetchone()[0])

    def wal_mode(self) -> str:
        with self.connect() as connection:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0])
