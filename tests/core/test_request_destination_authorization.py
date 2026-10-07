"""A destination key reaches the queue only from an admin (fork-only).

`authorize_destination_key` guarded `/api/releases/download` alone. A requester
could put a key in a request's `release_data` (top level or `extra`): a
download-policy submission queued it straight away, and a pending request
stored it, where a blank approval let `queue_release` revive the nested copy.
"""

from __future__ import annotations

import importlib
import os
import tempfile
import uuid
from typing import Any
from unittest.mock import patch

import pytest

from shelfmark.core.requests_service import fulfil_request
from shelfmark.core.user_db import UserDB


@pytest.fixture(scope="module")
def main_module():
    """Import `shelfmark.main` with background startup disabled."""
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


def _login(client, user: dict, *, is_admin: bool) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = user["username"]
        sess["db_user_id"] = user["id"]
        sess["is_admin"] = is_admin


def _create_user(main_module, *, role: str = "user") -> dict:
    return main_module.user_db.create_user(username=f"u-{uuid.uuid4().hex[:8]}", role=role)


def _policy(*, ebook: str, audiobook: str) -> dict:
    return {
        "REQUESTS_ENABLED": True,
        "REQUEST_POLICY_DEFAULT_EBOOK": ebook,
        "REQUEST_POLICY_DEFAULT_AUDIOBOOK": audiobook,
        "MAX_PENDING_REQUESTS_PER_USER": 20,
        "REQUESTS_ALLOW_NOTES": True,
        "REQUEST_POLICY_RULES": [],
    }


def _payload(content_type: str = "ebook") -> dict:
    tag = uuid.uuid4().hex[:8]
    return {
        "book_data": {
            "title": f"Planted {tag}",
            "author": "Shelfmark",
            "content_type": content_type,
            "provider": "openlibrary",
            "provider_id": f"planted-{tag}",
        },
        "context": {"source": "prowlarr", "content_type": content_type, "request_level": "release"},
        "release_data": {
            "source": "prowlarr",
            "source_id": f"planted-release-{tag}",
            "title": f"Planted {tag}.epub",
            "destination_key": "grimmory:5:8",
            "extra": {"destination_key": "grimmory:5:8", "indexer": "x"},
        },
    }


def _post(main_module, client, path, body, *, policy):
    queued: list[dict[str, Any]] = []

    def fake_queue_release(release_data, priority, user_id=None, username=None):
        queued.append(release_data)
        return True, None

    with (
        patch.object(main_module, "get_auth_mode", return_value="builtin"),
        patch.object(main_module, "load_users_request_policy_settings", return_value=policy),
        patch(
            "shelfmark.core.request_routes.load_users_request_policy_settings",
            return_value=policy,
        ),
        patch.object(main_module.backend, "queue_release", side_effect=fake_queue_release),
        patch("shelfmark.core.request_routes.notify_admin"),
        patch("shelfmark.core.request_routes.notify_user"),
    ):
        resp = client.post(path, json=body)
    return resp, queued


def _has_key(release_data: dict) -> bool:
    extra = release_data.get("extra") or {}
    return "destination_key" in release_data or "destination_key" in extra


class TestRequestSubmission:
    def test_a_stored_request_keeps_no_key(self, main_module, client):
        user = _create_user(main_module)
        _login(client, user, is_admin=False)

        resp, queued = _post(
            main_module,
            client,
            "/api/requests",
            _payload(),
            policy=_policy(ebook="request_release", audiobook="request_release"),
        )

        assert resp.status_code == 201, resp.json
        assert queued == []
        stored = main_module.user_db.get_request(resp.json["id"])
        assert not _has_key(stored["release_data"])
        assert stored["release_data"]["extra"] == {"indexer": "x"}

    def test_a_download_policy_submission_queues_no_key(self, main_module, client):
        user = _create_user(main_module)
        _login(client, user, is_admin=False)

        resp, queued = _post(
            main_module,
            client,
            "/api/requests",
            _payload(),
            policy=_policy(ebook="download", audiobook="download"),
        )

        assert resp.status_code == 200, resp.json
        assert len(queued) == 1
        assert not _has_key(queued[0])

    def test_a_batch_strips_both_policy_paths(self, main_module, client):
        user = _create_user(main_module)
        _login(client, user, is_admin=False)

        resp, queued = _post(
            main_module,
            client,
            "/api/requests/batch",
            {"requests": [_payload("ebook"), _payload("audiobook")]},
            policy=_policy(ebook="download", audiobook="request_release"),
        )

        assert resp.status_code == 201, resp.json
        assert len(queued) == 1
        assert not _has_key(queued[0])
        stored = [row for row in resp.json if row.get("kind") != "download"]
        assert len(stored) == 1
        assert not _has_key(main_module.user_db.get_request(stored[0]["id"])["release_data"])

    def test_an_admin_download_policy_submission_keeps_the_key(self, main_module, client):
        admin = _create_user(main_module, role="admin")
        _login(client, admin, is_admin=True)

        resp, queued = _post(
            main_module,
            client,
            "/api/requests",
            _payload(),
            policy=_policy(ebook="download", audiobook="download"),
        )

        assert resp.status_code == 200, resp.json
        assert queued[0]["destination_key"] == "grimmory:5:8"


@pytest.fixture
def user_db():
    with tempfile.TemporaryDirectory() as tmpdir:
        db = UserDB(os.path.join(tmpdir, "shelfmark.db"))
        db.initialize()
        yield db


class TestFulfilment:
    """Only the approving admin's explicit choice travels with an approval."""

    def approve(self, user_db: UserDB, **kwargs: Any) -> dict[str, Any]:
        requester = user_db.create_user("ada")
        admin = user_db.create_user("root", role="admin")
        # A row saved before submissions were sanitized, or written by hand.
        created = user_db.create_request(
            user_id=requester["id"],
            content_type="ebook",
            request_level="release",
            policy_mode="request_release",
            book_data={"title": "Overlord", "author": "Kugane Maruyama"},
            release_data={
                "source": "prowlarr",
                "source_id": "r1",
                "title": "Overlord.epub",
                "destination_key": "grimmory:9:9",
                "extra": {"destination_key": "grimmory:9:9", "indexer": "x"},
            },
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

    def test_a_blank_approval_revives_no_stored_key(self, user_db):
        queued = self.approve(user_db)

        assert queued["destination_key"] is None
        assert queued["extra"] == {"indexer": "x"}

    def test_the_approval_key_is_the_only_key(self, user_db):
        queued = self.approve(user_db, destination_key="grimmory:5:8")

        assert queued["destination_key"] == "grimmory:5:8"
        assert "destination_key" not in queued["extra"]

    def test_queue_release_sees_no_key_after_a_blank_approval(self, user_db, monkeypatch):
        from shelfmark.download import orchestrator

        captured = {}
        monkeypatch.setattr(orchestrator.config, "get", lambda _k, default=None, **_kw: default)
        monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _s: None)
        monkeypatch.setattr(orchestrator.book_queue, "add", lambda t: captured.setdefault("t", t))
        monkeypatch.setattr(orchestrator, "ws_manager", None)

        ok, error = orchestrator.queue_release(self.approve(user_db))

        assert ok, error
        assert captured["t"].destination_key is None
