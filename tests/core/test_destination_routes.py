"""Tests for `GET /api/download-destinations`, the release modal and approve panel picker."""

from unittest.mock import patch

import pytest
from flask import Flask

from shelfmark.core.destination_routes import register_destination_routes
from tests.audiobookshelf.test_destinations import patch_config

AUDIOBOOK_DESTINATIONS = {
    "AUDIOBOOK_DESTINATIONS": [
        {"key": "lib-fiction", "name": "Fiction", "path": "/audiobooks/fiction"},
        {"key": "lib-kids", "name": "Kids", "path": "/audiobooks/kids"},
    ]
}

EBOOK_OPTIONS = [
    {"key": "grimmory:3:3", "name": "Fiction"},
    {"key": "grimmory:5:8", "name": "Light Novels"},
]


def build_client(auth_mode: str = "builtin"):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    register_destination_routes(app, resolve_auth_mode=lambda: auth_mode)
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


def ebook_options(options=EBOOK_OPTIONS):
    return patch(
        "shelfmark.core.destination_routes.get_booklore_destination_options",
        return_value=options,
    )


class TestAudiobookDestinations:
    """`content_type=audiobook` serves today's Audiobookshelf destination map."""

    def test_lists_configured_destinations(self, client):
        as_admin(client)

        with patch_config(AUDIOBOOK_DESTINATIONS):
            response = client.get("/api/download-destinations?content_type=audiobook")

        assert response.status_code == 200
        assert response.get_json() == {
            "destinations": [
                {"key": "lib-fiction", "name": "Fiction"},
                {"key": "lib-kids", "name": "Kids"},
            ],
            "default_name": "",
        }

    def test_returns_an_empty_list_when_unconfigured(self, client):
        as_admin(client)

        with patch_config({}):
            response = client.get("/api/download-destinations?content_type=audiobook")

        assert response.status_code == 200
        assert response.get_json()["destinations"] == []

    def test_never_exposes_local_paths(self, client):
        as_admin(client)

        with patch_config(AUDIOBOOK_DESTINATIONS):
            response = client.get("/api/download-destinations?content_type=audiobook")

        assert "/audiobooks/fiction" not in response.get_data(as_text=True)


class TestEbookDestinations:
    """`content_type=ebook` serves the Grimmory library paths."""

    def test_lists_the_grimmory_library_paths(self, client):
        as_admin(client)

        with ebook_options():
            response = client.get("/api/download-destinations?content_type=ebook")

        assert response.status_code == 200
        assert response.get_json() == {"destinations": EBOOK_OPTIONS, "default_name": ""}

    def test_an_unreachable_grimmory_is_an_empty_list(self, client):
        as_admin(client)

        with ebook_options([]):
            response = client.get("/api/download-destinations?content_type=ebook")

        assert response.status_code == 200
        assert response.get_json() == {"destinations": [], "default_name": ""}


class TestContentTypeParameter:
    def test_a_missing_content_type_is_a_400(self, client):
        as_admin(client)

        assert client.get("/api/download-destinations").status_code == 400

    def test_an_unknown_content_type_is_a_400(self, client):
        as_admin(client)

        response = client.get("/api/download-destinations?content_type=magazine")

        assert response.status_code == 400

    def test_content_type_is_case_sensitive_and_untrimmed(self, client):
        # Review Focus #5: only the exact values the frontend sends are accepted.
        as_admin(client)

        for value in ("Ebook", " ebook", "AUDIOBOOK"):
            response = client.get(f"/api/download-destinations?content_type={value}")
            assert response.status_code == 400, value

    def test_the_old_endpoint_is_gone(self, client):
        as_admin(client)

        assert client.get("/api/audiobook-destinations").status_code == 404


class TestAccess:
    """Destination routing is admin-only; auth mode "none" makes every caller an admin."""

    @pytest.mark.parametrize("content_type", ["ebook", "audiobook"])
    def test_requires_admin(self, client, content_type):
        as_admin(client, is_admin=False)

        with ebook_options(), patch_config(AUDIOBOOK_DESTINATIONS):
            response = client.get(f"/api/download-destinations?content_type={content_type}")

        assert response.status_code == 403

    def test_a_non_admin_gets_403_even_with_a_bad_content_type(self, client):
        as_admin(client, is_admin=False)

        assert client.get("/api/download-destinations?content_type=x").status_code == 403

    @pytest.mark.parametrize("content_type", ["ebook", "audiobook"])
    def test_serves_an_anonymous_caller_in_no_auth_mode(self, content_type):
        client = build_client(auth_mode="none")

        with ebook_options(), patch_config(AUDIOBOOK_DESTINATIONS):
            response = client.get(f"/api/download-destinations?content_type={content_type}")

        assert response.status_code == 200
        assert len(response.get_json()["destinations"]) == 2

    def test_still_requires_admin_when_auth_is_configured(self):
        client = build_client(auth_mode="builtin")

        with ebook_options():
            response = client.get("/api/download-destinations?content_type=ebook")

        assert response.status_code == 403
