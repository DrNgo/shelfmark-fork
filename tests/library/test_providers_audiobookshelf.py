"""Tests for the Audiobookshelf provider feeding the shared index."""

import pytest

from shelfmark.audiobookshelf.client import AudiobookshelfLibrary
from shelfmark.library.index import MEDIA_TYPE_AUDIOBOOK, SOURCE_AUDIOBOOKSHELF
from shelfmark.library.providers.audiobookshelf import (
    AudiobookshelfProvider,
    extract_library_items,
)

LIBRARY = AudiobookshelfLibrary(id="lib_1", name="Audiobooks", media_type="book")
BOOKS_LIBRARY = AudiobookshelfLibrary(id="lib_books", name="Audiobooks", media_type="book")
KIDS_LIBRARY = AudiobookshelfLibrary(id="lib_kids", name="Kids", media_type="book")


def _raw(item_id="li_1", title="The Housemaid", **metadata):
    return {"id": item_id, "media": {"metadata": {"title": title, **metadata}}}


class TestExtractLibraryItems:
    def test_tags_every_item_with_its_source_and_media_type(self):
        items = extract_library_items([_raw(authorName="Freida McFadden")], LIBRARY)

        assert [i.source for i in items] == [SOURCE_AUDIOBOOKSHELF]
        assert [i.media_type for i in items] == [MEDIA_TYPE_AUDIOBOOK]

    def test_carries_no_isbn(self):
        # Audiobookshelf exposes no ISBN; the column must stay empty rather than
        # pick up a stray value that would key against ebook holdings.
        items = extract_library_items([_raw(authorName="Freida McFadden")], LIBRARY)

        assert items[0].isbn13 == ""

    def test_prefers_the_authors_list_over_the_joined_name(self):
        items = extract_library_items(
            [_raw(authors=[{"name": "Freida McFadden"}], authorName="McFadden, Freida & Other")],
            LIBRARY,
        )

        assert items[0].author == "Freida McFadden"

    def test_skips_items_with_no_id_or_title(self):
        assert (
            extract_library_items([{"id": "", "media": {"metadata": {"title": "X"}}}], LIBRARY)
            == []
        )
        assert extract_library_items([_raw(item_id="li_2", title="")], LIBRARY) == []

    def test_flattens_an_expanded_item(self):
        items = extract_library_items(
            [
                _raw(
                    subtitle="A Novel",
                    authorName="Freida McFadden",
                    asin="B0BSHZ1234",
                )
            ],
            LIBRARY,
        )

        assert len(items) == 1
        item = items[0]
        assert item.item_id == "li_1"
        assert item.library_id == "lib_1"
        assert item.library_name == "Audiobooks"
        assert item.title == "The Housemaid"
        assert item.subtitle == "A Novel"
        assert item.author == "Freida McFadden"
        assert item.asin == "B0BSHZ1234"

    def test_prefers_the_structured_author_over_the_joined_string(self):
        """`authorName` joins co-authors with commas; `authors[0]` never does."""
        raw = _raw(
            authorName="Freida McFadden, Someone Else",
            authors=[{"id": "au_1", "name": "Freida McFadden"}],
        )

        assert extract_library_items([raw], LIBRARY)[0].author == "Freida McFadden"

    def test_falls_back_to_the_joined_author_string(self):
        raw = _raw(authorName="Freida McFadden", authors=[])

        assert extract_library_items([raw], LIBRARY)[0].author == "Freida McFadden"

    def test_tolerates_missing_metadata(self):
        assert extract_library_items([{"id": "li_1"}], LIBRARY) == []

    def test_defaults_a_missing_asin_to_empty(self):
        raw = _raw(authorName="Freida McFadden")

        assert extract_library_items([raw], LIBRARY)[0].asin == ""

    def test_ignores_a_non_dict_row(self):
        assert extract_library_items(["nonsense", None], LIBRARY) == []


class _FakeClient:
    """Stands in for AudiobookshelfClient's library walk."""

    def __init__(self, libraries, items_by_library):
        self._libraries = libraries
        self._items = items_by_library
        self.requested_libraries: list[str] = []

    def get_book_libraries(self):
        return self._libraries

    def get_library_items(self, library_id, **_kwargs):
        self.requested_libraries.append(library_id)
        return self._items.get(library_id, [])


class TestAudiobookshelfProviderFetchItems:
    """fetch_items walks every book library and flattens it in one go."""

    def test_indexes_items_from_every_book_library(self, monkeypatch):
        client = _FakeClient(
            libraries=[BOOKS_LIBRARY, KIDS_LIBRARY],
            items_by_library={
                "lib_books": [_raw("li_1", authorName="Freida McFadden")],
                "lib_kids": [_raw("li_2", title="The Coworker", authorName="Freida McFadden")],
            },
        )
        monkeypatch.setattr(
            "shelfmark.audiobookshelf.settings.build_client_from_config", lambda: client
        )

        items = AudiobookshelfProvider().fetch_items()

        assert [i.item_id for i in items] == ["li_1", "li_2"]
        assert client.requested_libraries == ["lib_books", "lib_kids"]

    def test_raises_when_audiobookshelf_is_not_configured(self, monkeypatch):
        """The scheduler's PROVIDER_ERRORS guard needs something to catch."""
        monkeypatch.setattr(
            "shelfmark.audiobookshelf.settings.build_client_from_config", lambda: None
        )

        with pytest.raises(RuntimeError, match="not configured"):
            AudiobookshelfProvider().fetch_items()
