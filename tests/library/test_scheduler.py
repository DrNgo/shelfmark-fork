"""Tests for refreshing the shared library index from every registered provider."""

from datetime import UTC, datetime, timedelta

from shelfmark.library.index import MEDIA_TYPE_EBOOK, SOURCE_GRIMMORY, LibraryItem
from shelfmark.library.scheduler import SyncResult, is_index_stale, sync_provider


class _StubProvider:
    source = SOURCE_GRIMMORY

    def __init__(self, items=None, error=None):
        self._items = items or []
        self._error = error

    def is_enabled(self):
        return True

    def interval_hours(self):
        return 1.0

    def fetch_items(self):
        if self._error is not None:
            raise self._error
        return self._items


def _item(item_id="1", title="The Housemaid"):
    return LibraryItem(
        source=SOURCE_GRIMMORY,
        item_id=item_id,
        library_id="lib_1",
        library_name="Ebooks",
        media_type=MEDIA_TYPE_EBOOK,
        title=title,
        subtitle="",
        author="Freida McFadden",
        asin="",
        isbn13="",
    )


class TestSyncProvider:
    def test_stores_what_the_provider_returns(self, index):
        result = sync_provider(_StubProvider([_item()]), index)

        assert result == SyncResult(
            success=True, item_count=1, message="Indexed 1 items from grimmory"
        )
        assert index.get_state(SOURCE_GRIMMORY).item_count == 1

    def test_a_failure_leaves_the_previous_index_standing(self, index):
        sync_provider(_StubProvider([_item()]), index)

        result = sync_provider(_StubProvider(error=OSError("connection refused")), index)

        assert result.success is False
        assert index.get_state(SOURCE_GRIMMORY).item_count == 1
        assert "connection refused" in index.get_state(SOURCE_GRIMMORY).last_error

    def test_a_storage_failure_is_reported_rather_than_raised(self, index, monkeypatch):
        # The settings "Sync Library Now" button and the scheduler both call
        # straight through here, so a locked or full database has to come back
        # as a result, not as an exception.
        def boom(*args, **kwargs):
            raise OSError("database is locked")

        monkeypatch.setattr(index, "replace_items", boom)

        result = sync_provider(_StubProvider([_item()]), index)

        assert result.success is False
        assert "database is locked" in result.message

    def test_a_successful_sync_with_no_items_empties_the_index(self, index):
        """Genuinely-zero results are a real answer; a raising provider is how failure looks."""
        sync_provider(_StubProvider([_item()]), index)

        result = sync_provider(_StubProvider([]), index)

        assert result == SyncResult(
            success=True, item_count=0, message="Indexed 0 items from grimmory"
        )
        assert index.get_state(SOURCE_GRIMMORY).item_count == 0


class TestIsIndexStale:
    """Staleness drives the periodic refresh."""

    def test_a_never_synced_index_is_stale(self):
        assert is_index_stale(None, interval_hours=1)

    def test_a_fresh_index_is_not_stale(self):
        recent = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()

        assert not is_index_stale(recent, interval_hours=1)

    def test_an_old_index_is_stale(self):
        old = (datetime.now(UTC) - timedelta(hours=3)).isoformat()

        assert is_index_stale(old, interval_hours=1)

    def test_an_unparseable_timestamp_is_stale(self):
        """Better to re-sync than to trust a timestamp we cannot read."""
        assert is_index_stale("not a date", interval_hours=1)
