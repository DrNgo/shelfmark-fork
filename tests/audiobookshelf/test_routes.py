"""Tests for the Audiobookshelf HTTP API."""

import pytest
from flask import Flask

from shelfmark.audiobookshelf.routes import register_audiobookshelf_routes
from tests.audiobookshelf.test_destinations import patch_config

DESTINATIONS = {
    "AUDIOBOOK_DESTINATIONS": [
        {"key": "lib-fiction", "name": "Fiction", "path": "/audiobooks/fiction"},
        {"key": "lib-kids", "name": "Kids", "path": "/audiobooks/kids"},
    ]
}


def build_client(auth_mode: str = "builtin"):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    register_audiobookshelf_routes(app, resolve_auth_mode=lambda: auth_mode)
    return app.test_client()


@pytest.fixture
def client():
    return build_client()


def as_admin(client, *, is_admin: bool = True):
    with client.session_transaction() as session:
        session["is_admin"] = is_admin
        session["db_user_id"] = 1
        session["user_id"] = "admin"
    return client


def as_user(client):
    with client.session_transaction() as session:
        session["is_admin"] = False
        session["db_user_id"] = 2
        session["user_id"] = "ada"
    return client


class TestListAudiobookDestinations:
    """`GET /api/audiobook-destinations` feeds the approve dialog's picker."""

    def test_lists_configured_destinations(self, client):
        as_admin(client)

        with patch_config(DESTINATIONS):
            response = client.get("/api/audiobook-destinations")

        assert response.status_code == 200
        assert response.get_json()["destinations"] == [
            {"key": "lib-fiction", "name": "Fiction"},
            {"key": "lib-kids", "name": "Kids"},
        ]

    def test_returns_an_empty_list_when_unconfigured(self, client):
        as_admin(client)

        with patch_config({}):
            response = client.get("/api/audiobook-destinations")

        assert response.status_code == 200
        assert response.get_json()["destinations"] == []

    def test_requires_admin(self, client):
        """Destination routing is admin-only in v1; requesters never see the picker."""
        as_admin(client, is_admin=False)

        with patch_config(DESTINATIONS):
            response = client.get("/api/audiobook-destinations")

        assert response.status_code == 403

    def test_never_exposes_local_paths(self, client):
        as_admin(client)

        with patch_config(DESTINATIONS):
            response = client.get("/api/audiobook-destinations")

        assert "/audiobooks/fiction" not in response.get_data(as_text=True)

    def test_serves_an_anonymous_caller_in_no_auth_mode(self):
        """Auth mode "none" means no accounts exist and every caller is a full
        admin — `/api/auth/check` says so, and the UI renders admin controls on
        that basis. Gating on a session flag that no one can have would hide the
        picker on the most common self-hosted setup.
        """
        client = build_client(auth_mode="none")

        with patch_config(DESTINATIONS):
            response = client.get("/api/audiobook-destinations")

        assert response.status_code == 200
        assert response.get_json()["destinations"] == [
            {"key": "lib-fiction", "name": "Fiction"},
            {"key": "lib-kids", "name": "Kids"},
        ]

    def test_still_requires_admin_when_auth_is_configured(self):
        """The "none" allowance must not leak into a mode that has real users."""
        client = build_client(auth_mode="builtin")
        as_user(client)

        with patch_config(DESTINATIONS):
            response = client.get("/api/audiobook-destinations")

        assert response.status_code == 403
