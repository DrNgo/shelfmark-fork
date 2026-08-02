"""Local cache of what the user's libraries already hold.

The badge answers "do I already own this?" on every rendered search result, so
it reads a local table rather than asking each source per card. The index is
shared across sources — Audiobookshelf and Grimmory both write into it,
keyed by `(source, item_id)` so their IDs can never collide even when both
happen to use the same identifier. A sync is a full swap of one source's
slice: books removed from that source must stop claiming to be owned, but a
sync of one source must never touch another source's rows.

Failures are recorded beside the data, never instead of it — a source outage
should leave a stale badge standing, not blank the whole index.
"""

import os
import sqlite3
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from shelfmark.core.logger import setup_logger
from shelfmark.library.matching import build_match_keys

# Re-exported for backward compatibility: several modules and tests import
# these constants from here rather than from shelfmark.library.media_type,
# where they are actually defined.
from shelfmark.library.media_type import MEDIA_TYPE_AUDIOBOOK, MEDIA_TYPE_EBOOK  # noqa: F401

logger = setup_logger(__name__)

# SQLite's default variable limit is 999; keys are looked up in chunks so a
# batch lookup over a full page of search results can't blow past it.
_MAX_QUERY_KEYS = 500

SOURCE_AUDIOBOOKSHELF = "audiobookshelf"
SOURCE_GRIMMORY = "grimmory"

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS library_items (
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    library_id TEXT NOT NULL,
    library_name TEXT NOT NULL,
    media_type TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    author TEXT NOT NULL,
    asin TEXT,
    isbn13 TEXT,
    PRIMARY KEY (source, item_id)
);

CREATE TABLE IF NOT EXISTS library_item_keys (
    match_key TEXT NOT NULL,
    source TEXT NOT NULL,
    item_id TEXT NOT NULL,
    PRIMARY KEY (match_key, source, item_id)
);

CREATE INDEX IF NOT EXISTS idx_library_item_keys_key ON library_item_keys (match_key);

CREATE TABLE IF NOT EXISTS index_state (
    source TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (source, key)
);
"""

_STATE_LAST_SYNC = "last_sync_at"
_STATE_LAST_ERROR = "last_error"


@dataclass(frozen=True)
class LibraryItem:
    """One indexed item, flattened to the fields matching needs."""

    source: str
    item_id: str
    library_id: str
    library_name: str
    media_type: str
    title: str
    subtitle: str
    author: str
    asin: str
    isbn13: str


@dataclass(frozen=True)
class LibraryMatch:
    """An indexed item that matched a book, with enough detail to name it."""

    source: str
    item_id: str
    library_id: str
    library_name: str
    media_type: str
    title: str
    author: str
    asin: str
    isbn13: str


@dataclass(frozen=True)
class IndexState:
    """When the index was last refreshed, and whether the last attempt failed."""

    last_sync_at: str | None
    last_error: str | None
    item_count: int


def get_index_db_path(config_dir: str | None = None) -> str:
    """Return the path of the library index database."""
    root = config_dir or os.environ.get("CONFIG_DIR", "/config")
    return str(Path(root) / "library_index.db")


_LEGACY_DB_NAME = "audiobookshelf_index.db"


class LibraryIndexDB:
    """Thread-safe SQLite cache of library contents across sources."""

    def __init__(self, db_path: str) -> None:
        """Initialize the index wrapper for the given SQLite path."""
        self._db_path = db_path
        self._lock = threading.Lock()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def initialize(self) -> None:
        """Create the index tables if they do not exist yet."""
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(_CREATE_TABLES_SQL)
                conn.commit()
                conn.execute("PRAGMA journal_mode=WAL")
            finally:
                conn.close()

        self._remove_legacy_index()

    def _remove_legacy_index(self) -> None:
        """Delete the superseded single-source index file, best effort.

        The index is a cache that rebuilds on the next sync, so the old file is
        dead weight rather than data. Failing to remove it must never stop the
        new index coming up.

        The -wal and -shm sidecars go too: the old index ran in WAL mode, so
        removing only the main file would leave two orphans behind and defeat
        the point of cleaning up at all.
        """
        base = Path(self._db_path).with_name(_LEGACY_DB_NAME)
        for legacy in (
            base,
            base.with_name(f"{base.name}-wal"),
            base.with_name(f"{base.name}-shm"),
        ):
            with suppress(OSError):
                if legacy.exists():
                    legacy.unlink()
                    logger.info("Removed superseded index file %s", legacy)

    def replace_items(self, source: str, items: list[LibraryItem]) -> int:
        """Replace this source's slice of the index, returning how many were stored.

        Scoped to one source so a Grimmory sync can never drop Audiobookshelf
        rows. Items that cannot produce a match key are dropped: they could
        never answer a lookup, so keeping them would only inflate the count the
        UI reports.
        """
        rows: list[tuple[str, ...]] = []
        key_rows: list[tuple[str, str, str]] = []
        seen: set[str] = set()

        for item in items:
            if not item.item_id or item.item_id in seen:
                continue
            keys = build_match_keys(
                item.title, item.author, item.subtitle, asin=item.asin, isbn=item.isbn13
            )
            if not keys:
                continue

            seen.add(item.item_id)
            rows.append(
                (
                    source,
                    item.item_id,
                    item.library_id,
                    item.library_name,
                    item.media_type,
                    item.title,
                    item.subtitle,
                    item.author,
                    item.asin,
                    item.isbn13,
                )
            )
            key_rows.extend((key, source, item.item_id) for key in keys)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM library_items WHERE source = ?", (source,))
                conn.execute("DELETE FROM library_item_keys WHERE source = ?", (source,))
                conn.executemany(
                    """
                    INSERT INTO library_items
                        (source, item_id, library_id, library_name, media_type,
                         title, subtitle, author, asin, isbn13)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO library_item_keys (match_key, source, item_id) "
                    "VALUES (?, ?, ?)",
                    key_rows,
                )
                self._set_state(conn, source, _STATE_LAST_SYNC, datetime.now(UTC).isoformat())
                self._set_state(conn, source, _STATE_LAST_ERROR, None)
                conn.commit()
            finally:
                conn.close()

        return len(rows)

    def find_matches(self, match_keys: set[str]) -> list[LibraryMatch]:
        """Return every indexed item matching any of `match_keys`, across all sources."""
        keys = [key for key in match_keys if key]
        if not keys:
            return []

        matches: dict[tuple[str, str], LibraryMatch] = {}
        with self._lock:
            conn = self._connect()
            try:
                for start in range(0, len(keys), _MAX_QUERY_KEYS):
                    chunk = keys[start : start + _MAX_QUERY_KEYS]
                    placeholders = ",".join("?" * len(chunk))
                    cursor = conn.execute(
                        f"""
                        SELECT DISTINCT i.source, i.item_id, i.library_id, i.library_name,
                               i.media_type, i.title, i.author, i.asin, i.isbn13
                        FROM library_items i
                        JOIN library_item_keys k
                          ON k.item_id = i.item_id AND k.source = i.source
                        WHERE k.match_key IN ({placeholders})
                        """,  # noqa: S608 - placeholders only, keys are bound
                        chunk,
                    )
                    for row in cursor.fetchall():
                        match = LibraryMatch(
                            source=str(row["source"]),
                            item_id=str(row["item_id"]),
                            library_id=str(row["library_id"]),
                            library_name=str(row["library_name"]),
                            media_type=str(row["media_type"]),
                            title=str(row["title"]),
                            author=str(row["author"]),
                            asin=str(row["asin"] or ""),
                            isbn13=str(row["isbn13"] or ""),
                        )
                        matches[(match.source, match.item_id)] = match
            finally:
                conn.close()

        return list(matches.values())

    def record_failure(self, source: str, message: str) -> None:
        """Record that a sync attempt failed, leaving the existing index intact."""
        with self._lock:
            conn = self._connect()
            try:
                self._set_state(conn, source, _STATE_LAST_ERROR, message)
                conn.commit()
            finally:
                conn.close()

    def get_state(self, source: str) -> IndexState:
        """Return this source's index freshness and last failure, if any."""
        with self._lock:
            conn = self._connect()
            try:
                state = {
                    str(row["key"]): row["value"]
                    for row in conn.execute(
                        "SELECT key, value FROM index_state WHERE source = ?", (source,)
                    ).fetchall()
                }
                count_row = conn.execute(
                    "SELECT COUNT(*) AS n FROM library_items WHERE source = ?", (source,)
                ).fetchone()
            finally:
                conn.close()

        last_error = state.get(_STATE_LAST_ERROR)
        return IndexState(
            last_sync_at=state.get(_STATE_LAST_SYNC) or None,
            last_error=str(last_error) if last_error else None,
            item_count=int(count_row["n"]) if count_row else 0,
        )

    def _set_state(
        self, conn: sqlite3.Connection, source: str, key: str, value: str | None
    ) -> None:
        conn.execute(
            "INSERT INTO index_state (source, key, value) VALUES (?, ?, ?) "
            "ON CONFLICT(source, key) DO UPDATE SET value = excluded.value",
            (source, key, value),
        )


_index: LibraryIndexDB | None = None
_index_lock = threading.Lock()


def get_library_index() -> LibraryIndexDB:
    """Return the process-wide library index, initializing it on first use."""
    global _index

    with _index_lock:
        if _index is None:
            index = LibraryIndexDB(get_index_db_path())
            index.initialize()
            _index = index
        return _index
