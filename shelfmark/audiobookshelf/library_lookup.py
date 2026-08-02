"""Batch "do I already own this?" lookups for the in-library badge.

The frontend asks once per rendered page of results rather than once per card,
and the answer comes entirely from the local index — a search must never wait
on Audiobookshelf.
"""

from typing import Any

from shelfmark.library.index import (
    SOURCE_AUDIOBOOKSHELF,
    LibraryIndexDB,
    LibraryMatch,
    get_library_index,
)
from shelfmark.library.matching import build_match_keys
from shelfmark.library.providers.audiobookshelf import AudiobookshelfProvider
from shelfmark.library.scheduler import is_index_stale

# A page of search results is dozens of books; anything past this is either a
# bug or someone using the endpoint as a bulk library query.
MAX_LOOKUP_BOOKS = 200


def _match_payload(matches: list[LibraryMatch]) -> dict[str, Any]:
    """Describe a match well enough for the badge to name the edition held.

    "In library" is not "same recording" — a 2021 rip and a 2024 re-recording
    are both *The Locked Door*, so the payload carries the item's own title and
    ASIN rather than just a boolean.
    """
    items = sorted(matches, key=lambda m: (m.library_name, m.title))
    return {
        "libraries": sorted({m.library_name for m in items}),
        "items": [
            {
                "item_id": m.item_id,
                "library_id": m.library_id,
                "library_name": m.library_name,
                "title": m.title,
                "author": m.author,
                "asin": m.asin,
            }
            for m in items
        ],
    }


def lookup_books(books: list[Any], *, index: LibraryIndexDB | None = None) -> dict[str, Any]:
    """Look up which of `books` are already in the Audiobookshelf libraries.

    Books without both a title and an author are skipped rather than matched
    loosely: matching on a title alone would mark all four *Housemaid* titles
    as owned the moment any one of them was.
    """
    library_index = index if index is not None else get_library_index()
    provider = AudiobookshelfProvider()

    if not provider.is_enabled():
        return {"enabled": False, "stale": False, "last_sync_at": None, "matches": {}}

    state = library_index.get_state(SOURCE_AUDIOBOOKSHELF)
    result: dict[str, Any] = {
        "enabled": True,
        "stale": is_index_stale(state.last_sync_at, interval_hours=provider.interval_hours()),
        "last_sync_at": state.last_sync_at,
        "matches": {},
    }

    if not isinstance(books, list):
        return result

    matches: dict[str, Any] = {}
    for book in books[:MAX_LOOKUP_BOOKS]:
        if not isinstance(book, dict):
            continue

        book_id = str(book.get("id") or "").strip()
        if not book_id or book_id in matches:
            continue

        keys = build_match_keys(book.get("title"), book.get("author"), asin=book.get("asin"))
        if not keys:
            continue

        found = library_index.find_matches(keys)
        if found:
            matches[book_id] = _match_payload(found)

    result["matches"] = matches
    return result
