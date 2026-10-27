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
        "primaryFile": {"bookType": file_type},
        "metadata": {"title": title, "authors": ["Frank Herbert"], **meta},
    }


def _listing(monkeypatch: pytest.MonkeyPatch, pages: list[list[dict[str, Any]]]) -> None:
    from shelfmark.grimmory import client

    monkeypatch.setattr(client, "booklore_login", lambda cfg: "token")
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
