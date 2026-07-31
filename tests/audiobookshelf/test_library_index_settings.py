"""Tests for the library index controls on the Audiobookshelf settings tab."""

from unittest.mock import patch

from shelfmark.audiobookshelf import settings
from shelfmark.audiobookshelf.library_sync import SyncResult


def field_map():
    return {getattr(field, "key", None): field for field in settings.audiobookshelf_settings()}


class TestLibraryIndexFields:
    """The tab has to expose the index's switch, cadence, and a manual sync."""

    def test_exposes_an_index_toggle_defaulting_to_on(self):
        field = field_map()["AUDIOBOOKSHELF_LIBRARY_INDEX_ENABLED"]

        assert field.default is True
        assert field.show_when == {"field": "AUDIOBOOKSHELF_ENABLED", "value": True}

    def test_exposes_a_refresh_interval_that_cannot_be_set_below_an_hour(self):
        """A tighter loop would re-walk every library for no new information."""
        field = field_map()["AUDIOBOOKSHELF_INDEX_INTERVAL_HOURS"]

        assert field.default == 1
        assert field.min_value == 1

    def test_exposes_a_sync_now_button(self):
        field = field_map()["sync_audiobookshelf_library_index"]

        assert field.callback is settings.sync_library_index_now


class TestSyncLibraryIndexNow:
    """The manual sync is how an admin proves the connection actually indexes."""

    def test_reports_the_indexed_count_on_success(self):
        with patch(
            "shelfmark.audiobookshelf.library_sync.run_sync_now",
            return_value=SyncResult(success=True, item_count=412, message="Indexed 412 items"),
        ):
            result = settings.sync_library_index_now()

        assert result["success"] is True
        assert "412" in result["message"]

    def test_reports_why_a_sync_failed(self):
        with patch(
            "shelfmark.audiobookshelf.library_sync.run_sync_now",
            return_value=SyncResult(success=False, item_count=0, message="Connection refused"),
        ):
            result = settings.sync_library_index_now()

        assert result["success"] is False
        assert result["message"] == "Connection refused"

    def test_surfaces_an_unexpected_failure_instead_of_raising(self):
        """A settings button that 500s tells the admin nothing about their setup."""
        with patch(
            "shelfmark.audiobookshelf.library_sync.run_sync_now",
            side_effect=OSError("disk full"),
        ):
            result = settings.sync_library_index_now()

        assert result["success"] is False
        assert "disk full" in result["message"]
