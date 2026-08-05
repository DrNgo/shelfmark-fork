"""Tests for the authenticated Audible topic-tree endpoint."""

from unittest.mock import patch

import pytest

import shelfmark.main as main_module
from shelfmark.core.audible_topics import AudibleTopicTree
from shelfmark.metadata_providers.audible_taxonomy import AudibleTopicNode


@pytest.fixture
def client():
    main_module.app.config["TESTING"] = True
    with main_module.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def no_auth():
    with patch.object(main_module, "get_auth_mode", return_value="none"):
        yield


def _tree(*, stale: bool) -> AudibleTopicTree:
    child = AudibleTopicNode(
        name="Historical",
        path=("Romance", "Historical"),
        category_id="11",
    )
    root = AudibleTopicNode(
        name="Romance",
        path=("Romance",),
        category_id="10",
        children=(child,),
    )
    return AudibleTopicTree(region="us", tld="com", topics=(root,), stale=stale)


def test_auth_required_returns_401(client):
    with patch.object(main_module, "get_auth_mode", return_value="builtin"):
        response = client.get("/api/metadata/audible/topics")
    assert response.status_code == 401


def test_success_returns_browser_safe_public_tree_and_stale_flag(client, no_auth):
    with patch.object(
        main_module, "get_audible_topic_tree_service", return_value=_tree(stale=True)
    ):
        response = client.get("/api/metadata/audible/topics")
    assert response.status_code == 200
    assert response.get_json() == {
        "region": "us",
        "stale": True,
        "topics": [
            {
                "name": "Romance",
                "path": ["Romance"],
                "children": [
                    {
                        "name": "Historical",
                        "path": ["Romance", "Historical"],
                        "children": [],
                    }
                ],
            }
        ],
    }


def test_service_failure_returns_503(client, no_auth):
    with patch.object(main_module, "get_audible_topic_tree_service", return_value=None):
        response = client.get("/api/metadata/audible/topics")
    assert response.status_code == 503
    assert response.get_json() == {"error": "Audible topics are temporarily unavailable"}
