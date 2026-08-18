"""Focused tests for download_url() retry, fallback, and resume behavior."""

import requests


class _FakeResponse:
    def __init__(
        self,
        status_code: int,
        *,
        headers: dict | None = None,
        chunks: list[bytes] | None = None,
        url: str = "",
        iter_error: requests.exceptions.RequestException | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self._chunks = chunks or []
        self._iter_error = iter_error

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            error = requests.exceptions.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def iter_content(self, chunk_size: int = 8192):
        del chunk_size
        for chunk in self._chunks:
            yield chunk
        if self._iter_error:
            raise self._iter_error


class _DummyProgressBar:
    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    def update(self, amount: int) -> None:
        del amount

    def close(self) -> None:
        return None


def _prepare_download_test(monkeypatch):
    import shelfmark.download.http as http

    monkeypatch.setattr(http, "_is_cf_bypass_enabled", lambda: False)
    monkeypatch.setattr(http, "get_proxies", lambda _url: {})
    monkeypatch.setattr(http, "get_ssl_verify", lambda _url: True)
    monkeypatch.setattr(http.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(http, "tqdm", _DummyProgressBar)

    return http


def test_download_url_returns_none_on_rate_limit(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    status_updates: list[tuple[str, str | None]] = []

    def fake_get(_url: str, **_kwargs):
        error = requests.exceptions.HTTPError("busy")
        error.response = _FakeResponse(429, url=_url)
        raise error

    monkeypatch.setattr(http.requests, "get", fake_get)

    result = http.download_url(
        "https://example.com/file.epub",
        status_callback=lambda status, message: status_updates.append((status, message)),
    )

    assert result is None
    assert status_updates == [("resolving", "Server busy, trying next")]


def test_download_url_returns_none_on_timeout(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    status_updates: list[tuple[str, str | None]] = []

    def fake_get(_url: str, **_kwargs):
        raise requests.exceptions.Timeout("read timed out")

    monkeypatch.setattr(http.requests, "get", fake_get)

    result = http.download_url(
        "https://example.com/file.epub",
        status_callback=lambda status, message: status_updates.append((status, message)),
    )

    assert result is None
    assert status_updates == [("resolving", "Server timed out, trying next")]


def test_download_url_rejects_html_error_pages(monkeypatch):
    http = _prepare_download_test(monkeypatch)
    status_updates: list[tuple[str, str | None]] = []

    def fake_get(url: str, **_kwargs):
        return _FakeResponse(
            200,
            headers={
                "content-length": "100",
                "content-type": "text/html; charset=utf-8",
            },
            chunks=[b"<html>busy</html>"],
            url=url,
        )

    monkeypatch.setattr(http.requests, "get", fake_get)

    result = http.download_url(
        "https://example.com/file.epub",
        status_callback=lambda status, message: status_updates.append((status, message)),
    )

    assert result is None
    assert status_updates == [("downloading", "")]


def test_download_url_resumes_partial_download_after_connection_error(monkeypatch):
    http = _prepare_download_test(monkeypatch)

    calls: list[dict[str, object]] = []
    responses = [
        _FakeResponse(
            200,
            headers={
                "content-length": "8",
                "content-type": "application/octet-stream",
            },
            chunks=[b"abcd"],
            url="https://example.com/file.epub",
            iter_error=requests.exceptions.ConnectionError("socket reset"),
        ),
        _FakeResponse(
            206,
            headers={"content-length": "4"},
            chunks=[b"efgh"],
            url="https://example.com/file.epub",
        ),
    ]

    def fake_get(url: str, **kwargs):
        calls.append({"url": url, "headers": dict(kwargs.get("headers", {}))})
        return responses[len(calls) - 1]

    monkeypatch.setattr(http.requests, "get", fake_get)

    result = http.download_url("https://example.com/file.epub")

    assert result is not None
    # `tell()` must still report the full byte count before any rewind -- callers such as
    # direct_download._try_download_url use it as the downloaded-size check.
    assert result.tell() == 8
    result.seek(0)
    assert result.read() == b"abcdefgh"
    assert len(calls) == 2
    assert "Range" not in calls[0]["headers"]
    assert calls[1]["headers"]["Range"] == "bytes=4-"


def _run_sized_download(monkeypatch, payload: bytes):
    http = _prepare_download_test(monkeypatch)

    response = _FakeResponse(
        200,
        headers={
            "content-length": str(len(payload)),
            "content-type": "application/octet-stream",
        },
        chunks=[payload[i : i + 8192] for i in range(0, len(payload), 8192)],
        url="https://example.com/file.epub",
    )
    monkeypatch.setattr(http.requests, "get", lambda _url, **_kwargs: response)

    return http.download_url("https://example.com/file.epub")


def test_download_url_keeps_small_payload_in_memory(monkeypatch):
    """Below the spool threshold nothing touches disk -- same as the old BytesIO."""
    payload = b"small epub payload" * 16

    result = _run_sized_download(monkeypatch, payload)

    assert result is not None
    # `_rolled` is SpooledTemporaryFile's own flag for "has spilled to a real file".
    assert result._rolled is False
    assert result.tell() == len(payload)
    result.seek(0)
    assert result.read() == payload


def test_download_url_spills_large_payload_to_disk(monkeypatch):
    """Above the spool threshold the buffer rolls to a temp file, bounding peak RAM."""
    import shelfmark.download.http as http_module

    monkeypatch.setattr(http_module, "_DOWNLOAD_SPOOL_MAX_SIZE", 64 * 1024)
    payload = bytes(range(256)) * 1024  # 256 KiB, comfortably over the patched threshold

    result = _run_sized_download(monkeypatch, payload)

    assert result is not None
    # `_rolled` is SpooledTemporaryFile's own flag for "has spilled to a real file".
    assert result._rolled is True
    # tell()/seek()/read() must behave identically once rolled over -- direct_download
    # relies on tell() for its minimum-file-size check before writing the book out.
    assert result.tell() == len(payload)
    result.seek(0)
    assert result.read() == payload
