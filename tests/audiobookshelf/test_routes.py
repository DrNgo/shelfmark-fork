"""Tests for the audiobook destinations API used by the approve dialog."""

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


@pytest.fixture
def client():
    app = Flask(__name__)
    app.secret_key = "test-secret"
    register_audiobookshelf_routes(app)
    return app.test_client()


def as_admin(client, *, is_admin: bool = True):
    with client.session_transaction() as session:
        session["is_admin"] = is_admin
        session["db_user_id"] = 1
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
