"""Per-source providers feeding the shared library index.

A provider answers three questions: whether it is switched on, how often it
should refresh, and what its library currently holds. Everything else —
storage, staleness, scheduling, failure handling — belongs to the shared core,
so adding a library source means writing one small module rather than a second
copy of the index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from shelfmark.library.index import LibraryItem


class LibraryProvider(Protocol):
    """One library Shelfmark can ask what it already holds."""

    source: str

    def is_enabled(self) -> bool:
        """Whether this source should be indexed and consulted."""
        ...

    def interval_hours(self) -> float:
        """How often this source's slice of the index should be rebuilt."""
        ...

    def fetch_items(self) -> list[LibraryItem]:
        """Return everything this library holds. Raises on transport failure."""
        ...


def get_providers() -> list[LibraryProvider]:
    """Return every registered provider.

    Imported lazily: the providers pull in settings modules, which import config,
    which imports this package.
    """
    from shelfmark.library.providers.audiobookshelf import AudiobookshelfProvider
    from shelfmark.library.providers.grimmory import GrimmoryProvider

    return [AudiobookshelfProvider(), GrimmoryProvider()]
