"""Grimmory library provider (fork-only).

Reads the fork's library index (``shelfmark.library``) instead of Grimmory itself: the
index scheduler already lists Grimmory, reading each book for its Hardcover id, and a
second fetcher here would repeat that per-book traffic on every cache expiry. The index
sync time is the fingerprint, so a fresh sync is picked up at once.

Only ebook rows are offered. Grimmory can hold a stray audiobook import, and that must
not mark an ebook search result as owned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shelfmark.core.library_providers import LibraryEntry
from shelfmark.core.text_match import isbn_variants, tokens

if TYPE_CHECKING:
    from collections.abc import Mapping

    from shelfmark.library.index import LibraryItem


def item_to_entry(item: LibraryItem) -> LibraryEntry:
    title_tok = set(tokens(item.title))
    context_tok = set(tokens(item.author))
    return LibraryEntry(
        frozenset(title_tok | context_tok | set(tokens(item.subtitle))),
        isbn_variants(item.isbn13),
        frozenset({item.asin.upper()} if item.asin else set()),
        frozenset({("hardcover", item.hardcover_id)} if item.hardcover_id else set()),
        frozenset(title_tok),
        frozenset(context_tok),
    )


class GrimmoryLibrary:
    """Ebook ownership from the Grimmory slice of the fork's library index."""

    name = "grimmory"
    display_name = "Grimmory"
    content_types = frozenset({"ebook"})

    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        # Unsaved form values only matter to a fetcher; this reads the stored index.
        del overrides

    def is_enabled(self) -> bool:
        from shelfmark.library.providers.grimmory import GrimmoryProvider

        return GrimmoryProvider().is_enabled()

    def describe(self) -> str:
        return "Grimmory library index"

    def fingerprint(self) -> str | None:
        from shelfmark.library.index import SOURCE_GRIMMORY, get_library_index

        return get_library_index().get_state(SOURCE_GRIMMORY).last_sync_at

    def fetch_entries(self) -> list[LibraryEntry]:
        from shelfmark.library.index import SOURCE_GRIMMORY, get_library_index
        from shelfmark.library.media_type import MEDIA_TYPE_EBOOK

        index = get_library_index()
        if index.get_state(SOURCE_GRIMMORY).last_sync_at is None:
            # Never synced is not an empty library: strict callers must hold.
            msg = "Grimmory has not been indexed yet"
            raise RuntimeError(msg)
        return [
            item_to_entry(item)
            for item in index.list_items(SOURCE_GRIMMORY)
            if item.media_type == MEDIA_TYPE_EBOOK
        ]
