#!/usr/bin/env python3
"""Acceptance run for the release-search query ladder against a live Prowlarr.

USER-GATED: this talks to a real Prowlarr and its indexers. Never run it from CI or an
automated plan step.

It runs the production path in-process for the 19 books measured on 2026-10-07: the
Hardcover full-fetch parser with series fields, the release endpoint's title override,
``build_release_search_plan(content_type="ebook")`` and ``ProwlarrSource.search`` with
its real stopping, category and auto-expand behaviour, each search under the release
endpoint's own deadline (``search_deadline.search_deadline()``, configured budget).

It does not decide whether a book was found: the identity predicate under test cannot be
its own oracle. Per book it prints the Torznab requests, the time taken, whether the
search was incomplete, the returned titles (top 30, with the total) and - for
information only - which of them the predicate counted as hits (titles that look like
manga or comics are marked "suspect"). Every returned title is written to a JSON file
with ``"found": null`` per book, for a person to adjudicate.

Usage (Prowlarr reachable, e.g. `kubectl port-forward -n media svc/prowlarr 9696:9696`):

    PROWLARR_URL=http://localhost:9696 PROWLARR_API_KEY=... \\
        uv run python scripts/ladder_acceptance.py [--auto-expand] [--only DxD] \\
        [--json ladder_acceptance.json]

Target (adjudicated from the JSON): at least 18 of 19 found, and the three standalones
search exactly as before (no fallback requests - the one thing this script checks).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

TARGET_FOUND = 18
SHOWN_TITLES = 30
STANDALONES = frozenset({"Project Hail Mary", "The Housemaid", "Reminders of Him"})

MT = "Mushoku Tensei: Jobless Reincarnation (Light Novel)"
MT_SUB = "Jobless Reincarnation (Light Novel)"
OL = "Overlord (Light Novel)"
DXD = "High School DxD (Light Novel)"
SH = "The Rising of the Shield Hero (Light Novel)"
DM = "Death March to the Parallel World Rhapsody"

# (Hardcover id, title, subtitle, authors, series name, series position)
BOOKS: list[tuple[int, str, str | None, list[str], str | None, int | None]] = [
    (730298, f"{MT}, Vol. 3", MT_SUB, ["Rifujin na Magonote"], MT, 3),
    (730294, f"{MT}, Vol. 7", MT_SUB, ["Rifujin na Magonote"], MT, 7),
    (730290, f"{MT}, Vol. 11", MT_SUB, ["Rifujin na Magonote", "Shirotaka"], MT, 11),
    (427621, "Leviathan Wakes", "Cow at Sea", ["James S. A. Corey"], "The Expanse", 1),
    (
        886465,
        f"{OL}, Vol. 2: The Dark Warrior",
        "The Dark Warrior",
        ["Kugane Maruyama"],
        OL,
        2,
    ),
    (
        885683,
        f"{OL}, Vol. 5: The Men of the Kingdom Part I",
        "The Men of the Kingdom Part I",
        ["Kugane Maruyama"],
        OL,
        5,
    ),
    (
        1230950,
        f"{OL}, Vol. 10: The Ruler of Conspiracy",
        "The Ruler of Conspiracy",
        ["Kugane Maruyama"],
        OL,
        10,
    ),
    (
        2486566,
        f"{DXD}, Vol. 3: Excalibur of the Moonlit Schoolyard",
        "Excalibur of the Moonlit Schoolyard",
        ["Ichiei Ishibumi"],
        DXD,
        3,
    ),
    (
        2486567,
        f"{DXD}, Vol. 4: Vampire of the Suspended Classroom",
        "Vampire of the Suspended Classroom",
        ["Ichiei Ishibumi"],
        DXD,
        4,
    ),
    (
        2575261,
        f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp",
        None,
        ["Ichiei Ishibumi"],
        DXD,
        5,
    ),
    (
        2575267,
        f"{DXD}, Vol. 6: Holy Behind the Gymnasium",
        None,
        ["Ichiei Ishibumi"],
        DXD,
        6,
    ),
    (1282767, f"{SH}, Vol. 3", None, ["Aneko Yusagi"], SH, 3),
    (1283002, f"{SH}, Vol. 8", None, ["Aneko Yusagi"], SH, 8),
    (1561928, f"{SH}, Vol. 15", "The Manga Companion", ["Aneko Yusagi"], SH, 15),
    (785991, f"{DM}, Vol. 5", None, ["Hiro Ainana"], DM, 5),
    (785985, f"{DM}, Vol. 12", None, ["Hiro Ainana"], DM, 12),
    (427578, "Project Hail Mary", "A Novel", ["Andy Weir"], None, None),
    (
        511526,
        "The Housemaid",
        "An Absolutely Addictive Psychological Thriller with a Jaw-dropping Twist",
        ["Freida McFadden"],
        None,
        None,
    ),
    (476001, "Reminders of Him", None, ["Colleen Hoover"], None, None),
]

_SUSPECT_RE = re.compile(r"\b(?:manga|comic|cbz|cbr|digital-?sd|danke-empire)\b", re.IGNORECASE)


@dataclass
class BookResult:
    title: str
    requests: int
    fallback_variants: int
    elapsed: float
    releases: list[str] = field(default_factory=list)
    hits: list[str] = field(default_factory=list)  # informational: the predicate's view
    incomplete: bool = False
    error: str | None = None

    @property
    def suspect(self) -> list[str]:
        return [title for title in self.hits if _SUSPECT_RE.search(title)]


class _CountingClient:
    """A Prowlarr client that counts the Torznab requests sent through it."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.requests = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    def torznab_search(self, **kwargs: Any) -> Any:
        self.requests += 1
        return self._client.torznab_search(**kwargs)


def endpoint_book(
    book_id: int,
    title: str,
    subtitle: str | None,
    authors: list[str],
    series: str | None,
    position: int | None,
) -> Any:
    """A get_book result as /api/releases sees it, title override applied."""
    from shelfmark.metadata_providers.hardcover import HardcoverProvider

    raw: dict[str, object] = {
        "id": book_id,
        "title": title,
        "subtitle": subtitle,
        "contributions": [{"author": {"name": name}} for name in authors],
    }
    if series is not None:
        raw["featured_book_series"] = {
            "position": position,
            "series": {"id": 1, "name": series, "primary_books_count": 20},
        }
    book = HardcoverProvider(api_key="unused")._parse_book(raw)
    book.title = title
    return book


def run_books(
    books: Sequence[tuple[int, str, str | None, list[str], str | None, int | None]],
    client_factory: Callable[[], Any],
) -> list[BookResult]:
    """Search each book through the production plan and Prowlarr source path."""
    from shelfmark.core import search_deadline
    from shelfmark.core.search_plan import build_release_search_plan
    from shelfmark.core.search_queries import build_search_identity, is_identity_hit
    from shelfmark.release_sources import SourceUnavailableError
    from shelfmark.release_sources.prowlarr.source import ProwlarrSource

    results: list[BookResult] = []
    for row in books:
        book = endpoint_book(*row)
        plan = build_release_search_plan(book, languages=["en"], content_type="ebook")
        identity = plan.identity or build_search_identity(
            title=book.title,
            current_query=book.search_title or book.title,
            series_name=book.series_name,
            series_position=book.series_position,
        )

        client = _CountingClient(client_factory())
        source = ProwlarrSource()
        error: str | None = None
        started = time.monotonic()
        with (
            search_deadline.search_deadline(),
            patch.object(source, "_get_client", return_value=client),
        ):
            try:
                releases = source.search(book, plan, content_type="ebook")
            except (SourceUnavailableError, TimeoutError) as e:
                releases, error = [], str(e)
        elapsed = time.monotonic() - started

        hits = [
            release.title
            for release in releases
            if is_identity_hit(
                release.title,
                series_key=identity.series_key,
                position=identity.position,
                title_tokens=identity.title_tokens,
                content_type="ebook",
                book_is_comic=identity.book_is_comic,
            )
        ]
        results.append(
            BookResult(
                title=book.title,
                requests=client.requests,
                fallback_variants=sum(1 for v in plan.title_variants if v.fallback),
                elapsed=elapsed,
                releases=[release.title for release in releases],
                hits=hits,
                incomplete=getattr(source, "last_search_incomplete", False) is True,
                error=error,
            )
        )
    return results


def report(results: Sequence[BookResult], json_path: Path | None = None) -> int:
    """Print every book's titles for adjudication; write them as JSON; return the exit code.

    Only the standalone check decides the exit code: whether a book was *found* is for a
    person to mark in the JSON, not for the predicate under test.
    """
    for result in results:
        print(
            f"{result.requests:3d} req {result.elapsed:6.1f}s fallbacks={result.fallback_variants} "
            f"releases={len(result.releases)} predicate_hits={len(result.hits)}"
            f"{' INCOMPLETE' if result.incomplete else ''}  {result.title}"
        )
        if result.error:
            print(f"      error: {result.error}")
        for title in result.releases[:SHOWN_TITLES]:
            marker = "  "
            if title in result.hits:
                marker = "S " if title in result.suspect else "H "
            print(f"      {marker}{title}")
        if len(result.releases) > SHOWN_TITLES:
            print(f"      ... {len(result.releases) - SHOWN_TITLES} more (all in the JSON)")
    standalones_changed = [
        r.title for r in results if r.title in STANDALONES and r.fallback_variants
    ]
    print(f"\nrequests total {sum(r.requests for r in results)}")
    print(
        f"predicate hits on {sum(bool(r.hits) for r in results)}/{len(results)} books "
        f"(informational; suspect {sum(len(r.suspect) for r in results)})"
    )
    print(f"Adjudicate 'found' per book (target >= {TARGET_FOUND} of {len(BOOKS)}).")
    if json_path is not None:
        payload = [
            {
                "title": r.title,
                "found": None,
                "requests": r.requests,
                "elapsed_seconds": round(r.elapsed, 1),
                "fallback_variants": r.fallback_variants,
                "incomplete": r.incomplete,
                "error": r.error,
                "predicate_hits": r.hits,
                "suspect": r.suspect,
                "releases": r.releases,
            }
            for r in results
        ]
        json_path.write_text(json.dumps(payload, indent=1, ensure_ascii=False) + "\n")
        print(f"wrote {json_path}")
    if standalones_changed:
        print(f"standalones with fallbacks (should be none): {standalones_changed}")
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else "")
    parser.add_argument("--url", default=os.environ.get("PROWLARR_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("PROWLARR_API_KEY", ""))
    parser.add_argument("--auto-expand", action="store_true", help="PROWLARR_AUTO_EXPAND on")
    parser.add_argument("--indexer-timeout", type=int, default=None)
    parser.add_argument("--only", default="", help="run only books whose title contains this")
    parser.add_argument("--json", default="ladder_acceptance.json", help="where to write titles")
    args = parser.parse_args(argv)

    if not args.url or not args.api_key:
        print("Set PROWLARR_URL and PROWLARR_API_KEY (or pass --url/--api-key).")
        return 2

    from shelfmark.core.config import config
    from shelfmark.release_sources.prowlarr.api import ProwlarrClient

    overrides: dict[str, object] = {
        "PROWLARR_URL": args.url,
        "PROWLARR_API_KEY": args.api_key,
        "PROWLARR_INDEXERS": "",
        "PROWLARR_AUTO_EXPAND": args.auto_expand,
        "PROWLARR_USE_SEED_PREFERENCES": False,
    }
    if args.indexer_timeout is not None:
        overrides["PROWLARR_INDEXER_TIMEOUT"] = args.indexer_timeout
    real_get = config.get

    def get(key: str, default: object = None, user_id: int | None = None) -> object:
        if key in overrides:
            return overrides[key]
        return real_get(key, default, user_id=user_id)

    books = [row for row in BOOKS if args.only.lower() in row[1].lower()]
    with patch.object(config, "get", get):
        results = run_books(books, lambda: ProwlarrClient(args.url, args.api_key))
    return report(results, Path(args.json))


if __name__ == "__main__":
    sys.exit(main())
