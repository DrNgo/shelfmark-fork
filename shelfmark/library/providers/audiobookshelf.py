"""Audiobookshelf as a source for the shared library index.

Every book-type library is indexed, not just the ones mapped as download
destinations: a book sitting in an unmapped library is still a book you own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shelfmark.library.index import MEDIA_TYPE_AUDIOBOOK, SOURCE_AUDIOBOOKSHELF, LibraryItem

if TYPE_CHECKING:
    from shelfmark.audiobookshelf.client import AudiobookshelfLibrary

LIBRARY_INDEX_ENABLED_KEY = "AUDIOBOOKSHELF_LIBRARY_INDEX_ENABLED"
LIBRARY_INDEX_INTERVAL_KEY = "AUDIOBOOKSHELF_INDEX_INTERVAL_HOURS"

_DEFAULT_INTERVAL_HOURS = 1.0
_MIN_INTERVAL_HOURS = 1.0


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
                # Audiobookshelf exposes no ISBN. Leaving this empty keeps
                # audiobook rows from keying against ebook holdings.
                isbn13="",
            )
        )

    return items


class AudiobookshelfProvider:
    """Indexes every book-type Audiobookshelf library."""

    source = SOURCE_AUDIOBOOKSHELF

    def is_enabled(self) -> bool:
        """Report whether the Audiobookshelf slice should be maintained."""
        from shelfmark.core.config import config

        return bool(config.get("AUDIOBOOKSHELF_ENABLED", False)) and bool(
            config.get(LIBRARY_INDEX_ENABLED_KEY, True)
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
        """Walk every book-type library and flatten it into index rows."""
        from shelfmark.audiobookshelf.settings import build_client_from_config

        client = build_client_from_config()
        if client is None:
            msg = "Audiobookshelf is not configured"
            raise RuntimeError(msg)

        items: list[LibraryItem] = []
        for library in client.get_book_libraries():
            items.extend(extract_library_items(client.get_library_items(library.id), library))
        return items
