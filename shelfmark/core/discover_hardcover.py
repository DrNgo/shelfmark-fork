"""Hardcover rows for the Discover page (fork-only).

Kept out of metadata_providers/hardcover.py so upstream's provider stays its
own file: these use only the provider's API key, query client and record parser.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from shelfmark.core.logger import setup_logger
from shelfmark.metadata_providers.hardcover import HardcoverGraphQLError

if TYPE_CHECKING:
    from shelfmark.metadata_providers import BookMetadata
    from shelfmark.metadata_providers.hardcover import HardcoverProvider

logger = setup_logger(__name__)

_DISCOVER_BOOK_FIELDS = """
                id
                title
                subtitle
                slug
                release_date
                headline
                description
                pages
                rating
                ratings_count
                users_count
                cached_image
                cached_contributors
                contributions(where: {contribution: {_eq: "Author"}}) {
                    author {
                        name
                    }
                }
                featured_book_series {
                    position
                    series {
                        id
                        name
                        primary_books_count
                    }
                }
"""

DISCOVER_TRENDING_IDS_QUERY = """
query DiscoverTrendingIds($from: date!, $to: date!, $limit: Int!) {
    books_trending(from: $from, to: $to, limit: $limit, offset: 0) {
        error
        ids
    }
}
"""

DISCOVER_BOOKS_BY_IDS_QUERY = f"""
query DiscoverBooksByIds($ids: [Int!]!) {{
    books(where: {{id: {{_in: $ids}}}}) {{
{_DISCOVER_BOOK_FIELDS}
    }}
}}
"""

DISCOVER_BOOKS_BY_IDS_AUDIO_QUERY = f"""
query DiscoverBooksByIdsAudio($ids: [Int!]!) {{
    books(
        where: {{id: {{_in: $ids}}, default_audio_edition_id: {{_is_null: false}}}}
    ) {{
{_DISCOVER_BOOK_FIELDS}
    }}
}}
"""

_DISCOVER_NEW_RELEASES_WHERE = """
            release_date: {_gte: $from, _lte: $to},
            canonical_id: {_is_null: true},
            state: {_in: ["normalized", "normalizing"]},
            compilation: {_eq: false},
            users_count: {_gte: 10}
"""

DISCOVER_NEW_RELEASES_QUERY = f"""
query DiscoverNewReleases($from: date!, $to: date!, $limit: Int!) {{
    books(
        where: {{{_DISCOVER_NEW_RELEASES_WHERE}}},
        order_by: [{{users_count: desc_nulls_last}}, {{release_date: desc}}],
        limit: $limit
    ) {{
{_DISCOVER_BOOK_FIELDS}
    }}
}}
"""

DISCOVER_NEW_RELEASES_AUDIO_QUERY = f"""
query DiscoverNewReleasesAudio($from: date!, $to: date!, $limit: Int!) {{
    books(
        where: {{{_DISCOVER_NEW_RELEASES_WHERE},
            default_audio_edition_id: {{_is_null: false}}}},
        order_by: [{{users_count: desc_nulls_last}}, {{release_date: desc}}],
        limit: $limit
    ) {{
{_DISCOVER_BOOK_FIELDS}
    }}
}}
"""


def _parse_books(provider: HardcoverProvider, books: list) -> list[BookMetadata]:
    """Parse records, skipping (not failing on) malformed ones."""
    parsed: list[BookMetadata] = []
    for book in books:
        if not isinstance(book, dict):
            continue
        try:
            parsed.append(provider._parse_book(book))
        except TypeError, ValueError, AttributeError, KeyError:
            logger.debug("Skipping malformed discover record: %s", book.get("id"))
    return parsed


def trending(
    provider: HardcoverProvider, limit: int = 20, *, audio_only: bool = False
) -> list[BookMetadata] | None:
    """Trending row: ranked ids from books_trending, hydrated and re-ordered.

    Returns None on provider failure, [] when the feed is genuinely empty.
    """
    if not provider.api_key:
        return None

    to_date = datetime.now(UTC).date()
    from_date = to_date - timedelta(days=30)
    try:
        ids_data = provider._execute_query(
            DISCOVER_TRENDING_IDS_QUERY,
            {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                # Over-fetch: the audio filter (and skipped records) shrink
                # the hydrated set; 3x is the spec's top-up strategy.
                "limit": limit * 3,
            },
            raise_on_error=True,
        )
    except RuntimeError, HardcoverGraphQLError:
        return None
    if ids_data is None:
        return None

    trending = ids_data.get("books_trending") or {}
    if trending.get("error"):
        return None
    ids = [i for i in (trending.get("ids") or []) if isinstance(i, int)]
    if not ids:
        return []

    hydration_query = (
        DISCOVER_BOOKS_BY_IDS_AUDIO_QUERY if audio_only else DISCOVER_BOOKS_BY_IDS_QUERY
    )
    try:
        books_data = provider._execute_query(hydration_query, {"ids": ids}, raise_on_error=True)
    except RuntimeError, HardcoverGraphQLError:
        return None
    if books_data is None:
        return None
    books = books_data.get("books")
    if not isinstance(books, list):
        return None

    by_id = {
        book["id"]: book
        for book in books
        if isinstance(book, dict) and isinstance(book.get("id"), int)
    }
    # books(id: {_in: ...}) does not preserve order; restore trending rank.
    ordered = [by_id[book_id] for book_id in ids if book_id in by_id]
    return _parse_books(provider, ordered)[:limit]


def new_releases(
    provider: HardcoverProvider, limit: int = 20, *, audio_only: bool = False
) -> list[BookMetadata] | None:
    """New-releases row: notable books released in the last 90 days.

    Returns None on provider failure, [] when nothing qualifies.
    """
    if not provider.api_key:
        return None

    to_date = datetime.now(UTC).date()
    from_date = to_date - timedelta(days=90)
    query = DISCOVER_NEW_RELEASES_AUDIO_QUERY if audio_only else DISCOVER_NEW_RELEASES_QUERY
    try:
        data = provider._execute_query(
            query,
            {
                "from": from_date.isoformat(),
                "to": to_date.isoformat(),
                "limit": limit,
            },
            raise_on_error=True,
        )
    except RuntimeError, HardcoverGraphQLError:
        return None
    if data is None:
        return None

    books = data.get("books")
    if not isinstance(books, list):
        return None
    return _parse_books(provider, books)
