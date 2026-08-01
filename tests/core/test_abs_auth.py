"""Tests for the abs auth source: mode resolution and user-activity policy."""

import sqlite3

import pytest

from shelfmark.core.auth_modes import (
    AUTH_SOURCE_ABS,
    AUTH_SOURCE_SET,
    determine_auth_mode,
    is_user_active_for_auth_mode,
)
from shelfmark.core.user_db import UserDB


class TestDetermineAuthModeAbs:
    def test_returns_abs_when_local_admin_exists(self):
        result = determine_auth_mode({"AUTH_METHOD": "abs"}, None, has_local_admin=True)
        assert result == AUTH_SOURCE_ABS

    def test_returns_none_without_local_admin(self):
        result = determine_auth_mode({"AUTH_METHOD": "abs"}, None, has_local_admin=False)
        assert result == "none"

    def test_disable_local_auth_substitutes_for_local_admin(self):
        result = determine_auth_mode(
            {"AUTH_METHOD": "abs"},
            None,
            has_local_admin=False,
            disable_local_auth=True,
        )
        assert result == AUTH_SOURCE_ABS

    def test_abs_mode_does_not_gate_on_abs_connection_config(self):
        # Fail closed: no AUDIOBOOKSHELF_* keys in security_config at all,
        # mode must still resolve to "abs" (never degrade to open "none").
        security_config = {"AUTH_METHOD": "abs", "OIDC_DISCOVERY_URL": "", "OIDC_CLIENT_ID": ""}
        assert determine_auth_mode(security_config, None, has_local_admin=True) == AUTH_SOURCE_ABS

    def test_abs_registered_in_auth_source_set(self):
        assert "abs" in AUTH_SOURCE_SET


class TestIsUserActiveForAuthModeAbs:
    def test_builtin_user_active_under_abs_mode(self):
        user = {"auth_source": "builtin", "oidc_subject": None}
        assert is_user_active_for_auth_mode(user, AUTH_SOURCE_ABS) is True

    def test_abs_user_active_under_abs_mode(self):
        user = {"auth_source": "abs", "oidc_subject": None}
        assert is_user_active_for_auth_mode(user, AUTH_SOURCE_ABS) is True

    def test_abs_user_inactive_under_builtin_mode(self):
        user = {"auth_source": "abs", "oidc_subject": None}
        assert is_user_active_for_auth_mode(user, "builtin") is False

    def test_cwa_user_inactive_under_abs_mode(self):
        user = {"auth_source": "cwa", "oidc_subject": None}
        assert is_user_active_for_auth_mode(user, AUTH_SOURCE_ABS) is False


@pytest.fixture
def user_db(tmp_path):
    db = UserDB(str(tmp_path / "users.db"))
    db.initialize()
    return db


class TestUserDbAbsSubject:
    def test_create_and_lookup_by_abs_subject(self, user_db):
        created = user_db.create_user(username="absuser", auth_source="abs", abs_subject="abs-id-1")
        found = user_db.get_user(abs_subject="abs-id-1")
        assert found is not None
        assert found["id"] == created["id"]
        assert found["abs_subject"] == "abs-id-1"
        assert found["auth_source"] == "abs"

    def test_abs_subject_must_be_unique(self, user_db):
        user_db.create_user(username="a1", auth_source="abs", abs_subject="dup")
        with pytest.raises(ValueError):
            user_db.create_user(username="a2", auth_source="abs", abs_subject="dup")

    def test_multiple_null_abs_subjects_allowed(self, user_db):
        user_db.create_user(username="plain1")
        user_db.create_user(username="plain2")  # both NULL abs_subject: fine

    def test_update_abs_subject(self, user_db):
        created = user_db.create_user(username="linkme")
        user_db.update_user(created["id"], abs_subject="abs-id-9", auth_source="abs")
        found = user_db.get_user(abs_subject="abs-id-9")
        assert found is not None
        assert found["id"] == created["id"]

    def test_migration_adds_column_to_legacy_db(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                username      TEXT UNIQUE NOT NULL,
                email         TEXT,
                display_name  TEXT,
                password_hash TEXT,
                oidc_subject  TEXT UNIQUE,
                auth_source   TEXT NOT NULL DEFAULT 'builtin',
                role          TEXT NOT NULL DEFAULT 'user',
                created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )"""
        )
        conn.execute("INSERT INTO users (username) VALUES ('olduser')")
        conn.commit()
        conn.close()

        db = UserDB(str(db_path))
        db.initialize()
        old = db.get_user(username="olduser")
        assert old is not None
        assert old["abs_subject"] is None
        db.update_user(old["id"], abs_subject="migrated-id")
        assert db.get_user(abs_subject="migrated-id") is not None
