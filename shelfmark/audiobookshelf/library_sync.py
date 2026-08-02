"""Refreshing the library index from Audiobookshelf.

Every book-type library is indexed, not just the ones mapped as download
destinations: a book sitting in an unmapped library is still a book you own.

Nothing here raises at the caller. A sync that fails records why and leaves the
previous index in place, so an Audiobookshelf outage costs badge freshness
rather than the badges themselves.
"""

import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from shelfmark.audiobookshelf.client import ABS_CLIENT_ERRORS
from shelfmark.core.logger import setup_logger
from shelfmark.library.index import (
    MEDIA_TYPE_AUDIOBOOK,
    SOURCE_AUDIOBOOKSHELF,
    LibraryIndexDB,
    LibraryItem,
    get_library_index,
)

if TYPE_CHECKING:
    from shelfmark.audiobookshelf.client import AudiobookshelfClient, AudiobookshelfLibrary

logger = setup_logger(__name__)

LIBRARY_INDEX_ENABLED_KEY = "AUDIOBOOKSHELF_LIBRARY_INDEX_ENABLED"
LIBRARY_INDEX_INTERVAL_KEY = "AUDIOBOOKSHELF_INDEX_INTERVAL_HOURS"

_DEFAULT_INTERVAL_HOURS = 1
_MIN_INTERVAL_HOURS = 1
# The scheduler wakes far more often than it syncs so an interval change takes
# effect without a restart; staleness, not the sleep, decides when to sync.
_SCHEDULER_POLL_SECONDS = 300


@dataclass(frozen=True)
class SyncResult:
    """The outcome of one index refresh."""

    success: bool
    item_count: int
    message: str


def _metadata(raw: object) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    media = raw.get("media")
    if not isinstance(media, dict):
        return None
    metadata = media.get("metadata")
    return metadata if isinstance(metadata, dict) else None


def _primary_author(metadata: dict[str, Any]) -> str:
    """Return the first author's name.

    `authors` is preferred because `authorName` joins co-authors with commas,
    which the matcher would otherwise read as an inverted "Last, First" name.
    """
    authors = metadata.get("authors")
    if isinstance(authors, list):
        for author in authors:
            if isinstance(author, dict):
                name = str(author.get("name") or "").strip()
                if name:
                    return name

    return str(metadata.get("authorName") or "").strip()


def extract_library_items(
    raw_items: list[Any],
    library: AudiobookshelfLibrary,
) -> list[LibraryItem]:
    """Flatten Audiobookshelf's nested item payloads into index rows."""
    items: list[LibraryItem] = []

    for raw in raw_items:
        metadata = _metadata(raw)
        if metadata is None or not isinstance(raw, dict):
            continue

        item_id = str(raw.get("id") or "").strip()
        title = str(metadata.get("title") or "").strip()
        if not item_id or not title:
            continue

        items.append(
            LibraryItem(
                source=SOURCE_AUDIOBOOKSHELF,
                item_id=item_id,
                library_id=library.id,
                library_name=library.name,
                media_type=MEDIA_TYPE_AUDIOBOOK,
                title=title,
                subtitle=str(metadata.get("subtitle") or "").strip(),
                author=_primary_author(metadata),
                asin=str(metadata.get("asin") or "").strip(),
                isbn13="",
            )
        )

    return items


def sync_library_index(
    *,
    client: AudiobookshelfClient | None,
    index: LibraryIndexDB,
) -> SyncResult:
    """Rebuild the index from Audiobookshelf, or explain why it could not be."""
    if client is None:
        return SyncResult(
            success=False,
            item_count=0,
            message="Audiobookshelf is not configured",
        )

    items: list[LibraryItem] = []
    try:
        libraries = client.get_book_libraries()
        for library in libraries:
            items.extend(extract_library_items(client.get_library_items(library.id), library))
    except ABS_CLIENT_ERRORS as e:
        message = f"Library sync failed: {e!s}"
        logger.warning("%s", message)
        index.record_failure(SOURCE_AUDIOBOOKSHELF, message)
        return SyncResult(success=False, item_count=0, message=message)

    stored = index.replace_items(SOURCE_AUDIOBOOKSHELF, items)
    logger.info(
        "Indexed %d Audiobookshelf items across %d libraries",
        stored,
        len(libraries),
    )
    return SyncResult(
        success=True,
        item_count=stored,
        message=f"Indexed {stored} items from {len(libraries)} libraries",
    )


def is_index_stale(last_sync_at: str | None, *, interval_hours: float) -> bool:
    """Report whether the index is older than the refresh interval.

    An unreadable timestamp counts as stale: re-syncing costs one API walk,
    while trusting it could pin a broken index in place forever.
    """
    if not last_sync_at:
        return True

    try:
        synced = datetime.fromisoformat(last_sync_at)
    except ValueError:
        logger.warning("Unreadable library index timestamp %r; treating as stale", last_sync_at)
        return True

    if synced.tzinfo is None:
        synced = synced.replace(tzinfo=UTC)

    age_hours = (datetime.now(UTC) - synced).total_seconds() / 3600
    return age_hours >= interval_hours


def library_index_enabled() -> bool:
    """Report whether the library index should be maintained and consulted."""
    from shelfmark.core.config import config

    return bool(config.get("AUDIOBOOKSHELF_ENABLED", False)) and bool(
        config.get(LIBRARY_INDEX_ENABLED_KEY, True)
    )


def get_interval_hours() -> float:
    """Return the configured refresh interval, floored at one hour."""
    from shelfmark.core.config import config

    raw = config.get(LIBRARY_INDEX_INTERVAL_KEY, _DEFAULT_INTERVAL_HOURS)
    try:
        interval = float(raw)  # pyright: ignore[reportArgumentType] - config values are untyped
    except TypeError, ValueError:
        return _DEFAULT_INTERVAL_HOURS

    return max(interval, _MIN_INTERVAL_HOURS)


def run_sync_now() -> SyncResult:
    """Refresh the index using the configured Audiobookshelf connection."""
    from shelfmark.audiobookshelf.settings import build_client_from_config

    return sync_library_index(client=build_client_from_config(), index=get_library_index())


def _scheduler_loop() -> None:
    while True:
        try:
            if library_index_enabled():
                state = get_library_index().get_state(SOURCE_AUDIOBOOKSHELF)
                if is_index_stale(state.last_sync_at, interval_hours=get_interval_hours()):
                    run_sync_now()
        except Exception:
            # A scheduler that dies on one bad cycle stops refreshing forever.
            logger.exception("Library index sync cycle failed")

        time.sleep(_SCHEDULER_POLL_SECONDS)


_scheduler_thread: threading.Thread | None = None
_scheduler_lock = threading.Lock()


def start_library_index_sync() -> None:
    """Start the background index refresher. Safe to call multiple times."""
    global _scheduler_thread

    with _scheduler_lock:
        if _scheduler_thread is not None and _scheduler_thread.is_alive():
            return

        _scheduler_thread = threading.Thread(
            target=_scheduler_loop,
            daemon=True,
            name="AudiobookshelfLibraryIndex",
        )
        _scheduler_thread.start()

    logger.info("Audiobookshelf library index refresher started")
