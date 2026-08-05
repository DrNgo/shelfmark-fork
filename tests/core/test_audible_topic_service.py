"""Tests for Audible taxonomy caching, resolution, and path validation."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core import audible_topics as topic_service
from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers.audible_taxonomy import AudibleTopicNode


@pytest.fixture(autouse=True)
def clean_cache():
    get_metadata_cache().clear()
    yield
    get_metadata_cache().clear()


@pytest.fixture
def nodes() -> tuple[AudibleTopicNode, ...]:
    return (
        AudibleTopicNode(
            name="Romance",
            path=("Romance",),
            category_id="10",
            children=(
                AudibleTopicNode(
                    name="Historical",
                    path=("Romance", "Historical"),
                    category_id="11",
                ),
            ),
        ),
    )


@pytest.fixture
def provider() -> MagicMock:
    result = MagicMock()
    result.region = "us"
    result.tld = "com"
    return result


def test_success_writes_fresh_and_last_good_with_exact_ttls(provider, nodes):
    provider.fetch_topic_tree.return_value = nodes
    with (
        patch.object(topic_service, "get_provider", return_value=provider),
        patch.object(get_metadata_cache(), "set") as cache_set,
    ):
        result = topic_service.get_audible_topic_tree()
    assert result is not None and result.stale is False
    assert result.region == "us" and result.tld == "com"
    assert result.topics == nodes
    ttls = {call.args[0]: call.args[2] for call in cache_set.call_args_list}
    assert ttls["audible:topics:com:fresh"] == 24 * 3600
    assert ttls["audible:topics:com:last_good"] == 7 * 24 * 3600


def test_second_request_uses_fresh_cache(provider, nodes):
    provider.fetch_topic_tree.return_value = nodes
    with patch.object(topic_service, "get_provider", return_value=provider):
        first = topic_service.get_audible_topic_tree()
        second = topic_service.get_audible_topic_tree()
    assert first == second
    assert provider.fetch_topic_tree.call_count == 1


def test_empty_taxonomy_is_cached_as_success(provider):
    provider.fetch_topic_tree.return_value = ()
    with patch.object(topic_service, "get_provider", return_value=provider):
        first = topic_service.get_audible_topic_tree()
        second = topic_service.get_audible_topic_tree()
    assert first is not None and first.topics == () and first.stale is False
    assert second == first
    assert provider.fetch_topic_tree.call_count == 1


def test_failure_serves_last_good(provider, nodes):
    get_metadata_cache().set("audible:topics:com:last_good", nodes, 600)
    provider.fetch_topic_tree.return_value = None
    with patch.object(topic_service, "get_provider", return_value=provider):
        result = topic_service.get_audible_topic_tree()
    assert result is not None and result.stale is True
    assert result.topics == nodes


def test_failure_without_last_good_returns_none(provider):
    provider.fetch_topic_tree.return_value = None
    with patch.object(topic_service, "get_provider", return_value=provider):
        result = topic_service.get_audible_topic_tree()
    assert result is None


def test_cache_key_is_scoped_to_storefront(provider, nodes):
    provider.region = "de"
    provider.tld = "de"
    provider.fetch_topic_tree.return_value = nodes
    with patch.object(topic_service, "get_provider", return_value=provider):
        topic_service.get_audible_topic_tree()
    assert get_metadata_cache().get("audible:topics:de:fresh") == nodes
    assert get_metadata_cache().get("audible:topics:com:fresh") is None


class _SignallingLock:
    """A real lock that reports when the second caller reaches it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._guard = threading.Lock()
        self._attempts = 0
        self.second_waiter = threading.Event()

    def __enter__(self):
        with self._guard:
            self._attempts += 1
            if self._attempts == 2:
                self.second_waiter.set()
        self._lock.acquire()
        return self

    def __exit__(self, *exc: object) -> bool:
        self._lock.release()
        return False


def test_concurrent_cold_taxonomy_requests_fetch_once(provider, nodes):
    fetch_barrier = threading.Barrier(2)
    release_fetch = threading.Event()

    def paused_fetch():
        fetch_barrier.wait(timeout=5)
        assert release_fetch.wait(timeout=5)
        return nodes

    provider.fetch_topic_tree.side_effect = paused_fetch
    lock = _SignallingLock()
    results: list[topic_service.AudibleTopicTree | None] = []

    with (
        patch.object(topic_service, "get_provider", return_value=provider),
        patch.object(topic_service, "_lock_for", return_value=lock),
    ):
        first = threading.Thread(
            target=lambda: results.append(topic_service.get_audible_topic_tree())
        )
        second = threading.Thread(
            target=lambda: results.append(topic_service.get_audible_topic_tree())
        )
        first.start()
        fetch_barrier.wait(timeout=5)
        second.start()
        assert lock.second_waiter.wait(timeout=5)
        release_fetch.set()
        first.join(timeout=5)
        second.join(timeout=5)

    assert not first.is_alive() and not second.is_alive()
    assert len(results) == 2
    assert results[0] == results[1]
    assert provider.fetch_topic_tree.call_count == 1


def test_resolution_distinguishes_missing_from_failure(nodes):
    with patch.object(topic_service, "get_audible_topic_tree", return_value=None):
        assert topic_service.resolve_audible_topic(["Romance"]).failed is True
    tree = topic_service.AudibleTopicTree(region="us", tld="com", topics=nodes, stale=False)
    with patch.object(topic_service, "get_audible_topic_tree", return_value=tree):
        result = topic_service.resolve_audible_topic(["Missing"])
    assert result.failed is False and result.node is None


def test_resolution_returns_exact_node_and_propagates_stale(nodes):
    tree = topic_service.AudibleTopicTree(region="us", tld="com", topics=nodes, stale=True)
    with patch.object(topic_service, "get_audible_topic_tree", return_value=tree):
        result = topic_service.resolve_audible_topic([" Romance ", " Historical "])
    assert result.node == nodes[0].children[0]
    assert result.failed is False and result.stale is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ([" Romance ", " Historical "], ("Romance", "Historical")),
        (("Romance",), ("Romance",)),
        ([], ()),
        (None, None),
        ("Romance", None),
        ([""], None),
        (["Romance", 1], None),
        (["x"] * 9, None),
    ],
)
def test_normalize_audible_topic_path(value, expected):
    assert topic_service.normalize_audible_topic_path(value) == expected


def test_validate_empty_path_clears_selection():
    assert topic_service.validate_audible_topic_path([]) == ([], None)


def test_validate_resolved_path_returns_normalized_list(nodes):
    resolution = topic_service.AudibleTopicResolution(node=nodes[0].children[0], failed=False)
    with patch.object(topic_service, "resolve_audible_topic", return_value=resolution):
        value, error = topic_service.validate_audible_topic_path([" Romance ", " Historical "])
    assert value == ["Romance", "Historical"]
    assert error is None


@pytest.mark.parametrize("value", [None, "Romance", [""], ["x"] * 9])
def test_validate_malformed_path_returns_concrete_error(value):
    normalized, error = topic_service.validate_audible_topic_path(value)
    assert normalized == []
    assert isinstance(error, str) and error


def test_validate_missing_path_returns_concrete_error():
    resolution = topic_service.AudibleTopicResolution(node=None, failed=False)
    with patch.object(topic_service, "resolve_audible_topic", return_value=resolution):
        normalized, error = topic_service.validate_audible_topic_path(["Missing"])
    assert normalized == []
    assert isinstance(error, str) and "available" in error.lower()


def test_validate_unverifiable_path_returns_retry_error():
    resolution = topic_service.AudibleTopicResolution(node=None, failed=True)
    with patch.object(topic_service, "resolve_audible_topic", return_value=resolution):
        normalized, error = topic_service.validate_audible_topic_path(["Romance"])
    assert normalized == []
    assert isinstance(error, str) and "try again" in error.lower()


def test_path_digest_is_stable_and_segment_aware():
    assert topic_service.audible_topic_path_digest(["Romance", "Historical"]) == (
        topic_service.audible_topic_path_digest(("Romance", "Historical"))
    )
    assert topic_service.audible_topic_path_digest(["ab", "c"]) != (
        topic_service.audible_topic_path_digest(["a", "bc"])
    )
