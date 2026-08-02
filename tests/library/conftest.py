"""Shared fixtures for the library index tests."""

import pytest

from shelfmark.library.index import LibraryIndexDB


@pytest.fixture
def index(tmp_path):
    """A freshly initialized index backed by a temporary file."""
    db = LibraryIndexDB(str(tmp_path / "library_index.db"))
    db.initialize()
    return db


@pytest.fixture
def enabled_providers(monkeypatch):
    """Force both providers on, so lookup tests exercise matching not config."""
    from shelfmark.library.providers.audiobookshelf import AudiobookshelfProvider
    from shelfmark.library.providers.grimmory import GrimmoryProvider

    monkeypatch.setattr(AudiobookshelfProvider, "is_enabled", lambda self: True)
    monkeypatch.setattr(GrimmoryProvider, "is_enabled", lambda self: True)
