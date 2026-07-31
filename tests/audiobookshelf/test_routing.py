"""Tests for routing a download to an explicitly chosen audiobook destination."""

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask
from shelfmark.download import orchestrator
from shelfmark.download.postprocess.destination import get_final_destination
from tests.audiobookshelf.test_destinations import patch_config


def audiobook_task(**overrides: Any) -> DownloadTask:
    defaults: dict[str, Any] = {
        "task_id": "task-1",
        "source": "direct",
        "title": "Some Audiobook",
        "content_type": "audiobook",
    }
    defaults.update(overrides)
    return DownloadTask(**defaults)


DESTINATION_CONFIG = {
    "DESTINATION": "/books",
    "DESTINATION_AUDIOBOOK": "/audiobooks",
    "AUDIOBOOK_DESTINATIONS": [
        {"key": "lib-fiction", "name": "Fiction", "path": "/audiobooks/fiction"},
        {"key": "lib-kids", "name": "Kids", "path": "/audiobooks/kids"},
    ],
}


class TestFinalDestinationRouting:
    """`get_final_destination` honours an explicit destination key."""

    def test_routes_an_audiobook_to_its_mapped_library_path(self):
        with patch_config(DESTINATION_CONFIG):
            resolved = get_final_destination(audiobook_task(destination_key="lib-kids"))

        assert resolved == Path("/audiobooks/kids")

    def test_falls_back_to_the_default_audiobook_destination_without_a_key(self):
        with patch_config(DESTINATION_CONFIG):
            resolved = get_final_destination(audiobook_task())

        assert resolved == Path("/audiobooks")

    def test_dangling_key_falls_back_instead_of_failing(self):
        """The library was removed from the map after the request was approved."""
        with patch_config(DESTINATION_CONFIG):
            resolved = get_final_destination(audiobook_task(destination_key="lib-deleted"))

        assert resolved == Path("/audiobooks")

    def test_expands_the_user_placeholder_in_the_mapped_path(self):
        config_values = dict(DESTINATION_CONFIG)
        config_values["AUDIOBOOK_DESTINATIONS"] = [
            {"key": "lib-fiction", "name": "Fiction", "path": "/audiobooks/{User}/fiction"}
        ]

        with patch_config(config_values):
            resolved = get_final_destination(
                audiobook_task(destination_key="lib-fiction", username="ada")
            )

        assert resolved == Path("/audiobooks/ada/fiction")

    def test_ignores_a_destination_key_on_an_ebook(self):
        """Multi-library routing is audiobooks-only; the ebook lane is untouched."""
        task = audiobook_task(content_type="book (fiction)", destination_key="lib-kids")

        with patch_config(DESTINATION_CONFIG):
            resolved = get_final_destination(task)

        assert resolved == Path("/books")


class TestQueueReleasePassesDestinationKey:
    """An admin's explicit choice survives the trip into the download queue."""

    def queue(self, release_data: dict[str, Any]) -> DownloadTask:
        queue = MagicMock()
        queue.add.return_value = True

        with (
            patch_config(DESTINATION_CONFIG),
            patch.object(orchestrator, "book_queue", queue),
            patch.object(orchestrator, "ws_manager", None),
            patch.object(orchestrator, "_source_unavailable_message", return_value=None),
            patch.object(orchestrator, "_build_retry_resolution_fields", return_value={}),
        ):
            success, error = orchestrator.queue_release(release_data)

        assert success, error
        return queue.add.call_args.args[0]

    def test_carries_the_destination_key_onto_the_task(self):
        task = self.queue(
            {
                "source": "direct",
                "source_id": "abc123",
                "title": "Some Audiobook",
                "content_type": "audiobook",
                "destination_key": "lib-kids",
            }
        )

        assert task.destination_key == "lib-kids"

    def test_leaves_the_destination_key_unset_when_not_chosen(self):
        task = self.queue(
            {
                "source": "direct",
                "source_id": "abc123",
                "title": "Some Audiobook",
                "content_type": "audiobook",
            }
        )

        assert task.destination_key is None

    def test_normalizes_a_blank_destination_key_to_none(self):
        task = self.queue(
            {
                "source": "direct",
                "source_id": "abc123",
                "title": "Some Audiobook",
                "content_type": "audiobook",
                "destination_key": "   ",
            }
        )

        assert task.destination_key is None
