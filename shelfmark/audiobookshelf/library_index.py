"""Local cache of what Audiobookshelf already holds.

The badge answers "do I already own this?" on every rendered search result, so
it reads a local table rather than asking Audiobookshelf per card. A sync is a
full swap: books removed from Audiobookshelf must stop claiming to be owned.

Failures are recorded beside the data, never instead of it — an Audiobookshelf
outage should leave a stale badge standing, not blank the whole index.
"""

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from shelfmark.core.logger import setup_logger
from shelfmark.library.matching import build_match_keys

logger = setup_logger(__name__)

# SQLite's default variable limit is 999; keys are looked up in chunks so a
# batch lookup over a full page of search results can't blow past it.
_MAX_QUERY_KEYS = 500

_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS library_items (
    item_id TEXT PRIMARY KEY,
    library_id TEXT NOT NULL,
    library_name TEXT NOT NULL,
    title TEXT NOT NULL,
    subtitle TEXT,
    author TEXT NOT NULL,
    asin TEXT
);

CREATE TABLE IF NOT EXISTS library_item_keys (
    match_key TEXT NOT NULL,
    item_id TEXT NOT NULL,
    PRIMARY KEY (match_key, item_id)
);

CREATE INDEX IF NOT EXISTS idx_library_item_keys_key ON library_item_keys (match_key);

CREATE TABLE IF NOT EXISTS index_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""

_STATE_LAST_SYNC = "last_sync_at"
_STATE_LAST_ERROR = "last_error"


@dataclass(frozen=True)
class LibraryItem:
    """One Audiobookshelf item, flattened to the fields matching needs."""

    item_id: str
    library_id: str
    library_name: str
    title: str
    subtitle: str
    author: str
    asin: str


@dataclass(frozen=True)
class LibraryMatch:
    """An indexed item that matched a book, with enough detail to name it."""

    item_id: str
    library_id: str
    library_name: str
    title: str
    author: str
    asin: str


@dataclass(frozen=True)
class IndexState:
    """When the index was last refreshed, and whether the last attempt failed."""

    last_sync_at: str | None
    last_error: str | None
    item_count: int


def get_index_db_path(config_dir: str | None = None) -> str:
    """Return the path of the library index database."""
    root = config_dir or os.environ.get("CONFIG_DIR", "/config")
    return str(Path(root) / "audiobookshelf_index.db")


class LibraryIndexDB:
    """Thread-safe SQLite cache of Audiobookshelf library contents."""

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

    def replace_items(self, items: list[LibraryItem]) -> int:
        """Replace the whole index with `items`, returning how many were stored.

        Items that cannot produce a match key (no title or no author, and no
        usable ASIN) are dropped: they could never answer a lookup, so keeping
        them would only inflate the count the UI reports.
        """
        rows: list[tuple[str, str, str, str, str, str, str]] = []
        key_rows: list[tuple[str, str]] = []
        seen: set[str] = set()

        for item in items:
            if not item.item_id or item.item_id in seen:
                continue
            keys = build_match_keys(item.title, item.author, item.subtitle, asin=item.asin)
            if not keys:
                continue

            seen.add(item.item_id)
            rows.append(
                (
                    item.item_id,
                    item.library_id,
                    item.library_name,
                    item.title,
                    item.subtitle,
                    item.author,
                    item.asin,
                )
            )
            key_rows.extend((key, item.item_id) for key in keys)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM library_items")
                conn.execute("DELETE FROM library_item_keys")
                conn.executemany(
                    """
                    INSERT INTO library_items
                        (item_id, library_id, library_name, title, subtitle, author, asin)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO library_item_keys (match_key, item_id) VALUES (?, ?)",
                    key_rows,
                )
                self._set_state(conn, _STATE_LAST_SYNC, datetime.now(UTC).isoformat())
                self._set_state(conn, _STATE_LAST_ERROR, None)
                conn.commit()
            finally:
                conn.close()

        return len(rows)

    def find_matches(self, match_keys: set[str]) -> list[LibraryMatch]:
        """Return every indexed item matching any of `match_keys`."""
        keys = [key for key in match_keys if key]
        if not keys:
            return []

        matches: dict[str, LibraryMatch] = {}
        with self._lock:
            conn = self._connect()
            try:
                for start in range(0, len(keys), _MAX_QUERY_KEYS):
                    chunk = keys[start : start + _MAX_QUERY_KEYS]
                    placeholders = ",".join("?" * len(chunk))
                    cursor = conn.execute(
                        f"""
                        SELECT DISTINCT i.item_id, i.library_id, i.library_name,
                               i.title, i.author, i.asin
                        FROM library_items i
                        JOIN library_item_keys k ON k.item_id = i.item_id
                        WHERE k.match_key IN ({placeholders})
                        """,  # noqa: S608 - placeholders only, keys are bound
                        chunk,
                    )
                    for row in cursor.fetchall():
                        matches[str(row["item_id"])] = LibraryMatch(
                            item_id=str(row["item_id"]),
                            library_id=str(row["library_id"]),
                            library_name=str(row["library_name"]),
                            title=str(row["title"]),
                            author=str(row["author"]),
                            asin=str(row["asin"] or ""),
                        )
            finally:
                conn.close()

        return list(matches.values())

    def record_failure(self, message: str) -> None:
        """Record that a sync attempt failed, leaving the existing index intact."""
        with self._lock:
            conn = self._connect()
            try:
                self._set_state(conn, _STATE_LAST_ERROR, message)
                conn.commit()
            finally:
                conn.close()

    def get_state(self) -> IndexState:
        """Return the freshness of the index and the last failure, if any."""
        with self._lock:
            conn = self._connect()
            try:
                state = {
                    str(row["key"]): row["value"]
                    for row in conn.execute("SELECT key, value FROM index_state").fetchall()
                }
                count_row = conn.execute("SELECT COUNT(*) AS n FROM library_items").fetchone()
            finally:
                conn.close()

        last_error = state.get(_STATE_LAST_ERROR)
        return IndexState(
            last_sync_at=state.get(_STATE_LAST_SYNC) or None,
            last_error=str(last_error) if last_error else None,
            item_count=int(count_row["n"]) if count_row else 0,
        )

    def _set_state(self, conn: sqlite3.Connection, key: str, value: str | None) -> None:
        conn.execute(
            "INSERT INTO index_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
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
