"""Tests for carrying an approved destination key through the request lifecycle."""

import os
import sqlite3
import tempfile
from typing import Any

import pytest

from shelfmark.core.requests_service import RequestServiceError, fulfil_request
from shelfmark.core.user_db import UserDB


@pytest.fixture
def db_path():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "shelfmark.db")


@pytest.fixture
def user_db(db_path):
    db = UserDB(db_path)
    db.initialize()
    return db


def request_columns(db_path: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {str(row[1]) for row in conn.execute("PRAGMA table_info(download_requests)")}
    finally:
        conn.close()


def make_request(user_db: UserDB, **overrides: Any) -> dict[str, Any]:
    user = user_db.create_user("ada")
    payload: dict[str, Any] = {
        "user_id": user["id"],
        "content_type": "audiobook",
        "request_level": "release",
        "policy_mode": "request_release",
        "book_data": {"title": "Some Audiobook", "author": "Ada"},
        "release_data": {"source": "direct", "source_id": "abc123", "title": "Some Audiobook"},
    }
    payload.update(overrides)
    return user_db.create_request(**payload)


class TestSchema:
    """`download_requests.destination_key` exists on new and existing databases."""

    def test_new_databases_have_the_column(self, user_db, db_path):
        assert "destination_key" in request_columns(db_path)

    def test_existing_databases_are_migrated(self, user_db, db_path):
        """Simulate a database created before multi-library routing existed."""
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("ALTER TABLE download_requests DROP COLUMN destination_key")
            conn.commit()
        finally:
            conn.close()

        assert "destination_key" not in request_columns(db_path)

        UserDB(db_path).initialize()

        assert "destination_key" in request_columns(db_path)

    def test_migration_leaves_historical_rows_unrouted(self, user_db, db_path):
        created = make_request(user_db)

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("ALTER TABLE download_requests DROP COLUMN destination_key")
            conn.commit()
        finally:
            conn.close()

        migrated = UserDB(db_path)
        migrated.initialize()

        assert migrated.get_request(created["id"])["destination_key"] is None


class TestPersistence:
    """The chosen library survives on the request row."""

    def test_defaults_to_none(self, user_db):
        assert make_request(user_db)["destination_key"] is None

    def test_create_request_stores_the_key(self, user_db):
        created = make_request(user_db, destination_key="lib-kids")

        assert created["destination_key"] == "lib-kids"

    def test_update_request_stores_the_key(self, user_db):
        created = make_request(user_db)

        updated = user_db.update_request(created["id"], destination_key="lib-fiction")

        assert updated["destination_key"] == "lib-fiction"


class TestFulfilRequestRouting:
    """Approving a request routes it to the library the admin picked."""

    def approve(self, user_db: UserDB, **kwargs: Any) -> tuple[dict[str, Any], list[Any]]:
        admin = user_db.create_user("root", role="admin")
        created = make_request(user_db)
        queued: list[Any] = []

        def fake_queue_release(release_data, priority=0, **queue_kwargs):
            queued.append(release_data)
            return True, None

        row = fulfil_request(
            user_db,
            request_id=created["id"],
            admin_user_id=admin["id"],
            queue_release=fake_queue_release,
            **kwargs,
        )
        return row, queued

    def test_passes_the_chosen_key_to_the_queue(self, user_db):
        _, queued = self.approve(user_db, destination_key="lib-kids")

        assert queued[0]["destination_key"] == "lib-kids"

    def test_records_the_chosen_key_on_the_request(self, user_db):
        row, _ = self.approve(user_db, destination_key="lib-kids")

        assert row["destination_key"] == "lib-kids"

    def test_omits_the_key_when_no_library_was_chosen(self, user_db):
        row, queued = self.approve(user_db)

        assert queued[0].get("destination_key") is None
        assert row["destination_key"] is None

    def test_rejects_a_non_string_key(self, user_db):
        with pytest.raises(RequestServiceError) as exc_info:
            self.approve(user_db, destination_key=17)

        assert exc_info.value.status_code == 400

    def test_treats_a_blank_key_as_no_choice(self, user_db):
        row, queued = self.approve(user_db, destination_key="   ")

        assert queued[0].get("destination_key") is None
        assert row["destination_key"] is None
