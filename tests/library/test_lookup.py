"""Tests for the batch lookup behind the "in library" badge."""

from typing import Any
from unittest.mock import patch

import pytest

from shelfmark.library.index import (
    MEDIA_TYPE_AUDIOBOOK,
    MEDIA_TYPE_EBOOK,
    SOURCE_AUDIOBOOKSHELF,
    SOURCE_GRIMMORY,
    LibraryIndexDB,
    LibraryItem,
)
from shelfmark.library.lookup import MAX_LOOKUP_BOOKS, lookup_books


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
        SOURCE_AUDIOBOOKSHELF,
        [
            LibraryItem(
                source=SOURCE_AUDIOBOOKSHELF,
                item_id="li_1",
                library_id="lib_books",
                library_name="Audiobooks",
                media_type=MEDIA_TYPE_AUDIOBOOK,
                title="The Housemaid",
                subtitle="A Novel",
                author="Freida McFadden",
                asin="B0BSHZ1234",
                isbn13="",
            )
        ],
    )
    return db


class TestLookupBooks:
    """One request per rendered page of results, not one per card."""

    def test_flags_a_book_that_is_already_in_the_library(self, index):
        with patch_config(ENABLED):
            result = lookup_books(
                [
                    {
                        "id": "bk1",
                        "title": "The Housemaid: A Novel",
                        "author": "Freida McFadden",
                        "content_type": "audiobook",
                    }
                ],
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
                [
                    {
                        "id": "bk1",
                        "title": "The Coworker",
                        "author": "Freida McFadden",
                        "content_type": "audiobook",
                    }
                ],
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
                [
                    {
                        "id": "bk1",
                        "title": "The Housemaid",
                        "author": "Freida McFadden",
                        "content_type": "audiobook",
                    }
                ],
                index=index,
            )

        assert result["enabled"] is False
        assert result["matches"] == {}

    def test_skips_books_that_cannot_be_matched(self, index):
        """A missing author yields no key; matching on title alone is forbidden."""
        with patch_config(ENABLED):
            result = lookup_books(
                [
                    {
                        "id": "bk1",
                        "title": "The Housemaid",
                        "author": "",
                        "content_type": "audiobook",
                    },
                    {
                        "id": "bk2",
                        "title": "",
                        "author": "Freida McFadden",
                        "content_type": "audiobook",
                    },
                    {
                        "title": "The Housemaid",
                        "author": "Freida McFadden",
                        "content_type": "audiobook",
                    },
                ],
                index=index,
            )

        assert result["matches"] == {}

    def test_lists_every_library_holding_the_book(self, index):
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF,
            [
                LibraryItem(
                    source=SOURCE_AUDIOBOOKSHELF,
                    item_id="li_1",
                    library_id="lib_a",
                    library_name="Audiobooks",
                    media_type=MEDIA_TYPE_AUDIOBOOK,
                    title="The Housemaid",
                    subtitle="",
                    author="Freida McFadden",
                    asin="B0BSHZ1234",
                    isbn13="",
                ),
                LibraryItem(
                    source=SOURCE_AUDIOBOOKSHELF,
                    item_id="li_2",
                    library_id="lib_b",
                    library_name="Kids",
                    media_type=MEDIA_TYPE_AUDIOBOOK,
                    title="The Housemaid",
                    subtitle="",
                    author="Freida McFadden",
                    asin="B0BSHZ9999",
                    isbn13="",
                ),
            ],
        )

        with patch_config(ENABLED):
            result = lookup_books(
                [
                    {
                        "id": "bk1",
                        "title": "The Housemaid",
                        "author": "Freida McFadden",
                        "content_type": "audiobook",
                    }
                ],
                index=index,
            )

        assert result["matches"]["bk1"]["libraries"] == ["Audiobooks", "Kids"]

    def test_caps_the_number_of_books_per_request(self, index):
        books = [
            {
                "id": f"bk{n}",
                "title": "The Housemaid",
                "author": "Freida McFadden",
                "content_type": "audiobook",
            }
            for n in range(MAX_LOOKUP_BOOKS + 25)
        ]

        with patch_config(ENABLED):
            result = lookup_books(books, index=index)

        assert len(result["matches"]) == MAX_LOOKUP_BOOKS

    def test_tolerates_junk_input(self, index):
        with patch_config(ENABLED):
            result = lookup_books(["nonsense", None, 7], index=index)

        assert result["matches"] == {}

    def test_matches_on_asin_when_the_title_differs(self, index):
        """The payoff of an Audible-sourced ASIN: edition noise stops mattering."""
        with patch_config(ENABLED):
            result = lookup_books(
                [
                    {
                        "id": "bk1",
                        "title": "Housemaid, The (Unabridged)",
                        "author": "F. McFadden",
                        "asin": "b0bshz1234",
                        "content_type": "audiobook",
                    }
                ],
                index=index,
            )

        assert result["matches"]["bk1"]["items"][0]["item_id"] == "li_1"

    def test_a_book_with_only_an_asin_is_still_matchable(self, index):
        with patch_config(ENABLED):
            result = lookup_books(
                [
                    {
                        "id": "bk1",
                        "title": "",
                        "author": "",
                        "asin": "B0BSHZ1234",
                        "content_type": "audiobook",
                    }
                ],
                index=index,
            )

        assert result["matches"]["bk1"]["libraries"] == ["Audiobooks"]

    def test_a_different_asin_does_not_suppress_a_title_match(self, index):
        """A UK edition ASIN must not stop title+author from matching."""
        with patch_config(ENABLED):
            result = lookup_books(
                [
                    {
                        "id": "bk1",
                        "title": "The Housemaid: A Novel",
                        "author": "Freida McFadden",
                        "asin": "B0UKUKUK99",
                        "content_type": "audiobook",
                    }
                ],
                index=index,
            )

        assert result["matches"]["bk1"]["items"][0]["item_id"] == "li_1"

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


def _stored(source, media_type, item_id="1", **overrides):
    fields = {
        "source": source,
        "item_id": item_id,
        "library_id": "lib_1",
        "library_name": "Library",
        "media_type": media_type,
        "title": "The Housemaid",
        "subtitle": "",
        "author": "Freida McFadden",
        "asin": "",
        "isbn13": "",
    }
    fields.update(overrides)
    return LibraryItem(**fields)


@pytest.fixture
def both_formats(index, enabled_providers):
    index.replace_items(SOURCE_GRIMMORY, [_stored(SOURCE_GRIMMORY, MEDIA_TYPE_EBOOK)])
    index.replace_items(
        SOURCE_AUDIOBOOKSHELF, [_stored(SOURCE_AUDIOBOOKSHELF, MEDIA_TYPE_AUDIOBOOK, "abs_1")]
    )
    return index


def _book(content_type):
    return {
        "id": "b1",
        "title": "The Housemaid",
        "author": "Freida McFadden",
        "content_type": content_type,
    }


class TestFormatScoping:
    def test_an_ebook_matches_the_grimmory_holding(self, both_formats):
        result = lookup_books([_book("ebook")], index=both_formats)

        match = result["matches"]["b1"]
        assert [i["source"] for i in match["items"]] == [SOURCE_GRIMMORY]

    def test_an_ebook_reports_the_audiobook_as_another_format(self, both_formats):
        result = lookup_books([_book("ebook")], index=both_formats)

        match = result["matches"]["b1"]
        assert [i["source"] for i in match["other_formats"]] == [SOURCE_AUDIOBOOKSHELF]

    def test_an_audiobook_matches_the_other_way_round(self, both_formats):
        result = lookup_books([_book("audiobook")], index=both_formats)

        match = result["matches"]["b1"]
        assert [i["source"] for i in match["items"]] == [SOURCE_AUDIOBOOKSHELF]
        assert [i["source"] for i in match["other_formats"]] == [SOURCE_GRIMMORY]

    def test_an_audio_only_holding_leaves_an_ebook_unowned(self, index, enabled_providers):
        # The whole point of the format split: owning the audiobook must not
        # lock the ebook, so `items` has to come back empty.
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF, [_stored(SOURCE_AUDIOBOOKSHELF, MEDIA_TYPE_AUDIOBOOK)]
        )

        match = lookup_books([_book("ebook")], index=index)["matches"]["b1"]

        assert match["items"] == []
        assert len(match["other_formats"]) == 1

    def test_a_book_held_in_neither_format_is_absent(self, both_formats):
        book = {"id": "b2", "title": "Something Else", "author": "Nobody", "content_type": "ebook"}

        assert lookup_books([book], index=both_formats)["matches"] == {}

    def test_matches_by_isbn(self, index, enabled_providers):
        index.replace_items(
            SOURCE_GRIMMORY,
            [_stored(SOURCE_GRIMMORY, MEDIA_TYPE_EBOOK, isbn13="9780593135204")],
        )

        book = {"id": "b1", "isbn_10": "0593135202", "content_type": "ebook"}
        result = lookup_books([book], index=index)

        assert len(result["matches"]["b1"]["items"]) == 1


class TestSourceReporting:
    def test_reports_each_source_separately(self, both_formats):
        result = lookup_books([_book("ebook")], index=both_formats)

        assert set(result["sources"]) == {SOURCE_GRIMMORY, SOURCE_AUDIOBOOKSHELF}
        assert result["sources"][SOURCE_GRIMMORY]["item_count"] == 1

    def test_enabled_is_true_when_any_source_is_on(self, both_formats):
        assert lookup_books([_book("ebook")], index=both_formats)["enabled"] is True
