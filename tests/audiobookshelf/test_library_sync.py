"""Tests for refreshing the library index from Audiobookshelf."""

from datetime import UTC, datetime, timedelta

import pytest
import requests

from shelfmark.audiobookshelf.client import AudiobookshelfLibrary
from shelfmark.audiobookshelf.library_sync import (
    extract_library_items,
    is_index_stale,
    sync_library_index,
)
from shelfmark.library.index import SOURCE_AUDIOBOOKSHELF, LibraryIndexDB
from shelfmark.library.matching import build_match_keys

BOOKS_LIBRARY = AudiobookshelfLibrary(id="lib_books", name="Audiobooks", media_type="book")
KIDS_LIBRARY = AudiobookshelfLibrary(id="lib_kids", name="Kids", media_type="book")


def _raw_item(item_id: str = "li_1", **metadata: object) -> dict[str, object]:
    meta: dict[str, object] = {
        "title": "The Housemaid",
        "subtitle": "A Novel",
        "authorName": "Freida McFadden",
        "asin": "B0BSHZ1234",
    }
    meta.update(metadata)
    return {"id": item_id, "media": {"metadata": meta}}


class FakeClient:
    """Stands in for AudiobookshelfClient, including how it fails."""

    def __init__(self, items_by_library=None, libraries=None, error=None):
        self._items = items_by_library or {}
        self._libraries = libraries if libraries is not None else [BOOKS_LIBRARY]
        self._error = error
        self.requested_libraries: list[str] = []

    def get_book_libraries(self):
        if self._error:
            raise self._error
        return self._libraries

    def get_library_items(self, library_id, **_kwargs):
        self.requested_libraries.append(library_id)
        if self._error:
            raise self._error
        return self._items.get(library_id, [])


@pytest.fixture
def index(tmp_path):
    db = LibraryIndexDB(str(tmp_path / "abs_index.db"))
    db.initialize()
    return db


class TestExtractLibraryItems:
    """Audiobookshelf's payload is nested and inconsistently populated."""

    def test_flattens_an_expanded_item(self):
        items = extract_library_items([_raw_item()], BOOKS_LIBRARY)

        assert len(items) == 1
        item = items[0]
        assert item.item_id == "li_1"
        assert item.library_id == "lib_books"
        assert item.library_name == "Audiobooks"
        assert item.title == "The Housemaid"
        assert item.subtitle == "A Novel"
        assert item.author == "Freida McFadden"
        assert item.asin == "B0BSHZ1234"

    def test_prefers_the_structured_author_over_the_joined_string(self):
        """`authorName` joins co-authors with commas; `authors[0]` never does."""
        raw = _raw_item(
            authorName="Freida McFadden, Someone Else",
            authors=[{"id": "au_1", "name": "Freida McFadden"}],
        )

        assert extract_library_items([raw], BOOKS_LIBRARY)[0].author == "Freida McFadden"

    def test_falls_back_to_the_joined_author_string(self):
        raw = _raw_item(authors=[])

        assert extract_library_items([raw], BOOKS_LIBRARY)[0].author == "Freida McFadden"

    def test_tolerates_missing_metadata(self):
        assert extract_library_items([{"id": "li_1"}], BOOKS_LIBRARY) == []

    def test_skips_an_item_without_an_id(self):
        assert extract_library_items([_raw_item(item_id="")], BOOKS_LIBRARY) == []

    def test_defaults_a_missing_asin_to_empty(self):
        raw = {
            "id": "li_1",
            "media": {"metadata": {"title": "The Housemaid", "authorName": "Freida McFadden"}},
        }

        assert extract_library_items([raw], BOOKS_LIBRARY)[0].asin == ""

    def test_ignores_a_non_dict_row(self):
        assert extract_library_items(["nonsense", None], BOOKS_LIBRARY) == []


class TestSyncLibraryIndex:
    """A sync walks every book library and swaps the index in one go."""

    def test_indexes_items_from_every_book_library(self, index):
        client = FakeClient(
            libraries=[BOOKS_LIBRARY, KIDS_LIBRARY],
            items_by_library={
                "lib_books": [_raw_item("li_1")],
                "lib_kids": [_raw_item("li_2", title="The Coworker", subtitle="")],
            },
        )

        result = sync_library_index(client=client, index=index)

        assert result.success
        assert result.item_count == 2
        assert client.requested_libraries == ["lib_books", "lib_kids"]
        assert len(index.find_matches(build_match_keys("The Coworker", "Freida McFadden"))) == 1

    def test_records_the_failure_and_keeps_the_old_index(self, index):
        """An Audiobookshelf outage must leave yesterday's badges standing."""
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF, extract_library_items([_raw_item()], BOOKS_LIBRARY)
        )
        client = FakeClient(error=requests.exceptions.ConnectionError("refused"))

        result = sync_library_index(client=client, index=index)

        assert not result.success
        assert index.get_state(SOURCE_AUDIOBOOKSHELF).last_error
        assert len(index.find_matches(build_match_keys("The Housemaid", "Freida McFadden"))) == 1

    def test_does_nothing_without_a_client(self, index):
        """Audiobookshelf disabled is not a failure, and must not wipe the index."""
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF, extract_library_items([_raw_item()], BOOKS_LIBRARY)
        )

        result = sync_library_index(client=None, index=index)

        assert not result.success
        assert index.get_state(SOURCE_AUDIOBOOKSHELF).last_error is None
        assert len(index.find_matches(build_match_keys("The Housemaid", "Freida McFadden"))) == 1

    def test_an_empty_library_empties_the_index(self, index):
        """Genuinely-zero results are a real answer; the client raises otherwise."""
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF, extract_library_items([_raw_item()], BOOKS_LIBRARY)
        )
        client = FakeClient(items_by_library={"lib_books": []})

        result = sync_library_index(client=client, index=index)

        assert result.success
        assert result.item_count == 0
        assert index.find_matches(build_match_keys("The Housemaid", "Freida McFadden")) == []


class TestIsIndexStale:
    """Staleness drives the periodic refresh."""

    def test_a_never_synced_index_is_stale(self):
        assert is_index_stale(None, interval_hours=1)

    def test_a_fresh_index_is_not_stale(self):
        recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

        assert not is_index_stale(recent, interval_hours=1)

    def test_an_old_index_is_stale(self):
        old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()

        assert is_index_stale(old, interval_hours=1)

    def test_an_unparseable_timestamp_is_stale(self):
        """Better to re-sync than to trust a timestamp we cannot read."""
        assert is_index_stale("not a date", interval_hours=1)
