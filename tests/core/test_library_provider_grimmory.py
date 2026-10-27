"""Tests for the fork's Grimmory library provider."""

from __future__ import annotations

from typing import Any

import pytest

from shelfmark.core import library_index
from shelfmark.core.library_providers import all_providers, grimmory
from shelfmark.metadata_providers import BookMetadata


@pytest.fixture
def config(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    values: dict[str, Any] = {
        "BOOKLORE_ENABLED": True,
        "BOOKLORE_HOST": "http://grimmory",
        "BOOKLORE_USERNAME": "shelfmark",
        "BOOKLORE_PASSWORD": "pw",
    }
    monkeypatch.setattr(
        grimmory.app_config, "get", lambda key, default=None, user_id=None: values.get(key, default)
    )
    monkeypatch.setattr(library_index, "_cache", {})
    return values


def _book(title: str, *, file_type: str = "EPUB", **meta: Any) -> dict[str, Any]:
    return {
        "id": title,
        "primaryFile": {"bookType": file_type},
        "metadata": {"title": title, "authors": ["Frank Herbert"], **meta},
    }


def _listing(monkeypatch: pytest.MonkeyPatch, pages: list[list[dict[str, Any]]]) -> None:
    from shelfmark.grimmory import client

    monkeypatch.setattr(client, "booklore_login", lambda cfg: "token")
    monkeypatch.setattr(grimmory, "_hardcover_ids", {})
    monkeypatch.setattr(
        client,
        "get_book",
        lambda cfg, token, book_id: {"metadata": {"hardcoverBookId": f"hc-{book_id}"}},
    )
    monkeypatch.setattr(
        client, "list_books", lambda cfg, token, *, page, size: (pages[page], len(pages))
    )


def test_is_registered_and_gated_on_the_grimmory_switch(config):
    provider = next(p for p in all_providers() if p.name == "grimmory")
    assert provider.is_enabled()
    config["BOOKLORE_LIBRARY_INDEX_ENABLED"] = False
    assert not provider.is_enabled()


def test_reads_every_page_and_skips_audiobooks(config, monkeypatch):
    _listing(
        monkeypatch,
        [
            [_book("Dune", isbn13="9780441013593")],
            [_book("Dune Messiah", file_type="AUDIOBOOK"), _book("Children of Dune")],
        ],
    )

    entries = grimmory.GrimmoryLibrary().fetch_entries()

    assert len(entries) == 2
    assert "9780441013593" in entries[0].isbns


def test_an_owned_ebook_is_reported_by_the_library_check(config, monkeypatch):
    _listing(monkeypatch, [[_book("Dune")]])
    owned = BookMetadata(provider="hardcover", provider_id="1", title="Dune", authors=["Herbert"])
    other = BookMetadata(provider="hardcover", provider_id="2", title="Emma", authors=["Austen"])

    assert library_index.ownership(owned) == {"ebook": "owned"}
    assert library_index.ownership(other) == {"ebook": None}


def test_a_hardcover_id_matches_a_volume_with_a_different_title(config, monkeypatch):
    # Grimmory names the volume "Overlord, Vol. 5"; Hardcover calls it by its own title.
    _listing(monkeypatch, [[_book("Overlord, Vol. 5")]])
    hardcover_book = BookMetadata(
        provider="hardcover",
        provider_id="hc-Overlord, Vol. 5",
        title="The Men of the Kingdom Part I",
        authors=["Kugane Maruyama"],
    )

    assert library_index.ownership(hardcover_book) == {"ebook": "owned"}


def test_book_details_are_read_once(config, monkeypatch):
    calls: list[object] = []
    _listing(monkeypatch, [[_book("Dune")]])
    from shelfmark.grimmory import client

    real = client.get_book
    monkeypatch.setattr(client, "get_book", lambda *a, **k: calls.append(a[2]) or real(*a, **k))

    grimmory.GrimmoryLibrary().fetch_entries()
    grimmory.GrimmoryLibrary().fetch_entries()

    assert calls == ["Dune"]
