"""Discover rows: provider dispatch, dual-entry caching, serve-stale.

Fetch contract (shared with the provider fetchers): a fetcher returns
None on provider failure and [] when the feed is genuinely empty. Failures
fall back to the 7-day "last_good" cache entry; empties are cached successes.
CacheService is used as-is — the stale window lives in a second entry rather
than in modified cache semantics.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from shelfmark.core.audible_topics import (
    audible_topic_path_digest,
    resolve_audible_topic,
)
from shelfmark.core.cache import get_metadata_cache
from shelfmark.core.config import config as app_config
from shelfmark.core.logger import setup_logger
from shelfmark.metadata_providers import (
    BookMetadata,
    get_configured_provider_name,
    get_provider,
    get_provider_kwargs,
    is_provider_enabled,
)
from shelfmark.metadata_providers.audible_taxonomy import (
    CORE_TOPIC_DEFINITIONS,
    core_topic_path,
    preferred_topic_label,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = setup_logger(__name__)

ROWS_BY_PROVIDER: dict[str, list[tuple[str, str]]] = {
    "hardcover": [("trending", "Trending"), ("new_releases", "New Releases")],
    "audible": [("best_sellers", "Best Sellers"), ("new_releases", "New Releases")],
}
AUDIBLE_TOPIC_ROWS = tuple(
    (definition.key, definition.label) for definition in CORE_TOPIC_DEFINITIONS
)
PREFERRED_TOPIC_ROW_KEY = "preferred_topic"
ALL_DISCOVER_ROW_KEYS = (
    frozenset(key for rows in ROWS_BY_PROVIDER.values() for key, _label in rows)
    | frozenset(key for key, _label in AUDIBLE_TOPIC_ROWS)
    | {PREFERRED_TOPIC_ROW_KEY}
)

ROW_LIMIT = 20
ROW_TTLS: dict[str, int] = {
    "trending": 6 * 3600,
    "best_sellers": 6 * 3600,
    "new_releases": 24 * 3600,
}
LAST_GOOD_TTL = 7 * 24 * 3600


@dataclass(frozen=True)
class DiscoverRow:
    """One rendered discover row."""

    key: str
    label: str
    provider: str
    books: list[BookMetadata] = field(default_factory=list)
    stale: bool = False


# Bounded in practice: one lock per (provider, row, variant/region) actually
# requested — a handful of keys per deployment, so no eviction is needed.
_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _key_locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


def _fetch(
    provider: object, provider_name: str, row_key: str, *, audio_only: bool
) -> list[BookMetadata] | None:
    if provider_name == "hardcover":
        if row_key == "trending":
            return provider.discover_trending(ROW_LIMIT, audio_only=audio_only)
        return provider.discover_new_releases(ROW_LIMIT, audio_only=audio_only)
    if row_key == "best_sellers":
        return provider.discover_best_sellers(ROW_LIMIT)
    return provider.discover_new_releases(ROW_LIMIT)


def _row_ttl(row_key: str) -> int:
    return 24 * 3600 if row_key == "new_releases" else 6 * 3600


def _cached_row(
    *,
    base_key: str,
    row_key: str,
    label: str,
    provider_name: str,
    fetcher: Callable[[], list[BookMetadata] | None],
) -> DiscoverRow:
    """Run one discover fetcher through the shared dual-entry cache."""
    fresh_key = f"{base_key}:fresh"
    last_good_key = f"{base_key}:last_good"

    def _row(books: list[BookMetadata], *, stale: bool) -> DiscoverRow:
        return DiscoverRow(
            key=row_key, label=label, provider=provider_name, books=books, stale=stale
        )

    cache = get_metadata_cache()
    cached = cache.get(fresh_key)
    if cached is not None:
        return _row(cast("list[BookMetadata]", cached), stale=False)

    with _lock_for(base_key):
        cached = cache.get(fresh_key)
        if cached is not None:
            return _row(cast("list[BookMetadata]", cached), stale=False)

        books = fetcher()
        if books is None:
            stale_books = cache.get(last_good_key)
            if stale_books is not None:
                logger.warning("Discover %s: provider failed, serving stale", base_key)
                return _row(cast("list[BookMetadata]", stale_books), stale=True)
            logger.warning("Discover %s: provider failed, no stale data", base_key)
            return _row([], stale=False)

        cache.set(fresh_key, books, _row_ttl(row_key))
        cache.set(last_good_key, books, LAST_GOOD_TTL)
        return _row(books, stale=False)


def _get_audible_topic_row(
    content_type: str, row_key: str, user_id: int | None
) -> DiscoverRow | None:
    if content_type == "ebook":
        return None

    provider_name = get_configured_provider_name("audiobook", user_id=user_id)
    if provider_name != "audible" or not is_provider_enabled("audible"):
        return None

    provider = get_provider("audible", **get_provider_kwargs("audible"))
    if not provider.is_available():
        return None

    if row_key == PREFERRED_TOPIC_ROW_KEY:
        path = app_config.get("DEFAULT_DISCOVER_TOPIC", [], user_id=user_id)
        label = preferred_topic_label(path) if isinstance(path, (list, tuple)) else ""
        try:
            path_key = audible_topic_path_digest(path)
        except ValueError:
            path = ()
            path_key = audible_topic_path_digest(path)
        preferred_key = f"preferred:{path_key}"
    else:
        path = core_topic_path(provider.region, row_key)
        label = dict(AUDIBLE_TOPIC_ROWS)[row_key]
        preferred_key = row_key

    resolution = resolve_audible_topic(path)
    if resolution.failed:
        return DiscoverRow(
            key=row_key,
            label=label,
            provider="audible",
            books=[],
            stale=False,
        )

    if (resolution.region, resolution.tld) != (provider.region, provider.tld):
        logger.warning(
            "Audible topic storefront changed during resolution: product=%s/%s taxonomy=%s/%s",
            provider.region,
            provider.tld,
            resolution.region,
            resolution.tld,
        )
        return DiscoverRow(
            key=row_key,
            label=label,
            provider="audible",
            books=[],
            stale=False,
        )

    category_id = resolution.node.category_id if resolution.node is not None else None
    category_token = category_id or "unsupported"
    base_key = f"discover:audible:{provider.tld}:{preferred_key}:{category_token}"

    def fetch_topic() -> list[BookMetadata] | None:
        if category_id is None:
            return []
        return provider.discover_topic(category_id, ROW_LIMIT)

    return _cached_row(
        base_key=base_key,
        row_key=row_key,
        label=label,
        provider_name="audible",
        fetcher=fetch_topic,
    )


def get_discover_row(
    content_type: str, row_key: str, user_id: int | None = None
) -> DiscoverRow | None:
    """Return the discover row, or None if no provider/row applies.

    A returned row with empty books means "nothing to show" (hidden row);
    stale=True marks last-good data served through a provider outage.
    """
    if row_key in dict(AUDIBLE_TOPIC_ROWS) or row_key == PREFERRED_TOPIC_ROW_KEY:
        return _get_audible_topic_row(content_type, row_key, user_id)

    provider_name = get_configured_provider_name(content_type, user_id=user_id)
    rows = dict(ROWS_BY_PROVIDER.get(provider_name, []))
    label = rows.get(row_key)
    if label is None:
        return None
    if not is_provider_enabled(provider_name):
        return None

    provider = get_provider(provider_name, **get_provider_kwargs(provider_name))
    if not provider.is_available():
        return None
    audio_only = provider_name == "hardcover" and content_type == "audiobook"

    if provider_name == "hardcover":
        variant = "audio" if audio_only else "all"
        base_key = f"discover:hardcover:{row_key}:{variant}"
    else:
        # Region changes must not serve the old storefront (stale included).
        base_key = f"discover:audible:{provider.tld}:{row_key}"

    return _cached_row(
        base_key=base_key,
        row_key=row_key,
        label=label,
        provider_name=provider_name,
        fetcher=lambda: _fetch(provider, provider_name, row_key, audio_only=audio_only),
    )
