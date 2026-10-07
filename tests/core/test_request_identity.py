"""Book identity on approved requests (fork-only): fulfil fills it from book_data."""

import os
import tempfile
from typing import Any

import pytest

from shelfmark.core.requests_service import fulfil_request
from shelfmark.core.user_db import UserDB

ISBN_13 = "9780316005142"

BOOK_DATA = {
    "title": "Overlord",
    "author": "Kugane Maruyama",
    "content_type": "ebook",
    "provider": "hardcover",
    "provider_id": "886465",
    "isbn_13": ISBN_13,
    "asin": "B0BSHZ1234",
}


@pytest.fixture
def user_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "shelfmark.db"))
        db.initialize()
        yield db


def approve(user_db: UserDB, *, release_data: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    requester = user_db.create_user("ada")
    admin = user_db.create_user("root", role="admin")
    created = user_db.create_request(
        user_id=requester["id"],
        content_type="ebook",
        request_level="release",
        policy_mode="request_release",
        book_data=BOOK_DATA,
        release_data=release_data,
    )
    queued: list[dict[str, Any]] = []

    def fake_queue_release(data, priority=0, **_kwargs):
        queued.append(data)
        return True, None

    fulfil_request(
        user_db,
        request_id=created["id"],
        admin_user_id=admin["id"],
        queue_release=fake_queue_release,
        **kwargs,
    )
    return queued[0]


def test_an_old_release_without_identity_is_filled_from_book_data(user_db):
    queued = approve(user_db, release_data={"source": "prowlarr", "source_id": "r1", "title": "O"})

    assert {k: queued.get(k) for k in ("provider", "provider_id", "isbn_13", "asin")} == {
        "provider": "hardcover",
        "provider_id": "886465",
        "isbn_13": ISBN_13,
        "asin": "B0BSHZ1234",
    }


def test_a_browsed_release_for_another_provider_keeps_its_own_identity(user_db):
    release = {
        "source": "prowlarr",
        "source_id": "r2",
        "title": "O",
        "provider": "openlibrary",
        "provider_id": "OL1W",
    }

    queued = approve(user_db, release_data=release)

    assert (queued["provider"], queued["provider_id"]) == ("openlibrary", "OL1W")
    assert "isbn_13" not in queued
    assert "asin" not in queued


def test_the_destination_key_still_travels_alongside(user_db):
    queued = approve(
        user_db,
        release_data={"source": "prowlarr", "source_id": "r3", "title": "O"},
        destination_key="grimmory:5:8",
    )

    assert queued["destination_key"] == "grimmory:5:8"
    assert queued["provider_id"] == "886465"
