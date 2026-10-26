"""The upstream library check reads Grimmory from the fork's library index."""

import pytest

from shelfmark.core.library_index import match_entries
from shelfmark.core.library_providers import all_providers
from shelfmark.core.library_providers.grimmory import GrimmoryLibrary
from shelfmark.library import index as index_module
from shelfmark.library.index import SOURCE_GRIMMORY, LibraryItem
from shelfmark.metadata_providers import BookMetadata


def _item(item_id: str, title: str, media_type: str = "ebook", **fields: str) -> LibraryItem:
    return LibraryItem(
        source=SOURCE_GRIMMORY,
        item_id=item_id,
        library_id="1",
        library_name="Books",
        media_type=media_type,
        title=title,
        subtitle=fields.get("subtitle", ""),
        author=fields.get("author", "Frank Herbert"),
        asin="",
        isbn13=fields.get("isbn13", ""),
        hardcover_id=fields.get("hardcover_id", ""),
    )


@pytest.fixture
def grimmory(index, monkeypatch):
    monkeypatch.setattr(index_module, "get_library_index", lambda: index)
    return GrimmoryLibrary()


def test_is_registered_with_the_library_check():
    assert "grimmory" in [provider.name for provider in all_providers()]


def test_never_synced_is_unavailable_not_empty(grimmory):
    with pytest.raises(RuntimeError, match="not been indexed"):
        grimmory.fetch_entries()


def test_offers_only_ebooks_with_their_identifiers(grimmory, index):
    index.replace_items(
        SOURCE_GRIMMORY,
        [
            _item("1", "Dune", isbn13="9780441013593", hardcover_id="312460"),
            _item("2", "Dune Messiah", media_type="audiobook"),
        ],
    )

    entries = grimmory.fetch_entries()

    assert len(entries) == 1
    assert ("hardcover", "312460") in entries[0].external_ids
    assert "9780441013593" in entries[0].isbns
    assert grimmory.fingerprint() is not None


def test_a_hardcover_id_or_the_title_marks_the_book_owned(grimmory, index):
    index.replace_items(SOURCE_GRIMMORY, [_item("1", "Dune", hardcover_id="312460")])
    entries = grimmory.fetch_entries()

    by_id = BookMetadata(provider="hardcover", provider_id="312460", title="Something Else")
    by_title = BookMetadata(
        provider="openlibrary", provider_id="x", title="Dune", authors=["Frank Herbert"]
    )
    other = BookMetadata(
        provider="openlibrary", provider_id="y", title="Children of Dune", authors=["Herbert"]
    )
    assert match_entries(by_id, entries) == "owned"
    assert match_entries(by_title, entries) == "owned"
    assert match_entries(other, entries) is None
