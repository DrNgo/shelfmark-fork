"""Grimmory library provider (fork-only).

Lists the configured Grimmory account's books for upstream's library check, the
same way the Calibre and Audiobookshelf providers read their libraries. The listing
carries title, authors and ISBNs. The Hardcover book id, which is what matches a
volume titled "Overlord, Vol. 5" to Hardcover's "The Men of the Kingdom Part I",
is only on each book's own record, so that is read once per book and remembered.

Only ebooks are offered. Grimmory can hold a stray audiobook import, and that must
not mark an ebook search result as owned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shelfmark.core.config import config as app_config
from shelfmark.core.library_providers import LibraryEntry, setting
from shelfmark.core.text_match import isbn_variants, tokens

if TYPE_CHECKING:
    from collections.abc import Mapping

# The Grimmory tab's "flag ebooks already in your library" switch.
ENABLED_KEY = "BOOKLORE_LIBRARY_INDEX_ENABLED"

_PAGE_SIZE = 500
_MAX_PAGES = 1000
_AUDIOBOOK_FILE_TYPE = "AUDIOBOOK"


def _is_audiobook(raw: dict[str, Any]) -> bool:
    """An entry counts as an audiobook only when every file it holds is one."""
    files = [raw.get("primaryFile"), *(raw.get("alternativeFormats") or [])]
    types = [
        str(f.get("bookType") or "").strip().upper()
        for f in files
        if isinstance(f, dict) and f.get("bookType")
    ]
    return bool(types) and all(t == _AUDIOBOOK_FILE_TYPE for t in types)


# Grimmory book id -> Hardcover book id ("" when it has none), kept across re-indexes
# so only books added since the last one cost a request.
_hardcover_ids: dict[object, str] = {}


def book_to_entry(raw: dict[str, Any], hardcover_id: str = "") -> LibraryEntry | None:
    """One listed Grimmory book as a matchable entry, or None for an audiobook or no title."""
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    title = str(metadata.get("title") or raw.get("title") or "").strip()
    if not title or _is_audiobook(raw):
        return None
    authors = [str(a).strip() for a in metadata.get("authors") or [] if str(a or "").strip()]
    title_tok = set(tokens(title))
    context_tok = set(tokens(" ".join(authors)))
    return LibraryEntry(
        frozenset(title_tok | context_tok | set(tokens(str(metadata.get("subtitle") or "")))),
        isbn_variants(metadata.get("isbn13")) | isbn_variants(metadata.get("isbn10")),
        frozenset({str(metadata["asin"]).strip().upper()} if metadata.get("asin") else set()),
        frozenset({("hardcover", hardcover_id)} if hardcover_id else set()),
        frozenset(title_tok),
        frozenset(context_tok),
    )


class GrimmoryLibrary:
    """Ebook ownership via the Grimmory API."""

    name = "grimmory"
    display_name = "Grimmory"
    content_types = frozenset({"ebook"})

    def __init__(self, overrides: Mapping[str, Any] | None = None) -> None:
        self._overrides = overrides

    def _setting(self, key: str, default: object = "") -> str:
        return str(setting(app_config, key, default, self._overrides) or "").strip()

    def is_enabled(self) -> bool:
        return (
            bool(setting(app_config, "BOOKLORE_ENABLED", False, self._overrides))
            and bool(setting(app_config, ENABLED_KEY, True, self._overrides))
            and all(
                self._setting(k)
                for k in ("BOOKLORE_HOST", "BOOKLORE_USERNAME", "BOOKLORE_PASSWORD")
            )
        )

    def describe(self) -> str:
        host = self._setting("BOOKLORE_HOST")
        return f"Grimmory at {host}" if host else "Grimmory"

    def fingerprint(self) -> None:
        return None

    def fetch_entries(self) -> list[LibraryEntry]:
        from shelfmark.grimmory.client import BookloreConfig, booklore_login, get_book, list_books

        booklore_config = BookloreConfig(
            base_url=self._setting("BOOKLORE_HOST").rstrip("/"),
            username=self._setting("BOOKLORE_USERNAME"),
            password=str(setting(app_config, "BOOKLORE_PASSWORD", "", self._overrides) or ""),
            library_id=0,
            path_id=0,
        )
        token = booklore_login(booklore_config)
        entries: list[LibraryEntry] = []
        page, total_pages = 0, 1
        while page < total_pages:
            if page >= _MAX_PAGES:
                msg = f"Grimmory listing exceeded the {_MAX_PAGES}-page cap"
                raise RuntimeError(msg)
            books, total_pages = list_books(booklore_config, token, page=page, size=_PAGE_SIZE)
            for raw in books:
                if not isinstance(raw, dict):
                    continue
                book_id = raw.get("id")
                if book_id is not None and book_id not in _hardcover_ids:
                    detail = get_book(booklore_config, token, book_id)
                    _hardcover_ids[book_id] = str(detail["metadata"].get("hardcoverBookId") or "")
                if entry := book_to_entry(raw, _hardcover_ids.get(book_id, "")):
                    entries.append(entry)
            page += 1
        return entries
