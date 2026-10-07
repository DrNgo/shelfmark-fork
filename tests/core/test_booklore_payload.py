"""The custom-script payload after a Grimmory upload (fork-only additions, version 1)."""

import json
import subprocess
from datetime import datetime, timedelta
from threading import Event
from unittest.mock import ANY, MagicMock, patch

import pytest

from shelfmark.core.models import DownloadTask, SearchMode

SETTINGS = {
    "BOOKS_OUTPUT_MODE": "booklore",
    "BOOKLORE_HOST": "http://grimmory:6060",
    "BOOKLORE_USERNAME": "shelfmark",
    "BOOKLORE_PASSWORD": "secret",
    "BOOKLORE_DESTINATION": "library",
    "BOOKLORE_LIBRARY_ID": 3,
    "BOOKLORE_PATH_ID": 3,
    "CUSTOM_SCRIPT_JSON_PAYLOAD": True,
}

LIBRARIES = [{"id": 5, "name": "Light Novels", "paths": [{"id": 8, "path": "/books/ln"}]}]

IDENTITY = {
    "provider": "hardcover",
    "provider_id": "886465",
    "isbn_13": "9780316005142",
    "asin": "B0BSHZ1234",
}


def _run(tmp_path, *, files, responses, destination_key=None, identity=None, task_fields=None):
    """Run the pipeline on a folder of files and return (result, payload, events)."""
    from shelfmark.download.postprocess.router import post_process_download

    staging = tmp_path / "staging"
    source = staging / "release"
    source.mkdir(parents=True)
    for name, content in files.items():
        (source / name).write_bytes(content)

    task = DownloadTask(
        task_id="ebook-1",
        source="direct_download",
        title="Overlord",
        author="Kugane Maruyama",
        format="epub",
        search_mode=SearchMode.DIRECT,
        destination_key=destination_key,
        **(identity or {}),
        **(task_fields or {}),
    )
    events: list[str] = []

    def upload(_config, _token, file_path):
        events.append(f"upload {file_path.name}")
        return responses.get(file_path.name)

    def refresh(_config, _token):
        events.append("refresh")

    def clock():
        events.append("clock")
        return f"2026-10-06T12:00:0{events.count('clock')}+00:00"

    with (
        patch("shelfmark.core.config.config") as mock_config,
        patch("shelfmark.config.env.TMP_DIR", staging),
        patch("shelfmark.download.outputs.booklore.booklore_login", return_value="token"),
        patch(
            "shelfmark.download.outputs.booklore.booklore_list_libraries", return_value=LIBRARIES
        ),
        patch("shelfmark.download.outputs.booklore.booklore_upload_file", side_effect=upload),
        patch("shelfmark.download.outputs.booklore.booklore_refresh_library", side_effect=refresh),
        patch("shelfmark.download.outputs.booklore._utc_now", side_effect=clock),
        patch("subprocess.run") as mock_run,
    ):
        mock_config.get = MagicMock(
            side_effect=lambda key, default=None, **_kwargs: SETTINGS.get(key, default)
        )
        mock_config.CUSTOM_SCRIPT = "/opt/shelfmark-hooks/dispatch.py"
        mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = post_process_download(source, task, Event(), lambda *_args: None)

    payload = json.loads(mock_run.call_args.kwargs["input"])
    return result, payload, events


def test_the_task_carries_the_book_identity(tmp_path):
    _, payload, _ = _run(tmp_path, files={"a.epub": b"1234"}, responses={}, identity=IDENTITY)

    assert payload["version"] == 1
    assert {k: payload["task"][k] for k in IDENTITY} == IDENTITY


def test_identity_fields_are_null_when_unknown(tmp_path):
    _, payload, _ = _run(tmp_path, files={"a.epub": b"1234"}, responses={})

    assert {k: payload["task"][k] for k in IDENTITY} == dict.fromkeys(IDENTITY)


def test_a_multi_book_task_carries_no_identity(tmp_path):
    """One identity cannot be tied to each book of a pack, so none is sent."""
    _, payload, _ = _run(
        tmp_path,
        files={"a.epub": b"1234", "b.epub": b"5678"},
        responses={},
        identity=IDENTITY,
        task_fields={"multi_book": True},
    )

    assert {k: payload["task"][k] for k in IDENTITY} == dict.fromkeys(IDENTITY)


def test_every_uploaded_file_is_listed_with_its_response(tmp_path):
    result, payload, _ = _run(
        tmp_path,
        files={"a.epub": b"1234", "b.epub": b"123456789"},
        responses={"a.epub": {"id": 41, "fileName": "a.epub"}},
    )

    assert result == "booklore://ebook-1"
    uploaded = payload["output"]["details"]["booklore"]["uploaded_files"]
    assert sorted(uploaded, key=lambda f: f["name"]) == [
        {"name": "a.epub", "size_bytes": 4, "response": {"id": 41, "fileName": "a.epub"}},
        {"name": "b.epub", "size_bytes": 9, "response": None},
    ]


def test_the_upload_window_brackets_every_upload_and_the_refresh(tmp_path):
    _, payload, events = _run(tmp_path, files={"a.epub": b"1", "b.epub": b"2"}, responses={})

    details = payload["output"]["details"]["booklore"]
    assert events[0] == "clock"
    assert events[-1] == "clock"
    assert events.count("clock") == 2
    assert events[-2] == "refresh"
    assert details["upload_started_at"] == "2026-10-06T12:00:01+00:00"
    assert details["upload_finished_at"] == "2026-10-06T12:00:02+00:00"


def test_the_payload_names_the_library_actually_used(tmp_path):
    _, payload, _ = _run(
        tmp_path, files={"a.epub": b"1"}, responses={}, destination_key="grimmory:5:8"
    )

    details = payload["output"]["details"]["booklore"]
    assert (details["destination"], details["library_id"], details["path_id"]) == (
        "library",
        5,
        8,
    )


def test_the_real_clock_gives_ordered_utc_timestamps():
    from shelfmark.download.outputs.booklore import _utc_now

    first = datetime.fromisoformat(_utc_now())
    second = datetime.fromisoformat(_utc_now())

    assert first.utcoffset() == timedelta(0)
    assert first <= second


def _run_with_failing_hook(tmp_path, *, run_side_effect=None, payload_error=None):
    """Upload one file, then make the post-upload custom script fail."""
    from shelfmark.download.postprocess.router import post_process_download

    staging = tmp_path / "staging"
    staging.mkdir()
    temp_file = staging / "book.epub"
    temp_file.write_bytes(b"1234")
    task = DownloadTask(
        task_id="ebook-1",
        source="direct_download",
        title="Overlord",
        author="Kugane Maruyama",
        format="epub",
        search_mode=SearchMode.DIRECT,
    )
    statuses: list[tuple[str, str | None]] = []
    uploads: list[str] = []

    with (
        patch("shelfmark.core.config.config") as mock_config,
        patch("shelfmark.config.env.TMP_DIR", staging),
        patch("shelfmark.download.outputs.booklore.booklore_login", return_value="token"),
        patch(
            "shelfmark.download.outputs.booklore.booklore_upload_file",
            side_effect=lambda _c, _t, path: uploads.append(path.name),
        ),
        patch("shelfmark.download.outputs.booklore.booklore_refresh_library"),
        patch("shelfmark.download.outputs.booklore.logger") as mock_logger,
        patch("subprocess.run", side_effect=run_side_effect) as mock_run,
        patch(
            "shelfmark.download.postprocess.custom_script._build_custom_script_payload",
            side_effect=payload_error,
            return_value={"version": 1},
        ),
    ):
        mock_config.get = MagicMock(
            side_effect=lambda key, default=None, **_kwargs: SETTINGS.get(key, default)
        )
        mock_config.CUSTOM_SCRIPT = "/opt/shelfmark-hooks/dispatch.py"
        if run_side_effect is None:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
        result = post_process_download(
            temp_file, task, Event(), lambda status, message: statuses.append((status, message))
        )

    return result, statuses, uploads, mock_logger


class TestHookFailureAfterUpload:
    """The book is already in Grimmory, which cannot move or dedupe it.

    Failing the task would invite a retry that uploads it again, so any hook
    failure after a successful upload is logged and the task still completes.
    """

    @pytest.mark.parametrize(
        "run_side_effect",
        [
            FileNotFoundError("/opt/shelfmark-hooks/dispatch.py"),
            PermissionError("/opt/shelfmark-hooks/dispatch.py"),
            subprocess.TimeoutExpired("dispatch.py", 300),
            subprocess.CalledProcessError(1, "dispatch.py", stderr="boom"),
        ],
        ids=["missing", "not-executable", "timeout", "non-zero-exit"],
    )
    def test_a_failing_script_still_completes_the_upload(self, tmp_path, run_side_effect):
        result, statuses, uploads, mock_logger = _run_with_failing_hook(
            tmp_path, run_side_effect=run_side_effect
        )

        assert result == "booklore://ebook-1"
        assert uploads == ["book.epub"]
        assert [status for status, _ in statuses if status == "error"] == []
        assert statuses[-1][0] == "complete"
        mock_logger.warning.assert_any_call(
            "Task %s: post-upload custom script failed; the upload stands: %s",
            "ebook-1",
            ANY,
        )

    def test_a_payload_error_still_completes_the_upload(self, tmp_path):
        result, statuses, uploads, mock_logger = _run_with_failing_hook(
            tmp_path, payload_error=TypeError("not JSON serializable")
        )

        assert result == "booklore://ebook-1"
        assert uploads == ["book.epub"]
        assert statuses[-1][0] == "complete"
        mock_logger.exception.assert_called_once_with(
            "Task %s: post-upload custom script crashed; the upload stands", "ebook-1"
        )
