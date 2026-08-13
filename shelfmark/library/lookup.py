"""Batch "do I already own this?" lookups for the in-library badge.

The frontend asks once per rendered page of results rather than once per card,
and the answer comes entirely from the local index — a search must never wait
on a library server.

Matches are split by format. Owning the audiobook is worth telling someone about
but must not stop them acquiring the ebook, so a cross-format holding lands in
`other_formats`, which the badge reports and the acquire button ignores.
"""

from __future__ import annotations

from typing import Any

from shelfmark.library.index import LibraryIndexDB, LibraryMatch, get_library_index
from shelfmark.library.matching import build_match_keys, edition_qualifiers, normalize_asin
from shelfmark.library.media_type import media_type_for_content_type
from shelfmark.library.providers import get_providers
from shelfmark.library.scheduler import is_index_stale

# A page of search results is dozens of books; anything past this is either a
# bug or someone using the endpoint as a bulk library query.
MAX_LOOKUP_BOOKS = 200


def _item_payload(match: LibraryMatch) -> dict[str, Any]:
    return {
        "source": match.source,
        "media_type": match.media_type,
        "item_id": match.item_id,
        "library_id": match.library_id,
        "library_name": match.library_name,
        "title": match.title,
        "author": match.author,
        "asin": match.asin,
        "isbn13": match.isbn13,
    }


def _is_other_edition(match: LibraryMatch, book_asin: str, book_qualifiers: frozenset[str]) -> bool:
    """Whether this holding is demonstrably a different recording of the book.

    Two signals, either sufficient. A two-sided ASIN disagreement is the
    stronger one, but it is silent on sideloaded items, which carry no ASIN at
    all — and those are exactly where alternate editions collect. So an edition
    marker on one title and not the other counts too.

    Both are read only as *disagreement*, never as absence. A missing ASIN and
    a bare title say nothing, and must leave a real holding alone.
    """
    match_asin = normalize_asin(match.asin)
    if book_asin and match_asin and book_asin != match_asin:
        return True

    return edition_qualifiers(match.title) != book_qualifiers


def _match_payload(
    matches: list[LibraryMatch],
    requested_media_type: str,
    book_asin: str = "",
    book_qualifiers: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Split matches into same-format holdings, other editions, and other formats.

    "In library" is not "same edition" — a 2021 rip and a 2024 re-recording are
    both *The Locked Door* — so each entry carries its own title and identifiers
    rather than collapsing to a boolean.

    A same-format match that is provably a different recording moves to
    `other_editions`: still worth reporting, never worth blocking on. Only
    `items` locks the acquire button, so a full-cast release stays acquirable
    while the original sits on the shelf beside it.

    Cross-format matches are not split further. They already decline to lock
    the button, so a second bucket there would be one nothing reads.
    """
    ordered = sorted(matches, key=lambda m: (m.library_name, m.title))
    other = [m for m in ordered if m.media_type != requested_media_type]

    held: list[LibraryMatch] = []
    editions: list[LibraryMatch] = []
    for match in ordered:
        if match.media_type != requested_media_type:
            continue
        target = editions if _is_other_edition(match, book_asin, book_qualifiers) else held
        target.append(match)

    return {
        "libraries": sorted({m.library_name for m in held}),
        "items": [_item_payload(m) for m in held],
        "other_editions": [_item_payload(m) for m in editions],
        "other_formats": [_item_payload(m) for m in other],
    }


def _source_states(library_index: LibraryIndexDB) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    for provider in get_providers():
        if not provider.is_enabled():
            continue
        state = library_index.get_state(provider.source)
        states[provider.source] = {
            "enabled": True,
            "stale": is_index_stale(state.last_sync_at, interval_hours=provider.interval_hours()),
            "last_sync_at": state.last_sync_at,
            "item_count": state.item_count,
        }
    return states


def _oldest_sync(states: dict[str, dict[str, Any]]) -> str | None:
    """Return the oldest sync time, or None if any enabled source never synced.

    Answers "everything is current as of at least this time", which is the only
    reading of a single timestamp that is not misleading across two sources.
    """
    stamps = [state["last_sync_at"] for state in states.values()]
    if not stamps or any(stamp is None for stamp in stamps):
        return None
    return min(stamps)


def lookup_books(books: list[Any], *, index: LibraryIndexDB | None = None) -> dict[str, Any]:
    """Look up which of `books` are already held, in their own format or another.

    Books with no usable key are skipped rather than matched loosely: matching on
    a title alone would mark all four *Housemaid* titles as owned the moment any
    one of them was.
    """
    library_index = index if index is not None else get_library_index()
    states = _source_states(library_index)

    result: dict[str, Any] = {
        "enabled": bool(states),
        "stale": any(state["stale"] for state in states.values()),
        "last_sync_at": _oldest_sync(states),
        "sources": states,
        "matches": {},
    }

    if not states or not isinstance(books, list):
        return result

    matches: dict[str, Any] = {}
    for book in books[:MAX_LOOKUP_BOOKS]:
        if not isinstance(book, dict):
            continue

        book_id = str(book.get("id") or "").strip()
        if not book_id or book_id in matches:
            continue

        keys = build_match_keys(
            book.get("title"),
            book.get("author"),
            asin=book.get("asin"),
            isbn=book.get("isbn_13") or book.get("isbn_10"),
        )
        if not keys:
            continue

        found = library_index.find_matches(keys, states.keys())
        if found:
            matches[book_id] = _match_payload(
                found,
                media_type_for_content_type(book.get("content_type")),
                normalize_asin(book.get("asin")),
                edition_qualifiers(book.get("title")),
            )

    result["matches"] = matches
    return result
