"""Shared fixtures for the library index tests."""

import pytest

from shelfmark.library.index import LibraryIndexDB


@pytest.fixture
def index(tmp_path):
    """A freshly initialized index backed by a temporary file."""
    db = LibraryIndexDB(str(tmp_path / "library_index.db"))
    db.initialize()
    return db
