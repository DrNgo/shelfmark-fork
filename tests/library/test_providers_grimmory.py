"""Tests for the Grimmory provider feeding the shared index."""

import pytest

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


def _patch_sync(monkeypatch, pages, *, get_book=None, login=None):
    """Patch login, listing and per-book reads; by default a read echoes the listing row."""
    from shelfmark.library.providers import grimmory as provider_module

    rows = {str(row["id"]): row for books, _ in pages.values() for row in books}

    def echo(cfg, token, book_id, *, session=None):
        return rows[str(book_id)]

    monkeypatch.setattr(provider_module, "booklore_login", login or (lambda cfg: "token"))
    monkeypatch.setattr(
        provider_module, "list_books", lambda cfg, token, *, page, size: pages[page]
    )
    monkeypatch.setattr(provider_module, "get_book", get_book or echo)
    monkeypatch.setattr(provider_module.GrimmoryProvider, "is_enabled", lambda self: True)
    return provider_module


class TestPagination:
    def test_walks_every_page(self, monkeypatch):
        pages = {0: ([_book(book_id=1)], 3), 1: ([_book(book_id=2)], 3), 2: ([_book(book_id=3)], 3)}
        provider_module = _patch_sync(monkeypatch, pages)

        items = provider_module.GrimmoryProvider().fetch_items()

        assert [item.item_id for item in items] == ["1", "2", "3"]

    def test_fails_when_the_page_cap_stops_it_early(self, monkeypatch):
        # A server reporting an absurd totalPages must neither spin forever nor
        # be indexed partially: hitting the cap is a sync failure.
        from shelfmark.grimmory.client import BookloreError

        calls = {"n": 0}

        def endless(cfg, token, *, page, size):
            calls["n"] += 1
            return ([_book(book_id=page)], 10_000)

        provider_module = _patch_sync(monkeypatch, {})
        monkeypatch.setattr(provider_module, "list_books", endless)

        with pytest.raises(BookloreError, match="cap"):
            provider_module.GrimmoryProvider().fetch_items()
        assert calls["n"] == provider_module._MAX_PAGES

    def test_a_single_page_library_makes_one_call(self, monkeypatch):
        calls = {"n": 0}
        pages = {0: ([_book(book_id=1)], 1)}
        provider_module = _patch_sync(monkeypatch, pages)
        real_list = provider_module.list_books

        def counting(cfg, token, *, page, size):
            calls["n"] += 1
            return real_list(cfg, token, page=page, size=size)

        monkeypatch.setattr(provider_module, "list_books", counting)

        provider_module.GrimmoryProvider().fetch_items()

        assert calls["n"] == 1


class TestFullBookReads:
    """Only the single-book endpoint returns hardcoverBookId."""

    def test_reads_each_book_and_keeps_its_hardcover_id(self, monkeypatch):
        listed = _book(book_id=165, title="Overlord Volume 3")
        detailed = _book(book_id=165, title="Overlord Volume 3", hardcoverBookId="885682")
        provider_module = _patch_sync(
            monkeypatch,
            {0: ([listed], 1)},
            get_book=lambda cfg, token, book_id, *, session=None: detailed,
        )

        items = provider_module.GrimmoryProvider().fetch_items()

        assert items[0].hardcover_id == "885682"

    def test_file_and_library_fields_come_from_the_listing(self, monkeypatch):
        # Only the detail's metadata is taken: an audiobook must stay an
        # audiobook even when the single-book payload omits the file fields.
        listed = _book(book_id=9, file_type="AUDIOBOOK")
        detail = {"id": 9, "metadata": {**listed["metadata"], "hardcoverBookId": "42"}}
        provider_module = _patch_sync(
            monkeypatch,
            {0: ([listed], 1)},
            get_book=lambda cfg, token, book_id, *, session=None: detail,
        )

        [item] = provider_module.GrimmoryProvider().fetch_items()

        assert (item.media_type, item.library_name, item.hardcover_id) == (
            MEDIA_TYPE_AUDIOBOOK,
            "Ebooks",
            "42",
        )

    def test_a_non_digit_hardcover_id_is_dropped(self):
        assert extract_library_items([_book(hardcoverBookId="hc-1")])[0].hardcover_id == ""

    def test_a_book_without_a_hardcover_id_still_indexes(self):
        assert extract_library_items([_book()])[0].hardcover_id == ""

    def test_a_failed_read_fails_the_whole_sync(self, monkeypatch):
        from shelfmark.grimmory.client import BookloreError

        def broken(cfg, token, book_id, *, session=None):
            raise BookloreError("Grimmory book 1 read failed (404)")

        provider_module = _patch_sync(monkeypatch, {0: ([_book()], 1)}, get_book=broken)

        with pytest.raises(BookloreError, match="404"):
            provider_module.GrimmoryProvider().fetch_items()

    def test_one_expired_session_relogs_once(self, monkeypatch):
        from shelfmark.grimmory.client import BookloreAuthError

        logins = {"n": 0}
        reads = {"n": 0}

        def login(cfg):
            logins["n"] += 1
            return f"token-{logins['n']}"

        def read(cfg, token, book_id, *, session=None):
            reads["n"] += 1
            if token == "token-1":
                raise BookloreAuthError("Grimmory session expired")
            return _book(book_id=book_id)

        provider_module = _patch_sync(
            monkeypatch, {0: ([_book(book_id=1), _book(book_id=2)], 1)}, get_book=read, login=login
        )

        items = provider_module.GrimmoryProvider().fetch_items()

        assert [i.item_id for i in items] == ["1", "2"]
        assert logins["n"] == 2

    def test_a_second_expired_session_fails(self, monkeypatch):
        from shelfmark.grimmory.client import BookloreAuthError

        def always_expired(cfg, token, book_id, *, session=None):
            raise BookloreAuthError("Grimmory session expired")

        provider_module = _patch_sync(monkeypatch, {0: ([_book()], 1)}, get_book=always_expired)

        with pytest.raises(BookloreAuthError):
            provider_module.GrimmoryProvider().fetch_items()

    def test_reads_share_one_session(self, monkeypatch):
        sessions = []

        def read(cfg, token, book_id, *, session=None):
            sessions.append(session)
            return _book(book_id=book_id)

        provider_module = _patch_sync(
            monkeypatch, {0: ([_book(book_id=1), _book(book_id=2)], 1)}, get_book=read
        )
        provider_module.GrimmoryProvider().fetch_items()

        assert sessions[0] is not None and sessions[0] is sessions[1]

    def test_an_empty_library_makes_no_reads(self, monkeypatch):
        # Review Focus #3
        reads = {"n": 0}

        def read(cfg, token, book_id, *, session=None):
            reads["n"] += 1
            return {}

        provider_module = _patch_sync(monkeypatch, {0: ([], 1)}, get_book=read)

        assert provider_module.GrimmoryProvider().fetch_items() == []
        assert reads["n"] == 0

    def test_a_book_listed_twice_is_read_and_indexed_once(self, monkeypatch):
        # Review Focus #4
        reads = []

        def read(cfg, token, book_id, *, session=None):
            reads.append(book_id)
            return _book(book_id=book_id)

        pages = {0: ([_book(book_id=1)], 2), 1: ([_book(book_id=1)], 2)}
        provider_module = _patch_sync(monkeypatch, pages, get_book=read)

        items = provider_module.GrimmoryProvider().fetch_items()

        assert reads == [1]
        assert [i.item_id for i in items] == ["1"]
