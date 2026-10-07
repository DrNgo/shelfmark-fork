"""End-to-end Grimmory upload targeting: an explicit library, or the effective default."""

from threading import Event
from unittest.mock import MagicMock, patch

from shelfmark.core.models import DownloadTask, SearchMode

LIBRARIES = [
    {"id": 3, "name": "Fiction", "paths": [{"id": 3, "path": "/books/fiction"}]},
    {"id": 5, "name": "Light Novels", "paths": [{"id": 8, "path": "/books/ln"}]},
]

SETTINGS = {
    "BOOKS_OUTPUT_MODE": "booklore",
    "BOOKLORE_HOST": "http://grimmory:6060",
    "BOOKLORE_USERNAME": "shelfmark",
    "BOOKLORE_PASSWORD": "secret",
    "BOOKLORE_DESTINATION": "library",
    "BOOKLORE_LIBRARY_ID": 3,
    "BOOKLORE_PATH_ID": 3,
}


def _task(destination_key=None, user_id=None):
    return DownloadTask(
        task_id="ebook-1",
        source="direct_download",
        title="Overlord",
        author="Kugane Maruyama",
        format="epub",
        search_mode=SearchMode.DIRECT,
        destination_key=destination_key,
        user_id=user_id,
    )


def _run(tmp_path, task, *, settings=None, user_overrides=None, libraries=LIBRARIES):
    """Run the real post-processing pipeline with Grimmory's HTTP calls stubbed."""
    from shelfmark.download.postprocess.router import post_process_download

    values = settings or SETTINGS
    overrides = user_overrides or {}
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    temp_file = staging / "book.epub"
    temp_file.write_text("content")

    statuses = []
    uploads = []
    refreshes = []

    def config_get(key, default=None, user_id=None, **_kwargs):
        if user_id is not None and (user_id, key) in overrides:
            return overrides[(user_id, key)]
        return values.get(key, default)

    def upload(booklore_config, _token, file_path):
        uploads.append((booklore_config.library_id, booklore_config.path_id, file_path.name))

    def refresh(booklore_config, _token):
        refreshes.append(booklore_config.library_id)

    with (
        patch("shelfmark.core.config.config") as mock_config,
        patch("shelfmark.config.env.TMP_DIR", staging),
        patch("shelfmark.download.outputs.booklore.booklore_login", return_value="token"),
        patch(
            "shelfmark.download.outputs.booklore.booklore_list_libraries",
            return_value=libraries,
        ) as list_libraries,
        patch("shelfmark.download.outputs.booklore.booklore_upload_file", side_effect=upload),
        patch("shelfmark.download.outputs.booklore.booklore_refresh_library", side_effect=refresh),
    ):
        mock_config.get = MagicMock(side_effect=config_get)
        mock_config.CUSTOM_SCRIPT = None
        result = post_process_download(
            temp_file, task, Event(), lambda status, message: statuses.append((status, message))
        )

    return result, uploads, refreshes, statuses, list_libraries.call_count


def test_an_explicit_key_uploads_to_the_chosen_library(tmp_path):
    result, uploads, refreshes, _, list_calls = _run(tmp_path, _task("grimmory:5:8"))

    assert result == "booklore://ebook-1"
    assert uploads == [(5, 8, "book.epub")]
    assert refreshes == [5]
    assert list_calls == 1


def test_a_stale_key_fails_before_anything_is_uploaded(tmp_path):
    result, uploads, _, statuses, _ = _run(tmp_path, _task("grimmory:9:9"))

    assert result is None
    assert uploads == []
    errors = [message for status, message in statuses if status == "error"]
    assert errors
    assert "grimmory:9:9" in errors[-1]


def test_a_malformed_key_fails_before_anything_is_uploaded(tmp_path):
    result, uploads, _, statuses, list_calls = _run(tmp_path, _task("lib-kids"))

    assert result is None
    assert uploads == []
    assert list_calls == 0
    assert any(status == "error" and "lib-kids" in message for status, message in statuses)


def test_no_key_uses_the_users_effective_default(tmp_path):
    overrides = {(7, "BOOKLORE_LIBRARY_ID"): 5, (7, "BOOKLORE_PATH_ID"): 8}

    result, uploads, _, _, list_calls = _run(tmp_path, _task(user_id=7), user_overrides=overrides)

    assert result == "booklore://ebook-1"
    assert uploads == [(5, 8, "book.epub")]
    assert list_calls == 0


def test_a_blank_key_retry_re_evaluates_the_default(tmp_path):
    """The user's override changed between the first attempt and the retry."""
    first = _run(tmp_path, _task(user_id=7))
    retried = _run(
        tmp_path,
        _task(user_id=7),
        user_overrides={(7, "BOOKLORE_LIBRARY_ID"): 5, (7, "BOOKLORE_PATH_ID"): 8},
    )

    assert first[1] == [(3, 3, "book.epub")]
    assert retried[1] == [(5, 8, "book.epub")]


def test_bookdrop_mode_ignores_the_key(tmp_path):
    settings = {**SETTINGS, "BOOKLORE_DESTINATION": "bookdrop"}

    result, uploads, refreshes, _, list_calls = _run(
        tmp_path, _task("grimmory:9:9"), settings=settings
    )

    assert result == "booklore://ebook-1"
    assert uploads == [(0, 0, "book.epub")]
    assert refreshes == []
    assert list_calls == 0
