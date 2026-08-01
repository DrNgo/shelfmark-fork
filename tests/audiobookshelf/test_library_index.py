"""Tests for the local cache of what Audiobookshelf already holds."""

import pytest

from shelfmark.audiobookshelf.library_index import LibraryIndexDB, LibraryItem
from shelfmark.audiobookshelf.matching import build_match_keys


def _item(
    item_id: str = "li_1",
    title: str = "The Housemaid",
    author: str = "Freida McFadden",
    **overrides: object,
) -> LibraryItem:
    fields: dict[str, object] = {
        "item_id": item_id,
        "library_id": "lib_books",
        "library_name": "Audiobooks",
        "title": title,
        "subtitle": "",
        "author": author,
        "asin": "B0BSHZ1234",
    }
    fields.update(overrides)
    return LibraryItem(**fields)  # pyright: ignore[reportArgumentType]


@pytest.fixture
def index(tmp_path):
    db = LibraryIndexDB(str(tmp_path / "abs_index.db"))
    db.initialize()
    return db


class TestLibraryIndexLookup:
    """Looking a book up is the only thing the badge does on the request path."""

    def test_finds_an_indexed_item_by_its_match_keys(self, index):
        index.replace_items([_item()])

        matches = index.find_matches(build_match_keys("The Housemaid", "Freida McFadden"))

        assert len(matches) == 1
        assert matches[0].title == "The Housemaid"
        assert matches[0].library_name == "Audiobooks"
        assert matches[0].asin == "B0BSHZ1234"

    def test_matches_across_differing_spellings(self, index):
        """The library says "Last, First" with a split subtitle; search does not."""
        index.replace_items(
            [_item(title="The Housemaid", author="McFadden, Freida", subtitle="A Novel")]
        )

        matches = index.find_matches(build_match_keys("the housemaid: a novel", "Freida McFadden"))

        assert len(matches) == 1

    def test_does_not_match_a_different_book_by_the_same_author(self, index):
        index.replace_items([_item(title="The Housemaid")])

        matches = index.find_matches(build_match_keys("The Housemaid's Secret", "Freida McFadden"))

        assert matches == []

    def test_returns_every_library_holding_the_book(self, index):
        """A book in two libraries is two facts; the badge names which one."""
        index.replace_items(
            [
                _item(item_id="li_1", library_id="lib_a", library_name="Audiobooks"),
                _item(item_id="li_2", library_id="lib_b", library_name="Kids"),
            ]
        )

        matches = index.find_matches(build_match_keys("The Housemaid", "Freida McFadden"))

        assert {m.library_name for m in matches} == {"Audiobooks", "Kids"}

    def test_returns_nothing_for_an_empty_key_set(self, index):
        index.replace_items([_item()])

        assert index.find_matches(set()) == []

    def test_returns_nothing_when_nothing_was_ever_synced(self, index):
        assert index.find_matches(build_match_keys("The Housemaid", "Freida McFadden")) == []

    def test_indexes_an_item_by_its_asin(self, index):
        index.replace_items([_item(asin="B0BSHZ1234")])

        matches = index.find_matches(build_match_keys("", "", asin="B0BSHZ1234"))

        assert len(matches) == 1
        assert matches[0].item_id == "li_1"

    def test_an_asin_only_item_is_still_indexed(self, index):
        """Audiobookshelf items sometimes carry an ASIN and little else."""
        index.replace_items([_item(title="", author="", asin="B0BSHZ1234")])

        assert len(index.find_matches(build_match_keys("", "", asin="B0BSHZ1234"))) == 1

    def test_a_malformed_asin_indexes_no_asin_key(self, index):
        """Two items both tagged `N/A` must not become the same book."""
        index.replace_items([_item(item_id="li_1", asin="N/A"), _item(item_id="li_2", asin="N/A")])

        assert index.find_matches({"asin:N/A", "asin:"}) == []


class TestLibraryIndexReplace:
    """A sync is a full swap, so deletions in Audiobookshelf propagate."""

    def test_a_resync_drops_items_that_are_gone(self, index):
        index.replace_items([_item(item_id="li_1", title="The Housemaid")])

        index.replace_items([_item(item_id="li_2", title="The Coworker")])

        assert index.find_matches(build_match_keys("The Housemaid", "Freida McFadden")) == []
        assert len(index.find_matches(build_match_keys("The Coworker", "Freida McFadden"))) == 1

    def test_skips_items_that_can_never_match(self, index):
        """No author and no usable ASIN yields no key; storing it is dead weight."""
        stored = index.replace_items(
            [_item(item_id="li_1", author="", asin=""), _item(item_id="li_2")]
        )

        assert stored == 1

    def test_tolerates_a_duplicate_item_id_within_one_sync(self, index):
        index.replace_items([_item(item_id="li_1"), _item(item_id="li_1")])

        assert len(index.find_matches(build_match_keys("The Housemaid", "Freida McFadden"))) == 1


class TestLibraryIndexState:
    """The UI has to be able to say how stale the answer is."""

    def test_reports_no_sync_before_the_first_one(self, index):
        state = index.get_state()

        assert state.last_sync_at is None
        assert state.item_count == 0

    def test_records_the_sync_time_and_count(self, index):
        index.replace_items([_item(item_id="li_1"), _item(item_id="li_2", title="The Coworker")])

        state = index.get_state()

        assert state.last_sync_at is not None
        assert state.item_count == 2
        assert state.last_error is None

    def test_records_a_failure_without_discarding_the_index(self, index):
        """An ABS outage must degrade to a stale badge, never to a blank one."""
        index.replace_items([_item()])

        index.record_failure("Connection refused")
        state = index.get_state()

        assert state.last_error == "Connection refused"
        assert state.item_count == 1
        assert len(index.find_matches(build_match_keys("The Housemaid", "Freida McFadden"))) == 1

    def test_a_successful_sync_clears_a_previous_failure(self, index):
        index.record_failure("Connection refused")

        index.replace_items([_item()])

        assert index.get_state().last_error is None


class TestLibraryIndexInitialize:
    """Initialization runs on every boot, against whatever is already on disk."""

    def test_is_idempotent(self, tmp_path):
        path = str(tmp_path / "abs_index.db")
        first = LibraryIndexDB(path)
        first.initialize()
        first.replace_items([_item()])

        second = LibraryIndexDB(path)
        second.initialize()

        assert len(second.find_matches(build_match_keys("The Housemaid", "Freida McFadden"))) == 1
