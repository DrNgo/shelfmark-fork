"""Tests for the discover service: dispatch, gating, dual-entry caching, serve-stale."""

import threading
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core import discover
from shelfmark.core.audible_topics import AudibleTopicResolution
from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers import BookMetadata
from shelfmark.metadata_providers.audible_taxonomy import AudibleTopicNode


def _books(n: int) -> list[BookMetadata]:
    return [
        BookMetadata(provider="hardcover", provider_id=str(i), title=f"Book {i}") for i in range(n)
    ]


@pytest.fixture(autouse=True)
def clean_cache():
    get_metadata_cache().clear()
    yield
    get_metadata_cache().clear()


def _patch_provider(name: str, provider: MagicMock, *, enabled: bool = True):
    return (
        patch.object(discover, "get_configured_provider_name", return_value=name),
        patch.object(discover, "get_provider", return_value=provider),
        patch.object(discover, "get_provider_kwargs", return_value={}),
        patch.object(discover, "is_provider_enabled", return_value=enabled),
    )


def _hardcover_mock(trending=None, new_releases=None) -> MagicMock:
    provider = MagicMock()
    provider.is_available.return_value = True
    provider.discover_trending.return_value = trending
    provider.discover_new_releases.return_value = new_releases
    return provider


def _topic(category_id: str, *path: str) -> AudibleTopicNode:
    return AudibleTopicNode(
        name=path[-1],
        path=tuple(path),
        category_id=category_id,
    )


def _audible_mock(topic_books: list[BookMetadata]) -> MagicMock:
    provider = MagicMock()
    provider.region = "us"
    provider.tld = "com"
    provider.is_available.return_value = True
    provider.discover_topic.return_value = topic_books
    return provider


class TestDispatch:
    def test_unknown_provider_returns_none(self):
        p1, p2, p3, p4 = _patch_provider("openlibrary", MagicMock())
        with p1, p2, p3, p4:
            assert discover.get_discover_row("ebook", "trending") is None

    def test_unknown_row_returns_none(self):
        p1, p2, p3, p4 = _patch_provider("hardcover", _hardcover_mock())
        with p1, p2, p3, p4:
            assert discover.get_discover_row("ebook", "best_sellers") is None

    def test_disabled_provider_returns_none(self):
        provider = _hardcover_mock(trending=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider, enabled=False)
        with p1, p2, p3, p4:
            assert discover.get_discover_row("ebook", "trending") is None
        provider.discover_trending.assert_not_called()

    def test_unavailable_provider_returns_none(self):
        provider = _hardcover_mock(trending=_books(1))
        provider.is_available.return_value = False
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            assert discover.get_discover_row("ebook", "trending") is None
        provider.discover_trending.assert_not_called()

    def test_hardcover_audiobook_uses_audio_only(self):
        provider = _hardcover_mock(trending=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            row = discover.get_discover_row("audiobook", "trending")
        assert row is not None
        provider.discover_trending.assert_called_once_with(discover.ROW_LIMIT, audio_only=True)

    def test_combined_resolves_via_provider_name_helper(self):
        provider = _hardcover_mock(trending=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1 as name_mock, p2, p3, p4:
            row = discover.get_discover_row("combined", "trending", user_id=7)
        assert row is not None
        name_mock.assert_called_once_with("combined", user_id=7)
        # combined uses ebook-shaped rows
        provider.discover_trending.assert_called_once_with(discover.ROW_LIMIT, audio_only=False)

    def test_audible_best_sellers_dispatch(self):
        provider = MagicMock()
        provider.tld = "com"
        provider.is_available.return_value = True
        provider.discover_best_sellers.return_value = _books(2)
        p1, p2, p3, p4 = _patch_provider("audible", provider)
        with p1, p2, p3, p4:
            row = discover.get_discover_row("audiobook", "best_sellers")
        assert row is not None
        assert row.provider == "audible"
        assert len(row.books) == 2

    def test_combined_topic_uses_audiobook_provider_and_category(self):
        provider = _audible_mock(topic_books=_books(1))
        resolution = AudibleTopicResolution(
            node=_topic("99", "Science Fiction & Fantasy", "Fantasy"),
            failed=False,
        )
        with (
            patch.object(
                discover, "get_configured_provider_name", return_value="audible"
            ) as configured,
            patch.object(discover, "get_provider", return_value=provider),
            patch.object(discover, "get_provider_kwargs", return_value={}),
            patch.object(discover, "is_provider_enabled", return_value=True),
            patch.object(discover, "resolve_audible_topic", return_value=resolution),
        ):
            row = discover.get_discover_row("combined", "topic_fantasy", user_id=7)
        assert row is not None and row.label == "Fantasy"
        configured.assert_called_once_with("audiobook", user_id=7)
        provider.discover_topic.assert_called_once_with("99", discover.ROW_LIMIT)

    def test_book_view_never_resolves_topic(self):
        with patch.object(discover, "resolve_audible_topic") as resolve:
            row = discover.get_discover_row("ebook", "topic_fantasy", user_id=7)
        assert row is None
        resolve.assert_not_called()

    def test_preferred_topic_reads_user_scoped_setting(self):
        provider = _audible_mock(topic_books=_books(1))
        resolution = AudibleTopicResolution(
            node=_topic("101", "Romance", "Historical"),
            failed=False,
        )
        with (
            patch.object(discover, "get_configured_provider_name", return_value="audible"),
            patch.object(discover, "get_provider", return_value=provider),
            patch.object(discover, "get_provider_kwargs", return_value={}),
            patch.object(discover, "is_provider_enabled", return_value=True),
            patch.object(discover, "resolve_audible_topic", return_value=resolution),
            patch.object(
                discover.app_config,
                "get",
                return_value=["Romance", "Historical"],
            ) as config_get,
        ):
            row = discover.get_discover_row("audiobook", "preferred_topic", user_id=8)
        assert row is not None and row.label == "Romance — Historical"
        config_get.assert_called_once_with("DEFAULT_DISCOVER_TOPIC", [], user_id=8)

    @pytest.mark.parametrize("enabled,available", [(False, True), (True, False)])
    def test_topic_requires_enabled_available_audible(self, enabled, available):
        provider = _audible_mock(topic_books=_books(1))
        provider.is_available.return_value = available
        with (
            patch.object(discover, "get_configured_provider_name", return_value="audible"),
            patch.object(discover, "get_provider", return_value=provider),
            patch.object(discover, "get_provider_kwargs", return_value={}),
            patch.object(discover, "is_provider_enabled", return_value=enabled),
            patch.object(discover, "resolve_audible_topic") as resolve,
        ):
            row = discover.get_discover_row("audiobook", "topic_fantasy", user_id=9)
        assert row is None
        resolve.assert_not_called()

    def test_topic_rejects_non_audible_audiobook_provider(self):
        with (
            patch.object(discover, "get_configured_provider_name", return_value="hardcover"),
            patch.object(discover, "resolve_audible_topic") as resolve,
        ):
            row = discover.get_discover_row("audiobook", "topic_fantasy", user_id=9)
        assert row is None
        resolve.assert_not_called()


class TestCaching:
    def test_second_call_hits_cache(self):
        provider = _hardcover_mock(trending=_books(3))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            first = discover.get_discover_row("ebook", "trending")
            second = discover.get_discover_row("ebook", "trending")
        assert first is not None and second is not None
        assert provider.discover_trending.call_count == 1
        assert second.stale is False

    def test_success_writes_fresh_and_last_good_with_ttls(self):
        provider = _hardcover_mock(trending=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4, patch.object(get_metadata_cache(), "set") as mock_set:
            discover.get_discover_row("ebook", "trending")
        calls = {c.args[0]: c.args[2] for c in mock_set.call_args_list}
        assert calls["discover:hardcover:trending:all:fresh"] == discover.ROW_TTLS["trending"]
        assert calls["discover:hardcover:trending:all:last_good"] == discover.LAST_GOOD_TTL

    def test_new_releases_uses_24h_ttl(self):
        provider = _hardcover_mock(new_releases=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4, patch.object(get_metadata_cache(), "set") as mock_set:
            discover.get_discover_row("ebook", "new_releases")
        ttls = [c.args[2] for c in mock_set.call_args_list]
        assert discover.ROW_TTLS["new_releases"] in ttls
        assert discover.ROW_TTLS["new_releases"] == 24 * 3600

    def test_empty_result_is_cached_success(self):
        provider = _hardcover_mock(trending=[])
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            row = discover.get_discover_row("ebook", "trending")
            again = discover.get_discover_row("ebook", "trending")
        assert row is not None and row.books == [] and row.stale is False
        assert again is not None and again.books == []
        assert provider.discover_trending.call_count == 1

    def test_failure_serves_last_good_as_stale(self):
        books = _books(2)
        provider = _hardcover_mock(trending=books)
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            discover.get_discover_row("ebook", "trending")
        # Simulate fresh expiry while last_good survives.
        get_metadata_cache().invalidate("discover:hardcover:trending:all:fresh")
        provider.discover_trending.return_value = None
        with p1, p2, p3, p4:
            row = discover.get_discover_row("ebook", "trending")
        assert row is not None
        assert row.stale is True
        assert [b.provider_id for b in row.books] == ["0", "1"]

    def test_failure_without_stale_returns_empty_uncached(self):
        provider = _hardcover_mock(trending=None)
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            row = discover.get_discover_row("ebook", "trending")
            row2 = discover.get_discover_row("ebook", "trending")
        assert row is not None and row.books == []
        # Failures are never cached: a retry re-fetches.
        assert provider.discover_trending.call_count == 2
        assert row2 is not None and row2.books == []

    def test_audible_cache_key_includes_region(self):
        provider = MagicMock()
        provider.tld = "de"
        provider.is_available.return_value = True
        provider.discover_best_sellers.return_value = _books(1)
        p1, p2, p3, p4 = _patch_provider("audible", provider)
        with p1, p2, p3, p4:
            discover.get_discover_row("audiobook", "best_sellers")
        assert get_metadata_cache().get("discover:audible:de:best_sellers:fresh") is not None

    def test_hardcover_audio_variant_cached_separately(self):
        provider = _hardcover_mock(trending=_books(1))
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        with p1, p2, p3, p4:
            discover.get_discover_row("ebook", "trending")
            discover.get_discover_row("audiobook", "trending")
        assert provider.discover_trending.call_count == 2

    def test_topic_uses_6h_ttl(self):
        provider = _audible_mock(topic_books=_books(1))
        resolution = AudibleTopicResolution(
            node=_topic("99", "Science Fiction & Fantasy", "Fantasy"),
            failed=False,
        )
        p1, p2, p3, p4 = _patch_provider("audible", provider)
        with (
            p1,
            p2,
            p3,
            p4,
            patch.object(discover, "resolve_audible_topic", return_value=resolution),
            patch.object(get_metadata_cache(), "set") as mock_set,
        ):
            discover.get_discover_row("audiobook", "topic_fantasy")
        calls = {call.args[0]: call.args[2] for call in mock_set.call_args_list}
        assert calls["discover:audible:com:topic_fantasy:99:fresh"] == 6 * 3600
        assert calls["discover:audible:com:topic_fantasy:99:last_good"] == discover.LAST_GOOD_TTL

    def test_missing_topic_category_caches_empty_success(self):
        provider = _audible_mock(topic_books=_books(1))
        hit = AudibleTopicResolution(
            node=_topic("99", "Science Fiction & Fantasy", "Fantasy"),
            failed=False,
        )
        miss = AudibleTopicResolution(node=None, failed=False)
        p1, p2, p3, p4 = _patch_provider("audible", provider)
        with p1, p2, p3, p4, patch.object(discover, "resolve_audible_topic", return_value=hit):
            discover.get_discover_row("audiobook", "topic_fantasy")
        with p1, p2, p3, p4, patch.object(discover, "resolve_audible_topic", return_value=miss):
            row = discover.get_discover_row("audiobook", "topic_fantasy")
            again = discover.get_discover_row("audiobook", "topic_fantasy")
        assert row is not None and row.books == [] and row.stale is False
        assert again is not None and again.books == []
        assert provider.discover_topic.call_count == 1
        assert (
            get_metadata_cache().get("discover:audible:com:topic_fantasy:unsupported:fresh") == []
        )

    def test_topic_fetch_failure_serves_last_good(self):
        books = _books(2)
        provider = _audible_mock(topic_books=books)
        resolution = AudibleTopicResolution(
            node=_topic("99", "Science Fiction & Fantasy", "Fantasy"),
            failed=False,
        )
        p1, p2, p3, p4 = _patch_provider("audible", provider)
        with (
            p1,
            p2,
            p3,
            p4,
            patch.object(discover, "resolve_audible_topic", return_value=resolution),
        ):
            discover.get_discover_row("audiobook", "topic_fantasy")
        get_metadata_cache().invalidate("discover:audible:com:topic_fantasy:99:fresh")
        provider.discover_topic.return_value = None
        with (
            p1,
            p2,
            p3,
            p4,
            patch.object(discover, "resolve_audible_topic", return_value=resolution),
        ):
            row = discover.get_discover_row("audiobook", "topic_fantasy")
        assert row is not None and row.stale is True
        assert [book.provider_id for book in row.books] == ["0", "1"]

    def test_taxonomy_failure_returns_empty_without_caching(self):
        provider = _audible_mock(topic_books=_books(1))
        resolution = AudibleTopicResolution(node=None, failed=True)
        p1, p2, p3, p4 = _patch_provider("audible", provider)
        with (
            p1,
            p2,
            p3,
            p4,
            patch.object(discover, "resolve_audible_topic", return_value=resolution) as resolve,
        ):
            row = discover.get_discover_row("audiobook", "topic_fantasy")
            again = discover.get_discover_row("audiobook", "topic_fantasy")
        assert row is not None and row.books == [] and row.stale is False
        assert again is not None and again.books == []
        assert resolve.call_count == 2
        provider.discover_topic.assert_not_called()
        assert (
            get_metadata_cache().get("discover:audible:com:topic_fantasy:unsupported:fresh") is None
        )

    def test_category_id_change_uses_different_cache_key(self):
        provider = _audible_mock(topic_books=_books(1))
        resolutions = [
            AudibleTopicResolution(
                node=_topic("99", "Science Fiction & Fantasy", "Fantasy"),
                failed=False,
            ),
            AudibleTopicResolution(
                node=_topic("100", "Science Fiction & Fantasy", "Fantasy"),
                failed=False,
            ),
        ]
        p1, p2, p3, p4 = _patch_provider("audible", provider)
        with (
            p1,
            p2,
            p3,
            p4,
            patch.object(discover, "resolve_audible_topic", side_effect=resolutions),
        ):
            discover.get_discover_row("audiobook", "topic_fantasy")
            discover.get_discover_row("audiobook", "topic_fantasy")
        assert provider.discover_topic.call_args_list == [
            (("99", discover.ROW_LIMIT),),
            (("100", discover.ROW_LIMIT),),
        ]
        assert get_metadata_cache().get("discover:audible:com:topic_fantasy:99:fresh") is not None
        assert get_metadata_cache().get("discover:audible:com:topic_fantasy:100:fresh") is not None

    def test_users_with_same_preferred_path_share_cache_entry(self):
        provider = _audible_mock(topic_books=_books(1))
        path = ["Romance", "Historical"]
        resolution = AudibleTopicResolution(
            node=_topic("101", *path),
            failed=False,
        )
        p1, p2, p3, p4 = _patch_provider("audible", provider)
        with (
            p1,
            p2,
            p3,
            p4,
            patch.object(discover, "resolve_audible_topic", return_value=resolution),
            patch.object(discover.app_config, "get", return_value=path) as config_get,
        ):
            first = discover.get_discover_row("audiobook", "preferred_topic", user_id=7)
            second = discover.get_discover_row("audiobook", "preferred_topic", user_id=8)
        assert first is not None and second is not None
        assert provider.discover_topic.call_count == 1
        assert config_get.call_args_list == [
            (("DEFAULT_DISCOVER_TOPIC", []), {"user_id": 7}),
            (("DEFAULT_DISCOVER_TOPIC", []), {"user_id": 8}),
        ]


class _SignallingLock:
    """Context-manager lock that reports when a SECOND acquirer is waiting.

    Lets the test prove the race actually happened: caller 2 must be blocked
    at the lock while caller 1's fetch is still in flight — otherwise the test
    could pass on an ordinary sequential cache hit.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._guard = threading.Lock()
        self._attempts = 0
        self.second_waiter = threading.Event()

    def __enter__(self):
        with self._guard:
            self._attempts += 1
            if self._attempts >= 2:
                self.second_waiter.set()
        self._lock.acquire()
        return self

    def __exit__(self, *exc: object) -> bool:
        self._lock.release()
        return False


class TestSingleFlight:
    def test_concurrent_cold_requests_fetch_once(self):
        release = threading.Event()
        fetch_started = threading.Event()

        def slow_fetch(*args, **kwargs):
            fetch_started.set()
            release.wait(timeout=5)
            return _books(1)

        provider = MagicMock()
        provider.is_available.return_value = True
        provider.discover_trending.side_effect = slow_fetch

        lock = _SignallingLock()
        p1, p2, p3, p4 = _patch_provider("hardcover", provider)
        results: list = []
        with p1, p2, p3, p4, patch.object(discover, "_lock_for", return_value=lock):
            t1 = threading.Thread(
                target=lambda: results.append(discover.get_discover_row("ebook", "trending"))
            )
            t2 = threading.Thread(
                target=lambda: results.append(discover.get_discover_row("ebook", "trending"))
            )
            t1.start()
            assert fetch_started.wait(timeout=5)  # t1 holds the lock, fetch in flight
            t2.start()
            assert lock.second_waiter.wait(timeout=5)  # t2 is blocked at the lock
            release.set()  # only now may t1 finish and populate the cache
            t1.join(timeout=5)
            t2.join(timeout=5)

        assert len(results) == 2
        assert all(r is not None and len(r.books) == 1 for r in results)
        assert provider.discover_trending.call_count == 1
