"""Tests for the batch lookup behind the "in library" badge."""

from typing import Any
from unittest.mock import patch

import pytest

from shelfmark.audiobookshelf.library_index import LibraryIndexDB, LibraryItem
from shelfmark.audiobookshelf.library_lookup import MAX_LOOKUP_BOOKS, lookup_books


def config_getter(values: dict[str, Any]):
    """Build a config.get replacement backed by a plain dict."""

    def getter(key: str, default: Any = "", *, user_id: int | None = None) -> Any:
        del user_id
        return values.get(key, default)

    return getter


def patch_config(values: dict[str, Any]):
    """Patch the shared config singleton's get() for the duration of a with-block."""
    from shelfmark.core.config import config

    return patch.object(config, "get", config_getter(values))


ENABLED = {"AUDIOBOOKSHELF_ENABLED": True, "AUDIOBOOKSHELF_LIBRARY_INDEX_ENABLED": True}


@pytest.fixture
def index(tmp_path):
    db = LibraryIndexDB(str(tmp_path / "abs_index.db"))
    db.initialize()
    db.replace_items(
        [
            LibraryItem(
                item_id="li_1",
                library_id="lib_books",
                library_name="Audiobooks",
                title="The Housemaid",
                subtitle="A Novel",
                author="Freida McFadden",
                asin="B0BSHZ1234",
            )
        ]
    )
    return db


class TestLookupBooks:
    """One request per rendered page of results, not one per card."""

    def test_flags_a_book_that_is_already_in_the_library(self, index):
        with patch_config(ENABLED):
            result = lookup_books(
                [{"id": "bk1", "title": "The Housemaid: A Novel", "author": "Freida McFadden"}],
                index=index,
            )

        assert result["enabled"] is True
        match = result["matches"]["bk1"]
        assert match["libraries"] == ["Audiobooks"]
        assert match["items"][0]["asin"] == "B0BSHZ1234"
        assert match["items"][0]["title"] == "The Housemaid"

    def test_omits_books_that_are_not_in_the_library(self, index):
        with patch_config(ENABLED):
            result = lookup_books(
                [{"id": "bk1", "title": "The Coworker", "author": "Freida McFadden"}],
                index=index,
            )

        assert result["matches"] == {}

    def test_reports_disabled_without_touching_the_index(self, index):
        """The badge must vanish cleanly when Audiobookshelf is switched off."""
        with (
            patch_config({"AUDIOBOOKSHELF_ENABLED": False}),
            patch.object(index, "find_matches", side_effect=AssertionError("queried")),
        ):
            result = lookup_books(
                [{"id": "bk1", "title": "The Housemaid", "author": "Freida McFadden"}],
                index=index,
            )

        assert result["enabled"] is False
        assert result["matches"] == {}

    def test_skips_books_that_cannot_be_matched(self, index):
        """A missing author yields no key; matching on title alone is forbidden."""
        with patch_config(ENABLED):
            result = lookup_books(
                [
                    {"id": "bk1", "title": "The Housemaid", "author": ""},
                    {"id": "bk2", "title": "", "author": "Freida McFadden"},
                    {"title": "The Housemaid", "author": "Freida McFadden"},
                ],
                index=index,
            )

        assert result["matches"] == {}

    def test_lists_every_library_holding_the_book(self, index):
        index.replace_items(
            [
                LibraryItem(
                    item_id="li_1",
                    library_id="lib_a",
                    library_name="Audiobooks",
                    title="The Housemaid",
                    subtitle="",
                    author="Freida McFadden",
                    asin="B0BSHZ1234",
                ),
                LibraryItem(
                    item_id="li_2",
                    library_id="lib_b",
                    library_name="Kids",
                    title="The Housemaid",
                    subtitle="",
                    author="Freida McFadden",
                    asin="B0BSHZ9999",
                ),
            ]
        )

        with patch_config(ENABLED):
            result = lookup_books(
                [{"id": "bk1", "title": "The Housemaid", "author": "Freida McFadden"}],
                index=index,
            )

        assert result["matches"]["bk1"]["libraries"] == ["Audiobooks", "Kids"]

    def test_caps_the_number_of_books_per_request(self, index):
        books = [
            {"id": f"bk{n}", "title": "The Housemaid", "author": "Freida McFadden"}
            for n in range(MAX_LOOKUP_BOOKS + 25)
        ]

        with patch_config(ENABLED):
            result = lookup_books(books, index=index)

        assert len(result["matches"]) == MAX_LOOKUP_BOOKS

    def test_tolerates_junk_input(self, index):
        with patch_config(ENABLED):
            result = lookup_books(["nonsense", None, 7], index=index)

        assert result["matches"] == {}

    def test_reports_index_freshness(self, index):
        with patch_config(ENABLED):
            result = lookup_books([], index=index)

        assert result["last_sync_at"] is not None
        assert result["stale"] is False

    def test_reports_a_never_synced_index_as_stale(self, tmp_path):
        empty = LibraryIndexDB(str(tmp_path / "empty.db"))
        empty.initialize()

        with patch_config(ENABLED):
            result = lookup_books([], index=empty)

        assert result["stale"] is True
        assert result["last_sync_at"] is None
