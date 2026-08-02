"""Tests for HardcoverProvider discover fetchers (trending / new releases)."""

from unittest.mock import patch

import pytest

from shelfmark.metadata_providers.hardcover import HardcoverGraphQLError, HardcoverProvider


def _book(book_id: int, title: str = "Book") -> dict:
    return {
        "id": book_id,
        "title": f"{title} {book_id}",
        "slug": f"book-{book_id}",
        "release_date": "2026-06-01",
        "cached_image": {"url": f"https://img/{book_id}.jpg"},
        "contributions": [{"author": {"name": "Author Name"}}],
    }


@pytest.fixture
def provider() -> HardcoverProvider:
    return HardcoverProvider(api_key="test-key")


class TestDiscoverTrending:
    def test_returns_books_in_trending_id_order(self, provider):
        ids_payload = {"books_trending": {"error": None, "ids": [7, 3, 9]}}
        # Hydration returns a different order than the trending ids.
        books_payload = {"books": [_book(3), _book(9), _book(7)]}
        with patch.object(
            provider, "_execute_query", side_effect=[ids_payload, books_payload]
        ) as mock_q:
            result = provider.discover_trending(limit=3)
        assert result is not None
        assert [b.provider_id for b in result] == ["7", "3", "9"]
        # Step 1 over-fetches ids at 3x the row limit.
        assert mock_q.call_args_list[0].args[1]["limit"] == 9

    def test_respects_limit_after_reorder(self, provider):
        ids_payload = {"books_trending": {"error": None, "ids": [1, 2, 3]}}
        books_payload = {"books": [_book(1), _book(2), _book(3)]}
        with patch.object(provider, "_execute_query", side_effect=[ids_payload, books_payload]):
            result = provider.discover_trending(limit=2)
        assert result is not None
        assert len(result) == 2

    def test_trending_error_payload_returns_none(self, provider):
        ids_payload = {"books_trending": {"error": "boom", "ids": [1]}}
        with patch.object(provider, "_execute_query", return_value=ids_payload):
            assert provider.discover_trending() is None

    def test_transport_failure_returns_none(self, provider):
        with patch.object(provider, "_execute_query", side_effect=RuntimeError("down")):
            assert provider.discover_trending() is None

    def test_graphql_rejection_returns_none(self, provider):
        with patch.object(provider, "_execute_query", side_effect=HardcoverGraphQLError("no")):
            assert provider.discover_trending() is None

    def test_empty_ids_returns_empty_list(self, provider):
        ids_payload = {"books_trending": {"error": None, "ids": []}}
        with patch.object(provider, "_execute_query", return_value=ids_payload) as mock_q:
            assert provider.discover_trending() == []
        assert mock_q.call_count == 1  # no hydration call

    def test_malformed_record_is_skipped_not_fatal(self, provider):
        ids_payload = {"books_trending": {"error": None, "ids": [1, 2]}}
        # Book 1's contributions have a shape _parse_book chokes on.
        bad = _book(1)
        bad["contributions"] = [{"author": "not-a-dict"}]
        books_payload = {"books": [bad, _book(2)]}
        with patch.object(provider, "_execute_query", side_effect=[ids_payload, books_payload]):
            result = provider.discover_trending()
        assert result is not None
        assert [b.provider_id for b in result] == ["2"]

    def test_audio_only_uses_audio_hydration_query(self, provider):
        ids_payload = {"books_trending": {"error": None, "ids": [5]}}
        books_payload = {"books": [_book(5)]}
        with patch.object(
            provider, "_execute_query", side_effect=[ids_payload, books_payload]
        ) as mock_q:
            result = provider.discover_trending(audio_only=True)
        assert result is not None
        hydration_query = mock_q.call_args_list[1].args[0]
        assert "default_audio_edition_id" in hydration_query

    def test_no_api_key_returns_none(self):
        assert HardcoverProvider(api_key="").discover_trending() is None


class TestDiscoverNewReleases:
    def test_returns_parsed_books(self, provider):
        with patch.object(
            provider, "_execute_query", return_value={"books": [_book(1), _book(2)]}
        ) as mock_q:
            result = provider.discover_new_releases(limit=5)
        assert result is not None
        assert [b.provider_id for b in result] == ["1", "2"]
        variables = mock_q.call_args.args[1]
        assert variables["limit"] == 5
        assert variables["from"] < variables["to"]  # ISO date window

    def test_failure_returns_none(self, provider):
        with patch.object(provider, "_execute_query", side_effect=RuntimeError("down")):
            assert provider.discover_new_releases() is None

    def test_non_list_books_payload_returns_none(self, provider):
        with patch.object(provider, "_execute_query", return_value={"books": "garbage"}):
            assert provider.discover_new_releases() is None

    def test_empty_returns_empty_list(self, provider):
        with patch.object(provider, "_execute_query", return_value={"books": []}):
            assert provider.discover_new_releases() == []

    def test_audio_only_uses_audio_query(self, provider):
        with patch.object(
            provider, "_execute_query", return_value={"books": []}
        ) as mock_q:
            provider.discover_new_releases(audio_only=True)
        assert "default_audio_edition_id" in mock_q.call_args.args[0]
