"""The live acceptance harness runs offline against a fake Prowlarr.

The real run is user-gated (it needs a live Prowlarr); this only proves the harness
drives the production plan and source path and reports what it should.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import shelfmark.release_sources.prowlarr.source as prowlarr_source

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "ladder_acceptance.py"


def _load():
    spec = importlib.util.spec_from_file_location("ladder_acceptance", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve their module through sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _FakeProwlarr:
    indexer_timeout = 90

    def __init__(self, answers):
        self.answers = answers

    def get_enabled_indexers_detailed(self, *, raise_on_error=False):
        del raise_on_error
        return [{"id": 1, "name": "fake", "enable": True, "capabilities": {"categories": []}}]

    def torznab_search(self, *, indexer_id, query, categories=None, search_type="book", **_kw):
        del categories, search_type
        return [
            {"guid": f"{query}:{title}", "title": title, "indexerId": indexer_id, "size": 1}
            for title in self.answers.get(query, [])
        ]

    def get_enriched_indexer_ids(self, restrict_to=None, indexers=None):
        del restrict_to, indexers
        return []


def test_the_harness_runs_the_production_path_and_counts_requests(monkeypatch, capsys, tmp_path):
    harness = _load()
    monkeypatch.setattr(
        prowlarr_source.config, "get", lambda key, default=None, **_kw: {}.get(key, default)
    )
    dxd5 = next(row for row in harness.BOOKS if row[0] == 2575261)
    housemaid = next(row for row in harness.BOOKS if row[0] == 511526)
    answers = {
        "High School DxD v05": ["High School DxD v05 (2015) (Digital) (danke-Empire)"],
        "The Housemaid": ["The Housemaid by Freida McFadden [ENG / EPUB]", "Unrelated"],
    }

    results = harness.run_books([dxd5, housemaid], lambda: _FakeProwlarr(answers))

    assert [(r.requests, r.fallback_variants, len(r.releases)) for r in results] == [
        (3, 5, 1),
        (1, 0, 2),
    ]
    assert results[0].suspect == ["High School DxD v05 (2015) (Digital) (danke-Empire)"]
    out_file = tmp_path / "acceptance.json"
    assert harness.report(results, out_file) == 0
    printed = capsys.readouterr().out
    assert "Unrelated" in printed
    assert "Adjudicate 'found'" in printed
    written = json.loads(out_file.read_text())
    assert [(row["found"], row["releases"]) for row in written] == [
        (None, ["High School DxD v05 (2015) (Digital) (danke-Empire)"]),
        (None, ["The Housemaid by Freida McFadden [ENG / EPUB]", "Unrelated"]),
    ]


def test_each_search_runs_under_the_endpoint_deadline(monkeypatch):
    from shelfmark.core import search_deadline

    harness = _load()
    monkeypatch.setattr(
        prowlarr_source.config, "get", lambda key, default=None, **_kw: {}.get(key, default)
    )
    seen: list[object] = []

    class _Watching(_FakeProwlarr):
        def torznab_search(self, **kwargs):
            seen.append(search_deadline.current())
            return super().torznab_search(**kwargs)

    housemaid = next(row for row in harness.BOOKS if row[0] == 511526)
    harness.run_books([housemaid], lambda: _Watching({}))

    assert seen
    assert all(deadline is not None for deadline in seen)


def test_without_a_prowlarr_it_refuses_to_run(monkeypatch, capsys):
    harness = _load()
    monkeypatch.delenv("PROWLARR_URL", raising=False)
    monkeypatch.delenv("PROWLARR_API_KEY", raising=False)

    assert harness.main([]) == 2
    assert "PROWLARR_URL" in capsys.readouterr().out


def test_the_harness_books_are_the_nineteen_measured():
    harness = _load()

    assert len(harness.BOOKS) == 19
    assert {row[1] for row in harness.BOOKS} >= harness.STANDALONES
