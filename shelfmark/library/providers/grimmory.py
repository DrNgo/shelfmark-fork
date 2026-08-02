"""Grimmory as a source for the shared library index.

Grimmory is the ebook library, but it can hold audiobooks too, so each item's
real format is recorded rather than assumed from the source. A stray audiobook
import must not be able to badge an ebook search result.
"""

from __future__ import annotations

from typing import Any

from shelfmark.core.logger import setup_logger
from shelfmark.grimmory.client import BookloreConfig, booklore_login, list_books
from shelfmark.library.index import (
    MEDIA_TYPE_AUDIOBOOK,
    MEDIA_TYPE_EBOOK,
    SOURCE_GRIMMORY,
    LibraryItem,
)
from shelfmark.library.matching import normalize_isbn

logger = setup_logger(__name__)

LIBRARY_INDEX_ENABLED_KEY = "BOOKLORE_LIBRARY_INDEX_ENABLED"
LIBRARY_INDEX_INTERVAL_KEY = "BOOKLORE_INDEX_INTERVAL_HOURS"

_DEFAULT_INTERVAL_HOURS = 1.0
_MIN_INTERVAL_HOURS = 1.0
_PAGE_SIZE = 500
_MAX_PAGES = 1000

_AUDIOBOOK_FILE_TYPE = "AUDIOBOOK"


def _file_types(raw: dict[str, Any]) -> list[str]:
    types: list[str] = []

    primary = raw.get("primaryFile")
    if isinstance(primary, dict):
        types.append(str(primary.get("bookType") or "").strip().upper())

    alternatives = raw.get("alternativeFormats")
    if isinstance(alternatives, list):
        types.extend(
            str(alt.get("bookType") or "").strip().upper()
            for alt in alternatives
            if isinstance(alt, dict)
        )

    return [file_type for file_type in types if file_type]


def _media_type(raw: dict[str, Any]) -> str:
    """Classify a book by the formats it actually holds.

    Only an entry whose every known file is an audiobook counts as one. A book
    holding an EPUB and an M4B is still an ebook you own, and defaulting the
    unknown case to ebook matches what Grimmory is for.
    """
    types = _file_types(raw)
    if types and all(file_type == _AUDIOBOOK_FILE_TYPE for file_type in types):
        return MEDIA_TYPE_AUDIOBOOK
    return MEDIA_TYPE_EBOOK


def _first_author(metadata: dict[str, Any]) -> str:
    authors = metadata.get("authors")
    if isinstance(authors, list):
        for author in authors:
            name = str(author or "").strip()
            if name:
                return name
    return ""


def extract_library_items(raw_books: list[Any]) -> list[LibraryItem]:
    """Flatten Grimmory's book payloads into index rows."""
    items: list[LibraryItem] = []

    for raw in raw_books:
        if not isinstance(raw, dict):
            continue

        metadata = raw.get("metadata")
        metadata = metadata if isinstance(metadata, dict) else {}

        book_id = raw.get("id")
        item_id = "" if book_id is None else str(book_id).strip()
        title = str(metadata.get("title") or raw.get("title") or "").strip()
        if not item_id or not title:
            continue

        library_id = raw.get("libraryId")
        items.append(
            LibraryItem(
                source=SOURCE_GRIMMORY,
                item_id=item_id,
                library_id="" if library_id is None else str(library_id),
                library_name=str(raw.get("libraryName") or "").strip(),
                media_type=_media_type(raw),
                title=title,
                subtitle=str(metadata.get("subtitle") or "").strip(),
                author=_first_author(metadata),
                asin=str(metadata.get("asin") or "").strip(),
                isbn13=normalize_isbn(metadata.get("isbn13"))
                or normalize_isbn(metadata.get("isbn10")),
            )
        )

    return items


class GrimmoryProvider:
    """Indexes every book the configured Grimmory account can see."""

    source = SOURCE_GRIMMORY

    def is_enabled(self) -> bool:
        """Report whether the Grimmory slice should be maintained.

        Credentials are part of the gate: a half-configured connection would
        otherwise fail on every scheduler cycle and log noise forever.
        """
        from shelfmark.core.config import config

        if not bool(config.get("BOOKLORE_ENABLED", False)):
            return False
        if not bool(config.get(LIBRARY_INDEX_ENABLED_KEY, True)):
            return False

        return all(
            str(config.get(key, "") or "").strip()
            for key in ("BOOKLORE_HOST", "BOOKLORE_USERNAME", "BOOKLORE_PASSWORD")
        )

    def interval_hours(self) -> float:
        """Return the configured refresh interval, floored at one hour."""
        from shelfmark.core.config import config

        raw = config.get(LIBRARY_INDEX_INTERVAL_KEY, _DEFAULT_INTERVAL_HOURS)
        try:
            interval = float(raw)  # pyright: ignore[reportArgumentType] - config values are untyped
        except TypeError, ValueError:
            return _DEFAULT_INTERVAL_HOURS

        return max(interval, _MIN_INTERVAL_HOURS)

    def fetch_items(self) -> list[LibraryItem]:
        """Page through every book the configured account can see."""
        from shelfmark.core.config import config

        booklore_config = BookloreConfig(
            base_url=str(config.get("BOOKLORE_HOST", "") or "").strip().rstrip("/"),
            username=str(config.get("BOOKLORE_USERNAME", "") or "").strip(),
            password=str(config.get("BOOKLORE_PASSWORD", "") or ""),
            library_id=0,
            path_id=0,
        )

        token = booklore_login(booklore_config)

        items: list[LibraryItem] = []
        page = 0
        total_pages = 1
        while page < total_pages and page < _MAX_PAGES:
            books, total_pages = list_books(booklore_config, token, page=page, size=_PAGE_SIZE)
            items.extend(extract_library_items(books))
            page += 1

        if page >= _MAX_PAGES:
            logger.warning("Stopped indexing Grimmory at the %d page cap", _MAX_PAGES)

        return items
