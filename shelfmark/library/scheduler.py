"""Refreshing the shared library index from every registered provider.

Nothing here raises at the caller. A sync that fails records why and leaves the
previous index in place, so an outage costs badge freshness rather than the
badges themselves — and one source being down never blanks another.
"""

from __future__ import annotations

import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from shelfmark.core.logger import setup_logger
from shelfmark.grimmory.client import BookloreError
from shelfmark.library.index import LibraryIndexDB, get_library_index
from shelfmark.library.providers import LibraryProvider, get_providers

logger = setup_logger(__name__)

# The scheduler wakes far more often than it syncs so an interval change takes
# effect without a restart; staleness, not the sleep, decides when to sync.
_SCHEDULER_POLL_SECONDS = 300

# Anything a provider's transport can throw. Providers must not leak these.
# BookloreError subclasses Exception directly (not one of the others below),
# so without it a Grimmory outage would escape sync_provider instead of being
# recorded as a failure.
PROVIDER_ERRORS = (BookloreError, OSError, RuntimeError, TypeError, ValueError)


@dataclass(frozen=True)
class SyncResult:
    """The outcome of one index refresh."""

    success: bool
    item_count: int
    message: str


def sync_provider(provider: LibraryProvider, index: LibraryIndexDB) -> SyncResult:
    """Rebuild one source's slice of the index, or explain why it could not be.

    The storage write is inside the guard, not just the fetch. A full disk or a
    locked database is exactly as much a sync failure as an unreachable server,
    and letting it escape would take down the caller — the settings action
    button and the scheduler cycle both call straight through here.
    """
    try:
        items = provider.fetch_items()
        stored = index.replace_items(provider.source, items)
    except PROVIDER_ERRORS as e:
        message = f"Library sync failed: {e!s}"
        logger.warning("%s (%s)", message, provider.source)
        # Best effort: if the index itself is what failed, recording the failure
        # may fail too, and that must not escape either.
        with suppress(Exception):
            index.record_failure(provider.source, message)
        return SyncResult(success=False, item_count=0, message=message)

    logger.info("Indexed %d items from %s", stored, provider.source)
    return SyncResult(
        success=True,
        item_count=stored,
        message=f"Indexed {stored} items from {provider.source}",
    )


def run_sync_now(source: str) -> SyncResult:
    """Refresh one source immediately, by name."""
    for provider in get_providers():
        if provider.source == source:
            return sync_provider(provider, get_library_index())

    return SyncResult(success=False, item_count=0, message=f"Unknown library source {source!r}")


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


def _scheduler_loop() -> None:
    while True:
        try:
            index = get_library_index()
            for provider in get_providers():
                if not provider.is_enabled():
                    continue
                state = index.get_state(provider.source)
                if is_index_stale(state.last_sync_at, interval_hours=provider.interval_hours()):
                    sync_provider(provider, index)
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
            name="LibraryIndex",
        )
        _scheduler_thread.start()

    logger.info("Library index refresher started")
