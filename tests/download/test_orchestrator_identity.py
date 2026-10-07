"""Book identity survives queueing, retry and restart restore (fork-only)."""

from unittest.mock import MagicMock

import pytest

from shelfmark.core.models import DownloadTask
from shelfmark.download import orchestrator

ISBN_13 = "9780316005142"
ISBN_10 = "0316005142"

IDENTITY = {
    "provider": "hardcover",
    "provider_id": "886465",
    "isbn_13": ISBN_13,
    "asin": "B0BSHZ1234",
}


@pytest.fixture
def queued(monkeypatch):
    """Capture the task `queue_release` builds."""
    captured: dict[str, DownloadTask] = {}

    def fake_add(task: DownloadTask) -> bool:
        captured["task"] = task
        return True

    monkeypatch.setattr(orchestrator.config, "get", lambda _key, default=None, **_kw: default)
    monkeypatch.setattr(orchestrator, "_source_unavailable_message", lambda _source: None)
    monkeypatch.setattr(orchestrator.book_queue, "add", fake_add)
    monkeypatch.setattr(orchestrator, "ws_manager", None)

    def queue(release_data: dict) -> DownloadTask:
        ok, error = orchestrator.queue_release(
            {"source": "direct_download", "source_id": "abc", "title": "Overlord", **release_data}
        )
        assert ok, error
        return captured["task"]

    return queue


def _identity(task: DownloadTask) -> dict:
    return {field: getattr(task, field) for field in IDENTITY}


def test_a_task_has_no_identity_by_default():
    assert _identity(DownloadTask(task_id="t", source="prowlarr", title="T")) == dict.fromkeys(
        IDENTITY
    )


def test_queue_release_carries_the_identity(queued):
    assert _identity(queued(IDENTITY)) == IDENTITY


def test_queue_release_normalizes_the_identity(queued):
    task = queued({"provider": " hardcover ", "provider_id": " 886465 ", "isbn_13": ISBN_10})

    assert _identity(task) == {**dict.fromkeys(IDENTITY), **IDENTITY, "asin": None}


def test_queue_release_drops_a_half_pair(queued):
    task = queued({"provider": "hardcover", "isbn_13": ISBN_13})

    assert task.provider is None
    assert task.provider_id is None
    assert task.isbn_13 == ISBN_13


def test_identity_in_extra_is_not_read(queued):
    """Release-source `extra` blobs belong to the indexer, not to the book."""
    task = queued({"extra": {"provider": "hardcover", "provider_id": "1", "asin": "B0X"}})

    assert _identity(task) == dict.fromkeys(IDENTITY)


def test_retry_payload_round_trips_the_identity():
    task = DownloadTask(task_id="t", source="prowlarr", title="T", **IDENTITY)

    payload = orchestrator.serialize_task_for_retry(task)
    restored = orchestrator._restore_task_from_retry_payload(payload)

    assert {field: payload[field] for field in IDENTITY} == IDENTITY
    assert restored is not None
    assert _identity(restored) == IDENTITY


def test_a_legacy_retry_payload_restores_without_identity():
    payload = orchestrator.serialize_task_for_retry(
        DownloadTask(task_id="t", source="prowlarr", title="T", **IDENTITY)
    )
    for field in IDENTITY:
        del payload[field]

    restored = orchestrator._restore_task_from_retry_payload(payload)

    assert restored is not None
    assert _identity(restored) == dict.fromkeys(IDENTITY)


def test_restore_applies_the_pair_rule():
    payload = orchestrator.serialize_task_for_retry(
        DownloadTask(task_id="t", source="prowlarr", title="T")
    )
    payload["provider"] = "hardcover"

    restored = orchestrator._restore_task_from_retry_payload(payload)

    assert restored is not None
    assert restored.provider is None


def test_restart_restore_requeues_with_the_identity(monkeypatch):
    """`retry_persisted_download` rebuilds a task lost to a restart."""
    queue = MagicMock()
    queue.add.return_value = True
    monkeypatch.setattr(orchestrator, "book_queue", queue)
    monkeypatch.setattr(orchestrator, "ws_manager", None)
    payload = orchestrator.serialize_task_for_retry(
        DownloadTask(task_id="t", source="prowlarr", title="T", **IDENTITY)
    )

    ok, error = orchestrator.retry_persisted_download(payload, final_status="cancelled")

    assert ok, error
    assert _identity(queue.add.call_args.args[0]) == IDENTITY
