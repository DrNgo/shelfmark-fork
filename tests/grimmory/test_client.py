"""Tests for the Grimmory API client."""

import pytest
import requests

from shelfmark.grimmory.client import BookloreConfig, BookloreError, list_books

CONFIG = BookloreConfig(
    base_url="http://grimmory:6060",
    username="shelfmark",
    password="secret",
    library_id=0,
    path_id=0,
)


class _Response:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = ""

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(response=self)

    def json(self):
        return self._payload


class TestListBooks:
    def test_returns_the_page_content_and_total_pages(self, monkeypatch):
        captured = {}

        def fake_get(url, **kwargs):
            captured["url"] = url
            captured["params"] = kwargs.get("params")
            return _Response({"content": [{"id": 1}], "totalPages": 3})

        monkeypatch.setattr(requests, "get", fake_get)

        books, total_pages = list_books(CONFIG, "token", page=0, size=500)

        assert books == [{"id": 1}]
        assert total_pages == 3
        assert captured["url"] == "http://grimmory:6060/api/v1/books/page"
        assert captured["params"] == {"page": 0, "size": 500}

    def test_treats_a_missing_total_pages_as_one(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response({"content": []}))

        assert list_books(CONFIG, "token", page=0, size=500) == ([], 1)

    def test_raises_a_booklore_error_on_transport_failure(self, monkeypatch):
        def boom(url, **kwargs):
            raise requests.exceptions.ConnectionError

        monkeypatch.setattr(requests, "get", boom)

        with pytest.raises(BookloreError, match="Grimmory"):
            list_books(CONFIG, "token", page=0, size=500)

    def test_raises_on_an_unexpected_payload_shape(self, monkeypatch):
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response(["not", "an", "object"]))

        with pytest.raises(BookloreError):
            list_books(CONFIG, "token", page=0, size=500)

    def test_raises_a_booklore_error_when_content_is_missing(self, monkeypatch):
        """A 200 with no `content` field must never be read as an empty
        library — that would let a sync silently wipe the entire cached
        Grimmory index via replace_items() on a malformed response, when a
        sync failure is supposed to leave the previous index in place.
        """
        monkeypatch.setattr(requests, "get", lambda url, **kw: _Response({"totalPages": 3}))

        with pytest.raises(BookloreError):
            list_books(CONFIG, "token", page=0, size=500)

    def test_raises_a_booklore_error_when_content_is_not_a_list(self, monkeypatch):
        monkeypatch.setattr(
            requests, "get", lambda url, **kw: _Response({"content": "unexpected", "totalPages": 1})
        )

        with pytest.raises(BookloreError):
            list_books(CONFIG, "token", page=0, size=500)

    def test_an_explicit_empty_content_list_is_a_legitimately_empty_library(self, monkeypatch):
        """`{"content": [], "totalPages": 0}` is a real answer from an account
        that can see nothing — it must still succeed, not be mistaken for the
        malformed-response case above.
        """
        monkeypatch.setattr(
            requests, "get", lambda url, **kw: _Response({"content": [], "totalPages": 0})
        )

        assert list_books(CONFIG, "token", page=0, size=500) == ([], 1)


class TestConnectionMessage:
    def test_reports_both_library_and_book_counts(self, monkeypatch):
        from shelfmark.config import booklore_settings

        monkeypatch.setattr(
            booklore_settings,
            "_get_booklore_select_options",
            lambda *a, **kw: ([{"value": "1", "label": "Ebooks"}], []),
        )
        monkeypatch.setattr(booklore_settings, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(booklore_settings, "list_books", lambda *a, **kw: ([{"id": 1}], 3))

        result = booklore_settings.check_booklore_connection(
            {
                "BOOKLORE_HOST": "http://grimmory:6060",
                "BOOKLORE_USERNAME": "shelfmark",
                "BOOKLORE_PASSWORD": "secret",
            }
        )

        assert result["success"] is True
        assert "1 libraries" in result["message"]
        assert "3 books" in result["message"]

    def test_a_zero_book_account_is_not_reported_as_having_one_book(self, monkeypatch):
        """list_books() clamps totalPages to a minimum of 1 for pagination
        purposes. Reusing that clamp as a book count would report an account
        that can see nothing as "Connected to Grimmory (0 libraries, 1
        books)" -- inaccurate in exactly the diagnostic case this message
        exists to expose.
        """
        from shelfmark.config import booklore_settings

        monkeypatch.setattr(
            booklore_settings, "_get_booklore_select_options", lambda *a, **kw: ([], [])
        )
        monkeypatch.setattr(booklore_settings, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(booklore_settings, "list_books", lambda *a, **kw: ([], 1))

        result = booklore_settings.check_booklore_connection(
            {
                "BOOKLORE_HOST": "http://grimmory:6060",
                "BOOKLORE_USERNAME": "shelfmark",
                "BOOKLORE_PASSWORD": "secret",
            }
        )

        assert result["success"] is True
        assert "1 books" not in result["message"]
        assert "0 books" in result["message"]

    def test_a_single_book_account_uses_grammatical_singular(self, monkeypatch):
        from shelfmark.config import booklore_settings

        monkeypatch.setattr(
            booklore_settings, "_get_booklore_select_options", lambda *a, **kw: ([], [])
        )
        monkeypatch.setattr(booklore_settings, "booklore_login", lambda cfg: "token")
        monkeypatch.setattr(booklore_settings, "list_books", lambda *a, **kw: ([{"id": 1}], 1))

        result = booklore_settings.check_booklore_connection(
            {
                "BOOKLORE_HOST": "http://grimmory:6060",
                "BOOKLORE_USERNAME": "shelfmark",
                "BOOKLORE_PASSWORD": "secret",
            }
        )

        assert result["success"] is True
        assert "1 book)" in result["message"]
        assert "1 books" not in result["message"]
