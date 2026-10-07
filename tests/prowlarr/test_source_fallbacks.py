"""Prowlarr runs the fallback ladder only while nothing found is the requested book.

DxD vol 5 is the motivating case: Hardcover gives it no subtitle, so today's query is
the full "High School DxD (Light Novel), Vol. 5: Hellcat..." title, which no release
name contains.
"""

from __future__ import annotations

import pytest

import shelfmark.release_sources.prowlarr.source as prowlarr_source
from shelfmark.core.search_plan import build_release_search_plan
from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import SourceUnavailableError
from shelfmark.release_sources.prowlarr.api import ProwlarrSearchError
from shelfmark.release_sources.prowlarr.source import FALLBACK_REQUESTS_PER_INDEXER, ProwlarrSource

DXD5 = "High School DxD (Light Novel), Vol. 5: Hellcat of the Underworld Training Camp"
RUNG_1 = "High School DxD Vol. 5"
RUNG_2 = "High School DxD v05"
RUNG_3 = "Hellcat of the Underworld Training Camp"
RUNG_4 = "High School DxD Volume 05"
RUNG_5 = "High School DxD Vol. 5 Hellcat of the Underworld Training Camp"

HIT = "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp [ENG / EPUB]"
WRONG_VOLUME = "High School DxD, Vol. 15 [ENG / EPUB]"
VIDEO = "High School DxD S01E05 1080p WEB-DL x264"


@pytest.fixture(autouse=True)
def source_available_by_default(monkeypatch):
    import shelfmark.download.orchestrator as orchestrator

    class _Available:
        display_name = "Prowlarr"

        def is_available(self):
            return True

    monkeypatch.setattr(orchestrator, "get_source", lambda _source: _Available())


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _LadderClient:
    """Torznab client answering per (indexer, query) and recording every request."""

    def __init__(
        self,
        answers=None,
        indexers=(1,),
        clock=None,
        seconds_per_request=0.0,
        category_ids=None,
    ):
        self.answers = answers or {}
        self.indexers = list(indexers)
        self.indexer_timeout = 90
        self.calls: list[tuple[int, str, object]] = []
        self.detail_calls = 0
        self.clock = clock
        self.seconds_per_request = seconds_per_request
        self.category_ids = category_ids or {}

    def get_enabled_indexers_detailed(self, *, raise_on_error=False):
        del raise_on_error
        self.detail_calls += 1
        return [
            {
                "id": indexer_id,
                "name": f"idx{indexer_id}",
                "enable": True,
                "capabilities": {
                    "categories": [
                        {"id": category_id, "subCategories": []}
                        for category_id in self.category_ids.get(indexer_id, [7000])
                    ]
                },
            }
            for indexer_id in self.indexers
        ]

    def torznab_search(
        self, *, indexer_id, query, categories=None, search_type="book", limit=100, offset=0
    ):
        del search_type, limit, offset
        self.calls.append((indexer_id, query, categories))
        if self.clock is not None:
            self.clock.now += self.seconds_per_request
        answer = self.answers.get((indexer_id, query), [])
        if isinstance(answer, Exception):
            raise answer
        return [
            {
                "guid": f"{indexer_id}:{title}",
                "title": title,
                "indexerId": indexer_id,
                "indexer": f"idx{indexer_id}",
                "protocol": "torrent",
                "size": 1048576,
                "seeders": 5,
            }
            for title in answer
        ]

    def get_enriched_indexer_ids(self, restrict_to=None, indexers=None):
        del restrict_to, indexers
        return []

    def get_indexer_seed_settings(self, restrict_to=None):
        del restrict_to
        return {}

    def queries(self, indexer_id=1):
        return [query for idx, query, _ in self.calls if idx == indexer_id]


def _dxd5(**overrides) -> BookMetadata:
    fields: dict[str, object] = {
        "provider": "hardcover",
        "provider_id": "2575261",
        "title": DXD5,
        "authors": ["Ichiei Ishibumi"],
        "series_name": "High School DxD (Light Novel)",
        "series_position": 5,
    }
    fields.update(overrides)
    return BookMetadata(**fields)


def _search(monkeypatch, client, *, book=None, languages=("en",), auto_expand=False, source=None):
    values = {"PROWLARR_INDEXERS": "", "PROWLARR_AUTO_EXPAND": auto_expand}
    monkeypatch.setattr(
        prowlarr_source.config, "get", lambda key, default=None: values.get(key, default)
    )
    source = source or ProwlarrSource()
    monkeypatch.setattr(source, "_get_client", lambda: client)
    book = book or _dxd5()
    plan = build_release_search_plan(book, languages=list(languages), content_type="ebook")
    return source.search(book, plan, content_type="ebook")


def _info_lines(monkeypatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(
        prowlarr_source.logger, "info", lambda message, *args: lines.append(message % args)
    )
    return lines


class TestStopping:
    def test_a_real_hit_from_the_mandatory_query_skips_every_fallback(self, monkeypatch):
        client = _LadderClient({(1, DXD5): [HIT]})

        releases = _search(monkeypatch, client)

        assert client.queries() == [DXD5]
        assert [r.title for r in releases] == [HIT]

    def test_a_wrong_volume_or_video_result_does_not_stop_them(self, monkeypatch):
        client = _LadderClient({(1, DXD5): [WRONG_VOLUME, VIDEO], (1, RUNG_2): [HIT]})

        releases = _search(monkeypatch, client)

        assert client.queries() == [DXD5, RUNG_1, RUNG_2]
        assert {r.title for r in releases} == {WRONG_VOLUME, VIDEO, HIT}

    def test_the_first_real_hit_stops_them(self, monkeypatch):
        client = _LadderClient({(1, RUNG_1): [HIT]})

        _search(monkeypatch, client)

        assert client.queries() == [DXD5, RUNG_1]

    def test_with_nothing_found_rungs_run_in_order_until_the_cap(self, monkeypatch):
        client = _LadderClient()

        assert _search(monkeypatch, client) == []
        # Five rungs planned; the fifth would be a fifth request to the same indexer.
        assert client.queries() == [DXD5, RUNG_1, RUNG_2, RUNG_3, RUNG_4]

    def test_localized_variants_still_run_before_fallbacks(self, monkeypatch):
        client = _LadderClient({(1, "Highschool DxD 5"): [HIT]})
        book = _dxd5(titles_by_language={"de": "Highschool DxD 5"})

        _search(monkeypatch, client, book=book, languages=("en", "de"))

        assert client.queries() == [DXD5, "Highschool DxD 5"]

    def test_a_hit_on_the_base_query_does_not_skip_localized_variants(self, monkeypatch):
        client = _LadderClient({(1, DXD5): [HIT]})
        book = _dxd5(titles_by_language={"de": "Highschool DxD 5"})

        _search(monkeypatch, client, book=book, languages=("en", "de"))

        assert client.queries() == [DXD5, "Highschool DxD 5"]


class TestFailedIndexers:
    def test_failed_and_rate_limited_indexers_sit_out_while_healthy_ones_continue(
        self, monkeypatch
    ):
        client = _LadderClient(
            {
                (1, DXD5): ProwlarrSearchError("indexer 1 did not respond within 90s"),
                (2, RUNG_1): ProwlarrSearchError("indexer 2 search failed: 429", rate_limited=True),
            },
            indexers=(1, 2, 3),
        )

        with pytest.raises(SourceUnavailableError, match="2 of 8 indexer searches failed"):
            _search(monkeypatch, client)

        assert client.queries(1) == [DXD5]
        assert client.queries(2) == [DXD5, RUNG_1]
        assert client.queries(3) == [DXD5, RUNG_1, RUNG_2, RUNG_3, RUNG_4]

    def test_failures_with_nothing_found_still_report_unavailable(self, monkeypatch):
        client = _LadderClient(
            {(1, DXD5): ProwlarrSearchError("indexer 1 did not respond within 90s")},
            indexers=(1, 2),
        )

        with pytest.raises(SourceUnavailableError, match="1 of 6 indexer searches failed"):
            _search(monkeypatch, client)

    def test_a_failed_indexer_gets_no_expansion_either(self, monkeypatch):
        client = _LadderClient(
            {(1, RUNG_1): ProwlarrSearchError("indexer 1 did not respond within 90s")},
            indexers=(1, 2),
        )

        with pytest.raises(SourceUnavailableError):
            _search(monkeypatch, client, auto_expand=True)

        assert [c for c in client.calls if c[0] == 1] == [
            (1, DXD5, [7000]),
            (1, DXD5, None),
            (1, RUNG_1, [7000]),
        ]


class TestRequestCap:
    def test_the_cap_counts_auto_expanded_calls(self, monkeypatch):
        client = _LadderClient()
        lines = _info_lines(monkeypatch)

        assert _search(monkeypatch, client, auto_expand=True) == []

        fallback_calls = client.calls[2:]
        assert len(fallback_calls) == FALLBACK_REQUESTS_PER_INDEXER == 4
        assert fallback_calls == [
            (1, RUNG_1, [7000]),
            (1, RUNG_1, None),
            (1, RUNG_2, [7000]),
            (1, RUNG_2, None),
        ]
        assert lines[-1].startswith("Prowlarr fallbacks: ran=yes stop=cap rungs=2/5")

    def test_the_cap_is_per_indexer(self, monkeypatch):
        client = _LadderClient({(1, RUNG_1): ["unrelated"]}, indexers=(1, 2))

        _search(monkeypatch, client, auto_expand=True)

        # Rung 1 found something (not the book) on indexer 1, so it was not expanded;
        # each indexer then spends its own four requests, expansions included.
        assert len([c for c in client.calls[4:] if c[0] == 1]) == 4
        assert len([c for c in client.calls[4:] if c[0] == 2]) == 4


class TestDeadline:
    def test_fallbacks_that_cannot_finish_are_skipped_and_results_kept(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(prowlarr_source.time, "monotonic", clock)
        client = _LadderClient({(1, DXD5): [WRONG_VOLUME]}, clock=clock, seconds_per_request=100)
        lines = _info_lines(monkeypatch)

        source = ProwlarrSource()

        releases = _search(monkeypatch, client, source=source)

        # Budget 180s, one 100s request spent: 80s left cannot cover a 90s request.
        assert client.queries() == [DXD5]
        assert [r.title for r in releases] == [WRONG_VOLUME]
        assert lines[-2].startswith("Prowlarr fallbacks: ran=no stop=deadline")
        assert source.last_search_incomplete is True

    def test_an_empty_search_cut_short_is_reported_incomplete(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(prowlarr_source.time, "monotonic", clock)
        client = _LadderClient(clock=clock, seconds_per_request=100)

        with pytest.raises(SourceUnavailableError, match="search incomplete"):
            _search(monkeypatch, client)

    def test_the_endpoint_budget_counts_too(self, monkeypatch):
        from shelfmark.core import search_deadline

        client = _LadderClient()
        with search_deadline.search_deadline(60):
            with pytest.raises(SourceUnavailableError, match="search incomplete"):
                _search(monkeypatch, client)

        assert client.queries() == [DXD5]


class TestNoCallsOnceNothingCanRun:
    """Eligibility comes from the snapshot already taken; nothing is sent to find out."""

    def test_a_capped_ladder_makes_no_further_calls(self, monkeypatch):
        client = _LadderClient()

        _search(monkeypatch, client, auto_expand=True)

        # One snapshot, plus one target lookup per mandatory pass (categorised, expanded).
        assert client.detail_calls == 3
        assert len(client.calls) == 2 + FALLBACK_REQUESTS_PER_INDEXER

    def test_an_expired_budget_makes_no_further_calls(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(prowlarr_source.time, "monotonic", clock)
        client = _LadderClient({(1, DXD5): [WRONG_VOLUME]}, clock=clock, seconds_per_request=100)

        _search(monkeypatch, client)

        assert client.detail_calls == 2
        assert len(client.calls) == 1


class TestCategoryIncompatibleIndexers:
    def test_rungs_go_unrestricted_to_an_indexer_without_book_categories(self, monkeypatch):
        # Indexer 1 (books) fails; indexer 2 only lists TV categories, so no categorised
        # rung can reach it - that is not the same as every indexer being used up.
        client = _LadderClient(
            {(1, DXD5): ProwlarrSearchError("indexer 1 did not respond within 90s")},
            indexers=(1, 2),
            category_ids={2: [5000]},
        )
        lines = _info_lines(monkeypatch)

        with pytest.raises(SourceUnavailableError):
            _search(monkeypatch, client)

        assert [c for c in client.calls if c[0] == 2] == [
            (2, RUNG_1, None),
            (2, RUNG_2, None),
            (2, RUNG_3, None),
            (2, RUNG_4, None),
        ]
        assert "Prowlarr fallbacks: ran=yes stop=cap rungs=4/5 requests=4" in lines


class TestMandatoryTimeout:
    def _two_variant_book(self):
        return _dxd5(titles_by_language={"de": "Highschool DxD 5"})

    def test_results_found_before_the_timeout_are_kept(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(prowlarr_source.time, "monotonic", clock)
        client = _LadderClient({(1, DXD5): [WRONG_VOLUME]}, clock=clock, seconds_per_request=200)
        source = ProwlarrSource()

        releases = _search(
            monkeypatch,
            client,
            book=self._two_variant_book(),
            languages=("en", "de"),
            source=source,
        )

        # The 200s first request spends the 180s budget; the localized variant never runs.
        assert client.queries() == [DXD5]
        assert [r.title for r in releases] == [WRONG_VOLUME]
        assert source.last_search_incomplete is True

    def test_with_nothing_found_it_still_raises_as_before(self, monkeypatch):
        clock = _Clock()
        monkeypatch.setattr(prowlarr_source.time, "monotonic", clock)
        client = _LadderClient(clock=clock, seconds_per_request=200)

        with pytest.raises(TimeoutError, match="timed out"):
            _search(monkeypatch, client, book=self._two_variant_book(), languages=("en", "de"))

    def test_a_complete_search_is_not_marked_incomplete(self, monkeypatch):
        source = ProwlarrSource()

        _search(monkeypatch, _LadderClient({(1, DXD5): [HIT]}), source=source)

        assert source.last_search_incomplete is False


class _XmlResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.ok = status_code < 400
        self.reason = "OK"

    def raise_for_status(self):
        return None


class TestIndexerErrorDocument:
    """A real ProwlarrClient: an indexer answering 200 with <error/> has failed."""

    def test_an_xml_error_indexer_gets_no_fallback_or_expansion(self, monkeypatch):
        import re

        from shelfmark.release_sources.prowlarr.api import ProwlarrClient

        client = ProwlarrClient("http://prowlarr:9696", "key", indexer_timeout=90)
        indexers = [
            {
                "id": indexer_id,
                "name": f"idx{indexer_id}",
                "enable": True,
                "capabilities": {"categories": [{"id": 7000, "subCategories": []}]},
            }
            for indexer_id in (1, 2)
        ]
        monkeypatch.setattr(
            client, "get_enabled_indexers_detailed", lambda *, raise_on_error=False: indexers
        )
        monkeypatch.setattr(
            client, "get_enriched_indexer_ids", lambda restrict_to=None, indexers=None: []
        )
        sent: list[tuple[int, str, object]] = []

        def fake_get(*, url, params, **_kwargs):
            indexer_id = int(re.search(r"/indexer/(\d+)/", url).group(1))
            sent.append((indexer_id, params["q"], params.get("cat")))
            if indexer_id == 1:
                return _XmlResponse(
                    '<?xml version="1.0"?><error code="100" description="Bad key"/>'
                )
            return _XmlResponse('<?xml version="1.0"?><rss><channel></channel></rss>')

        monkeypatch.setattr(client._session, "get", fake_get)

        with pytest.raises(SourceUnavailableError, match="returned error 100"):
            _search(monkeypatch, client, auto_expand=True)

        assert [c for c in sent if c[0] == 1] == [(1, DXD5, "7000")]
        assert [c for c in sent if c[0] == 2] == [
            (2, DXD5, "7000"),
            (2, RUNG_1, "7000"),
            (2, RUNG_1, None),
            (2, RUNG_2, "7000"),
            (2, RUNG_2, None),
        ]


class TestLogging:
    def test_every_request_and_the_ladder_outcome_are_logged(self, monkeypatch):
        client = _LadderClient({(1, DXD5): [WRONG_VOLUME], (1, RUNG_1): [HIT]}, indexers=(1,))
        lines = _info_lines(monkeypatch)

        _search(monkeypatch, client)

        requests_logged = [line for line in lines if line.startswith("Prowlarr request:")]
        assert requests_logged == [
            f"Prowlarr request: query='{DXD5}' indexer=idx1 categories=7000 rung=mandatory 1 "
            "expanded=no outcome=ok results=1",
            f"Prowlarr request: query='{RUNG_1}' indexer=idx1 categories=7000 rung=fallback 1 "
            "expanded=no outcome=ok results=1",
        ]
        assert "Prowlarr fallbacks: ran=yes stop=hit rungs=1/5 requests=1" in lines

    def test_failures_and_rate_limits_are_logged_as_such(self, monkeypatch):
        client = _LadderClient(
            {
                (1, DXD5): ProwlarrSearchError("timeout"),
                (2, DXD5): ProwlarrSearchError("429", rate_limited=True),
                (3, DXD5): [HIT],
            },
            indexers=(1, 2, 3),
        )
        lines = _info_lines(monkeypatch)

        _search(monkeypatch, client)

        outcomes = [line.split(" outcome=")[1] for line in lines if "Prowlarr request:" in line]
        assert outcomes == ["failed results=0", "rate-limited results=0", "ok results=1"]
        assert "Prowlarr fallbacks: ran=no stop=hit rungs=0/5 requests=0" in lines

    def test_a_search_without_fallbacks_says_so(self, monkeypatch):
        client = _LadderClient()
        lines = _info_lines(monkeypatch)

        _search(
            monkeypatch,
            client,
            book=BookMetadata(
                provider="hardcover", provider_id="1", title="Dune", authors=["Frank Herbert"]
            ),
        )

        assert "Prowlarr fallbacks: ran=no stop=not planned rungs=0/0 requests=0" in lines
