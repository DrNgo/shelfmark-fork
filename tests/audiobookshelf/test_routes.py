"""Tests for the Audiobookshelf HTTP API."""

import pytest
from flask import Flask

from shelfmark.audiobookshelf.routes import register_audiobookshelf_routes
from shelfmark.library import index as library_index
from shelfmark.library.index import MEDIA_TYPE_AUDIOBOOK, SOURCE_AUDIOBOOKSHELF, LibraryItem
from tests.audiobookshelf.test_destinations import patch_config

DESTINATIONS = {
    "AUDIOBOOK_DESTINATIONS": [
        {"key": "lib-fiction", "name": "Fiction", "path": "/audiobooks/fiction"},
        {"key": "lib-kids", "name": "Kids", "path": "/audiobooks/kids"},
    ]
}

INDEX_ENABLED = {"AUDIOBOOKSHELF_ENABLED": True, "AUDIOBOOKSHELF_LIBRARY_INDEX_ENABLED": True}


def build_client(auth_mode: str = "builtin"):
    app = Flask(__name__)
    app.secret_key = "test-secret"
    register_audiobookshelf_routes(app, resolve_auth_mode=lambda: auth_mode)
    return app.test_client()


@pytest.fixture
def client():
    return build_client()


@pytest.fixture
def indexed_library(tmp_path, monkeypatch):
    """Point the process-wide index at a temp database holding one book."""
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(library_index, "_index", None)

    index = library_index.get_library_index()
    index.replace_items(
        SOURCE_AUDIOBOOKSHELF,
        [
            LibraryItem(
                source=SOURCE_AUDIOBOOKSHELF,
                item_id="li_1",
                library_id="lib_books",
                library_name="Audiobooks",
                media_type=MEDIA_TYPE_AUDIOBOOK,
                title="The Housemaid",
                subtitle="",
                author="Freida McFadden",
                asin="B0BSHZ1234",
                isbn13="",
            )
        ],
    )
    yield index
    library_index._index = None


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


class TestLookupLibraryMatches:
    """`POST /api/library-matches` is what puts the badge on a search result."""

    def test_reports_a_book_already_in_the_library(self, client, indexed_library):
        """`content_type` here is the post-Task-11/12 payload shape.

        The current frontend (`buildLibraryLookupPayload` / `singleBookLookup`
        in libraryMatches.ts) does not send `content_type` yet — that wiring
        lands in Task 11 (payload builders) and Task 12 Step 5 (threading it
        through DetailsModal, RequestConfirmationModal, ActivityCard). A book
        without `content_type` classifies as an ebook by design (see
        `lookup.py::_requested_media_type`), so until those tasks land, an
        audiobook holding badges only book payloads that already carry
        `content_type: "audiobook"` — see
        `test_a_book_without_a_content_type_is_treated_as_an_ebook` below for
        today's actual, honest behavior with no `content_type` on the wire.
        """
        del indexed_library
        as_user(client)

        with patch_config(INDEX_ENABLED):
            response = client.post(
                "/api/library-matches",
                json={
                    "books": [
                        {
                            "id": "bk1",
                            "title": "The Housemaid",
                            "author": "Freida McFadden",
                            "content_type": "audiobook",
                        },
                        {
                            "id": "bk2",
                            "title": "The Coworker",
                            "author": "Freida McFadden",
                            "content_type": "audiobook",
                        },
                    ]
                },
            )

        assert response.status_code == 200
        payload = response.get_json()
        assert payload["enabled"] is True
        assert payload["matches"]["bk1"]["libraries"] == ["Audiobooks"]
        assert "bk2" not in payload["matches"]

    def test_a_book_without_a_content_type_is_treated_as_an_ebook(self, client, indexed_library):
        """Documents the interim gap: today's frontend sends no `content_type`.

        Until Task 11/12 wire it through, every book payload arrives without
        `content_type`, so it classifies as an ebook (`lookup.py`'s documented
        default) regardless of what format is actually held. An audiobook-only
        holding must therefore report no same-format `items` — the badge and
        acquire lock stay off — while still surfacing the holding as an
        advisory `other_formats` entry. This test pins that honest behavior so
        it cannot be mistaken for "audiobook badges already work end to end,"
        and it keeps passing unchanged once Tasks 11-12 add `content_type` to
        real requests, since it never sends one itself.
        """
        del indexed_library
        as_user(client)

        with patch_config(INDEX_ENABLED):
            response = client.post(
                "/api/library-matches",
                json={
                    "books": [
                        {"id": "bk1", "title": "The Housemaid", "author": "Freida McFadden"},
                    ]
                },
            )

        assert response.status_code == 200
        payload = response.get_json()
        match = payload["matches"]["bk1"]
        assert match["items"] == []
        assert match["libraries"] == []
        assert len(match["other_formats"]) == 1
        assert match["other_formats"][0]["source"] == "audiobookshelf"

    def test_is_available_to_requesters_not_just_admins(self, client, indexed_library):
        """The point is that a requester sees "you already have this" first."""
        del indexed_library
        as_user(client)

        with patch_config(INDEX_ENABLED):
            response = client.post(
                "/api/library-matches",
                json={"books": [{"id": "bk1", "title": "The Housemaid", "author": "F. McFadden"}]},
            )

        assert response.status_code == 200

    def test_requires_a_session_when_auth_is_configured(self, indexed_library):
        del indexed_library
        client = build_client(auth_mode="builtin")

        with patch_config(INDEX_ENABLED):
            response = client.post("/api/library-matches", json={"books": []})

        assert response.status_code == 401

    def test_allows_anonymous_access_in_no_auth_mode(self, indexed_library):
        del indexed_library
        client = build_client(auth_mode="none")

        with patch_config(INDEX_ENABLED):
            response = client.post("/api/library-matches", json={"books": []})

        assert response.status_code == 200

    def test_rejects_a_body_that_is_not_an_object(self, client, indexed_library):
        del indexed_library
        as_user(client)

        with patch_config(INDEX_ENABLED):
            response = client.post("/api/library-matches", json=["nope"])

        assert response.status_code == 400

    def test_reports_disabled_rather_than_failing(self, client, indexed_library):
        """Audiobookshelf off must return a clean "no badges", not an error."""
        del indexed_library
        as_user(client)

        with patch_config({"AUDIOBOOKSHELF_ENABLED": False}):
            response = client.post(
                "/api/library-matches",
                json={"books": [{"id": "bk1", "title": "The Housemaid", "author": "F. McFadden"}]},
            )

        assert response.status_code == 200
        assert response.get_json() == {
            "enabled": False,
            "stale": False,
            "last_sync_at": None,
            "sources": {},
            "matches": {},
        }
