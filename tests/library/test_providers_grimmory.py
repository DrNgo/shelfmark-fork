"""Tests for the Grimmory provider feeding the shared index."""

from shelfmark.library.index import MEDIA_TYPE_AUDIOBOOK, MEDIA_TYPE_EBOOK, SOURCE_GRIMMORY
from shelfmark.library.providers.grimmory import extract_library_items


def _book(book_id=1, file_type="EPUB", alternatives=None, **metadata):
    base = {
        "title": "The Housemaid",
        "authors": ["Freida McFadden"],
    }
    base.update(metadata)
    return {
        "id": book_id,
        "libraryId": 7,
        "libraryName": "Ebooks",
        "primaryFile": {"bookType": file_type},
        "alternativeFormats": alternatives or [],
        "metadata": base,
    }


class TestExtractLibraryItems:
    def test_flattens_a_book_into_an_index_row(self):
        items = extract_library_items([_book(isbn13="9780593135204", asin="B09XYZ1234")])

        assert len(items) == 1
        item = items[0]
        assert item.source == SOURCE_GRIMMORY
        assert item.item_id == "1"
        assert item.library_id == "7"
        assert item.library_name == "Ebooks"
        assert item.title == "The Housemaid"
        assert item.author == "Freida McFadden"
        assert item.isbn13 == "9780593135204"
        assert item.asin == "B09XYZ1234"

    def test_canonicalizes_an_isbn10_into_isbn13(self):
        items = extract_library_items([_book(isbn10="0306406152")])

        assert items[0].isbn13 == "9780306406157"

    def test_prefers_isbn13_when_both_are_present(self):
        items = extract_library_items([_book(isbn13="9780593135204", isbn10="0306406152")])

        assert items[0].isbn13 == "9780593135204"

    def test_takes_the_first_author(self):
        items = extract_library_items([_book(authors=["Freida McFadden", "Someone Else"])])

        assert items[0].author == "Freida McFadden"

    def test_falls_back_to_the_top_level_title(self):
        raw = _book()
        raw["metadata"].pop("title")
        raw["title"] = "The Housemaid"

        assert extract_library_items([raw])[0].title == "The Housemaid"

    def test_skips_a_book_with_no_id_or_title(self):
        assert extract_library_items([_book(book_id=None)]) == []
        no_title = _book()
        no_title["metadata"]["title"] = ""
        no_title["title"] = ""
        assert extract_library_items([no_title]) == []


class TestMediaTypeDerivation:
    def test_an_epub_is_an_ebook(self):
        assert extract_library_items([_book(file_type="EPUB")])[0].media_type == MEDIA_TYPE_EBOOK

    def test_an_audiobook_only_entry_is_an_audiobook(self):
        items = extract_library_items([_book(file_type="AUDIOBOOK")])

        assert items[0].media_type == MEDIA_TYPE_AUDIOBOOK

    def test_a_book_with_both_an_epub_and_an_audiobook_is_an_ebook(self):
        # Owning the M4B alongside the EPUB must not demote the ebook holding,
        # or the ebook badge silently disappears for dual-format books.
        items = extract_library_items(
            [_book(file_type="EPUB", alternatives=[{"bookType": "AUDIOBOOK"}])]
        )

        assert items[0].media_type == MEDIA_TYPE_EBOOK

    def test_an_unknown_file_type_defaults_to_ebook(self):
        raw = _book()
        raw["primaryFile"] = None
        raw["alternativeFormats"] = []

        assert extract_library_items([raw])[0].media_type == MEDIA_TYPE_EBOOK


class TestPagination:
    def test_walks_every_page(self, monkeypatch):
        from shelfmark.library.providers import grimmory as provider_module

        pages = {0: ([_book(book_id=1)], 3), 1: ([_book(book_id=2)], 3), 2: ([_book(book_id=3)], 3)}
        monkeypatch.setattr(provider_module, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(
            provider_module, "list_books", lambda cfg, token, *, page, size: pages[page]
        )
        monkeypatch.setattr(provider_module.GrimmoryProvider, "is_enabled", lambda self: True)

        items = provider_module.GrimmoryProvider().fetch_items()

        assert [item.item_id for item in items] == ["1", "2", "3"]

    def test_stops_at_the_page_cap(self, monkeypatch):
        # A server reporting an absurd totalPages must not spin forever.
        from shelfmark.library.providers import grimmory as provider_module

        calls = {"n": 0}

        def endless(cfg, token, *, page, size):
            calls["n"] += 1
            return ([_book(book_id=page)], 10_000)

        monkeypatch.setattr(provider_module, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(provider_module, "list_books", endless)
        monkeypatch.setattr(provider_module.GrimmoryProvider, "is_enabled", lambda self: True)

        provider_module.GrimmoryProvider().fetch_items()

        assert calls["n"] == provider_module._MAX_PAGES

    def test_a_single_page_library_makes_one_call(self, monkeypatch):
        from shelfmark.library.providers import grimmory as provider_module

        calls = {"n": 0}

        def one_page(cfg, token, *, page, size):
            calls["n"] += 1
            return ([_book(book_id=1)], 1)

        monkeypatch.setattr(provider_module, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(provider_module, "list_books", one_page)
        monkeypatch.setattr(provider_module.GrimmoryProvider, "is_enabled", lambda self: True)

        provider_module.GrimmoryProvider().fetch_items()

        assert calls["n"] == 1
