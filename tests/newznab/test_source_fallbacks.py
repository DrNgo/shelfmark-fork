"""Newznab runs the fallback ladder per connection, only while nothing it found is the book."""

from __future__ import annotations

import logging

import pytest

import shelfmark.release_sources.newznab.source as newznab_source
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.newznab.api import NewznabSearchError
from shelfmark.release_sources.newznab.source import (
    FALLBACK_REQUESTS_PER_CONNECTION,
    NewznabSource,
)

DXD5 = "High School DxD (Light Novel), Vol. 5: Hellcat of the Underworld Training Camp"
RUNG_1 = "High School DxD Vol. 5"
RUNG_2 = "High School DxD v05"
RUNG_3 = "Hellcat of the Underworld Training Camp"
RUNG_4 = "High School DxD Volume 05"
RUNG_5 = "High School DxD Vol. 5 Hellcat of the Underworld Training Camp"
# Five rungs are planned; the cap of four requests per connection stops before the fifth.
UNTIL_THE_CAP = [DXD5, RUNG_1, RUNG_2, RUNG_3, RUNG_4]

HIT = "High School DxD, Vol. 5 - Hellcat of the Underworld Training Camp (epub)"
WRONG_VOLUME = "High School DxD, Vol. 15 (epub)"


@pytest.fixture(autouse=True)
def source_available_by_default(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    class _Available:
        display_name = "Newznab"

        def is_available(self):
            return True

    monkeypatch.setattr(orchestrator, "get_source", lambda _source: _Available())


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _FakeNewznab:
    """One connection: answers per query (titles, or (title, indexer) pairs, or an error)."""

    def __init__(self, answers=None, *, clock=None, seconds_per_request=0.0, api_key=""):
        self.answers = answers or {}
        self.api_key = api_key
        self.timeout = 30
        self.calls: list[tuple[str, object]] = []
        self.clock = clock
        self.seconds_per_request = seconds_per_request

    def search(self, query, categories=None, search_type="search", limit=100, offset=0):
        del search_type, limit, offset
        self.calls.append((query, categories))
        if self.clock is not None:
            self.clock.now += self.seconds_per_request
        # A (query, categories) key answers that exact request; a bare query answers any.
        cats_key = tuple(categories) if categories else None
        answer = self.answers.get((query, cats_key), self.answers.get(query, []))
        if isinstance(answer, Exception):
            raise answer
        rows = []
        for item in answer:
            if isinstance(item, dict):
                rows.append({"protocol": "usenet", "size": 1048576, "categories": [7000], **item})
                continue
            title, indexer = item if isinstance(item, tuple) else (item, None)
            rows.append(
                {
                    "title": title,
                    "guid": f"{id(self)}:{title}",
                    "protocol": "usenet",
                    "size": 1048576,
                    "indexer": indexer,
                    "categories": [7000],
                }
            )
        return rows

    def queries(self):
        return [query for query, _ in self.calls]


def _dxd5() -> BookMetadata:
    return BookMetadata(
        provider="hardcover",
        provider_id="2575261",
        title=DXD5,
        authors=["Ichiei Ishibumi"],
        series_name="High School DxD (Light Novel)",
        series_position=5,
    )


def _search(
    monkeypatch,
    connections: dict[str, _FakeNewznab],
    *,
    indexers=None,
    auto_expand=False,
    source=None,
    book=None,
):
    rows = [{"name": name, "url": f"https://{name}.example"} for name in connections]
    values = {"NEWZNAB_INDEXERS": rows, "NEWZNAB_AUTO_EXPAND": auto_expand}
    monkeypatch.setattr(
        newznab_source.config, "get", lambda key, default=None: values.get(key, default)
    )
    by_url = {f"https://{name}.example": client for name, client in connections.items()}
    monkeypatch.setattr(newznab_source, "NewznabClient", lambda url, _key: by_url[url])

    book = book or _dxd5()
    plan = build_release_search_plan(
        book, languages=["en"], indexers=indexers, content_type="ebook"
    )
    return (source or NewznabSource()).search(book, plan, content_type="ebook")


def _info_lines(monkeypatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(
        newznab_source.logger, "info", lambda message, *args: lines.append(message % args)
    )
    return lines


def _level_lines(monkeypatch, level: str) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(
        newznab_source.logger, level, lambda message, *args: lines.append(message % args)
    )
    return lines


@pytest.fixture
def all_logs():
    """Every record the Newznab source and API loggers emit, tracebacks included."""
    import shelfmark.release_sources.newznab.api as newznab_api

    records: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            records.append(logging.Formatter().format(record))

    handler = _Capture(level=logging.DEBUG)
    loggers = [newznab_source.logger, newznab_api.logger]
    previous = [lg.level for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
    yield records
    for lg, level in zip(loggers, previous, strict=True):
        lg.removeHandler(handler)
        lg.setLevel(level)


class TestPerConnectionLadder:
    def test_a_hit_on_one_connection_does_not_suppress_another(self, monkeypatch):
        geek = _FakeNewznab({DXD5: [HIT]})
        slug = _FakeNewznab({RUNG_2: [HIT]})

        releases = _search(monkeypatch, {"geek": geek, "slug": slug})

        assert geek.queries() == [DXD5]
        assert slug.queries() == [DXD5, RUNG_1, RUNG_2]
        assert sorted(r.indexer for r in releases) == ["geek", "slug"]

    def test_a_wrong_volume_does_not_stop_the_ladder(self, monkeypatch):
        geek = _FakeNewznab({DXD5: [WRONG_VOLUME], RUNG_1: [HIT]})

        releases = _search(monkeypatch, {"geek": geek})

        assert geek.queries() == [DXD5, RUNG_1]
        assert {r.title for r in releases} == {WRONG_VOLUME, HIT}


class TestFailureIsNotEmpty:
    def test_a_failed_connection_gets_no_fallbacks_while_an_empty_one_does(self, monkeypatch):
        down = _FakeNewznab({DXD5: NewznabSearchError("Newznab search failed: down")})
        empty = _FakeNewznab()

        # Nothing found and a search failed: unavailable, not "no releases".
        with pytest.raises(SourceUnavailableError, match="1 Newznab search"):
            _search(monkeypatch, {"down": down, "empty": empty})

        assert down.queries() == [DXD5]
        assert empty.queries() == UNTIL_THE_CAP

    def test_with_results_a_partial_failure_still_returns_them(self, monkeypatch):
        down = _FakeNewznab({DXD5: NewznabSearchError("Newznab search failed: down")})
        working = _FakeNewznab({DXD5: [HIT]})

        releases = _search(monkeypatch, {"down": down, "working": working})

        assert [r.title for r in releases] == [HIT]

    def test_a_failed_mandatory_request_is_still_auto_expanded_as_before(self, monkeypatch):
        # Mandatory requests run exactly as before, and an empty-looking answer has always
        # been retried without categories; only fallbacks see the failure.
        limited = _FakeNewznab(
            {DXD5: NewznabSearchError("Newznab search failed: 429", rate_limited=True)}
        )

        with pytest.raises(SourceUnavailableError):
            _search(monkeypatch, {"limited": limited}, auto_expand=True)

        assert limited.calls == [(DXD5, [7000]), (DXD5, None)]

    def test_a_fallback_failure_ends_that_connections_ladder(self, monkeypatch):
        flaky = _FakeNewznab(
            {RUNG_1: NewznabSearchError("Newznab search failed: indexer error 900")}
        )
        lines = _info_lines(monkeypatch)

        _search(monkeypatch, {"flaky": flaky})

        assert flaky.queries() == [DXD5, RUNG_1]
        assert "Newznab [flaky] fallbacks: ran=yes stop=failed rungs=1/5 requests=1" in lines

    def test_a_fallback_failure_is_not_a_failed_search(self, monkeypatch):
        # Every mandatory request answered (empty); only a fallback failed. The search
        # completed and found nothing: "no releases", not "source unavailable".
        flaky = _FakeNewznab(
            {RUNG_1: NewznabSearchError("Newznab search failed: 429", rate_limited=True)}
        )
        empty = _FakeNewznab()
        source = NewznabSource()

        releases = _search(monkeypatch, {"flaky": flaky, "empty": empty}, source=source)

        assert releases == []
        assert source.last_search_incomplete is False
        assert flaky.queries() == [DXD5, RUNG_1]
        assert empty.queries() == UNTIL_THE_CAP

    def test_a_fallback_failure_does_not_mask_a_mandatory_one(self, monkeypatch):
        down = _FakeNewznab({DXD5: NewznabSearchError("Newznab search failed: down")})
        flaky = _FakeNewznab(
            {RUNG_1: NewznabSearchError("Newznab search failed: indexer error 900")}
        )

        with pytest.raises(SourceUnavailableError, match="1 Newznab search") as excinfo:
            _search(monkeypatch, {"down": down, "flaky": flaky})

        assert "down" in str(excinfo.value)
        assert "indexer error 900" not in str(excinfo.value)

    def test_an_unexpected_fallback_exception_is_not_a_failed_search(self, monkeypatch, all_logs):
        broken = _FakeNewznab({RUNG_1: RuntimeError("client bug")})
        lines = _info_lines(monkeypatch)

        releases = _search(monkeypatch, {"broken": broken})

        assert releases == []
        assert broken.queries() == [DXD5, RUNG_1]
        # The request that blew up was sent: the summary counts it.
        assert "Newznab [broken] fallbacks: ran=yes stop=failed rungs=1/5 requests=1" in lines
        assert any("client bug" in line and "Traceback" in line for line in all_logs)

    def test_a_failure_its_expansion_answered_is_not_a_failed_search(self, monkeypatch):
        # The categorized request failed but the uncategorized retry answered (empty):
        # the connection did answer. It still sits out the fallbacks.
        flaky = _FakeNewznab(
            {(DXD5, (7000,)): NewznabSearchError("Newznab search failed: HTTPError (HTTP 502)")}
        )

        releases = _search(monkeypatch, {"flaky": flaky}, auto_expand=True)

        assert releases == []
        assert flaky.calls == [(DXD5, [7000]), (DXD5, None)]

    def test_a_failure_whose_expansion_also_fails_is_still_one(self, monkeypatch):
        down = _FakeNewznab({DXD5: NewznabSearchError("Newznab search failed: ConnectionError")})

        with pytest.raises(SourceUnavailableError, match=r"Newznab search\(es\) failed"):
            _search(monkeypatch, {"down": down}, auto_expand=True)


class TestSecretsStayOut:
    SECRET_URL = "https://geek.example/api?t=search&q=x&apikey=SECRET&cat=7000"

    def test_failure_text_with_an_api_key_is_redacted(self, monkeypatch, all_logs):
        searched = _FakeNewznab(
            {DXD5: NewznabSearchError(f"Newznab search failed: {self.SECRET_URL}")}
        )
        broken = _FakeNewznab({DXD5: RuntimeError(f"GET {self.SECRET_URL} api_key=SECRET")})

        with pytest.raises(SourceUnavailableError) as excinfo:
            _search(monkeypatch, {"searched": searched, "broken": broken})

        assert "SECRET" not in str(excinfo.value)
        assert "apikey=REDACTED" in str(excinfo.value)
        # Guard: the records these checks read really were captured.
        assert any("Traceback" in line for line in all_logs)
        assert not any("SECRET" in line for line in all_logs)

    def test_the_connections_own_api_key_is_redacted_wherever_it_appears(
        self, monkeypatch, all_logs
    ):
        keyed = _FakeNewznab(
            {DXD5: NewznabSearchError("Newznab search failed: indexer error 100: bad SECRET")},
            api_key="SECRET",
        )
        broken = _FakeNewznab({DXD5: RuntimeError("token SECRET rejected")}, api_key="SECRET")

        with pytest.raises(SourceUnavailableError) as excinfo:
            _search(monkeypatch, {"keyed": keyed, "broken": broken})

        assert "SECRET" not in str(excinfo.value)
        assert "bad REDACTED" in str(excinfo.value)
        assert any("Traceback" in line for line in all_logs)
        assert not any("SECRET" in line for line in all_logs)


class TestOnlyFilteredResultsCount:
    def test_a_hit_from_an_unselected_indexer_does_not_stop_the_ladder(self, monkeypatch):
        hydra = _FakeNewznab(
            {DXD5: [(HIT, "Unwanted")], RUNG_1: [("High School DxD v05 (epub)", "Wanted")]}
        )

        releases = _search(monkeypatch, {"hydra": hydra}, indexers=["Wanted"])

        assert hydra.queries() == [DXD5, RUNG_1]
        assert [r.indexer for r in releases] == ["Wanted"]

    def test_a_repeated_guid_is_judged_by_the_row_that_was_kept(self, monkeypatch):
        # The fallback's copy of guid g1 is dropped as a duplicate, so its (matching)
        # title cannot stop the ladder: the row kept and shown is the wrong volume.
        geek = _FakeNewznab(
            {
                DXD5: [{"guid": "g1", "title": WRONG_VOLUME}],
                RUNG_1: [{"guid": "g1", "title": HIT}],
            }
        )

        releases = _search(monkeypatch, {"geek": geek})

        assert geek.queries() == UNTIL_THE_CAP
        assert [r.title for r in releases] == [WRONG_VOLUME]


class TestSeriesBookNamedByTitle:
    def test_the_natural_release_name_skips_the_ladder(self, monkeypatch):
        natural = "Leviathan Wakes - James S.A. Corey EPUB"
        geek = _FakeNewznab({"Leviathan Wakes": [natural]})
        book = BookMetadata(
            provider="hardcover",
            provider_id="1",
            title="Leviathan Wakes",
            authors=["James S.A. Corey"],
            series_name="The Expanse",
            series_position=1,
        )
        lines = _info_lines(monkeypatch)

        releases = _search(monkeypatch, {"geek": geek}, book=book)

        assert geek.queries() == ["Leviathan Wakes"]
        assert [r.title for r in releases] == [natural]
        assert "Newznab [geek] fallbacks: ran=no stop=hit rungs=0/3 requests=0" in lines


class TestCap:
    def test_the_cap_counts_auto_expanded_calls_per_connection(self, monkeypatch):
        geek = _FakeNewznab()
        slug = _FakeNewznab()
        lines = _info_lines(monkeypatch)

        _search(monkeypatch, {"geek": geek, "slug": slug}, auto_expand=True)

        for client in (geek, slug):
            assert client.calls[2:] == [
                (RUNG_1, [7000]),
                (RUNG_1, None),
                (RUNG_2, [7000]),
                (RUNG_2, None),
            ]
            assert len(client.calls[2:]) == FALLBACK_REQUESTS_PER_CONNECTION
        assert "Newznab [geek] fallbacks: ran=yes stop=cap rungs=2/5 requests=4" in lines


class TestDeadline:
    def test_fallbacks_that_cannot_finish_are_skipped_and_results_kept(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(newznab_source.time, "monotonic", clock)
        geek = _FakeNewznab({DXD5: [WRONG_VOLUME]}, clock=clock, seconds_per_request=100)
        warnings = _level_lines(monkeypatch, "warning")
        source = NewznabSource()

        releases = _search(monkeypatch, {"geek": geek}, source=source)

        # Budget 120s, one 100s request spent: 20s left cannot cover a 60s (connect + read) request.
        assert geek.queries() == [DXD5]
        assert [r.title for r in releases] == [WRONG_VOLUME]
        # Skipping the ladder for lack of budget is a warning, not routine.
        assert "Newznab [geek] fallbacks: ran=no stop=deadline rungs=0/5 requests=0" in warnings
        assert source.last_search_incomplete is True

    def test_a_fallback_needs_connect_plus_read_time(self, monkeypatch):
        # requests applies the 30s scalar timeout to connect and to read: 60s per
        # request. After 70s, 50s is left - enough for one timeout, not for both.
        clock = _Clock()
        monkeypatch.setattr(newznab_source.time, "monotonic", clock)
        geek = _FakeNewznab(clock=clock, seconds_per_request=70)
        warnings = _level_lines(monkeypatch, "warning")

        with pytest.raises(
            SourceUnavailableError,
            match=r"^Newznab search incomplete: not enough time left to try fallback queries "
            r"\(needs up to 60s, 50s left\)$",
        ):
            _search(monkeypatch, {"geek": geek})

        assert geek.queries() == [DXD5]
        assert "Newznab [geek] fallbacks: ran=no stop=deadline rungs=0/5 requests=0" in warnings

    def test_fallbacks_never_starve_another_connections_mandatory_search(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(newznab_source.time, "monotonic", clock)
        # 59s per request: interleaved, slow's mandatory request leaves 61s - enough for
        # one 60s fallback, which pushes middle past the deadline and last never runs.
        slow = _FakeNewznab({DXD5: [WRONG_VOLUME]}, clock=clock, seconds_per_request=59)
        middle = _FakeNewznab(clock=clock, seconds_per_request=10)
        last = _FakeNewznab({DXD5: [HIT]}, clock=clock, seconds_per_request=1)

        releases = _search(monkeypatch, {"slow": slow, "middle": middle, "last": last})

        assert last.queries()[0] == DXD5
        assert HIT in {r.title for r in releases}

    def test_a_mandatory_timeout_keeps_what_was_found(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(newznab_source.time, "monotonic", clock)
        geek = _FakeNewznab({DXD5: [WRONG_VOLUME]}, clock=clock, seconds_per_request=200)
        slug = _FakeNewznab()
        source = NewznabSource()

        releases = _search(monkeypatch, {"geek": geek, "slug": slug}, source=source)

        assert [r.title for r in releases] == [WRONG_VOLUME]
        assert slug.calls == []
        assert source.last_search_incomplete is True

    def test_a_complete_search_is_not_marked_incomplete(self, monkeypatch):
        source = NewznabSource()

        _search(monkeypatch, {"geek": _FakeNewznab({DXD5: [HIT]})}, source=source)

        assert source.last_search_incomplete is False

    def test_an_empty_search_cut_short_is_reported_incomplete(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(newznab_source.time, "monotonic", clock)
        geek = _FakeNewznab(clock=clock, seconds_per_request=100)

        with pytest.raises(SourceUnavailableError, match="Newznab search incomplete"):
            _search(monkeypatch, {"geek": geek})

    def test_a_mandatory_timeout_with_nothing_found_is_incomplete_too(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(newznab_source.time, "monotonic", clock)
        geek = _FakeNewznab(clock=clock, seconds_per_request=200)
        slug = _FakeNewznab()

        # A real timeout keeps its own wording.
        with pytest.raises(
            SourceUnavailableError, match=r"^Newznab search incomplete: ran out of time \(120s"
        ):
            _search(monkeypatch, {"geek": geek, "slug": slug})

        assert slug.calls == []


class TestLogging:
    def test_every_request_and_each_ladder_outcome_are_logged(self, monkeypatch):
        geek = _FakeNewznab({RUNG_1: [HIT]})
        lines = _info_lines(monkeypatch)

        _search(monkeypatch, {"geek": geek})

        assert [line for line in lines if line.startswith("Newznab request:")] == [
            f"Newznab request: query='{DXD5}' connection=geek categories=7000 "
            "rung=mandatory 1 expanded=no outcome=empty results=0",
            f"Newznab request: query='{RUNG_1}' connection=geek categories=7000 "
            "rung=fallback 1 expanded=no outcome=ok results=1",
        ]
        assert "Newznab [geek] fallbacks: ran=yes stop=hit rungs=1/5 requests=1" in lines

    def test_a_failed_connection_says_why_it_ran_no_fallbacks(self, monkeypatch):
        down = _FakeNewznab({DXD5: NewznabSearchError("x", rate_limited=True)})
        lines = _info_lines(monkeypatch)

        with pytest.raises(SourceUnavailableError):
            _search(monkeypatch, {"down": down})

        assert any("outcome=rate-limited results=0" in line for line in lines)
        assert "Newznab [down] fallbacks: ran=no stop=failed rungs=0/5 requests=0" in lines
