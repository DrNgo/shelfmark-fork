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

    def test_a_disabled_source_contributes_nothing_even_with_indexed_rows(self, index):
        """Disabling one source while the other stays on is the case
        `test_reports_disabled_without_touching_the_index` cannot catch,
        because it disables both sources at once. A user who switches
        Grimmory off must lose its badges and its acquire lock immediately,
        even though the rows are still sitting in the index (only a resync
        removes them) — Grimmory's rows must not surface in `items` *or*
        `other_formats`.
        """
        index.replace_items(
            SOURCE_GRIMMORY,
            [
                _stored(SOURCE_GRIMMORY, MEDIA_TYPE_EBOOK, item_id="grim_ebook"),
                _stored(SOURCE_GRIMMORY, MEDIA_TYPE_AUDIOBOOK, item_id="grim_audio"),
            ],
        )

        with patch_config({**ENABLED, "BOOKLORE_ENABLED": False}):
            result = lookup_books([_book("ebook")], index=index)

        match = result["matches"].get("b1")
        assert match is not None, "the still-enabled Audiobookshelf audiobook should surface"
        assert match["items"] == [], "the disabled Grimmory ebook must not badge or lock"
        other_sources = {item["source"] for item in match["other_formats"]}
        assert other_sources == {SOURCE_AUDIOBOOKSHELF}
        assert all(item["item_id"] != "grim_audio" for item in match["other_formats"])

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
        """A UK edition ASIN must not stop title+author from matching.

        The match survives, but it is no longer counted as a holding: a
        two-sided ASIN disagreement now files the item under `other_editions`
        (see `TestOtherEditions`). This is the known cost of that rule —
        regional ASINs differ for an identical recording, so a UK-tagged shelf
        item loses its solid badge and stops locking the button.

        The trade is deliberate and asymmetric. Demoting a book you own costs a
        redundant download; the alternative left a full-cast release reading as
        already-owned and blocked the request outright.
        """
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

        match = result["matches"]["bk1"]
        assert match["items"] == []
        assert match["other_editions"][0]["item_id"] == "li_1"

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


def _edition(item_id: str, title: str, asin: str = "", subtitle: str = "") -> LibraryItem:
    """One indexed audiobook edition of Dungeon Crawler Carl, Book 1."""
    return LibraryItem(
        source=SOURCE_AUDIOBOOKSHELF,
        item_id=item_id,
        library_id="lib_books",
        library_name="Audiobooks",
        media_type=MEDIA_TYPE_AUDIOBOOK,
        title=title,
        subtitle=subtitle,
        author="Matt Dinniman",
        asin=asin,
        isbn13="",
    )


def _dcc_search_result(title: str = "Dungeon Crawler Carl", asin: str = "") -> dict[str, Any]:
    return {
        "id": "bk1",
        "title": title,
        "author": "Matt Dinniman",
        "asin": asin,
        "content_type": "audiobook",
    }


class TestOtherEditions:
    """A same-format holding that is demonstrably a different recording.

    Title+author cannot tell a full-cast adaptation from the single-narrator
    original, so both key together and the newer release reads as owned. Two
    signals separate them once they have met: a two-sided ASIN disagreement,
    and an edition marker present on one title but not the other.

    A demoted match still surfaces — you own *something* here — but it never
    locks the acquire button, because you do not own *this*.
    """

    def test_a_differing_asin_on_both_sides_demotes_the_match(self, tmp_path):
        index = LibraryIndexDB(str(tmp_path / "i.db"))
        index.initialize()
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF, [_edition("li_1", "Dungeon Crawler Carl", "B08V8B2CGV")]
        )

        with patch_config(ENABLED):
            result = lookup_books([_dcc_search_result(asin="B0FZZZZZZZ")], index=index)

        match = result["matches"]["bk1"]
        assert match["items"] == []
        assert len(match["other_editions"]) == 1
        assert match["other_editions"][0]["asin"] == "B08V8B2CGV"

    def test_a_matching_asin_stays_held(self, tmp_path):
        index = LibraryIndexDB(str(tmp_path / "i.db"))
        index.initialize()
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF, [_edition("li_1", "Dungeon Crawler Carl", "B08V8B2CGV")]
        )

        with patch_config(ENABLED):
            result = lookup_books([_dcc_search_result(asin="B08V8B2CGV")], index=index)

        match = result["matches"]["bk1"]
        assert len(match["items"]) == 1
        assert match["other_editions"] == []

    def test_an_asin_only_on_the_search_side_does_not_demote(self, tmp_path):
        """A sideloaded item has no ASIN. Absence is not disagreement."""
        index = LibraryIndexDB(str(tmp_path / "i.db"))
        index.initialize()
        index.replace_items(SOURCE_AUDIOBOOKSHELF, [_edition("li_1", "Dungeon Crawler Carl")])

        with patch_config(ENABLED):
            result = lookup_books([_dcc_search_result(asin="B0FZZZZZZZ")], index=index)

        assert len(result["matches"]["bk1"]["items"]) == 1

    def test_an_edition_qualifier_demotes_an_asin_less_holding(self, tmp_path):
        """The case ASIN alone cannot reach, and the one that prompted this.

        The Audio Immersion Tunnel rip carries no ASIN, so an ASIN comparison
        sits out and the full-cast release reads as already owned.
        """
        index = LibraryIndexDB(str(tmp_path / "i.db"))
        index.initialize()
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF,
            [_edition("li_1", "Dungeon Crawler Carl (Audio Immersion Tunnel)")],
        )

        with patch_config(ENABLED):
            result = lookup_books([_dcc_search_result()], index=index)

        match = result["matches"]["bk1"]
        assert match["items"] == []
        assert len(match["other_editions"]) == 1

    def test_both_editions_demote_together(self, tmp_path):
        """The real shape of the reported bug: two owned editions, neither one
        the release being searched for. Either surviving in `items` would keep
        the acquire button locked, so both signals have to fire at once.
        """
        index = LibraryIndexDB(str(tmp_path / "i.db"))
        index.initialize()
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF,
            [
                _edition("li_1", "Dungeon Crawler Carl", "B08V8B2CGV"),
                _edition("li_2", "Dungeon Crawler Carl (Audio Immersion Tunnel)"),
            ],
        )

        with patch_config(ENABLED):
            result = lookup_books(
                [_dcc_search_result("Dungeon Crawler Carl (GraphicAudio)", "B0FZZZZZZZ")],
                index=index,
            )

        match = result["matches"]["bk1"]
        assert match["items"] == []
        assert match["libraries"] == []
        assert len(match["other_editions"]) == 2

    def test_a_multipart_dramatization_does_not_read_as_the_standard_edition(self, tmp_path):
        """Mistborn: three ASIN-less parts of a dramatized adaptation, all
        keying to the plain title. Owning the adaptation is not owning the
        recording being searched for.
        """
        index = LibraryIndexDB(str(tmp_path / "i.db"))
        index.initialize()
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF,
            [
                LibraryItem(
                    source=SOURCE_AUDIOBOOKSHELF,
                    item_id=f"li_{part}",
                    library_id="lib_books",
                    library_name="Audiobooks",
                    media_type=MEDIA_TYPE_AUDIOBOOK,
                    title=f"The Final Empire (Part {part} of 3) (Dramatized Adaptation)",
                    subtitle=f"Mistborn Era 1, Book 1 — Part {part} of 3",
                    author="Brandon Sanderson",
                    asin="",
                    isbn13="",
                )
                for part in (1, 2, 3)
            ],
        )

        with patch_config(ENABLED):
            result = lookup_books(
                [
                    {
                        "id": "bk1",
                        "title": "The Final Empire",
                        "author": "Brandon Sanderson",
                        "content_type": "audiobook",
                    }
                ],
                index=index,
            )

        match = result["matches"]["bk1"]
        assert match["items"] == []
        assert len(match["other_editions"]) == 3

    def test_unabridged_is_not_an_edition_difference(self, tmp_path):
        """The shelf noise case. Demoting here would strip a real holding's
        badge from every item whose library spells out "(Unabridged)".
        """
        index = LibraryIndexDB(str(tmp_path / "i.db"))
        index.initialize()
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF, [_edition("li_1", "Dungeon Crawler Carl (Unabridged)")]
        )

        with patch_config(ENABLED):
            result = lookup_books([_dcc_search_result()], index=index)

        assert len(result["matches"]["bk1"]["items"]) == 1

    def test_the_same_qualifier_on_both_sides_stays_held(self, tmp_path):
        index = LibraryIndexDB(str(tmp_path / "i.db"))
        index.initialize()
        index.replace_items(
            SOURCE_AUDIOBOOKSHELF,
            [_edition("li_1", "Dungeon Crawler Carl (Audio Immersion Tunnel)")],
        )

        with patch_config(ENABLED):
            result = lookup_books(
                [_dcc_search_result("Dungeon Crawler Carl (Audio Immersion Tunnel)")], index=index
            )

        assert len(result["matches"]["bk1"]["items"]) == 1

    def test_a_plain_holding_is_untouched(self, index):
        """The overwhelmingly common case must not regress: no qualifiers
        anywhere, no ASIN on the search side, still owned.
        """
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

        match = result["matches"]["bk1"]
        assert len(match["items"]) == 1
        assert match["other_editions"] == []
        assert match["libraries"] == ["Audiobooks"]

    def test_a_cross_format_holding_is_not_demoted(self, index, enabled_providers):
        """`other_formats` already declines to lock the button, so splitting it
        further would only add a bucket nothing reads.
        """
        index.replace_items(
            SOURCE_GRIMMORY,
            [
                LibraryItem(
                    source=SOURCE_GRIMMORY,
                    item_id="g1",
                    library_id="1",
                    library_name="Ebooks",
                    media_type=MEDIA_TYPE_EBOOK,
                    title="Dungeon Crawler Carl (Illustrated Edition)",
                    subtitle="",
                    author="Matt Dinniman",
                    asin="",
                    isbn13="",
                )
            ],
        )

        result = lookup_books([_dcc_search_result(asin="B0FZZZZZZZ")], index=index)

        match = result["matches"]["bk1"]
        assert len(match["other_formats"]) == 1
        assert match["other_editions"] == []


class TestSourceReporting:
    def test_reports_each_source_separately(self, both_formats):
        result = lookup_books([_book("ebook")], index=both_formats)

        assert set(result["sources"]) == {SOURCE_GRIMMORY, SOURCE_AUDIOBOOKSHELF}
        assert result["sources"][SOURCE_GRIMMORY]["item_count"] == 1

    def test_enabled_is_true_when_any_source_is_on(self, both_formats):
        assert lookup_books([_book("ebook")], index=both_formats)["enabled"] is True
