"""Tests for the read-only Audiobookshelf API client."""

from typing import Any

import pytest
import requests

from shelfmark.audiobookshelf.client import AudiobookshelfClient

TOKEN = "abs_0123456789abcdef"


class FakeResponse:
    """Minimal stand-in for requests.Response covering what the client touches."""

    def __init__(self, payload: object, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.reason = "OK" if status_code < 400 else "Error"
        self.text = str(payload)

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}", response=self)  # pyright: ignore[reportArgumentType]

    def json(self) -> object:
        return self._payload


class FakeSession:
    """Records requests and replays queued responses in order."""

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.headers: dict[str, str] = {}
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def request(self, **kwargs: Any) -> FakeResponse:
        self.calls.append(kwargs)
        if not self.responses:
            msg = "FakeSession ran out of queued responses"
            raise AssertionError(msg)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def build_client(
    responses: list[FakeResponse | Exception], url: str = "http://abs:13378"
) -> tuple[AudiobookshelfClient, FakeSession]:
    """Build a client whose network boundary is a FakeSession."""
    client = AudiobookshelfClient(url, TOKEN)
    session = FakeSession(responses)
    session.headers.update(dict(client._session.headers))
    client._session = session  # pyright: ignore[reportAttributeAccessIssue]
    return client, session


def libraries_payload(*libraries: dict[str, Any]) -> dict[str, Any]:
    return {"libraries": list(libraries)}


BOOK_LIBRARY = {"id": "lib_books", "name": "Audiobooks", "mediaType": "book"}
KIDS_LIBRARY = {"id": "lib_kids", "name": "Kids", "mediaType": "book"}
PODCAST_LIBRARY = {"id": "lib_pods", "name": "Podcasts", "mediaType": "podcast"}


class TestClientConstruction:
    """URL normalization and auth header wiring."""

    def test_trailing_slash_is_stripped_from_base_url(self):
        client = AudiobookshelfClient("http://abs:13378/", TOKEN)

        assert client.base_url == "http://abs:13378"

    def test_bare_host_gets_http_scheme(self):
        client = AudiobookshelfClient("abs:13378", TOKEN)

        assert client.base_url.startswith("http://")

    def test_token_is_sent_as_bearer_authorization_header(self):
        client, session = build_client([FakeResponse(libraries_payload(BOOK_LIBRARY))])

        client.get_libraries()

        assert session.headers["Authorization"] == f"Bearer {TOKEN}"


class TestTestConnection:
    """test_connection() maps transport outcomes to user-facing messages."""

    def test_reports_success_with_library_count(self):
        client, _ = build_client(
            [FakeResponse(libraries_payload(BOOK_LIBRARY, PODCAST_LIBRARY))],
        )

        success, message = client.test_connection()

        assert success is True
        assert "2" in message

    def test_reports_invalid_token_on_401(self):
        client, _ = build_client([FakeResponse({"error": "unauthorized"}, status_code=401)])

        success, message = client.test_connection()

        assert success is False
        assert "token" in message.lower()

    def test_reports_unreachable_host_on_connection_error(self):
        client, _ = build_client([requests.exceptions.ConnectionError("no route to host")])

        success, message = client.test_connection()

        assert success is False
        assert "connect" in message.lower()

    def test_hits_the_libraries_endpoint(self):
        client, session = build_client([FakeResponse(libraries_payload(BOOK_LIBRARY))])

        client.test_connection()

        assert session.calls[0]["url"] == "http://abs:13378/api/libraries"


class TestGetLibraries:
    """Library listing, shaping, and failure propagation."""

    def test_returns_id_name_and_media_type(self):
        client, _ = build_client([FakeResponse(libraries_payload(BOOK_LIBRARY, PODCAST_LIBRARY))])

        libraries = client.get_libraries()

        assert [(lib.id, lib.name, lib.media_type) for lib in libraries] == [
            ("lib_books", "Audiobooks", "book"),
            ("lib_pods", "Podcasts", "podcast"),
        ]

    def test_book_libraries_excludes_podcast_libraries(self):
        client, _ = build_client(
            [FakeResponse(libraries_payload(BOOK_LIBRARY, PODCAST_LIBRARY, KIDS_LIBRARY))],
        )

        libraries = client.get_book_libraries()

        assert [lib.id for lib in libraries] == ["lib_books", "lib_kids"]

    def test_http_errors_propagate_instead_of_returning_empty(self):
        """An empty list must mean "no libraries", never "the request failed"."""
        client, _ = build_client([FakeResponse({"error": "boom"}, status_code=500)])

        with pytest.raises(requests.exceptions.HTTPError):
            client.get_libraries()

    def test_unexpected_payload_shape_raises(self):
        client, _ = build_client([FakeResponse(["not", "an", "object"])])

        with pytest.raises(TypeError):
            client.get_libraries()


class TestGetLibraryItems:
    """Item listing requests expanded records and follows pagination."""

    def test_requests_expanded_items(self):
        client, session = build_client(
            [FakeResponse({"results": [{"id": "item1"}], "total": 1, "page": 0})],
        )

        client.get_library_items("lib_books")

        assert session.calls[0]["url"] == "http://abs:13378/api/libraries/lib_books/items"
        assert session.calls[0]["params"]["expanded"] == 1

    def test_follows_pagination_until_total_is_reached(self):
        client, session = build_client(
            [
                FakeResponse({"results": [{"id": "a"}, {"id": "b"}], "total": 3, "page": 0}),
                FakeResponse({"results": [{"id": "c"}], "total": 3, "page": 1}),
            ],
        )

        items = client.get_library_items("lib_books", page_size=2)

        assert [item["id"] for item in items] == ["a", "b", "c"]
        assert [call["params"]["page"] for call in session.calls] == [0, 1]

    def test_stops_when_a_page_returns_no_results(self):
        """A total that overstates reality must not spin forever."""
        client, session = build_client(
            [
                FakeResponse({"results": [{"id": "a"}], "total": 99, "page": 0}),
                FakeResponse({"results": [], "total": 99, "page": 1}),
            ],
        )

        items = client.get_library_items("lib_books", page_size=1)

        assert [item["id"] for item in items] == ["a"]
        assert len(session.calls) == 2
