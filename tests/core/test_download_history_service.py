"""Tests for persisted download-history helpers."""

from __future__ import annotations

import os
import tempfile

from shelfmark.core.download_history_service import DownloadHistoryService
from shelfmark.core.user_db import UserDB


def test_iso_to_epoch_treats_naive_sqlite_timestamp_as_utc():
    epoch = DownloadHistoryService._iso_to_epoch("2026-01-02 03:04:05")
    assert epoch == 1767323045.0


def test_record_download_stores_utc_iso_timestamps():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = DownloadHistoryService(db_path)

        service.record_download(
            task_id="task-1",
            user_id=None,
            username=None,
            request_id=None,
            source="direct_download",
            source_display_name="Direct Download",
            title="Example",
            author=None,
            file_format=None,
            size=None,
            preview=None,
            content_type="ebook",
            origin="direct",
        )

        conn = user_db._connect()
        try:
            row = conn.execute(
                "SELECT queued_at, terminal_at FROM download_history WHERE task_id = ?",
                ("task-1",),
            ).fetchone()
        finally:
            conn.close()

        assert row is not None
        assert "+00:00" in row["queued_at"]
        assert "+00:00" in row["terminal_at"]


def test_cover_aspect_survives_into_the_download_payload():
    """Square audiobook art must outlive the queue: terminal downloads are served
    from the persisted row, not from the live task."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = DownloadHistoryService(db_path)

        service.record_download(
            task_id="task-1",
            user_id=None,
            username=None,
            request_id=None,
            source="prowlarr",
            source_display_name="Prowlarr",
            title="Example Audiobook",
            author=None,
            file_format="m4b",
            size=None,
            preview="https://example.invalid/cover.jpg",
            content_type="audiobook",
            cover_aspect="square",
            origin="direct",
        )
        service.finalize_download(task_id="task-1", final_status="complete")

        row = service.get_by_task_id("task-1")
        assert row is not None
        assert DownloadHistoryService.to_download_payload(row)["cover_aspect"] == "square"


def test_download_payload_reports_no_cover_aspect_when_unset():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = DownloadHistoryService(db_path)

        service.record_download(
            task_id="task-1",
            user_id=None,
            username=None,
            request_id=None,
            source="direct_download",
            source_display_name="Direct Download",
            title="Example",
            author=None,
            file_format=None,
            size=None,
            preview=None,
            content_type="ebook",
            origin="direct",
        )

        row = service.get_by_task_id("task-1")
        assert row is not None
        assert DownloadHistoryService.to_download_payload(row)["cover_aspect"] is None


def test_requeue_refreshes_the_cover_aspect():
    """A retry re-records the row; the aspect must track the fresh task."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "users.db")
        user_db = UserDB(db_path)
        user_db.initialize()
        service = DownloadHistoryService(db_path)

        common = {
            "task_id": "task-1",
            "user_id": None,
            "username": None,
            "request_id": None,
            "source": "prowlarr",
            "source_display_name": "Prowlarr",
            "title": "Example",
            "author": None,
            "file_format": None,
            "size": None,
            "preview": None,
            "content_type": "audiobook",
            "origin": "direct",
        }
        service.record_download(**common)
        service.record_download(**common, cover_aspect="square")

        row = service.get_by_task_id("task-1")
        assert row is not None
        assert DownloadHistoryService.to_download_payload(row)["cover_aspect"] == "square"
