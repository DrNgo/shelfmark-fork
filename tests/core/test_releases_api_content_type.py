"""/api/releases hands the request's content type to the search plan, and reports a
search that was cut short.

The fallback ladder is ebook-only, and the plan can only know which kind of search it
is building if the endpoint tells it. A source that ran out of time keeps what it found;
`search_info` tells the caller those releases are partial.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

import shelfmark.core.search_plan as search_plan


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


class _Source:
    def search(self, book, plan, *, expand_search=False, content_type="ebook"):
        return []

    def get_column_config(self):
        from shelfmark.release_sources import _default_column_config

        return _default_column_config()


@pytest.mark.parametrize(
    ("query_content_type", "expected"),
    [("ebook", "ebook"), ("audiobook", "audiobook"), (None, "ebook")],
)
def test_the_plan_is_built_for_the_requested_content_type(
    client, main_module, query_content_type, expected
):
    with client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7

    seen: list[object] = []
    real_build = search_plan.build_release_search_plan

    def _spy(*args, **kwargs):
        seen.append(kwargs.get("content_type"))
        return real_build(*args, **kwargs)

    query = {"provider": "manual", "book_id": "abc", "title": "Dune"}
    if query_content_type is not None:
        query["content_type"] = query_content_type

    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch.object(search_plan, "build_release_search_plan", _spy),
        patch(
            "shelfmark.release_sources.list_available_sources",
            return_value=[{"name": "prowlarr", "enabled": True}],
        ),
        patch("shelfmark.release_sources.get_source", return_value=_Source()),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        resp = client.get("/api/releases", query_string=query)

    assert resp.status_code == 200
    assert seen == [expected]


class _IncompleteSource(_Source):
    last_search_type = "categories"
    last_search_incomplete = True

    def search(self, book, plan, *, expand_search=False, content_type="ebook"):
        from shelfmark.release_sources import Release

        return [Release(source="prowlarr", source_id="p1", title="Dune (epub)")]


def test_an_incomplete_search_is_reported_with_its_releases(client, main_module):
    with client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7

    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch(
            "shelfmark.release_sources.list_available_sources",
            return_value=[{"name": "prowlarr", "enabled": True}],
        ),
        patch("shelfmark.release_sources.get_source", return_value=_IncompleteSource()),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        resp = client.get(
            "/api/releases", query_string={"provider": "manual", "book_id": "abc", "title": "Dune"}
        )

    body = resp.get_json()
    assert resp.status_code == 200
    assert [r["title"] for r in body["releases"]] == ["Dune (epub)"]
    assert body["search_info"] == {"prowlarr": {"search_type": "categories", "incomplete": True}}


def test_a_complete_search_carries_no_incomplete_flag(client, main_module):
    with client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7

    complete = _IncompleteSource()
    complete.last_search_incomplete = False
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch(
            "shelfmark.release_sources.list_available_sources",
            return_value=[{"name": "prowlarr", "enabled": True}],
        ),
        patch("shelfmark.release_sources.get_source", return_value=complete),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        resp = client.get(
            "/api/releases", query_string={"provider": "manual", "book_id": "abc", "title": "Dune"}
        )

    assert resp.get_json()["search_info"] == {"prowlarr": {"search_type": "categories"}}
