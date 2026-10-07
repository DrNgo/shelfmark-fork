# Release Search Query Ladder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ebook release searches through Prowlarr and Newznab find light-novel volumes that today's single title query misses, by trying a few release-shaped fallback queries only while nothing found so far is actually the requested book.

**Architecture:** A new pure module, `shelfmark/core/search_queries.py`, builds the fallback ladder (`build_fallback_queries`) and decides when a release name is the requested book (`is_identity_hit`). `build_release_search_plan` gains `content_type` and, for ebook metadata searches only, appends the ladder as `ReleaseSearchVariant(fallback=True)` entries after today's mandatory and localized variants, plus a `SearchIdentity` on the plan. Prowlarr and Newznab run mandatory variants exactly as today, then run fallback variants only while no identity hit exists, excluding failed indexers/connections, capping fallbacks at 4 requests per indexer/connection (expansion included), skipping requests the remaining budget cannot cover, and logging every request at INFO. A search cut short keeps what it found and says so through `last_search_incomplete`, which `/api/releases` reports in `search_info`.

**Tech Stack:** Python 3.14 (Flask backend), uv, pytest (+xdist), Ruff 0.16.5, BasedPyright, Vulture, defusedxml.

**Spec:** `docs/superpowers/specs/2026-10-07-release-search-query-ladder-design.md` — read it fully, including the revisions table, before any task.

## Global Constraints

- **Fallbacks apply to ebook searches through Prowlarr and Newznab only.** Audiobook searches, IRC, AudiobookBay and direct download (`grouped_title_variants`) behave exactly as today.
- Today's query (`book.search_title or book.title`) stays the first variant: `primary_query` and `grouped_title_variants` are unchanged.
- Ladder order: `<Series> Vol. N`, `<Series> vNN` (zero-padded to 2 digits), `<Name>` after `Vol. N:`/`Volume N:`, `<Series> Volume NN`, the cleaned full title — deduplicated against `current_query` (case and whitespace only) and each other (cleaned).
- Cleaning: remove `(Light Novel)`, `(Novel)`, `(LN)` anywhere (case-insensitive), replace `:` and `,` with spaces, collapse whitespace. `(Manga)` and other parentheses stay.
- Positions: finite, non-negative, integral (including `0`), at most 10 000; integral floats and numeric strings normalized exactly (no truncation); booleans, negatives, NaN, infinity and digit strings longer than six digits rejected. Metadata vs title disagreement → no series rungs.
- Fractional positions, ranges, omnibus/collected editions and `Part I`/`Part II` titles get no single-volume series rungs (a `Part` in the book name after a single parsed `Vol. N:` does not count — Ruling 6); Roman-numeral volumes produce only rung 5. Standalones get no fallbacks.
- `build_fallback_queries` and `is_identity_hit` are pure and total: junk input drops rungs or returns `False`, never raises.
- `is_identity_hit` decides only whether fallbacks may stop; it never filters or reorders results.
- Fallback cap: **4 requests per indexer (Prowlarr) / per connection (Newznab) per search, auto-expanded calls included.** A rate-limit response counts as a failure.
- Persistent failure exclusion applies to **fallback requests and the expansion of fallback requests only**; mandatory variants (and their expansion) run exactly as today.
- Fallbacks share the existing source and endpoint deadlines; a fallback request is skipped when the remaining budget is below that request's timeout; accumulated results are always kept; a search cut short is reported as incomplete, never as a completed "no releases".
- Manual queries keep today's trimming and 256-character limit and get no fallbacks; manual-provider books get no fallbacks.
- Backend only: no frontend change.
- Python: bare `except A, B:` (PEP 758) is valid here — do not "fix" it.
- Never contact Prowlarr, Hardcover or the cluster from a plan step except the user-gated Task 7 and Task 8.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Rulings on spec ambiguities

These are binding for this plan; each is pinned by a test in the owning task.

1. **Fallback queries carry no author** in what Prowlarr/Newznab send: both sources already send `variant.title` only, and the measurement (spec "Evidence") was title-only. Fallback variants still store the plan author so `ReleaseSearchVariant.query` stays meaningful.
2. **Dedup.** Today's query and every mandatory/localized title are sent *uncleaned*, so a rung is dropped against them only when it is the same request apart from case and whitespace (`exact_query_key`): DxD vol 5's cleaned full title `"High School DxD Vol. 5 Hellcat of the Underworld Training Camp"` is a different request from `"High School DxD (Light Novel), Vol. 5: Hellcat…"` and stays; Death March's `"… Rhapsody Vol. 5"` stays next to `"… Rhapsody, Vol. 5"`. Rungs are deduplicated among themselves by their cleaned key (`query_key`): Shield Hero's cleaned full title equals rung 1 and is dropped.
3. **Positions.** Only an *absent* `series_position` (None or blank) is filled from the title; a present but unusable one (1.5, -1, `True`, NaN, > 10 000, a digit string longer than six digits) suppresses series rungs instead of being replaced by the title's number. Numeric strings are parsed exactly (`Decimal`), so `"3.0000000000000001"` is not 3. A title volume token longer than six digits or above 10 000 is not parsed.
4. **`series_name` wins over the parsed series name** when both exist (spec: "series_name, cleaned, if non-blank … Otherwise … parsed"); only *position* disagreement is a conflict.
5. **A volume marker the parser cannot read** (`Vol. III`, `Vol. 1.5`, `Vol. 5 Part 1`) suppresses series rungs even when metadata has a position — the title says it is not a plain single volume.
6. **A `Part N`/`Part I` marker suppresses series rungs (1, 2, 4) only when it is attached to the volume token** (`"Spice, Vol. 2 Part 1"` — which never parses, see Ruling 5) **or when the title has no volume number** (`"Spice Part II"` with metadata position 2). A `Part` in the book *name* after a single integral `Vol. N:` does not: Overlord vol 5 (`"Overlord (Light Novel), Vol. 5: The Men of the Kingdom Part I"`) and vol 6 (`"…Part II"`) are distinct volumes and get rungs 1, 2, 4 (`"Overlord v05"` found the real light-novel release, `Overlord.v05.2018.Digital.danke-Empire`, on 2026-10-07).
7. **Standalone** = no volume marker in the title and no (series name + present position) pair in metadata.
8. **Identity for a series volume** (spec §3) requires a *complete* volume token for this volume and no other: `Vol. N`, `Volume N`, `vN`, `[N]`, `- N`, and `<last series token> NN - ` (`"The Expanse 01 - Leviathan Wakes"`, `"Overlord 10 - The Ruler of Conspiracy"`). A number followed by a fraction (`5.5`), a letter (`5a`) or a second volume (`5 & 6`, `5 and 6`, `5 to 7`, `5-7`, `5–7`, `5—7`, `5+6`, `v05-07`) is not a single complete volume, so the release is not a hit; a year after the number (`Vol.05.2016`) is not a fraction. **When the series identity is suppressed** (conflict, fraction, collection, split part), the title-token rule uses the significant tokens of the *full cleaned title* (minus bare `vol`/`volume`), not today's often-shortened query, and needs at least two of them — with fewer, nothing stops the ladder (costs requests, never results).
9. **"Not video" for an ebook search also rejects audio-only names** (`M4B`, `MP3`, `audiobook`, …) — that is what the predicate's `content_type` argument is for. The measured `"…Volume 15 … [ENG / M4B]"` is therefore not an ebook hit.
10. **Manga/comic releases are not the light novel** (an extension of the spec's "not video" clause). If neither the book's title nor its series name contains `manga`/`comic` (case-insensitive), a release name carrying the token `manga`, `comic`/`comics` or `graphic novel` is not an identity hit — it is still returned, never filtered. `SearchIdentity.book_is_comic` carries this; `is_identity_hit` takes it as an optional `book_is_comic: bool = False` keyword (the spec's other arguments are unchanged). Live 2026-10-07: `"Overlord Vol. 5"` returned only `Yen.Press-Overlord.Vol.05.Manga.2022.Hybrid.Comic.eBook-BitBook` and `Yen.Press-Overlord.The.Undead.King.Oh.Vol.05.2022.Hybrid.Comic.eBook-BitBook`; without this rule they stopped the ladder before `"Overlord v05"`.
11. **Fallbacks need a non-empty title**: an empty title still falls back to ISBN queries with no ladder.
12. **`content_type` defaults to `None`** in `build_release_search_plan` (no ladder); only the release endpoint passes it. The Prowlarr retry handler (`prowlarr/handler.py`) keeps building plans without it.
13. **Deadlines and incompleteness.** A timeout among the *mandatory* variants no longer discards what was already found: the source returns those results and marks the search incomplete; with nothing found it raises exactly as today (Prowlarr: `TimeoutError`). Fallback requests never raise on the deadline: they are skipped (stop reason `deadline`); with nothing found the source raises `SourceUnavailableError("…search incomplete…")`. Newznab, which used to swallow its timeout and return `[]`, raises the same "incomplete" error when a cut-short search found nothing. Each source sets `last_search_incomplete: bool` (the existing `last_search_type` pattern) and `/api/releases` adds `"incomplete": true` to that source's `search_info` entry. No frontend change.
14. **Exclusion scope.** A failed (or rate-limited) indexer/connection gets no further *fallback* requests, nor expansion of fallback requests. Mandatory variants and their auto-expansion run exactly as today: Prowlarr does not expand a mandatory pass in which anything failed (today's #1249 rule); Newznab retries an empty-looking mandatory answer — now including a detected failure, which looked empty before — without categories, as it always did. Fallback expansion goes to every indexer still eligible when the rung returned nothing.
15. **Fallback targets** come from the enabled-indexer snapshot taken at the start of the search: eligibility (not failed, under cap) and the remaining budget are checked before anything is sent, so a capped or expired ladder makes no client call at all. When no category-compatible indexer is eligible but others are, the rung is sent to them uncategorized (whatever `PROWLARR_AUTO_EXPAND` says — it is the only way the rung reaches them) before the ladder stops with `cap`.
16. **Stop reasons logged:** `hit`, `cap` (no indexer has fallback requests left — capped or failed), `deadline`, `exhausted`, plus `not planned` (no ladder) and, for Newznab, `failed` (the connection failed, so it ran no fallbacks, or a fallback request failed).
17. **Indexer error documents.** A 200 response whose body is a Torznab/Newznab `<error code=… description=…/>` document is a failed search. It is parsed as XML with defusedxml (`parse_torznab_error`, shared by both clients — single or double quotes, any whitespace, a namespace prefix), never pattern-matched. Rate-limited = code 429/500/501 or a description containing "limit". Prowlarr raises `ProwlarrSearchError`, Newznab `NewznabSearchError`.
18. **Newznab result reporting.** Nothing left after the `plan.indexers` filter and any search failed → `SourceUnavailableError` naming the failures; with results, they are returned and the partial failure is logged. Ladder hits are judged only on rows actually retained (newly kept after GUID dedup and passing the `plan.indexers` filter).
19. **Acceptance oracle.** The harness runs each search under the production `search_deadline.search_deadline()` context and does not use `is_identity_hit` to decide "found": it prints the returned titles (top 30 and the total) with the predicate's hits/suspects as an informational column, and writes every title to JSON with `"found": null` for a person to adjudicate.

## Review Focus

1. **Scene-style release names** (`Seven.Seas-High.School.DxD.Vol.05.2016.Retail.eBook-BitBook`, a year after the volume) → still the right volume; the year is neither "another volume" nor a fraction. Tests: `TestIdentityPredicate::test_the_right_volume_is_a_hit` and `::test_a_year_after_the_volume_is_not_a_fraction` in Task 2.
2. **Volume 0 and numeric-string positions** (`series_position=0`, `"3"`, `3.0`) → normal rungs (`v00`, `Vol. 3`), never dropped as falsy. Test: `TestPositions::test_volume_zero_is_a_volume` and `::test_numeric_string_and_integral_float_positions_match_the_title` in Task 1.
3. **A localized title that equals a ladder query** (case/whitespace aside) → appears once, still mandatory, never run twice. Test: `TestFallbackVariants::test_a_duplicate_title_keeps_its_mandatory_status` in Task 3.
4. **The endpoint budget nearly spent by an earlier source** → Prowlarr skips fallbacks instead of starting requests it cannot finish, and reports the search incomplete. Test: `TestDeadline::test_the_endpoint_budget_counts_too` in Task 4.
5. **An aggregator (NZBHydra) returning the right book from an indexer the user filtered out** → does not stop the ladder, and is not shown. Test: `TestOnlyFilteredResultsCount::test_a_hit_from_an_unselected_indexer_does_not_stop_the_ladder` in Task 5.

## File Structure

| File | Change |
|---|---|
| `shelfmark/core/search_queries.py` | **new** — cleaning, position rules, title parse, `build_fallback_queries` (Task 1); `SearchIdentity`, `build_search_identity`, `is_identity_hit`, `any_identity_hit` (Task 2) |
| `shelfmark/core/search_plan.py` | `ReleaseSearchVariant.fallback`, `ReleaseSearchPlan.identity`, `build_release_search_plan(content_type=)` (Task 3) |
| `shelfmark/main.py` | `/api/releases` passes `content_type` to the plan; `search_info[source]["incomplete"]` (Task 3) |
| `shelfmark/core/search_deadline.py` | `remaining_seconds(source_deadline)` (Task 4) |
| `shelfmark/release_sources/prowlarr/torznab.py` | `TorznabError`, `parse_torznab_error` (Task 4; Newznab reuses it in Task 5) |
| `shelfmark/release_sources/prowlarr/api.py` | `ProwlarrSearchError.rate_limited`; error documents raise (Task 4) |
| `shelfmark/release_sources/prowlarr/source.py` | fallback ladder from the indexer snapshot, per-indexer exclusion and cap, deadline skip, partial results on timeout, `last_search_incomplete`, INFO request logs (Task 4) |
| `shelfmark/release_sources/newznab/api.py` | `NewznabSearchError`; `search` raises on failure and on error documents (Task 5) |
| `shelfmark/release_sources/newznab/source.py` | per-connection ladder on retained rows, failure reporting, cap, deadline, `last_search_incomplete`, INFO request logs (Task 5) |
| `scripts/ladder_acceptance.py` | **new** — user-gated live acceptance harness (Task 6) |
| `shelfmark/release_sources/irc/source.py`, `audiobookbay/source.py`, `direct_download.py`, frontend | **unchanged** |
| Tests | `tests/core/test_search_queries.py` (new), `tests/core/test_search_plan.py`, `tests/core/test_releases_api_content_type.py` (new), `tests/core/test_search_deadline.py`, `tests/prowlarr/test_torznab.py`, `tests/prowlarr/test_api_timeout.py`, `tests/prowlarr/test_source.py`, `tests/prowlarr/test_source_fallbacks.py` (new), `tests/newznab/test_api.py`, `tests/newznab/test_source.py`, `tests/newznab/test_source_fallbacks.py` (new), `tests/core/test_ladder_acceptance_script.py` (new) |

**Test-run notes (environment, not this feature):**
- `pytest` runs with `-n auto` by default (`pyproject.toml`). Run endpoint tests (`tests/core/test_releases_api_*.py`) without `tests/newznab` in the same invocation: `tests/newznab/conftest.py` stubs `flask_socketio`, which breaks `import shelfmark.main` when only those directories are collected. The full `tests/` run is unaffected.
- On a sandboxed macOS host `tests/core/test_search_deadline.py::test_html_get_page_will_not_start_a_bypass_on_a_spent_budget` can hang (it reaches the network). It is pre-existing and unrelated; deselect it with `--deselect` where noted.
- `tests/config/test_entrypoint_permissions.py` has 9 pre-existing failures on macOS; `basedpyright` has 4 pre-existing errors at `shelfmark/main.py:2305-2308`.

---

### Task 1: Fallback query ladder — cleaning, position rules, title parse

**Files:**
- Create: `shelfmark/core/search_queries.py`
- Test: `tests/core/test_search_queries.py` (new)

**Interfaces:**
- Consumes: `HardcoverProvider._parse_book(raw: dict) -> BookMetadata` (test fixtures only, to model `get_book`).
- Produces: `clean_query(text: object) -> str`; `query_key(text: object) -> str` (cleaned + casefolded: how rungs are compared with each other); `exact_query_key(text: object) -> str` (whitespace-collapsed + casefolded: how a rung is compared with a query sent as-is); `MAX_POSITION = 10_000`; `normalize_position(value: object) -> int | None`; `build_fallback_queries(*, title: object, current_query: object, series_name: object, series_position: object) -> list[str]`; private `_resolve(title: str, series_name: object, series_position: object) -> _Resolved` (fields `series: str`, `position: int | None`, `parsed: _ParsedTitle | None`, `standalone: bool`) and `_normalize_title(title: object) -> str`, which Task 2 reuses.

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_search_queries.py`. The 19 fixtures are built exactly the way `/api/releases` builds its input: Hardcover's `get_book` parser with series fields, then the endpoint's `book.title = title_param` override (`main.py`, release endpoint). `current_query` is today's real query, computed by the real `_compute_search_title`.

```python
"""The fallback query ladder and the identity check that stops it.

The 19 books are the ones measured against the live Prowlarr on 2026-10-07 (see the
design spec). Each fixture is built the way the release endpoint builds its input:
Hardcover's full-fetch parser (`get_book` -> `_parse_book`) with series fields, then the
endpoint's title override (`book.title = title_param`, main.py), so `search_title` is
the one `get_book` computed and `current_query` is today's real query.
"""

from __future__ import annotations

import math

import pytest

from shelfmark.core.search_queries import (
    build_fallback_queries,
    clean_query,
    normalize_position,
)
from shelfmark.metadata_providers import BookMetadata
from shelfmark.metadata_providers.hardcover import HardcoverProvider


def _endpoint_book(
    book_id: int,
    title: str,
    subtitle: str | None,
    authors: list[str],
    series: str | None = None,
    position: object = None,
) -> BookMetadata:
    """A get_book result as /api/releases sees it, title override applied."""
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
    book = HardcoverProvider(api_key="test-token")._parse_book(raw)
    # main.py: `if title_param: book.title = title_param` - the release modal sends the
    # search result's title, which for Hardcover is the same `title` field.
    book.title = title
    return book


def _ladder(book: BookMetadata) -> list[str]:
    return build_fallback_queries(
        title=book.title,
        current_query=book.search_title or book.title,
        series_name=book.series_name,
        series_position=book.series_position,
    )


MT = "Mushoku Tensei: Jobless Reincarnation (Light Novel)"
MT_SUB = "Jobless Reincarnation (Light Novel)"
OL = "Overlord (Light Novel)"
DXD = "High School DxD (Light Novel)"
SH = "The Rising of the Shield Hero (Light Novel)"
DM = "Death March to the Parallel World Rhapsody"

# (book, today's current query, exact fallback list)
MEASURED_BOOKS = [
    (
        _endpoint_book(730298, f"{MT}, Vol. 3", MT_SUB, ["Rifujin na Magonote"], MT, 3),
        "Jobless Reincarnation",
        [
            "Mushoku Tensei Jobless Reincarnation Vol. 3",
            "Mushoku Tensei Jobless Reincarnation v03",
            "Mushoku Tensei Jobless Reincarnation Volume 03",
        ],
    ),
    (
        _endpoint_book(730294, f"{MT}, Vol. 7", MT_SUB, ["Rifujin na Magonote"], MT, 7),
        "Jobless Reincarnation",
        [
            "Mushoku Tensei Jobless Reincarnation Vol. 7",
            "Mushoku Tensei Jobless Reincarnation v07",
            "Mushoku Tensei Jobless Reincarnation Volume 07",
        ],
    ),
    (
        _endpoint_book(
            730290,
            f"{MT}, Vol. 11",
            MT_SUB,
            ["Rifujin na Magonote", "Shirotaka"],
            MT,
            11,
        ),
        "Jobless Reincarnation",
        [
            "Mushoku Tensei Jobless Reincarnation Vol. 11",
            "Mushoku Tensei Jobless Reincarnation v11",
            "Mushoku Tensei Jobless Reincarnation Volume 11",
        ],
    ),
    (
        _endpoint_book(
            427621,
            "Leviathan Wakes",
            "Cow at Sea",
            ["James S. A. Corey"],
            "The Expanse",
            1,
        ),
        "Leviathan Wakes",
        ["The Expanse Vol. 1", "The Expanse v01", "The Expanse Volume 01"],
    ),
    (
        _endpoint_book(
            886465,
            f"{OL}, Vol. 2: The Dark Warrior",
            "The Dark Warrior",
            ["Kugane Maruyama"],
            OL,
            2,
        ),
        "The Dark Warrior",
        [
            "Overlord Vol. 2",
            "Overlord v02",
            "Overlord Volume 02",
            "Overlord Vol. 2 The Dark Warrior",
        ],
    ),
    (
        # "Part I" is the book's name; Vol. 5 is still a distinct volume. The name rung
        # equals today's query, so it is dropped.
        _endpoint_book(
            885683,
            f"{OL}, Vol. 5: The Men of the Kingdom Part I",
            "The Men of the Kingdom Part I",
            ["Kugane Maruyama"],
            OL,
            5,
        ),
        "The Men of the Kingdom Part I",
        [
            "Overlord Vol. 5",
            "Overlord v05",
            "Overlord Volume 05",
            "Overlord Vol. 5 The Men of the Kingdom Part I",
        ],
    ),
    (
        _endpoint_book(
            1230950,
            f"{OL}, Vol. 10: The Ruler of Conspiracy",
            "The Ruler of Conspiracy",
            ["Kugane Maruyama"],
            OL,
            10,
        ),
        "The Ruler of Conspiracy",
        [
            "Overlord Vol. 10",
            "Overlord v10",
            "Overlord Volume 10",
            "Overlord Vol. 10 The Ruler of Conspiracy",
        ],
    ),
    (
        _endpoint_book(
            2486566,
            f"{DXD}, Vol. 3: Excalibur of the Moonlit Schoolyard",
            "Excalibur of the Moonlit Schoolyard",
            ["Ichiei Ishibumi"],
            DXD,
            3,
        ),
        "Excalibur of the Moonlit Schoolyard",
        [
            "High School DxD Vol. 3",
            "High School DxD v03",
            "High School DxD Volume 03",
            "High School DxD Vol. 3 Excalibur of the Moonlit Schoolyard",
        ],
    ),
    (
        _endpoint_book(
            2486567,
            f"{DXD}, Vol. 4: Vampire of the Suspended Classroom",
            "Vampire of the Suspended Classroom",
            ["Ichiei Ishibumi"],
            DXD,
            4,
        ),
        "Vampire of the Suspended Classroom",
        [
            "High School DxD Vol. 4",
            "High School DxD v04",
            "High School DxD Volume 04",
            "High School DxD Vol. 4 Vampire of the Suspended Classroom",
        ],
    ),
    (
        _endpoint_book(
            2575261,
            f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp",
            None,
            ["Ichiei Ishibumi"],
            DXD,
            5,
        ),
        f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp",
        [
            "High School DxD Vol. 5",
            "High School DxD v05",
            "Hellcat of the Underworld Training Camp",
            "High School DxD Volume 05",
            # Today's query is sent uncleaned ("(Light Novel), Vol. 5:"), so the
            # cleaned full title is a different request and stays.
            "High School DxD Vol. 5 Hellcat of the Underworld Training Camp",
        ],
    ),
    (
        _endpoint_book(
            2575267,
            f"{DXD}, Vol. 6: Holy Behind the Gymnasium",
            None,
            ["Ichiei Ishibumi"],
            DXD,
            6,
        ),
        f"{DXD}, Vol. 6: Holy Behind the Gymnasium",
        [
            "High School DxD Vol. 6",
            "High School DxD v06",
            "Holy Behind the Gymnasium",
            "High School DxD Volume 06",
            # Today's query is sent uncleaned ("(Light Novel), Vol. 6:"), so the
            # cleaned full title is a different request and stays.
            "High School DxD Vol. 6 Holy Behind the Gymnasium",
        ],
    ),
    (
        _endpoint_book(1282767, f"{SH}, Vol. 3", None, ["Aneko Yusagi"], SH, 3),
        f"{SH}, Vol. 3",
        [
            "The Rising of the Shield Hero Vol. 3",
            "The Rising of the Shield Hero v03",
            "The Rising of the Shield Hero Volume 03",
        ],
    ),
    (
        _endpoint_book(1283002, f"{SH}, Vol. 8", None, ["Aneko Yusagi"], SH, 8),
        f"{SH}, Vol. 8",
        [
            "The Rising of the Shield Hero Vol. 8",
            "The Rising of the Shield Hero v08",
            "The Rising of the Shield Hero Volume 08",
        ],
    ),
    (
        _endpoint_book(1561928, f"{SH}, Vol. 15", "The Manga Companion", ["Aneko Yusagi"], SH, 15),
        f"{SH}, Vol. 15",
        [
            "The Rising of the Shield Hero Vol. 15",
            "The Rising of the Shield Hero v15",
            "The Rising of the Shield Hero Volume 15",
        ],
    ),
    (
        _endpoint_book(785991, f"{DM}, Vol. 5", None, ["Hiro Ainana"], DM, 5),
        f"{DM}, Vol. 5",
        [f"{DM} Vol. 5", f"{DM} v05", f"{DM} Volume 05"],
    ),
    (
        _endpoint_book(785985, f"{DM}, Vol. 12", None, ["Hiro Ainana"], DM, 12),
        f"{DM}, Vol. 12",
        [f"{DM} Vol. 12", f"{DM} v12", f"{DM} Volume 12"],
    ),
    (
        _endpoint_book(427578, "Project Hail Mary", "A Novel", ["Andy Weir"]),
        "Project Hail Mary",
        [],
    ),
    (
        _endpoint_book(
            511526,
            "The Housemaid",
            "An Absolutely Addictive Psychological Thriller with a Jaw-dropping Twist",
            ["Freida McFadden"],
        ),
        "The Housemaid",
        [],
    ),
    (
        _endpoint_book(476001, "Reminders of Him", None, ["Colleen Hoover"]),
        "Reminders of Him",
        [],
    ),
]


class TestMeasuredBooks:
    @pytest.mark.parametrize(
        ("book", "current_query", "expected"),
        MEASURED_BOOKS,
        ids=[book.title for book, _, _ in MEASURED_BOOKS],
    )
    def test_each_measured_book_gets_its_exact_ladder(self, book, current_query, expected):
        assert (book.search_title or book.title) == current_query
        assert _ladder(book) == expected

    def test_there_are_nineteen(self):
        assert len(MEASURED_BOOKS) == 19


class TestCleaning:
    def test_medium_labels_go_anywhere_in_any_case(self):
        assert clean_query("Overlord (light NOVEL), Vol. 2") == "Overlord Vol. 2"
        assert clean_query("A (Novel) B (LN)") == "A B"

    def test_other_parentheses_stay(self):
        assert clean_query("Overlord (Manga), Vol. 2") == "Overlord (Manga) Vol. 2"

    def test_colons_and_commas_become_spaces(self):
        assert clean_query("Series:Book,  Vol. 3") == "Series Book Vol. 3"

    def test_junk_is_empty(self):
        assert clean_query(None) == ""
        assert clean_query(42) == ""

    def test_every_rung_is_cleaned(self):
        ladder = build_fallback_queries(
            title="Spice (Light Novel), Vol. 2: Wolf, Again",
            current_query="Nothing Alike",
            series_name="Spice (LN)",
            series_position=2,
        )
        assert ladder == [
            "Spice Vol. 2",
            "Spice v02",
            "Wolf Again",
            "Spice Volume 02",
            "Spice Vol. 2 Wolf Again",
        ]


class TestPositions:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (0, 0),
            (3, 3),
            (3.0, 3),
            ("3", 3),
            (" 07 ", 7),
            ("3.0", 3),
            (12, 12),
            (10_000, 10_000),
            ("10000", 10_000),
        ],
    )
    def test_usable_positions(self, value, expected):
        assert normalize_position(value) == expected

    @pytest.mark.parametrize(
        "value",
        [
            True,
            False,
            -1,
            -1.0,
            "-2",
            1.5,
            "1.5",
            math.nan,
            math.inf,
            "nan",
            "inf",
            "",
            "x",
            [3],
            10_001,
            "10001",
            1e300,
            "1234567",
            "9" * 5000,
            # Exact parsing: as a float this would round to 3.0.
            "3.0000000000000001",
        ],
    )
    def test_unusable_positions(self, value):
        assert normalize_position(value) is None

    def test_volume_zero_is_a_volume(self):
        assert build_fallback_queries(
            title="Spice, Vol. 0",
            current_query="Spice, Vol. 0",
            series_name="Spice",
            series_position=0,
        ) == ["Spice Vol. 0", "Spice v00", "Spice Volume 00"]

    def test_numeric_string_and_integral_float_positions_match_the_title(self):
        for position in ("3", 3.0):
            assert build_fallback_queries(
                title="Spice, Vol. 3",
                current_query="x",
                series_name="Spice",
                series_position=position,
            )[:2] == ["Spice Vol. 3", "Spice v03"]

    @pytest.mark.parametrize("position", [1.5, True, -3, math.nan])
    def test_an_unusable_position_gives_no_series_rungs(self, position):
        assert build_fallback_queries(
            title="Spice, Vol. 3",
            current_query="x",
            series_name="Spice",
            series_position=position,
        ) == ["Spice Vol. 3"]

    def test_position_only_from_metadata_when_the_title_has_none(self):
        assert build_fallback_queries(
            title="Wolf and Parchment",
            current_query="Wolf and Parchment",
            series_name="Spice",
            series_position=4,
        ) == ["Spice Vol. 4", "Spice v04", "Spice Volume 04"]

    def test_position_only_from_the_title_when_metadata_has_none(self):
        assert build_fallback_queries(
            title="Spice, Vol. 4",
            current_query="Spice, Vol. 4",
            series_name="Spice",
            series_position=None,
        ) == ["Spice Vol. 4", "Spice v04", "Spice Volume 04"]

    def test_series_only_from_the_title_when_metadata_has_none(self):
        assert build_fallback_queries(
            title="Spice and Wolf Volume 4: Pagan Town",
            current_query="Pagan Town",
            series_name=None,
            series_position=4,
        ) == [
            "Spice and Wolf Vol. 4",
            "Spice and Wolf v04",
            "Spice and Wolf Volume 04",
            "Spice and Wolf Volume 4 Pagan Town",
        ]


class TestDistinguishingIdentities:
    def test_conflicting_positions_give_no_series_rungs(self):
        assert build_fallback_queries(
            title="Spice, Vol. 5: Wolf",
            current_query="Wolf",
            series_name="Spice",
            series_position=6,
        ) == ["Spice Vol. 5 Wolf"]

    @pytest.mark.parametrize(
        ("title", "position", "expected"),
        [
            ("Spice, Vol. 1-3", 1, ["Spice Vol. 1-3"]),
            ("Spice, Vol. 1\u20133", 1, ["Spice Vol. 1\u20133"]),
            ("Spice, Vols. 1-3", 1, ["Spice Vols. 1-3"]),
            ("Spice Omnibus 1", 1, ["Spice Omnibus 1"]),
            ("Spice: Collected Edition 1", 1, ["Spice Collected Edition 1"]),
            ("Spice Box Set 1", 1, ["Spice Box Set 1"]),
            ("Spice, Vol. 2 Part 1", 2, ["Spice Vol. 2 Part 1"]),
            ("Spice Part II", 2, ["Spice Part II"]),
            ("Spice, Vol. 1.5", 1.5, ["Spice Vol. 1.5"]),
        ],
    )
    def test_ranges_collections_parts_and_fractions_keep_only_their_own_text(
        self, title, position, expected
    ):
        assert (
            build_fallback_queries(
                title=title,
                current_query="nothing alike",
                series_name="Spice",
                series_position=position,
            )
            == expected
        )

    @pytest.mark.parametrize(
        ("book_id", "volume", "name"),
        [
            (885683, 5, "The Men of the Kingdom Part I"),
            (885684, 6, "The Men of the Kingdom Part II"),
        ],
    )
    def test_a_part_in_the_book_name_keeps_the_series_rungs(self, book_id, volume, name):
        # Overlord vols 5 and 6 are distinct volumes whose names end in "Part I"/"Part II";
        # "Overlord v05" is what found the real light-novel release on 2026-10-07.
        book = _endpoint_book(
            book_id,
            f"{OL}, Vol. {volume}: {name}",
            name,
            ["Kugane Maruyama"],
            OL,
            volume,
        )

        assert _ladder(book) == [
            f"Overlord Vol. {volume}",
            f"Overlord v{volume:02d}",
            f"Overlord Volume {volume:02d}",
            f"Overlord Vol. {volume} {name}",
        ]

    def test_roman_numeral_volumes_only_get_the_cleaned_title(self):
        assert build_fallback_queries(
            title="Spice, Vol. III",
            current_query="Spice",
            series_name=None,
            series_position=None,
        ) == ["Spice Vol. III"]

    def test_roman_numeral_title_overrides_a_metadata_position(self):
        assert build_fallback_queries(
            title="Spice, Vol. III",
            current_query="Spice",
            series_name="Spice",
            series_position=3,
        ) == ["Spice Vol. III"]


class TestStandalones:
    @pytest.mark.parametrize(
        ("title", "current_query", "series_name", "series_position"),
        [
            ("Project Hail Mary", "Project Hail Mary", None, None),
            ("Mistborn: The Final Empire", "The Final Empire", None, None),
            ("Mistborn: The Final Empire", "The Final Empire", "Mistborn", None),
            ("Dune", "Dune", None, 1),
            ("", "", None, None),
        ],
    )
    def test_no_series_and_no_volume_means_no_fallbacks(
        self, title, current_query, series_name, series_position
    ):
        assert (
            build_fallback_queries(
                title=title,
                current_query=current_query,
                series_name=series_name,
                series_position=series_position,
            )
            == []
        )

    def test_oversized_title_volumes_are_not_parsed(self):
        for title in ("Spice, Vol. 9999999", "Spice, Vol. 10001", f"Spice, Vol. {'9' * 5000}"):
            assert build_fallback_queries(
                title=title, current_query="x", series_name="Spice", series_position=None
            ) == [clean_query(title)]

    def test_junk_input_never_raises(self):
        assert (
            build_fallback_queries(
                title=None, current_query=None, series_name=7, series_position=object()
            )
            == []
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_search_queries.py -q`
Expected: FAIL — collection error `ModuleNotFoundError: No module named 'shelfmark.core.search_queries'`

- [ ] **Step 3: Implement `shelfmark/core/search_queries.py`**

Create the file with exactly:

```python
"""Fallback release-search queries and the identity check that decides when they stop.

Release search sends one title-shaped query per language variant, built from
``book.search_title or book.title``. For light-novel volumes that query often finds
nothing: Hardcover data puts "(Light Novel)" in titles no release carries, or reduces a
volume to a subtitle no release uses. The ladder below adds a few release-shaped
queries ("<Series> Vol. N", "<Series> vNN", ...) for Prowlarr and Newznab to try, in
order, only while nothing found so far is actually the requested book.

Both entry points are pure and total: junk input drops rungs or returns False, and
nothing here raises.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Medium labels no release name carries. "(Manga)" is a different adaptation, so it stays.
_MEDIUM_LABEL_RE = re.compile(r"\s*\((?:light\s+novel|novel|ln)\)", re.IGNORECASE)

# "<Series>, Vol. N[: <Name>]" / "<Series> Volume N[: <Name>]" (medium labels removed first).
# The token is everything up to whitespace or a colon, so "1-3", "1.5" and "III" reach the
# digit check whole instead of being cut down to a number they do not mean.
_TITLE_PARSE_RE = re.compile(
    r"^(?P<series>.+?)(?:\s*,\s*|\s+)vol(?:ume)?s?\b\.?\s*(?P<token>[^\s:]+)"
    r"(?:\s*:\s*(?P<name>.*))?$",
    re.IGNORECASE,
)
_VOLUME_MARKER_RE = re.compile(r"\bvol(?:ume)?s?\b", re.IGNORECASE)

# One part of a split book. Only a title without a single parsed volume number is
# suppressed by it: in "Overlord, Vol. 5: The Men of the Kingdom Part I" the part is the
# book's name and Vol. 5 is still a distinct volume, while "Spice, Vol. 2 Part 1" never
# parses (its volume token is not alone) and "Spice Part II" carries no volume at all.
_PART_RE = re.compile(r"\bpart\s+(?:[ivx]+|\d+|one|two|three|four|five)\b", re.IGNORECASE)

# Titles naming more than one volume: "Overlord Vol. 5" would be a different book (or
# several), so these get no single-volume series rungs.
_DISTINGUISHING_RE = re.compile(
    r"\b(?:omnibus|collected|box(?:ed)?\s*set"
    r"|vol(?:ume)?s?\.?\s*\d+\s*(?:-|–|—|~|to|and|&)\s*\d+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ParsedTitle:
    series: str
    volume: int
    name: str


def clean_query(text: object) -> str:
    """Drop medium labels, turn ``:`` and ``,`` into spaces, collapse whitespace."""
    if not isinstance(text, str):
        return ""
    without_labels = _MEDIUM_LABEL_RE.sub(" ", text)
    return " ".join(re.sub(r"[:,]", " ", without_labels).split())


def query_key(text: object) -> str:
    """How two *ladder* queries are compared: cleaned, so punctuation does not count."""
    return clean_query(text).casefold()


def exact_query_key(text: object) -> str:
    """How a ladder query is compared with a query that is sent as-is.

    Today's query goes to the indexer uncleaned, so "High School DxD (Light Novel), Vol. 5:
    Hellcat..." and its cleaned form are different requests: only case and whitespace
    are ignored here.
    """
    return " ".join(text.split()).casefold() if isinstance(text, str) else ""


# No real series reaches this; anything larger is junk, and bounding it keeps int()
# away from pathological digit strings.
MAX_POSITION = 10_000
_MAX_POSITION_DIGITS = 6


def normalize_position(value: object) -> int | None:
    """A finite, non-negative, integral position up to ``MAX_POSITION``, or None.

    Integral floats and numeric strings are accepted without truncation ("3", 3.0,
    "3.0" parsed exactly); booleans, negatives, fractions, NaN, infinity, oversized
    numbers and digit strings longer than six digits are not.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        number = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        digits = text.split(".", 1)[0].lstrip("+")
        if not text or len(text) > 2 * _MAX_POSITION_DIGITS or len(digits) > _MAX_POSITION_DIGITS:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    else:
        return None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        return None
    if number > MAX_POSITION:
        return None
    return int(number)


def _position_is_absent(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_title(title: str) -> _ParsedTitle | None:
    match = _TITLE_PARSE_RE.match(_MEDIUM_LABEL_RE.sub("", title).strip())
    if match is None:
        return None
    token = match.group("token")
    if not (token.isascii() and token.isdigit()) or len(token) > _MAX_POSITION_DIGITS:
        return None
    if int(token) > MAX_POSITION:
        return None
    return _ParsedTitle(
        series=match.group("series").strip(),
        volume=int(token),
        name=(match.group("name") or "").strip(),
    )


@dataclass(frozen=True)
class _Resolved:
    series: str
    position: int | None
    parsed: _ParsedTitle | None
    standalone: bool


def _resolve(title: str, series_name: object, series_position: object) -> _Resolved:
    """Work out the single-volume identity the series rungs may use, if any."""
    parsed = _parse_title(title)
    has_marker = bool(_VOLUME_MARKER_RE.search(title))
    cleaned_series_name = clean_query(series_name)

    position_absent = _position_is_absent(series_position)
    metadata_position = None if position_absent else normalize_position(series_position)

    standalone = not has_marker and not (cleaned_series_name and not position_absent)

    series = cleaned_series_name or (clean_query(parsed.series) if parsed else "")
    # Only an absent position is filled in from the title; an unusable one stays None.
    title_volume = parsed.volume if parsed else None
    position = title_volume if position_absent else metadata_position

    usable = (
        bool(series)
        and position is not None
        # A position that is present but unusable (1.5, -1, True) is not replaced by
        # the title's number: the metadata says this is not a plain single volume.
        and (position_absent or metadata_position is not None)
        # Metadata and title disagree: neither can be trusted.
        and not (parsed is not None and parsed.volume != position)
        # The title names a volume we could not parse ("Vol. III", "Vol. 1.5").
        and not (has_marker and parsed is None)
        and not _DISTINGUISHING_RE.search(title)
        and not (parsed is None and _PART_RE.search(title))
    )
    if not usable:
        return _Resolved(series="", position=None, parsed=parsed, standalone=standalone)
    return _Resolved(series=series, position=position, parsed=parsed, standalone=standalone)


def _normalize_title(title: object) -> str:
    return " ".join(title.split()) if isinstance(title, str) else ""


def build_fallback_queries(
    *,
    title: object,
    current_query: object,
    series_name: object,
    series_position: object,
) -> list[str]:
    """Release-shaped queries to try after ``current_query`` finds nothing usable.

    Order: ``<Series> Vol. N``, ``<Series> vNN``, the book name after ``Vol. N:``,
    ``<Series> Volume NN``, the cleaned full title - each cleaned. A rung is dropped
    when it is the same request as ``current_query`` (case and whitespace aside; today's
    query is sent uncleaned) or the same cleaned query as an earlier rung.
    """
    title_text = _normalize_title(title)
    resolved = _resolve(title_text, series_name, series_position)
    if resolved.standalone:
        return []

    candidates: list[str] = []
    if resolved.position is not None:
        candidates += [
            f"{resolved.series} Vol. {resolved.position}",
            f"{resolved.series} v{resolved.position:02d}",
        ]
    if resolved.parsed is not None and resolved.parsed.name:
        candidates.append(clean_query(resolved.parsed.name))
    if resolved.position is not None:
        candidates.append(f"{resolved.series} Volume {resolved.position:02d}")
    candidates.append(clean_query(title_text))

    current = exact_query_key(current_query)
    seen: set[str] = set()
    queries: list[str] = []
    for candidate in candidates:
        key = query_key(candidate)
        if not key or key in seen or exact_query_key(candidate) == current:
            continue
        seen.add(key)
        queries.append(candidate)
    return queries
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_search_queries.py -q`
Expected: PASS (84 passed)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark/core/search_queries.py tests/core/test_search_queries.py
uv run ruff format --check shelfmark/core/search_queries.py tests/core/test_search_queries.py
uv run basedpyright shelfmark/core/search_queries.py
git add shelfmark/core/search_queries.py tests/core/test_search_queries.py
git commit -m "feat(search): fallback query ladder for release search

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

Expected: ruff "All checks passed!", "2 files already formatted", basedpyright "0 errors".

---

### Task 2: Identity predicate — when a release name is the requested book

**Files:**
- Modify: `shelfmark/core/search_queries.py` (imports; append the identity section)
- Test: `tests/core/test_search_queries.py`

**Interfaces:**
- Consumes: `clean_query`, `_resolve`, `_normalize_title` from Task 1.
- Produces: `SearchIdentity(series_key: str = "", position: int | None = None, title_tokens: tuple[str, ...] = (), book_is_comic: bool = False)` (frozen dataclass); `significant_tokens(text: object) -> tuple[str, ...]`; `build_search_identity(*, title: object, current_query: object, series_name: object, series_position: object) -> SearchIdentity`; `is_identity_hit(release_title: object, *, series_key: str, position: int | None, title_tokens: tuple[str, ...] | list[str], content_type: str, book_is_comic: bool = False) -> bool`; `any_identity_hit(release_titles: Iterable[object], identity: SearchIdentity | None, *, content_type: str) -> bool`.

- [ ] **Step 1: Write the failing tests**

In `tests/core/test_search_queries.py`, replace the import block

```python
from shelfmark.core.search_queries import (
    build_fallback_queries,
    clean_query,
    normalize_position,
)
```

with

```python
from shelfmark.core.search_queries import (
    SearchIdentity,
    any_identity_hit,
    build_fallback_queries,
    build_search_identity,
    clean_query,
    is_identity_hit,
    normalize_position,
)
```

and append to the end of the file, after two blank lines:

```python
class TestIdentityPredicate:
    DXD5 = SearchIdentity(series_key="High School DxD", position=5, title_tokens=("hellcat",))
    STANDALONE = SearchIdentity(title_tokens=("project", "hail", "mary"))

    def _hit(self, title: str, identity: SearchIdentity, content_type: str = "ebook") -> bool:
        return is_identity_hit(
            title,
            series_key=identity.series_key,
            position=identity.position,
            title_tokens=identity.title_tokens,
            content_type=content_type,
        )

    @pytest.mark.parametrize(
        "title",
        [
            "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp by Ichiei Ishibumi [ENG / EPUB]",
            "Ichiei Ishibumi - [High School DxD - Volume 05] - Hellcat of the Underworld Training Camp",
            "High School DxD v05 (2015) (Digital) (danke-Empire)",
            "Seven.Seas-High.School.DxD.Vol.05.2016.Retail.eBook-BitBook",
            "High School DxD [5] (epub)",
            "High School DxD - 05 (epub)",
            "High School DxD Vol 5",
        ],
    )
    def test_the_right_volume_is_a_hit(self, title):
        assert self._hit(title, self.DXD5)

    @pytest.mark.parametrize(
        "title",
        [
            "High School DxD, Vol. 15 by Ichiei Ishibumi [ENG / EPUB]",
            "High School DxD v04 (2015) (Digital)",
            "High School DxD Vol. 5-6 (epub)",
            "High School DxD (epub)",
        ],
    )
    def test_a_wrong_missing_or_extra_volume_is_not(self, title):
        assert not self._hit(title, self.DXD5)

    @pytest.mark.parametrize(
        "title",
        [
            "High School DxD Vol. 5.5 (epub)",
            "High School DxD Vol. 5a (epub)",
            "High School DxD Vol. 5 & 6 (epub)",
            "High School DxD Vol. 5 and 6 (epub)",
            "High School DxD Vol. 5 to 7 (epub)",
            "High School DxD Vol. 5\u20147 (epub)",
            "High School DxD Vol. 5\u20137 (epub)",
            "High School DxD Vol. 5+6 (epub)",
            "High School DxD v05-07 (Digital)",
            "High School DxD v05-v07 (Digital)",
            "High School DxD [5] Vol. 5.5",
        ],
    )
    def test_an_incomplete_volume_token_is_not(self, title):
        assert not self._hit(title, self.DXD5)

    @pytest.mark.parametrize(("position", "hit"), [(1, True), (10, False)])
    def test_series_then_number_then_dash_names_the_volume(self, position, hit):
        expanse = SearchIdentity(series_key="The Expanse", position=position)
        title = "Reader Corey, James S A - The Expanse 01 - Leviathan Wakes (Retail)"

        assert self._hit(title, expanse) is hit

    def test_a_year_after_the_volume_is_not_a_fraction(self):
        assert self._hit("High.School.DxD.Vol.05.2016.eBook", self.DXD5)

    @pytest.mark.parametrize(
        "title",
        [
            "High School DxD S01E05 1080p WEB-DL x264",
            "High School DxD Vol. 5 [BD 720p]",
            "High School DxD - 05 (mkv)",
            "High School DxD Episode 5",
        ],
    )
    def test_video_is_not(self, title):
        assert not self._hit(title, self.DXD5)

    def test_another_series_is_not(self):
        assert not self._hit("Overlord, Vol. 5 by Kugane Maruyama [ENG / EPUB]", self.DXD5)

    def test_an_audiobook_does_not_stop_an_ebook_search(self):
        title = "High School DxD, Volume 5 by Ichiei Ishibumi [ENG / M4B]"
        assert not self._hit(title, self.DXD5)
        assert self._hit(title, self.DXD5, content_type="audiobook")

    def test_standalone_needs_its_title_tokens(self):
        assert self._hit("Project Hail Mary by Andy Weir [ENG / EPUB]", self.STANDALONE)
        assert self._hit("Andy.Weir-Project.Hail.Mary.2021.RETAIL.EPUB", self.STANDALONE)
        assert not self._hit("Project Hail (epub)", self.STANDALONE)
        assert not self._hit("Project Hail Mary 2160p WEB-DL", self.STANDALONE)

    def test_suppressed_identity_uses_the_full_title(self):
        # "Spice, Vol. 5: Wolf" with metadata position 6 conflicts, so the series rule is
        # off; today's query would be just "Wolf", which another series' release has.
        identity = build_search_identity(
            title="Spice, Vol. 5: Wolf",
            current_query="Wolf",
            series_name="Spice",
            series_position=6,
        )

        assert identity.title_tokens == ("spice", "5", "wolf")
        assert not self._hit("Other Series Vol. 1 Wolf EPUB", identity)
        assert self._hit("Spice Vol. 5 Wolf (epub)", identity)

    def test_fewer_than_two_title_tokens_never_stop_the_ladder(self):
        assert not self._hit("Wolf (epub)", SearchIdentity(title_tokens=("wolf",)))

    def test_junk_is_never_a_hit(self):
        for title in (None, "", "   ", 7):
            assert not self._hit(title, self.DXD5)
        assert not self._hit("anything", SearchIdentity())

    @pytest.mark.parametrize(
        ("series_key", "position", "title_tokens"),
        [
            ("High School DxD", 5, None),
            (None, 5, ("high", "school")),
            ("High School DxD", True, ("high", "school")),
            ("High School DxD", "5", ("high", "school")),
            ("High School DxD", 5, "high school"),
        ],
    )
    def test_junk_arguments_never_raise(self, series_key, position, title_tokens):
        result = is_identity_hit(
            "High School DxD Vol. 5",
            series_key=series_key,
            position=position,
            title_tokens=title_tokens,
            content_type="ebook",
        )
        assert result in (True, False)
        if title_tokens is None or isinstance(title_tokens, str):
            assert result is False


class TestComicReleases:
    """A manga or comic edition is not the light novel, even with the right volume."""

    OVERLORD5 = SearchIdentity(series_key="Overlord", position=5)
    OVERLORD5_MANGA = SearchIdentity(series_key="Overlord", position=5, book_is_comic=True)
    LIVE_RESULTS = (
        "Yen.Press-Overlord.Vol.05.Manga.2022.Hybrid.Comic.eBook-BitBook",
        "Yen.Press-Overlord.The.Undead.King.Oh.Vol.05.2022.Hybrid.Comic.eBook-BitBook",
    )

    def _hit(self, title: str, identity: SearchIdentity) -> bool:
        return is_identity_hit(
            title,
            series_key=identity.series_key,
            position=identity.position,
            title_tokens=identity.title_tokens,
            content_type="ebook",
            book_is_comic=identity.book_is_comic,
        )

    @pytest.mark.parametrize("title", LIVE_RESULTS)
    def test_the_live_manga_results_do_not_stop_a_light_novel_ladder(self, title):
        assert not self._hit(title, self.OVERLORD5)

    @pytest.mark.parametrize(
        "title",
        [
            "Overlord Vol. 5 (Graphic Novel)",
            "Overlord v05 (Comics)",
            "Overlord.Vol.05.graphic.novel",
        ],
    )
    def test_comics_and_graphic_novels_are_not_the_light_novel(self, title):
        assert not self._hit(title, self.OVERLORD5)

    def test_the_light_novel_release_still_is(self):
        assert self._hit("Overlord.v05.2018.Digital.danke-Empire", self.OVERLORD5)

    @pytest.mark.parametrize("title", LIVE_RESULTS)
    def test_a_manga_book_accepts_manga_releases(self, title):
        assert self._hit(title, self.OVERLORD5_MANGA)

    def test_any_identity_hit_uses_the_identity_s_comic_flag(self):
        assert not any_identity_hit(self.LIVE_RESULTS, self.OVERLORD5, content_type="ebook")
        assert any_identity_hit(self.LIVE_RESULTS, self.OVERLORD5_MANGA, content_type="ebook")


class TestBuildSearchIdentity:
    def test_a_series_volume_identity(self):
        identity = build_search_identity(
            title=f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp",
            current_query=f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp",
            series_name=DXD,
            series_position=5,
        )
        assert (identity.series_key, identity.position) == ("High School DxD", 5)

    def test_a_part_in_the_book_name_is_still_a_series_volume(self):
        identity = build_search_identity(
            title=f"{OL}, Vol. 5: The Men of the Kingdom Part I",
            current_query="The Men of the Kingdom Part I",
            series_name=OL,
            series_position=5,
        )
        assert (identity.series_key, identity.position) == ("Overlord", 5)

    def test_a_split_volume_falls_back_to_title_tokens(self):
        identity = build_search_identity(
            title="Spice, Vol. 2 Part 1",
            current_query="Spice Vol. 2 Part 1",
            series_name="Spice",
            series_position=2,
        )
        assert identity == SearchIdentity(title_tokens=("spice", "2", "part", "1"))

    def test_a_manga_or_comic_book_is_flagged(self):
        for title, series in (
            ("Overlord (Manga), Vol. 5", "Overlord (Manga)"),
            ("Spice", "Spice Comics"),
        ):
            identity = build_search_identity(
                title=title, current_query=title, series_name=series, series_position=5
            )
            assert identity.book_is_comic is True

        light_novel = build_search_identity(
            title=f"{OL}, Vol. 5: The Men of the Kingdom Part I",
            current_query="The Men of the Kingdom Part I",
            series_name=OL,
            series_position=5,
        )
        assert light_novel.book_is_comic is False

    def test_a_standalone_identity(self):
        identity = build_search_identity(
            title="The Housemaid",
            current_query="The Housemaid",
            series_name=None,
            series_position=None,
        )
        assert identity == SearchIdentity(title_tokens=("housemaid",))


class TestAnyIdentityHit:
    def test_true_when_one_title_is_the_book(self):
        identity = SearchIdentity(series_key="Overlord", position=2)

        assert any_identity_hit(
            ["Overlord, Vol. 3", None, "Overlord v02 (2016) (Digital)"],
            identity,
            content_type="ebook",
        )

    def test_false_without_an_identity_or_a_hit(self):
        assert not any_identity_hit(["Overlord v02"], None, content_type="ebook")
        assert not any_identity_hit(
            ["Overlord, Vol. 3"],
            SearchIdentity(series_key="Overlord", position=2),
            content_type="ebook",
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_search_queries.py -q`
Expected: FAIL — collection error `ImportError: cannot import name 'SearchIdentity' from 'shelfmark.core.search_queries'`

- [ ] **Step 3: Implement**

In `shelfmark/core/search_queries.py`, after the line `from decimal import Decimal, InvalidOperation` add:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
```

Append to the end of the file, after two blank lines:

```python
_STOPWORDS = frozenset(
    {"a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
)
# Words that say "a volume" without saying which book; not identity on their own.
_TITLE_NOISE = frozenset({"vol", "volume"})
# Title-token identity needs this many significant tokens, or it would stop on almost
# anything; with fewer, nothing stops the ladder (which costs requests, never results).
_MIN_TITLE_TOKENS = 2
_TOKEN_RE = re.compile(r"[^\W_]+")

# Release names that are not an ebook of the book: video encodes and episode markers.
_VIDEO_RE = re.compile(
    r"\b(?:2160p|1080p|720p|480p|x264|x265|h\.?264|h\.?265|hevc|bd|bdrip|blu-?ray|web-?dl"
    r"|webrip|mkv|mp4|avi|dual[ ._-]?audio|s\d{1,2}e\d{1,3}|episodes?|ep\.?\s?\d+)\b",
    re.IGNORECASE,
)
# A manga or comic edition is a different book from the novel it adapts. Applied only
# when the requested book itself is not a manga or comic.
_COMIC_RELEASE_RE = re.compile(r"\b(?:manga|comics?|graphic[\s._-]+novels?)\b", re.IGNORECASE)
_COMIC_BOOK_RE = re.compile(r"manga|comic", re.IGNORECASE)
# An ebook search is not satisfied by a recording of the book.
_AUDIO_RE = re.compile(r"\b(?:mp3|m4b|m4a|flac|aac|audiobook|unabridged)\b", re.IGNORECASE)

# Volume numbers a release name can carry: "Vol. 5", "Volume 05", "v05", "[5]", "- 5".
# The number is captured whole (at most six digits, so int() stays cheap); what follows
# it decides whether it is a complete volume token.
_RELEASE_VOLUME_RES = (
    re.compile(r"\bvol(?:ume)?s?\b\.?\s*(\d{1,6})(?!\d)", re.IGNORECASE),
    re.compile(r"\bv(\d{1,6})(?!\d)", re.IGNORECASE),
    re.compile(r"\[\s*(\d{1,3})\s*\](?!\d)"),
    re.compile(r"(?:^|\s)-\s*(\d{1,3})(?![\d.])"),
)
# After a volume number: a fraction ("5.5" - but not "05.2022", a year), a letter
# ("5a"), or a second volume ("5 & 6", "5 to 7", "5-7", "5—7", "v05-07") make it not a
# single complete volume.
_INCOMPLETE_VOLUME_RE = re.compile(
    r"\.\d(?!\d)|[^\W\d_]|\s*(?:[-–—~&+]|\bto\b|\band\b)\s*v?\d",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SearchIdentity:
    """What a release name has to show to count as the requested book.

    ``series_key`` and ``position`` are set together, only when the book has a
    complete, consistent single-volume identity; otherwise ``title_tokens`` decide.
    """

    series_key: str = ""
    position: int | None = None
    title_tokens: tuple[str, ...] = ()
    # The requested book is itself a manga or comic, so such releases may be it.
    book_is_comic: bool = False


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.casefold())


def _title_tokens(title: str) -> tuple[str, ...]:
    """Significant tokens of the full cleaned title, without bare volume words."""
    return tuple(t for t in significant_tokens(title) if t not in _TITLE_NOISE)


def significant_tokens(text: object) -> tuple[str, ...]:
    """Word tokens of ``text`` without stopwords (all tokens if only stopwords remain)."""
    tokens = _tokens(clean_query(text))
    significant = [t for t in tokens if t not in _STOPWORDS]
    return tuple(significant or tokens)


def build_search_identity(
    *,
    title: object,
    current_query: object,
    series_name: object,
    series_position: object,
) -> SearchIdentity:
    """The identity ``is_identity_hit`` checks results against for this book."""
    title_text = _normalize_title(title)
    resolved = _resolve(title_text, series_name, series_position)
    # The full title, not today's (often shortened) query: "Spice, Vol. 5: Wolf" must not
    # be stopped by any release that merely says "Wolf".
    title_tokens = _title_tokens(title_text) or _title_tokens(clean_query(current_query))
    series_text = series_name if isinstance(series_name, str) else ""
    return SearchIdentity(
        series_key=resolved.series,
        position=resolved.position,
        title_tokens=title_tokens,
        book_is_comic=bool(_COMIC_BOOK_RE.search(f"{title_text} {series_text}")),
    )


def _release_volumes(text: str, series_tokens: tuple[str, ...]) -> set[int] | None:
    """The volume numbers ``text`` names, or None if any of them is not a whole volume."""
    patterns = list(_RELEASE_VOLUME_RES)
    if series_tokens:
        # "Expanse 01 - Leviathan Wakes": the series, its number, then " - ".
        last = re.escape(series_tokens[-1])
        patterns.append(re.compile(rf"\b{last}[\s._]+(\d{{1,3}})\s+-\s"))
    numbers: set[int] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            if _INCOMPLETE_VOLUME_RE.match(text, match.end(1)):
                return None
            numbers.add(int(match.group(1)))
    return numbers


def is_identity_hit(
    release_title: object,
    *,
    series_key: str,
    position: int | None,
    title_tokens: tuple[str, ...] | list[str],
    content_type: str,
    book_is_comic: bool = False,
) -> bool:
    """Whether a release name is the requested book, for deciding when fallbacks stop.

    A series volume must name this volume (and no other), carry the series key tokens
    and not be video. Any other book must carry its significant title tokens and not be
    video. Unless ``book_is_comic``, a manga, comic or graphic-novel release is not the
    book either. Never used to filter or reorder results.
    """
    if not isinstance(release_title, str) or not release_title.strip():
        return False
    if not isinstance(title_tokens, (tuple, list)):
        return False
    if not isinstance(series_key, str) or isinstance(position, bool):
        series_key, position = "", None
    if position is not None and not isinstance(position, int):
        position = None
    text = release_title.casefold()
    if _VIDEO_RE.search(text):
        return False
    if str(content_type).strip().lower() == "ebook" and _AUDIO_RE.search(text):
        return False
    if not book_is_comic and _COMIC_RELEASE_RE.search(text):
        return False

    present = set(_tokens(text))
    if series_key and position is not None:
        key_tokens = significant_tokens(series_key)
        if not key_tokens or not all(token in present for token in key_tokens):
            return False
        return _release_volumes(text, key_tokens) == {position}

    wanted = [token.casefold() for token in title_tokens if isinstance(token, str) and token]
    return len(wanted) >= _MIN_TITLE_TOKENS and all(token in present for token in wanted)


def any_identity_hit(
    release_titles: Iterable[object],
    identity: SearchIdentity | None,
    *,
    content_type: str,
) -> bool:
    """Whether any of ``release_titles`` is the book ``identity`` describes."""
    if identity is None:
        return False
    return any(
        is_identity_hit(
            title,
            series_key=identity.series_key,
            position=identity.position,
            title_tokens=identity.title_tokens,
            content_type=content_type,
            book_is_comic=identity.book_is_comic,
        )
        for title in release_titles
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_search_queries.py -q`
Expected: PASS (140 passed)

- [ ] **Step 5: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark/core/search_queries.py tests/core/test_search_queries.py
uv run ruff format --check shelfmark/core/search_queries.py tests/core/test_search_queries.py
uv run basedpyright shelfmark/core/search_queries.py
git add shelfmark/core/search_queries.py tests/core/test_search_queries.py
git commit -m "feat(search): identity predicate that decides when fallbacks stop

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Search plan — fallback variants, ebook only; endpoint passes `content_type`

**Files:**
- Modify: `shelfmark/core/search_plan.py` (imports; `ReleaseSearchVariant`; `ReleaseSearchPlan`; new `_wants_fallbacks`, `_with_fallbacks`; `build_release_search_plan`)
- Modify: `shelfmark/main.py` (release endpoint `_search_source_releases`, the `build_release_search_plan(` call)
- Test: `tests/core/test_search_plan.py`, `tests/core/test_releases_api_content_type.py` (new)

**Interfaces:**
- Consumes: `build_fallback_queries`, `build_search_identity`, `query_key`, `SearchIdentity` (Tasks 1–2).
- Produces: `/api/releases` adds `"incomplete": true` to `search_info[<source>]` when the source's `last_search_incomplete` is `True`; `ReleaseSearchVariant.fallback: bool = False` (new last field); `ReleaseSearchPlan.identity: SearchIdentity | None = None` (new last field; set whenever the plan has fallback variants); `build_release_search_plan(book, languages=None, manual_query=None, indexers=None, source_filters=None, user_id=None, content_type: str | None = None) -> ReleaseSearchPlan`. Variant order: mandatory base, localized, then fallbacks.

- [ ] **Step 1: Write the failing tests**

At the top of `tests/core/test_search_plan.py`, before `from shelfmark.core.search_plan import build_release_search_plan`, add:

```python
import pytest

```

Append to the end of `tests/core/test_search_plan.py`, after two blank lines:

```python
DXD5_TITLE = "High School DxD (Light Novel), Vol. 5: Hellcat of the Underworld Training Camp"


def _dxd5(**overrides) -> BookMetadata:
    """DxD vol 5 as Hardcover's get_book returns it: no subtitle, so no search_title."""
    fields: dict[str, object] = {
        "provider": "hardcover",
        "provider_id": "2575261",
        "title": DXD5_TITLE,
        "authors": ["Ichiei Ishibumi"],
        "search_author": "Ichiei Ishibumi",
        "series_name": "High School DxD (Light Novel)",
        "series_position": 5,
        "isbn_13": "9780316559294",
    }
    fields.update(overrides)
    return BookMetadata(**fields)


DXD5_LADDER = [
    "High School DxD Vol. 5",
    "High School DxD v05",
    "Hellcat of the Underworld Training Camp",
    "High School DxD Volume 05",
    "High School DxD Vol. 5 Hellcat of the Underworld Training Camp",
]


class TestFallbackVariants:
    def test_order_is_mandatory_then_localized_then_fallbacks(self):
        book = _dxd5(titles_by_language={"de": "Highschool DxD 5"})

        plan = build_release_search_plan(book, languages=["en", "de"], content_type="ebook")

        assert [(v.title, v.fallback) for v in plan.title_variants] == [
            (DXD5_TITLE, False),
            ("Highschool DxD 5", False),
            *[(query, True) for query in DXD5_LADDER],
        ]
        assert plan.identity is not None
        assert (plan.identity.series_key, plan.identity.position) == ("High School DxD", 5)

    def test_fallbacks_carry_the_search_author_like_every_variant(self):
        plan = build_release_search_plan(_dxd5(), languages=["en"], content_type="ebook")

        assert {v.author for v in plan.title_variants} == {"Ichiei Ishibumi"}
        assert all(v.languages is None for v in plan.title_variants if v.fallback)

    def test_a_duplicate_title_keeps_its_mandatory_status(self):
        book = _dxd5(titles_by_language={"de": "high school dxd  vol. 5"})

        plan = build_release_search_plan(book, languages=["en", "de"], content_type="ebook")

        titles = [(v.title, v.fallback) for v in plan.title_variants]
        assert ("high school dxd  vol. 5", False) in titles
        assert ("High School DxD Vol. 5", True) not in titles
        assert [t for t, fallback in titles if fallback] == DXD5_LADDER[1:]

    def test_a_title_that_differs_only_in_punctuation_is_a_different_request(self):
        book = _dxd5(titles_by_language={"de": "High School DxD, Vol. 5"})

        plan = build_release_search_plan(book, languages=["en", "de"], content_type="ebook")

        assert [v.title for v in plan.title_variants if v.fallback] == DXD5_LADDER

    @pytest.mark.parametrize("content_type", ["audiobook", None, "", "comic"])
    def test_only_ebook_searches_get_fallbacks(self, content_type):
        plan = build_release_search_plan(_dxd5(), languages=["en"], content_type=content_type)

        assert [v.title for v in plan.title_variants] == [DXD5_TITLE]
        assert plan.identity is None

    def test_content_type_is_matched_case_insensitively(self):
        plan = build_release_search_plan(_dxd5(), languages=["en"], content_type=" EBook ")

        assert [v.title for v in plan.title_variants if v.fallback] == DXD5_LADDER

    def test_primary_query_and_grouped_variants_are_unchanged(self):
        book = _dxd5(titles_by_language={"de": "Highschool DxD 5"})

        before = build_release_search_plan(book, languages=["en", "de"])
        after = build_release_search_plan(book, languages=["en", "de"], content_type="ebook")

        assert after.primary_query == before.primary_query == f"{DXD5_TITLE} Ichiei Ishibumi"
        assert after.grouped_title_variants == before.grouped_title_variants
        assert after.title_variants[: len(before.title_variants)] == before.title_variants
        assert after.isbn_candidates == before.isbn_candidates

    def test_a_manual_query_keeps_its_trimming_and_gets_no_fallbacks(self):
        plan = build_release_search_plan(
            _dxd5(), languages=["en"], manual_query="  dxd v05  ", content_type="ebook"
        )

        assert [(v.title, v.fallback) for v in plan.title_variants] == [("dxd v05", False)]
        assert plan.identity is None

        long_plan = build_release_search_plan(
            _dxd5(), languages=["en"], manual_query="x" * 300, content_type="ebook"
        )
        assert [v.title for v in long_plan.title_variants] == ["x" * 256]

    def test_a_manual_provider_book_gets_no_fallbacks(self):
        book = BookMetadata(
            provider="manual",
            provider_id="abc",
            title="Overlord, Vol. 2",
            search_title="Overlord, Vol. 2",
            authors=["Kugane Maruyama"],
        )

        plan = build_release_search_plan(book, languages=["en"], content_type="ebook")

        assert [v.title for v in plan.title_variants] == ["Overlord, Vol. 2"]
        assert plan.identity is None

    def test_an_empty_title_still_falls_back_to_isbn_only(self):
        plan = build_release_search_plan(_dxd5(title=""), languages=["en"], content_type="ebook")

        assert [(v.title, v.fallback) for v in plan.title_variants] == [("9780316559294", False)]
        assert plan.identity is None


class TestFallbacksPerProvider:
    def test_openlibrary_without_series_fields_parses_the_title(self):
        book = BookMetadata(
            provider="openlibrary",
            provider_id="OL1W",
            title="Overlord, Vol. 2: The Dark Warrior",
            authors=["Kugane Maruyama"],
        )

        plan = build_release_search_plan(book, languages=["en"], content_type="ebook")

        assert [v.title for v in plan.title_variants if v.fallback] == [
            "Overlord Vol. 2",
            "Overlord v02",
            "The Dark Warrior",
            "Overlord Volume 02",
            # Today's query keeps its comma and colon, so this is a different request.
            "Overlord Vol. 2 The Dark Warrior",
        ]

    def test_google_books_standalone_gets_none(self):
        book = BookMetadata(
            provider="googlebooks", provider_id="g1", title="Dune", authors=["Frank Herbert"]
        )

        plan = build_release_search_plan(book, languages=["en"], content_type="ebook")

        assert [v.title for v in plan.title_variants] == ["Dune"]
        assert plan.identity is None

    def test_moly_display_only_series_is_not_used(self):
        from shelfmark.metadata_providers import DisplayField

        book = BookMetadata(
            provider="moly",
            provider_id="m1",
            title="A Sötét Harcos",
            authors=["Kugane Maruyama"],
            display_fields=[DisplayField(label="Series", value="Overlord", icon="editions")],
        )

        plan = build_release_search_plan(book, languages=["hu"], content_type="ebook")

        assert [v.title for v in plan.title_variants] == ["A Sötét Harcos"]

    def test_audible_audiobook_gets_none(self):
        book = BookMetadata(
            provider="audible",
            provider_id="B0X",
            title="Overlord, Vol. 2",
            authors=["Kugane Maruyama"],
            series_name="Overlord",
            series_position=2,
        )

        plan = build_release_search_plan(book, languages=["en"], content_type="audiobook")

        assert [v.title for v in plan.title_variants] == ["Overlord, Vol. 2"]
```

Create `tests/core/test_releases_api_content_type.py`:

```python
"""/api/releases hands the request's content type to the search plan, and reports a
search that was cut short.

The fallback ladder is ebook-only, and the plan can only know which kind of search it
is building if the endpoint tells it. A source that ran out of time keeps what it found;
`search_info` tells the caller those releases are partial.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

import shelfmark.core.search_plan as search_plan


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    return main_module.app.test_client()


class _Source:
    def search(self, book, plan, *, expand_search=False, content_type="ebook"):
        return []

    def get_column_config(self):
        from shelfmark.release_sources import _default_column_config

        return _default_column_config()


@pytest.mark.parametrize(
    ("query_content_type", "expected"),
    [("ebook", "ebook"), ("audiobook", "audiobook"), (None, "ebook")],
)
def test_the_plan_is_built_for_the_requested_content_type(
    client, main_module, query_content_type, expected
):
    with client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7

    seen: list[object] = []
    real_build = search_plan.build_release_search_plan

    def _spy(*args, **kwargs):
        seen.append(kwargs.get("content_type"))
        return real_build(*args, **kwargs)

    query = {"provider": "manual", "book_id": "abc", "title": "Dune"}
    if query_content_type is not None:
        query["content_type"] = query_content_type

    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch.object(search_plan, "build_release_search_plan", _spy),
        patch(
            "shelfmark.release_sources.list_available_sources",
            return_value=[{"name": "prowlarr", "enabled": True}],
        ),
        patch("shelfmark.release_sources.get_source", return_value=_Source()),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        resp = client.get("/api/releases", query_string=query)

    assert resp.status_code == 200
    assert seen == [expected]


class _IncompleteSource(_Source):
    last_search_type = "categories"
    last_search_incomplete = True

    def search(self, book, plan, *, expand_search=False, content_type="ebook"):
        from shelfmark.release_sources import Release

        return [Release(source="prowlarr", source_id="p1", title="Dune (epub)")]


def test_an_incomplete_search_is_reported_with_its_releases(client, main_module):
    with client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7

    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch(
            "shelfmark.release_sources.list_available_sources",
            return_value=[{"name": "prowlarr", "enabled": True}],
        ),
        patch("shelfmark.release_sources.get_source", return_value=_IncompleteSource()),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        resp = client.get(
            "/api/releases", query_string={"provider": "manual", "book_id": "abc", "title": "Dune"}
        )

    body = resp.get_json()
    assert resp.status_code == 200
    assert [r["title"] for r in body["releases"]] == ["Dune (epub)"]
    assert body["search_info"] == {"prowlarr": {"search_type": "categories", "incomplete": True}}


def test_a_complete_search_carries_no_incomplete_flag(client, main_module):
    with client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7

    complete = _IncompleteSource()
    complete.last_search_incomplete = False
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch(
            "shelfmark.release_sources.list_available_sources",
            return_value=[{"name": "prowlarr", "enabled": True}],
        ),
        patch("shelfmark.release_sources.get_source", return_value=complete),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        resp = client.get(
            "/api/releases", query_string={"provider": "manual", "book_id": "abc", "title": "Dune"}
        )

    assert resp.get_json()["search_info"] == {"prowlarr": {"search_type": "categories"}}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_search_plan.py tests/core/test_releases_api_content_type.py -q`
Expected: FAIL — 21 failed: 17 new `test_search_plan.py` tests with `TypeError: build_release_search_plan() got an unexpected keyword argument 'content_type'`, the 3 content-type endpoint tests with `AssertionError: assert [None] == ['ebook']` (or `['audiobook']`), and `test_an_incomplete_search_is_reported_with_its_releases` with `AssertionError` (no `"incomplete"` in `search_info`). The 7 pre-existing plan tests and `test_a_complete_search_carries_no_incomplete_flag` pass.

- [ ] **Step 3: Implement the plan changes in `shelfmark/core/search_plan.py`**

Replace

```python
from shelfmark.core.logger import setup_logger
from shelfmark.metadata_providers import (
```

with

```python
from shelfmark.core.logger import setup_logger
from shelfmark.core.search_queries import (
    SearchIdentity,
    build_fallback_queries,
    build_search_identity,
    exact_query_key,
)
from shelfmark.metadata_providers import (
```

In `ReleaseSearchVariant`, replace

```python
    title: str
    author: str
    languages: list[str] | None = None

    @property
```

with

```python
    title: str
    author: str
    languages: list[str] | None = None
    # A ladder query (shelfmark.core.search_queries): Prowlarr and Newznab run it only
    # while nothing found so far is the requested book. Every other source ignores it.
    fallback: bool = False

    @property
```

In `ReleaseSearchPlan`, replace

```python
    source_filters: SearchFilters | None = None

    @property
    def primary_query(self) -> str:
```

with

```python
    source_filters: SearchFilters | None = None
    # What a result must show to stop the fallback variants; set whenever there are any.
    identity: SearchIdentity | None = None

    @property
    def primary_query(self) -> str:
```

Replace

```python
def _pick_search_title(book: BookMetadata) -> str:
    return book.search_title or book.title
```

with

```python
def _pick_search_title(book: BookMetadata) -> str:
    return book.search_title or book.title


def _wants_fallbacks(book: BookMetadata, content_type: str | None) -> bool:
    """Fallbacks are for ebook metadata searches only.

    Audiobook searches keep today's queries, and a manual-provider book is whatever the
    user typed, so it gets no ladder either.
    """
    if not isinstance(content_type, str) or content_type.strip().lower() != "ebook":
        return False
    return book.provider != "manual"


def _with_fallbacks(
    book: BookMetadata,
    base_title: str,
    author: str,
    title_variants: list[ReleaseSearchVariant],
) -> tuple[list[ReleaseSearchVariant], SearchIdentity | None]:
    """Append the ladder after the mandatory and localized variants.

    A ladder query that is the same request as a mandatory title (case and whitespace
    aside - mandatory titles are sent uncleaned) is dropped, so the title keeps its
    mandatory status.
    """
    ladder = build_fallback_queries(
        title=book.title,
        current_query=base_title,
        series_name=book.series_name,
        series_position=book.series_position,
    )
    seen = {exact_query_key(variant.title) for variant in title_variants}
    fallbacks: list[ReleaseSearchVariant] = []
    for query in ladder:
        key = exact_query_key(query)
        if key in seen:
            continue
        seen.add(key)
        fallbacks.append(ReleaseSearchVariant(title=query, author=author, fallback=True))
    if not fallbacks:
        return title_variants, None
    identity = build_search_identity(
        title=book.title,
        current_query=base_title,
        series_name=book.series_name,
        series_position=book.series_position,
    )
    return [*title_variants, *fallbacks], identity
```

In `build_release_search_plan`, replace

```python
    source_filters: SearchFilters | None = None,
    user_id: int | None = None,
) -> ReleaseSearchPlan:
    """Build normalized search variants shared across release sources.

    ``user_id`` picks up that user's default languages when the caller does not
    filter explicitly, so a search started without a language filter uses the
    reader's own default rather than the instance-wide one.
    """
```

with

```python
    source_filters: SearchFilters | None = None,
    user_id: int | None = None,
    content_type: str | None = None,
) -> ReleaseSearchPlan:
    """Build normalized search variants shared across release sources.

    ``user_id`` picks up that user's default languages when the caller does not
    filter explicitly, so a search started without a language filter uses the
    reader's own default rather than the instance-wide one.

    ``content_type`` "ebook" adds the fallback ladder after today's variants; any
    other value (or none) leaves the plan exactly as it was.
    """
```

Replace

```python
    # If no titles could be built, fall back to ISBN queries.
```

with

```python
    identity: SearchIdentity | None = None
    if title_variants and _wants_fallbacks(book, content_type):
        title_variants, identity = _with_fallbacks(book, base_title, author, title_variants)

    # If no titles could be built, fall back to ISBN queries.
```

and in the final `return ReleaseSearchPlan(...)` replace

```python
        manual_query=None,
        indexers=indexers,
        source_filters=source_filters,
    )
```

with

```python
        manual_query=None,
        indexers=indexers,
        source_filters=source_filters,
        identity=identity,
    )
```

- [ ] **Step 4: Pass `content_type` from the endpoint and report incomplete searches**

In `shelfmark/main.py`, inside `_search_source_releases` in the release endpoint, replace

```python
                    indexers=indexers,
                    source_filters=source_query_filters,
                    user_id=db_user_id,
                )
```

with

```python
                    indexers=indexers,
                    source_filters=source_query_filters,
                    user_id=db_user_id,
                    content_type=content_type,
                )
```

(`content_type` is already read from the request a few lines below as `request.args.get("content_type", "ebook").strip()`; the closure reads it at call time.)

Further down, where the response's `search_info` is built, replace

```python
            if hasattr(source_instance, "last_search_type") and source_instance.last_search_type:
                search_info[source_name] = {"search_type": source_instance.last_search_type}
```

with

```python
            if hasattr(source_instance, "last_search_type") and source_instance.last_search_type:
                search_info[source_name] = {"search_type": source_instance.last_search_type}
            # A search cut short by its deadline still returns what it found; say so.
            if getattr(source_instance, "last_search_incomplete", False) is True:
                search_info.setdefault(source_name, {})["incomplete"] = True
```

(Tasks 4 and 5 make Prowlarr and Newznab set `last_search_incomplete`; any source without the attribute is unaffected.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_search_plan.py tests/core/test_releases_api_content_type.py tests/core/test_search_plan_language_codes.py tests/core/test_manual_query.py tests/core/test_search_author_narrowing.py tests/core/test_releases_api_budget.py tests/core/test_releases_api_direct_provider.py -q`
Expected: PASS (all)

Run: `uv run pytest tests/prowlarr tests/newznab tests/irc tests/audiobookbay tests/direct_download -q`
Expected: PASS (sources unchanged by this task)

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark/core/search_plan.py shelfmark/main.py tests/core/test_search_plan.py tests/core/test_releases_api_content_type.py
uv run ruff format --check shelfmark/core/search_plan.py shelfmark/main.py tests/core/test_search_plan.py tests/core/test_releases_api_content_type.py
uv run basedpyright shelfmark/core/search_plan.py
git add shelfmark/core/search_plan.py shelfmark/main.py tests/core/test_search_plan.py tests/core/test_releases_api_content_type.py
git commit -m "feat(search): ebook plans carry fallback variants after today's queries

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Prowlarr — ladder gating, failed-indexer exclusion, cap, deadline, incompleteness, request logs

**Files:**
- Modify: `shelfmark/core/search_deadline.py` (new `remaining_seconds`)
- Modify: `shelfmark/release_sources/prowlarr/torznab.py` (new `TorznabError`, `parse_torznab_error`)
- Modify: `shelfmark/release_sources/prowlarr/api.py` (`ProwlarrSearchError.rate_limited`, `_is_rate_limited`, error documents in `torznab_search`)
- Modify: `shelfmark/release_sources/prowlarr/source.py` (imports; `FALLBACK_REQUESTS_PER_INDEXER`; `_RequestLog`; `_IndexerSearchOutcome.deadline_reached`; `last_search_incomplete`; the search loop; result reporting)
- Test: `tests/core/test_search_deadline.py`, `tests/prowlarr/test_torznab.py`, `tests/prowlarr/test_api_timeout.py`, `tests/prowlarr/test_source.py`, `tests/prowlarr/test_source_fallbacks.py` (new)

**Interfaces:**
- Consumes: `any_identity_hit` (Task 2); `ReleaseSearchVariant.fallback`, `ReleaseSearchPlan.identity`, `build_release_search_plan(..., content_type=)` (Task 3).
- Produces: `search_deadline.remaining_seconds(source_deadline: float) -> float`; `prowlarr.torznab.TorznabError(code: str, description: str)` with `.rate_limited` and `parse_torznab_error(xml_text: str) -> TorznabError | None` (Task 5 uses both); `ProwlarrSearchError(message: str, *, rate_limited: bool = False)` with `.rate_limited`; `prowlarr.source.FALLBACK_REQUESTS_PER_INDEXER = 4`; `ProwlarrSource.last_search_incomplete: bool`. Log formats: `Prowlarr request: query='…' indexer=<name> categories=<7000|all> rung=<mandatory N|fallback N> expanded=<yes|no> outcome=<ok|empty|failed|rate-limited> results=<n>` and `Prowlarr fallbacks: ran=<yes|no> stop=<hit|cap|deadline|exhausted|not planned> rungs=<run>/<planned> requests=<n>`.

- [ ] **Step 1: Write the failing tests**

In `tests/core/test_search_deadline.py`, insert before `def test_message_names_the_challenge_not_the_proxy():`:

```python
def test_remaining_is_the_source_deadline_outside_a_search():
    import time

    remaining = search_deadline.remaining_seconds(time.monotonic() + 50)

    assert 49 < remaining <= 50


def test_remaining_is_the_sooner_of_the_source_and_endpoint_budgets():
    import time

    with search_deadline.search_deadline(30):
        assert search_deadline.remaining_seconds(time.monotonic() + 500) <= 30
        assert search_deadline.remaining_seconds(time.monotonic() + 5) <= 5


def test_an_expired_endpoint_budget_leaves_nothing():
    import time

    with search_deadline.search_deadline(3600) as deadline:
        deadline.event.set()
        assert search_deadline.remaining_seconds(time.monotonic() + 500) == 0.0


```

In `tests/prowlarr/test_torznab.py`, replace

```python
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_xml
```

with

```python
import pytest

from shelfmark.release_sources.prowlarr.torznab import (
    TorznabError,
    parse_torznab_error,
    parse_torznab_xml,
)
```

and append to the end of the file, after two blank lines:

```python
class TestParseTorznabError:
    @pytest.mark.parametrize(
        "body",
        [
            '<?xml version="1.0"?><error code="500" description="Request limit reached"/>',
            "<error code='500' description='Request limit reached'/>",
            '<error\n    code = "500"\n    description = "Request limit reached" />',
            '<nn:error xmlns:nn="http://www.newznab.com/DTD/2010/feeds/attributes/" '
            'code="500" description="Request limit reached"/>',
        ],
    )
    def test_quotes_whitespace_and_namespaces(self, body):
        assert parse_torznab_error(body) == TorznabError("500", "Request limit reached")

    @pytest.mark.parametrize(
        "body",
        [
            "",
            "not xml",
            '<?xml version="1.0"?><rss><channel></channel></rss>',
            '<rss><channel><item><title>About &lt;error code="500"&gt;</title></item></channel></rss>',
        ],
    )
    def test_anything_else_is_not_an_error(self, body):
        assert parse_torznab_error(body) is None

    @pytest.mark.parametrize(
        ("code", "description", "rate_limited"),
        [
            ("500", "", True),
            ("501", "", True),
            ("429", "", True),
            ("900", "API limit exceeded", True),
            ("100", "Incorrect user credentials", False),
        ],
    )
    def test_rate_limits(self, code, description, rate_limited):
        assert TorznabError(code, description).rate_limited is rate_limited
```

In `tests/prowlarr/test_api_timeout.py`, class `TestTorznabSearchFailures`, insert before `    def test_an_indexer_with_nothing_still_returns_empty(self, monkeypatch):`:

```python
    def test_a_429_is_marked_rate_limited(self, monkeypatch):
        client, _ = self._client(monkeypatch, _Response("", status_code=429, reason="Too Many"))

        with pytest.raises(ProwlarrSearchError) as excinfo:
            client.torznab_search(indexer_id=1, query="Dune")

        assert excinfo.value.rate_limited is True

    @pytest.mark.parametrize(
        "failure",
        [
            requests.exceptions.ReadTimeout("read timeout=90"),
            _Response("", status_code=500, reason="Server Error"),
        ],
    )
    def test_other_failures_are_not_rate_limited(self, monkeypatch, failure):
        client, _ = self._client(monkeypatch, failure)

        with pytest.raises(ProwlarrSearchError) as excinfo:
            client.torznab_search(indexer_id=1, query="Dune")

        assert excinfo.value.rate_limited is False

    @pytest.mark.parametrize(
        ("body", "rate_limited"),
        [
            (
                '<?xml version="1.0"?><error code="100" description="Incorrect user credentials"/>',
                False,
            ),
            ('<?xml version="1.0"?><error code="500" description="Request limit reached"/>', True),
            ("<error code='900' description='Daily API limit exceeded' />", True),
        ],
    )
    def test_an_error_document_is_a_failed_search(self, monkeypatch, body, rate_limited):
        client, _ = self._client(monkeypatch, _Response(body))

        with pytest.raises(ProwlarrSearchError, match="indexer 1 returned error") as excinfo:
            client.torznab_search(indexer_id=1, query="Dune")

        assert excinfo.value.rate_limited is rate_limited

```

In `tests/prowlarr/test_source.py`, `test_auto_expand_logs_query_argument` now also sees the per-request INFO lines. Replace

```python
        assert info_calls == [
```

with

```python
        # Per-request lines are logged at INFO too; this pins the auto-expand line.
        assert [call for call in info_calls if "auto-expanding" in call[0]] == [
```

Create `tests/prowlarr/test_source_fallbacks.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_search_deadline.py tests/prowlarr/test_torznab.py tests/prowlarr/test_api_timeout.py tests/prowlarr/test_source_fallbacks.py -q --deselect tests/core/test_search_deadline.py::test_html_get_page_will_not_start_a_bypass_on_a_spent_budget`
Expected: FAIL — collection errors in `tests/prowlarr/test_torznab.py` (`ImportError: cannot import name 'TorznabError'`) and `tests/prowlarr/test_source_fallbacks.py` (`ImportError: cannot import name 'FALLBACK_REQUESTS_PER_INDEXER'`); the three deadline tests fail with `AttributeError: module 'shelfmark.core.search_deadline' has no attribute 'remaining_seconds'`; the 429 tests with `AttributeError: 'ProwlarrSearchError' object has no attribute 'rate_limited'`; the error-document tests with `Failed: DID NOT RAISE`.

- [ ] **Step 3: Add `remaining_seconds` to `shelfmark/core/search_deadline.py`**

Insert before `def cancel_event() -> threading.Event | None:`:

```python
def remaining_seconds(source_deadline: float) -> float:
    """Seconds left before ``source_deadline`` (a ``time.monotonic()`` value) or the
    budget in force, whichever comes first.

    A release source keeps its own deadline on top of the endpoint's; a request that
    cannot finish inside the sooner of the two is not worth starting.
    """
    remaining = source_deadline - time.monotonic()
    deadline = _current.get()
    if deadline is not None:
        remaining = min(remaining, 0.0 if deadline.expired else deadline.remaining)
    return remaining


```

- [ ] **Step 4: Parse error documents in `shelfmark/release_sources/prowlarr/torznab.py`**

Replace

```python
from typing import Any

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException
```

with

```python
from dataclasses import dataclass
from typing import Any

from defusedxml import ElementTree as DefusedElementTree
from defusedxml.common import DefusedXmlException

# Newznab/Torznab error codes that mean "slow down": 500 request limit, 501 download
# limit (and an HTTP-style 429 some indexers put in the document).
_RATE_LIMIT_ERROR_CODES = frozenset({"429", "500", "501"})


@dataclass(frozen=True)
class TorznabError:
    """An ``<error code=... description=.../>`` document sent instead of a feed."""

    code: str
    description: str

    @property
    def rate_limited(self) -> bool:
        return self.code in _RATE_LIMIT_ERROR_CODES or "limit" in self.description.lower()


def parse_torznab_error(xml_text: str) -> TorznabError | None:
    """The error an indexer answered with, or None when the document is not an error.

    Parsed as XML (single or double quotes, any whitespace, a namespace prefix), never
    pattern-matched, so a feed that merely mentions "<error" in an item is not one.
    """
    if not xml_text or not xml_text.strip():
        return None
    try:
        root = DefusedElementTree.fromstring(xml_text)
    except DefusedElementTree.ParseError, DefusedXmlException:
        return None
    if _local_name(root.tag).lower() != "error":
        return None
    attributes = {
        _local_name(key).lower(): (value or "").strip() for key, value in root.attrib.items()
    }
    return TorznabError(
        code=attributes.get("code", ""), description=attributes.get("description", "")
    )
```

- [ ] **Step 5: Mark failures and rate limits in `shelfmark/release_sources/prowlarr/api.py`**

Replace

```python
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_xml
```

with

```python
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_error, parse_torznab_xml
```

In the `ProwlarrSearchError` docstring, replace

```python
    auto-expand retry fire a second request on top of the one still running.
    """
```

with

```python
    auto-expand retry fire a second request on top of the one still running.

    ``rate_limited`` marks an HTTP 429 or a request-limit error document: still a
    failure, but logged as its own outcome.
    """

    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited


def _is_rate_limited(error: BaseException) -> bool:
    """Whether a failed request was refused with HTTP 429 Too Many Requests."""
    response = getattr(error, "response", None)
    return getattr(response, "status_code", None) == HTTPStatus.TOO_MANY_REQUESTS
```

At the end of `torznab_search`, replace

```python
            msg = f"indexer {indexer_id} search failed: {e}"
            raise ProwlarrSearchError(msg) from e
        else:
            return results
```

with

```python
            msg = f"indexer {indexer_id} search failed: {e}"
            raise ProwlarrSearchError(msg, rate_limited=_is_rate_limited(e)) from e
        else:
            # A 200 carrying an <error .../> document is a failed search, not an empty one.
            error = None if results else parse_torznab_error(response.text)
            if error is not None:
                msg = (
                    f"indexer {indexer_id} returned error {error.code}: "
                    f"{error.description or 'no description'}"
                )
                raise ProwlarrSearchError(msg, rate_limited=error.rate_limited)
            return results
```

- [ ] **Step 6: Implement the ladder in `shelfmark/release_sources/prowlarr/source.py`**

Replace

```python
from shelfmark.core.config import config
from shelfmark.core.languages import normalize_language
```

with

```python
from shelfmark.core import search_deadline
from shelfmark.core.config import config
from shelfmark.core.languages import normalize_language
```

Replace

```python
from shelfmark.core.search_plan import ReleaseSearchVariant
```

with

```python
from shelfmark.core.search_plan import ReleaseSearchVariant
from shelfmark.core.search_queries import any_identity_hit
```

Replace

```python
@dataclass
class _IndexerSearchOutcome:
```

with

```python
# Fallback queries (shelfmark.core.search_queries) may send each indexer at most this
# many requests per search, auto-expanded retries included.
FALLBACK_REQUESTS_PER_INDEXER = 4


@dataclass(frozen=True)
class _RequestLog:
    """One Torznab request, logged at INFO once its outcome is known."""

    query: str
    indexer: str
    categories: list[int] | None
    rung: str
    expanded: bool

    def log(self, outcome: str, count: int) -> None:
        logger.info(
            "Prowlarr request: query='%s' indexer=%s categories=%s rung=%s expanded=%s "
            "outcome=%s results=%s",
            self.query,
            self.indexer,
            ",".join(str(c) for c in self.categories) if self.categories else "all",
            self.rung,
            "yes" if self.expanded else "no",
            outcome,
            count,
        )


@dataclass
class _IndexerSearchOutcome:
```

In `_IndexerSearchOutcome`, replace

```python
    results: list[dict]
    attempted: int = 0
    failed: int = 0
    last_error: str | None = None
```

with

```python
    results: list[dict]
    attempted: int = 0
    failed: int = 0
    last_error: str | None = None
    # A fallback pass stopped because the budget left could not cover another request.
    deadline_reached: bool = False
```

In `ProwlarrSource.__init__`, replace

```python
        """Initialize per-instance search state for Prowlarr."""
        self.last_search_type: str | None = None
```

with

```python
        """Initialize per-instance search state for Prowlarr."""
        self.last_search_type: str | None = None
        # The last search was cut short by its deadline (results, if any, are partial).
        self.last_search_incomplete = False
```

At the top of `ProwlarrSource.search`, replace

```python
        """Search Prowlarr indexers for releases matching the book."""
        client = self._get_client()
```

with

```python
        """Search Prowlarr indexers for releases matching the book."""
        self.last_search_incomplete = False
        client = self._get_client()
```

In `ProwlarrSource.search`, replace everything from the line

```python
            def search_indexers(query: str, cats: list[int] | None) -> _IndexerSearchOutcome:
```

up to, but not including, the line

```python
            if failed_searches:
```

(i.e. the old `search_indexers`, the `seen_keys`/`all_results`/counter initialisation and the whole `for idx, variant in enumerate(variants, start=1):` loop) with:

```python
            indexer_names = {
                parsed_id: str(indexer.get("name") or parsed_id)
                for indexer in enabled_indexers
                if (parsed_id := _coerce_indexer_id(indexer.get("id"))) is not None
            }
            failed_indexers: set[int] = set()
            fallback_requests: dict[int, int] = {}

            def fallback_targets(cats: list[int] | None) -> list[int]:
                """Indexers a fallback request may go to, from the snapshot taken above.

                No network call: an indexer that failed during this search or has used
                its cap is left out before anything is sent.
                """
                if indexer_ids is not None:
                    candidates = list(indexer_ids)
                else:
                    candidates = [
                        parsed_id
                        for indexer in enabled_indexers
                        if _indexer_supports_search_categories(indexer, cats)
                        and (parsed_id := _coerce_indexer_id(indexer.get("id"))) is not None
                    ]
                return [
                    indexer_id
                    for indexer_id in candidates
                    if indexer_id not in failed_indexers
                    and fallback_requests.get(indexer_id, 0) < FALLBACK_REQUESTS_PER_INDEXER
                ]

            def search_indexers(
                query: str,
                cats: list[int] | None,
                *,
                rung: str,
                expanded: bool = False,
                targets: list[int] | None = None,
            ) -> _IndexerSearchOutcome:
                """Search indexers with given categories via Torznab/Newznab.

                Every indexer gets the same title-only query. Enriched indexers used
                to be sent "{title} {author}", but an indexer that ANDs its search
                terms (MyAnonamouse) returns nothing whenever the metadata provider
                spells the author differently to the tracker - "Timothy Ferriss" vs
                "Tim Ferriss" - and the UI reports the book as missing (#1293). The
                author still decides ordering below, where a spelling difference
                costs a release its position rather than its existence.

                ``targets`` marks a fallback pass: those indexers only, each request
                counted against the indexer's cap and sent only while the remaining
                budget still covers a whole indexer timeout - past that it is skipped,
                never raised, so what was already found is kept.
                """
                outcome = _IndexerSearchOutcome(results=[])
                fallback = targets is not None
                target_indexer_ids = (
                    targets
                    if targets is not None
                    else self._get_search_indexer_ids(client, indexer_ids, cats)
                )
                if not target_indexer_ids:
                    return outcome

                for indexer_id in target_indexer_ids:
                    if fallback:
                        if search_deadline.remaining_seconds(deadline) < client.indexer_timeout:
                            outcome.deadline_reached = True
                            break
                        fallback_requests[indexer_id] = fallback_requests.get(indexer_id, 0) + 1
                    else:
                        _check_timeout()
                    outcome.attempted += 1
                    request = _RequestLog(
                        query=query,
                        indexer=indexer_names.get(indexer_id, str(indexer_id)),
                        categories=cats,
                        rung=rung,
                        expanded=expanded,
                    )
                    try:
                        raw = client.torznab_search(
                            indexer_id=indexer_id,
                            query=query,
                            categories=cats,
                            search_type="book",
                        )
                    except ProwlarrSearchError as e:
                        # One unreachable indexer must not sink the others, but it
                        # is not "no results" either - record it so the caller can
                        # report a failed search instead of an empty one.
                        outcome.failed += 1
                        outcome.last_error = str(e)
                        failed_indexers.add(indexer_id)
                        request.log("rate-limited" if e.rate_limited else "failed", 0)
                        continue
                    request.log("ok" if raw else "empty", len(raw))
                    if raw:
                        outcome.results.extend(raw)

                return outcome

            seen_keys: set[tuple[int | None, str]] = set()
            all_results: list[dict] = []
            attempted_searches = 0
            failed_searches = 0
            last_search_error: str | None = None

            def add_results(outcome: _IndexerSearchOutcome) -> list[dict]:
                """Fold one pass into the totals; return the results it newly added."""
                nonlocal attempted_searches, failed_searches, last_search_error
                attempted_searches += outcome.attempted
                failed_searches += outcome.failed
                last_search_error = outcome.last_error or last_search_error

                added: list[dict] = []
                for r in outcome.results:
                    key = _result_dedup_key(r)
                    if key is not None:
                        if key in seen_keys:
                            continue
                        seen_keys.add(key)
                    all_results.append(r)
                    added.append(r)
                return added

            def has_identity_hit(found: list[dict]) -> bool:
                return any_identity_hit(
                    (r.get("title") for r in found), plan.identity, content_type=content_type
                )

            mandatory_variants = [v for v in variants if not v.fallback]
            fallback_variants = [v for v in variants if v.fallback]

            # Mandatory variants run exactly as before; a timeout among them now keeps
            # what was already found instead of discarding it (raised below if nothing).
            mandatory_timeout: TimeoutError | None = None
            try:
                for idx, variant in enumerate(mandatory_variants, start=1):
                    _check_timeout()
                    query = variant.title
                    rung = f"mandatory {idx}"

                    if len(mandatory_variants) > 1:
                        logger.debug(
                            "Prowlarr query %s/%s: '%s'", idx, len(mandatory_variants), query
                        )

                    outcome = search_indexers(query=query, cats=categories, rung=rung)

                    # Auto-expand: if no results with categories and auto-expand enabled, retry
                    # without. Only when every indexer actually answered: a failed search says
                    # nothing about whether the category filter is what hid the book, and
                    # retrying it stacks a second request on an indexer that is still busy
                    # solving a Cloudflare challenge (#1249).
                    if (
                        not outcome.results
                        and not outcome.failed
                        and categories
                        and auto_expand_enabled
                    ):
                        _check_timeout()
                        logger.info(
                            "Prowlarr: no results for query '%s' with category filter, auto-expanding search",
                            query,
                        )
                        expanded = search_indexers(query=query, cats=None, rung=rung, expanded=True)
                        outcome.results = expanded.results
                        outcome.attempted += expanded.attempted
                        outcome.failed += expanded.failed
                        outcome.last_error = expanded.last_error or outcome.last_error
                        self.last_search_type = "expanded"

                    add_results(outcome)
            except TimeoutError as e:
                logger.warning("Prowlarr search timed out: %s", e)
                mandatory_timeout = e

            # Fallbacks run only while nothing found so far is the requested book, and
            # stop at the first rung that finds it. Failed indexers sit them out.
            fallback_stop = "not planned"
            fallback_rungs_run = 0
            if fallback_variants:
                if mandatory_timeout is not None:
                    fallback_stop = "deadline"
                elif has_identity_hit(all_results):
                    fallback_stop = "hit"
                else:
                    fallback_stop = "exhausted"
            ladder = fallback_variants if fallback_stop == "exhausted" else []
            for idx, variant in enumerate(ladder, start=1):
                query = variant.title
                rung = f"fallback {idx}"
                cats = categories
                targets = fallback_targets(cats)
                if not targets and cats:
                    # No category-compatible indexer can take this rung, but others may:
                    # send it to them unrestricted rather than stopping the ladder.
                    cats = None
                    targets = fallback_targets(None)
                if not targets:
                    # Every indexer has failed during this search or used its cap.
                    fallback_stop = "cap"
                    break
                outcome = search_indexers(
                    query=query,
                    cats=cats,
                    rung=rung,
                    expanded=cats is None and bool(categories),
                    targets=targets,
                )
                if not outcome.attempted:
                    fallback_stop = "deadline"
                    break
                fallback_rungs_run += 1

                # Expansion goes only to indexers still eligible, so a failed one is
                # never asked twice; a healthy one that answered empty still is.
                if (
                    not outcome.results
                    and not outcome.deadline_reached
                    and cats
                    and auto_expand_enabled
                ):
                    expand_targets = fallback_targets(None)
                    if expand_targets:
                        expanded = search_indexers(
                            query=query,
                            cats=None,
                            rung=rung,
                            expanded=True,
                            targets=expand_targets,
                        )
                        outcome.results = expanded.results
                        outcome.attempted += expanded.attempted
                        outcome.failed += expanded.failed
                        outcome.last_error = expanded.last_error or outcome.last_error
                        outcome.deadline_reached = expanded.deadline_reached
                        if expanded.attempted:
                            self.last_search_type = "expanded"

                if has_identity_hit(add_results(outcome)):
                    fallback_stop = "hit"
                    break
                if outcome.deadline_reached:
                    fallback_stop = "deadline"
                    break

            self.last_search_incomplete = fallback_stop == "deadline"
            logger.info(
                "Prowlarr fallbacks: ran=%s stop=%s rungs=%s/%s requests=%s",
                "yes" if fallback_rungs_run else "no",
                fallback_stop,
                fallback_rungs_run,
                len(fallback_variants),
                sum(fallback_requests.values()),
            )

```

Finally, at the end of `search`, replace

```python
            # An empty list is the UI's "No releases found for this book", so it has
            # to mean the indexers answered and had nothing. When they failed instead,
            # say so rather than blaming the book (#1249).
            if not results and failed_searches:
                msg = (
                    f"{failed_searches} of {attempted_searches} indexer searches failed "
                    f"({last_search_error})"
                )
                raise SourceUnavailableError(msg)
            return results
```

with

```python
            if mandatory_timeout is not None:
                if not results:
                    # As before: a timed-out search with nothing found is an error.
                    raise mandatory_timeout
                # Partial results are still results; the endpoint reports the search as
                # incomplete through last_search_incomplete.
                self.last_search_incomplete = True
            # An empty list is the UI's "No releases found for this book", so it has
            # to mean the indexers answered and had nothing. When they failed instead,
            # say so rather than blaming the book (#1249).
            if not results and failed_searches:
                msg = (
                    f"{failed_searches} of {attempted_searches} indexer searches failed "
                    f"({last_search_error})"
                )
                raise SourceUnavailableError(msg)
            # Cut short before every fallback ran: not a completed "no releases".
            if not results and fallback_stop == "deadline":
                msg = (
                    "search incomplete: ran out of time before every fallback query ran "
                    f"({int(search_budget)}s budget)"
                )
                raise SourceUnavailableError(msg)
            return results
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_search_deadline.py tests/prowlarr -q --deselect tests/core/test_search_deadline.py::test_html_get_page_will_not_start_a_bypass_on_a_spent_budget`
Expected: PASS (all; 42 skipped are pre-existing)

- [ ] **Step 8: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark/core/search_deadline.py shelfmark/release_sources/prowlarr tests/prowlarr tests/core/test_search_deadline.py
uv run ruff format --check shelfmark/core/search_deadline.py shelfmark/release_sources/prowlarr tests/prowlarr tests/core/test_search_deadline.py
uv run basedpyright shelfmark/core/search_deadline.py shelfmark/release_sources/prowlarr
git add shelfmark/core/search_deadline.py shelfmark/release_sources/prowlarr/torznab.py shelfmark/release_sources/prowlarr/api.py shelfmark/release_sources/prowlarr/source.py tests/core/test_search_deadline.py tests/prowlarr/test_torznab.py tests/prowlarr/test_api_timeout.py tests/prowlarr/test_source.py tests/prowlarr/test_source_fallbacks.py
git commit -m "feat(prowlarr): run fallback queries only until a real hit, capped per indexer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Newznab — failure vs empty, per-connection ladder, retained hits, cap, deadline, logs

**Files:**
- Modify: `shelfmark/release_sources/newznab/api.py` (`NewznabSearchError`; `NewznabClient.search`)
- Modify: `shelfmark/release_sources/newznab/source.py` (imports, constants, `_request_timeout`, `_search_once`, `NewznabSource.__init__`, `NewznabSource.search`)
- Test: `tests/newznab/test_api.py`, `tests/newznab/test_source.py`, `tests/newznab/test_source_fallbacks.py` (new)

**Interfaces:**
- Consumes: `any_identity_hit` (Task 2); `ReleaseSearchVariant.fallback`, `ReleaseSearchPlan.identity` (Task 3); `search_deadline.remaining_seconds`, `prowlarr.torznab.parse_torznab_error` (Task 4).
- Produces: `NewznabSearchError(message: str, *, rate_limited: bool = False)`; `NewznabClient.search(...)` raises it on failure (request error or `<error>` document) and returns `[]` only for an empty success; `newznab.source.FALLBACK_REQUESTS_PER_CONNECTION = 4`; `NewznabSource.last_search_incomplete: bool`; `NewznabSource.search` raises `SourceUnavailableError` when nothing remains and any search failed. Log formats: `Newznab request: query='…' connection=<name> categories=<…|all> rung=<mandatory N|fallback N> expanded=<yes|no> outcome=<ok|empty|failed|rate-limited> results=<n>` and `Newznab [<name>] fallbacks: ran=<yes|no> stop=<hit|cap|deadline|exhausted|failed|not planned> rungs=<run>/<planned> requests=<n>`.

- [ ] **Step 1: Write the failing tests**

In `tests/newznab/test_api.py`, replace

```python
from unittest.mock import MagicMock, patch

import requests

from shelfmark.release_sources.newznab.api import NewznabClient
```

with

```python
from unittest.mock import MagicMock, patch

import pytest
import requests

from shelfmark.release_sources.newznab.api import NewznabClient, NewznabSearchError
```

and replace the whole `test_returns_empty_on_request_error` method

```python
    def test_returns_empty_on_request_error(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        with patch.object(
            client,
            "_get",
            side_effect=requests.exceptions.ConnectionError("down"),
        ):
            results = client.search(query="book")
        assert results == []
```

with

```python
    def test_a_request_error_is_a_failure_not_an_empty_result(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        with (
            patch.object(
                client,
                "_get",
                side_effect=requests.exceptions.ConnectionError("down"),
            ),
            pytest.raises(NewznabSearchError, match="down") as excinfo,
        ):
            client.search(query="book")
        assert excinfo.value.rate_limited is False

    def test_http_429_is_a_rate_limited_failure(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        error = requests.exceptions.HTTPError(response=_make_response("", status=429))
        with (
            patch.object(client, "_get", side_effect=error),
            pytest.raises(NewznabSearchError) as excinfo,
        ):
            client.search(query="book")
        assert excinfo.value.rate_limited is True

    @pytest.mark.parametrize(
        ("code", "rate_limited"),
        [("500", True), ("501", True), ("100", False), ("900", False)],
    )
    def test_a_newznab_error_document_is_a_failure(self, code, rate_limited):
        client = NewznabClient("http://nzbhydra:5076", "key")
        body = f'<?xml version="1.0"?><error code="{code}" description="nope"/>'
        with (
            patch.object(client, "_get", return_value=_make_response(body)),
            pytest.raises(NewznabSearchError, match=f"indexer error {code}") as excinfo,
        ):
            client.search(query="book")
        assert excinfo.value.rate_limited is rate_limited

    @pytest.mark.parametrize(
        "body",
        [
            "<error code='500' description='Request limit reached'/>",
            '<error\n  code = "500"\n  description = "Request limit reached" />',
            '<nn:error xmlns:nn="http://www.newznab.com/DTD/2010/feeds/attributes/" '
            'code="500" description="Request limit reached"/>',
        ],
    )
    def test_error_documents_are_parsed_not_pattern_matched(self, body):
        client = NewznabClient("http://nzbhydra:5076", "key")
        with (
            patch.object(client, "_get", return_value=_make_response(body)),
            pytest.raises(NewznabSearchError, match="indexer error 500") as excinfo,
        ):
            client.search(query="book")
        assert excinfo.value.rate_limited is True

    def test_a_feed_that_mentions_error_is_not_one(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        body = (
            '<?xml version="1.0"?><rss><channel><description>&lt;error code="500"&gt;'
            "</description></channel></rss>"
        )
        with patch.object(client, "_get", return_value=_make_response(body)):
            assert client.search(query="book") == []

    def test_an_empty_feed_is_still_an_empty_success(self):
        client = NewznabClient("http://nzbhydra:5076", "key")
        empty = '<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>'
        with patch.object(client, "_get", return_value=_make_response(empty)):
            assert client.search(query="book") == []
```

In `tests/newznab/test_source.py`, a client exception is now reported instead of read as "no releases". Replace

```python
    def test_exception_in_client_returns_empty(self, monkeypatch):
        client = MagicMock()
        client.search.side_effect = RuntimeError("boom")
        src = self._patched_source(monkeypatch, client)
        book = _make_book()
        results = src.search(book, _make_plan(book))
        assert results == []
```

with

```python
    def test_exception_in_client_is_reported_not_returned_as_empty(self, monkeypatch):
        from shelfmark.release_sources import SourceUnavailableError

        client = MagicMock()
        client.search.side_effect = RuntimeError("boom")
        src = self._patched_source(monkeypatch, client)
        book = _make_book()
        with pytest.raises(SourceUnavailableError, match="boom"):
            src.search(book, _make_plan(book))
```

Create `tests/newznab/test_source_fallbacks.py`:

```python
"""Newznab runs the fallback ladder per connection, only while nothing it found is the book."""

from __future__ import annotations

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

    def __init__(self, answers=None, *, clock=None, seconds_per_request=0.0):
        self.answers = answers or {}
        self.timeout = 30
        self.calls: list[tuple[str, object]] = []
        self.clock = clock
        self.seconds_per_request = seconds_per_request

    def search(self, query, categories=None, search_type="search", limit=100, offset=0):
        del search_type, limit, offset
        self.calls.append((query, categories))
        if self.clock is not None:
            self.clock.now += self.seconds_per_request
        answer = self.answers.get(query, [])
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
):
    rows = [{"name": name, "url": f"https://{name}.example"} for name in connections]
    values = {"NEWZNAB_INDEXERS": rows, "NEWZNAB_AUTO_EXPAND": auto_expand}
    monkeypatch.setattr(
        newznab_source.config, "get", lambda key, default=None: values.get(key, default)
    )
    by_url = {f"https://{name}.example": client for name, client in connections.items()}
    monkeypatch.setattr(newznab_source, "NewznabClient", lambda url, _key: by_url[url])

    book = _dxd5()
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

        with pytest.raises(SourceUnavailableError, match="indexer error 900"):
            _search(monkeypatch, {"flaky": flaky})

        assert flaky.queries() == [DXD5, RUNG_1]


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
        lines = _info_lines(monkeypatch)
        source = NewznabSource()

        releases = _search(monkeypatch, {"geek": geek}, source=source)

        # Budget 120s, one 100s request spent: 20s left cannot cover a 30s request.
        assert geek.queries() == [DXD5]
        assert [r.title for r in releases] == [WRONG_VOLUME]
        assert "Newznab [geek] fallbacks: ran=no stop=deadline rungs=0/5 requests=0" in lines
        assert source.last_search_incomplete is True

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

        with pytest.raises(SourceUnavailableError, match="Newznab search incomplete"):
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/newznab -q`
Expected: FAIL — collection errors in `tests/newznab/test_api.py` and `tests/newznab/test_source_fallbacks.py`: `ImportError: cannot import name 'NewznabSearchError' from 'shelfmark.release_sources.newznab.api'`; `test_exception_in_client_is_reported_not_returned_as_empty` fails with `Failed: DID NOT RAISE`.

- [ ] **Step 3: Raise on failure in `shelfmark/release_sources/newznab/api.py`**

Replace

```python
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_xml

logger = setup_logger(__name__)
```

with

```python
from shelfmark.release_sources.prowlarr.torznab import parse_torznab_error, parse_torznab_xml

logger = setup_logger(__name__)

_HTTP_TOO_MANY_REQUESTS = 429


class NewznabSearchError(RuntimeError):
    """A Newznab search could not be completed - never the same as "no results".

    ``rate_limited`` marks an HTTP 429 or a Newznab request/download-limit error.
    """

    def __init__(self, message: str, *, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.rate_limited = rate_limited
```

In `NewznabClient.search`, replace

```python
        Returns:
            List of result dicts shaped like Prowlarr JSON search results so that
            the shared ``_prowlarr_result_to_release`` converter can process them.
        """
```

with

```python
        Returns:
            List of result dicts shaped like Prowlarr JSON search results so that
            the shared ``_prowlarr_result_to_release`` converter can process them.
            An empty list strictly means the indexer answered with no matches.

        Raises:
            NewznabSearchError: The request failed, or the indexer answered with a
                Newznab ``<error>`` instead of a result feed.
        """
```

and replace the request block at the end of the method

```python
        try:
            response = self._get(params, accept_xml=True)
            results = parse_torznab_xml(response.text)
            logger.debug("Newznab search '%s': %d results", query, len(results))
            if not results:
                preview = response.text[:300].strip() if response.text else "<empty>"
                logger.debug("Newznab empty response body: %s", preview)
        except requests.exceptions.RequestException:
            logger.exception("Newznab search request failed")
            return []
        except Exception:
            logger.exception("Newznab search failed")
            return []
        else:
            return results
```

with

```python
        try:
            response = self._get(params, accept_xml=True)
        except requests.exceptions.RequestException as e:
            logger.warning("Newznab search request failed: %s", e)
            status = getattr(e.response, "status_code", None)
            msg = f"Newznab search failed: {e}"
            raise NewznabSearchError(msg, rate_limited=status == _HTTP_TOO_MANY_REQUESTS) from e

        text = response.text or ""
        results = parse_torznab_xml(text)
        logger.debug("Newznab search '%s': %d results", query, len(results))
        if not results:
            error = parse_torznab_error(text)
            if error is not None:
                msg = (
                    f"Newznab search failed: indexer error {error.code}: "
                    f"{error.description or 'no description'}"
                )
                raise NewznabSearchError(msg, rate_limited=error.rate_limited)
            preview = text[:300].strip() or "<empty>"
            logger.debug("Newznab empty response body: %s", preview)
        return results
```

- [ ] **Step 4: Per-connection ladder in `shelfmark/release_sources/newznab/source.py`**

Replace

```python
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.utils import normalize_http_url
```

with

```python
from shelfmark.core import search_deadline
from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
from shelfmark.core.search_queries import any_identity_hit
from shelfmark.core.utils import normalize_http_url
```

Replace

```python
    ReleaseSource,
    register_source,
)
from shelfmark.release_sources.newznab.api import NewznabClient
```

with

```python
    ReleaseSource,
    SourceUnavailableError,
    register_source,
)
from shelfmark.release_sources.newznab.api import NewznabClient, NewznabSearchError
```

Replace

```python
# Reuse the same timeout constant as Prowlarr.
NEWZNAB_SEARCH_TIMEOUT_SECONDS = _SEARCH_TIMEOUT
```

with

```python
# Reuse the same timeout constant as Prowlarr.
NEWZNAB_SEARCH_TIMEOUT_SECONDS = _SEARCH_TIMEOUT

# Fallback queries (shelfmark.core.search_queries) may send each connection at most
# this many requests per search, auto-expanded retries included.
FALLBACK_REQUESTS_PER_CONNECTION = 4

# What a fallback request is assumed to need when a client reports no usable timeout.
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0
```

Insert before `def _parse_indexer_rows(raw: object) -> list[tuple[str, str, str]]:`:

```python
def _request_timeout(client: object) -> float:
    """The read timeout one request on ``client`` may take."""
    timeout = getattr(client, "timeout", None)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        return _DEFAULT_REQUEST_TIMEOUT_SECONDS
    return float(timeout)


def _search_once(
    connection: _NamedClient,
    query: str,
    categories: list[int] | None,
    *,
    rung: str,
    errors: list[str],
    expanded: bool = False,
) -> list[dict] | None:
    """Send one search and log it at INFO; None means it failed (recorded in ``errors``)."""
    try:
        raw = connection.client.search(query=query, categories=categories)
    except NewznabSearchError as e:
        outcome, count = ("rate-limited" if e.rate_limited else "failed"), 0
        errors.append(f"{connection.name}: {e}")
        raw = None
    else:
        outcome, count = ("ok" if raw else "empty"), len(raw)
    logger.info(
        "Newznab request: query='%s' connection=%s categories=%s rung=%s expanded=%s "
        "outcome=%s results=%s",
        query,
        connection.name,
        ",".join(str(c) for c in categories) if categories else "all",
        rung,
        "yes" if expanded else "no",
        outcome,
        count,
    )
    return raw


```

In `NewznabSource`, replace

```python
    supported_content_types: ClassVar[list[str]] = ["ebook", "audiobook"]

    def get_column_config(self) -> ReleaseColumnConfig:
```

with

```python
    supported_content_types: ClassVar[list[str]] = ["ebook", "audiobook"]

    def __init__(self) -> None:
        """Initialize per-instance search state for Newznab."""
        # The last search was cut short by its deadline (results, if any, are partial).
        self.last_search_incomplete = False

    def get_column_config(self) -> ReleaseColumnConfig:
```

At the top of `NewznabSource.search`, replace

```python
        """Search the Newznab indexer for releases matching the book."""
        clients = self._get_clients()
```

with

```python
        """Search the Newznab indexer for releases matching the book."""
        self.last_search_incomplete = False
        clients = self._get_clients()
```

In `NewznabSource.search`, replace everything from the line

```python
        queries = [v.title for v in plan.title_variants if v.title]
```

up to, but not including, the line

```python
        results = [_newznab_result_to_release(r, content_type, categories) for r in all_results]
```

with:

```python
        variants = [v for v in plan.title_variants if v.title]
        queries = [v.title for v in variants if not v.fallback]
        fallback_queries = [v.title for v in variants if v.fallback]

        if not queries and plan.isbn_candidates:
            queries = list(plan.isbn_candidates)

        if not queries:
            logger.warning("Newznab: no search query available")
            return []

        # Category selection — omit categories when expanding search
        categories = None if expand_search else _configured_categories(content_type)

        auto_expand = config.get("NEWZNAB_AUTO_EXPAND", False)
        deadline = time.monotonic() + NEWZNAB_SEARCH_TIMEOUT_SECONDS
        selected_indexers = set(plan.indexers) if plan.indexers else None

        def _check_timeout() -> None:
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"Newznab search timed out after {int(NEWZNAB_SEARCH_TIMEOUT_SECONDS)}s"
                )

        seen_keys: set = set()
        all_results: list[dict] = []
        cut_short = False
        errors: list[str] = []

        def add_results(connection: _NamedClient, raw: list[dict]) -> list[dict]:
            """Label and keep one response; return the rows it newly kept that are shown.

            Only those can stop the ladder: a repeated GUID was already judged, and a
            row the plan.indexers filter drops is never shown.
            """
            retained: list[dict] = []
            for raw_result in raw:
                r = dict(raw_result)
                # Aggregators can identify the underlying indexer. Plain feeds
                # generally cannot, so use the user-configured connection name.
                r["indexer"] = r.get("indexer") or connection.name
                r["_newznab_connection_id"] = connection.connection_id
                key = (
                    connection.connection_id,
                    r.get("guid") or r.get("downloadUrl") or f"{r.get('indexer')}:{r.get('title')}",
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                all_results.append(r)
                if selected_indexers is None or r["indexer"] in selected_indexers:
                    retained.append(r)
            return retained

        def has_identity_hit(rows: list[dict]) -> bool:
            # Only rows that survive the plan.indexers filter can stop the ladder.
            return any_identity_hit(
                (r.get("title") for r in rows), plan.identity, content_type=content_type
            )

        def run_fallbacks(connection: _NamedClient, found: list[dict]) -> tuple[str, int, int]:
            """Run this connection's ladder; return (stop reason, rungs, requests)."""
            if has_identity_hit(found):
                return "hit", 0, 0
            timeout = _request_timeout(connection.client)
            requests_sent = 0
            rungs = 0

            def request(query: str, cats: list[int] | None, rung: str) -> list[dict] | str:
                nonlocal requests_sent, cut_short
                if requests_sent >= FALLBACK_REQUESTS_PER_CONNECTION:
                    return "cap"
                if search_deadline.remaining_seconds(deadline) < timeout:
                    cut_short = True
                    return "deadline"
                requests_sent += 1
                raw = _search_once(
                    connection,
                    query,
                    cats,
                    rung=rung,
                    errors=errors,
                    expanded=cats is None and bool(categories),
                )
                return "failed" if raw is None else raw

            for idx, query in enumerate(fallback_queries, start=1):
                rung = f"fallback {idx}"
                raw = request(query, categories, rung)
                if raw in ("cap", "deadline"):
                    return str(raw), rungs, requests_sent
                rungs += 1
                if isinstance(raw, str):
                    return raw, rungs, requests_sent
                if not raw and categories and auto_expand:
                    raw = request(query, None, rung)
                    if isinstance(raw, str):
                        return raw, rungs, requests_sent
                if has_identity_hit(add_results(connection, raw)):
                    return "hit", rungs, requests_sent
            return "exhausted", rungs, requests_sent

        try:
            for connection in clients:
                found: list[dict] = []
                failed = False
                try:
                    for idx, query in enumerate(queries, start=1):
                        _check_timeout()
                        if len(queries) > 1:
                            logger.debug(
                                "Newznab [%s] query %d/%d: '%s'",
                                connection.name,
                                idx,
                                len(queries),
                                query,
                            )

                        rung = f"mandatory {idx}"
                        raw = _search_once(connection, query, categories, rung=rung, errors=errors)
                        if raw is None:
                            # The connection gets no fallbacks; the mandatory retry below
                            # still happens, as it always has for an empty answer.
                            failed = True

                        # Auto-expand: retry without category filter if no results
                        if not raw and categories and auto_expand:
                            _check_timeout()
                            logger.info(
                                "Newznab [%s]: no results for '%s' with category filter, "
                                "auto-expanding",
                                connection.name,
                                query,
                            )
                            raw = _search_once(
                                connection, query, None, rung=rung, errors=errors, expanded=True
                            )
                            if raw is None:
                                failed = True

                        found.extend(add_results(connection, raw or []))

                    if not fallback_queries:
                        stop, rungs, sent = "not planned", 0, 0
                    elif failed:
                        # A failed connection gets no further fallback requests.
                        stop, rungs, sent = "failed", 0, 0
                    else:
                        stop, rungs, sent = run_fallbacks(connection, found)
                    logger.info(
                        "Newznab [%s] fallbacks: ran=%s stop=%s rungs=%s/%s requests=%s",
                        connection.name,
                        "yes" if rungs else "no",
                        stop,
                        rungs,
                        len(fallback_queries),
                        sent,
                    )
                except TimeoutError:
                    raise
                except Exception as e:
                    logger.exception("Newznab search failed for %s", connection.name)
                    errors.append(f"{connection.name}: {e}")

        except TimeoutError as e:
            logger.warning("Newznab search timed out: %s", e)
            cut_short = True

```

Directly below that line, replace

```python
        if plan.indexers:
            selected_indexers = set(plan.indexers)
            results = [r for r in results if r.indexer in selected_indexers]
```

with

```python
        if selected_indexers is not None:
            results = [r for r in results if r.indexer in selected_indexers]
```

and replace

```python
        else:
            logger.debug("Newznab: no results found")

        return results
```

with

```python
            if errors:
                logger.warning(
                    "Newznab: %d search(es) failed, returning what the others found (%s)",
                    len(errors),
                    errors[-1],
                )
        else:
            logger.debug("Newznab: no results found")
            if errors:
                # Not "no releases": some of the indexers never answered.
                msg = f"{len(errors)} Newznab search(es) failed ({'; '.join(errors[-3:])})"
                raise SourceUnavailableError(msg)
            if cut_short:
                # Ran out of time before every query ran: not a completed "no releases".
                msg = (
                    "Newznab search incomplete: ran out of time "
                    f"({int(NEWZNAB_SEARCH_TIMEOUT_SECONDS)}s budget)"
                )
                raise SourceUnavailableError(msg)

        self.last_search_incomplete = cut_short
        return results
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/newznab tests/prowlarr -q`
Expected: PASS (all; 42 skipped are pre-existing)

- [ ] **Step 6: Lint, typecheck, commit**

```bash
uv run ruff check shelfmark/release_sources/newznab tests/newznab
uv run ruff format --check shelfmark/release_sources/newznab tests/newznab
uv run basedpyright shelfmark/release_sources/newznab
git add shelfmark/release_sources/newznab/api.py shelfmark/release_sources/newznab/source.py tests/newznab/test_api.py tests/newznab/test_source.py tests/newznab/test_source_fallbacks.py
git commit -m "feat(newznab): tell failure from empty; per-connection fallback ladder

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Acceptance harness (offline-tested) and full gates

**Files:**
- Create: `scripts/ladder_acceptance.py`
- Test: `tests/core/test_ladder_acceptance_script.py` (new)

**Interfaces:**
- Consumes: `build_release_search_plan(..., content_type="ebook")`, `ProwlarrSource.search`, `build_search_identity`, `is_identity_hit`, `HardcoverProvider._parse_book`, `ProwlarrClient`.
- Produces: `scripts/ladder_acceptance.py` with `BOOKS` (19 rows), `STANDALONES`, `run_books(books, client_factory) -> list[BookResult]` (each search under `search_deadline.search_deadline()`), `report(results, json_path=None) -> int` (prints every book's titles, writes the adjudication JSON; exit code only reflects the standalone check), `main(argv=None) -> int` (`--json`, default `ladder_acceptance.json`). **The plan never runs `main()` against a real Prowlarr**; the tests drive `run_books` with a fake client.

- [ ] **Step 1: Write the failing test**

Create `tests/core/test_ladder_acceptance_script.py`:

```python
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/core/test_ladder_acceptance_script.py -q`
Expected: FAIL — 4 failed with `FileNotFoundError: [Errno 2] No such file or directory: '…/scripts/ladder_acceptance.py'`

- [ ] **Step 3: Create `scripts/ladder_acceptance.py`**

```python
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/core/test_ladder_acceptance_script.py -q`
Expected: PASS (4 passed)

Run: `uv run python scripts/ladder_acceptance.py --help`
Expected: usage text; exits 0 without any network access.

- [ ] **Step 5: Full gates**

```bash
uv run pytest tests/ -q -m "not integration and not e2e" --deselect tests/core/test_search_deadline.py::test_html_get_page_will_not_start_a_bypass_on_a_spent_budget
uv run ruff check shelfmark tests scripts/ladder_acceptance.py
uv run ruff format --check shelfmark tests scripts/ladder_acceptance.py
uv run basedpyright
uv run basedpyright tests --skipunannotated
uv run basedpyright scripts/ladder_acceptance.py
uv run vulture shelfmark
```

Expected: pytest — only the 9 pre-existing failures in `tests/config/test_entrypoint_permissions.py`; ruff clean; basedpyright — only the 4 pre-existing errors at `shelfmark/main.py:2305-2308`; `tests` and the script 0 errors; vulture prints nothing. (Off a sandboxed host, also run without the `--deselect`.)

- [ ] **Step 6: Commit**

```bash
git add scripts/ladder_acceptance.py tests/core/test_ladder_acceptance_script.py
git commit -m "test(search): live acceptance harness for the query ladder (user-gated)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Live acceptance — USER-GATED, do not run without the user's OK

This step talks to the live Prowlarr and its indexers. Ask the user first; run it only with their explicit go-ahead.

- [ ] **Step 1: Reach Prowlarr** (user's terminal or with their OK): `kubectl port-forward -n media svc/prowlarr 9696:9696`
- [ ] **Step 2: Run the production path for the 19 books**, once with the production `PROWLARR_AUTO_EXPAND` value (ask the user which it is) — add `--auto-expand` if it is on:

```bash
PROWLARR_URL=http://localhost:9696 PROWLARR_API_KEY=<key from the user> \
  uv run python scripts/ladder_acceptance.py --json ladder_acceptance.json
```

- [ ] **Step 3: Adjudicate.** The harness does not decide "found" (Ruling 19). For each book, read its printed titles (and the full list in `ladder_acceptance.json`) and set `"found": true` only when a returned release is that exact book and volume in an ebook edition — not the manga or another volume; the `H`/`S` markers (predicate hit / suspect) are informational. Record per book: found, requests, elapsed, `INCOMPLETE` flags, and any predicate hit you judged wrong (a false positive of the stopping rule). Target: **≥ 18/19 found** (no book is a known expected miss; Overlord vol 5 now gets `"Overlord v05"` — Rulings 6 and 10), standalones with `fallbacks=0` and 1 request each (the harness exits 1 otherwise). Paste the summary into the PR description.
- [ ] **Step 4: UI check** after deploy (Task 8): DxD vol 5 and Shield Hero vol 8 return releases in the release modal.

### Task 8: Release — USER-GATED, text only

Do not run any of this without the user's explicit OK for each push.

1. Shelfmark release: `scripts/release-local.sh` (the canonical deploy; never rebuild a published tag).
2. fleet-infra: bump the Shelfmark image tag, push with the user's OK, then `flux reconcile` as that repo documents.
3. Then Task 7 Step 4 (UI check) against the deployed version.

---

## Self-review

- **Spec coverage:** §1 ladder → Task 1; §3 predicate (complete volume tokens, full-title tokens, manga/comic extension — Rulings 8, 10) → Task 2; §2 plan (order, ebook-only, manual untouched, `primary_query`/grouped unchanged, endpoint passes `content_type`) and incompleteness in `search_info` → Task 3; §4 Prowlarr (gating, fallback-only exclusion, error documents, rate-limit, cap with expansion, snapshot eligibility, deadline, partial results, incomplete) and §5 logging → Task 4; §4 Newznab (failure vs empty, error documents, per-connection ladder on retained rows, failure reporting, cap, deadline) and §5 → Task 5; Testing/Acceptance (adjudicated, under the endpoint deadline) → Tasks 6–7; Rollout → Task 8. Providers (OpenLibrary, Google Books, Moly, Audible) → Task 3 `TestFallbacksPerProvider`.
- **Request bound:** mandatory requests are unchanged (Task 4/5 tests `test_a_real_hit_from_the_mandatory_query_skips_every_fallback`, existing source tests); fallbacks ≤ 4 per indexer/connection (`TestRequestCap`, `TestCap`).
- **Unchanged sources:** IRC (`plan.primary_query`), AudiobookBay (`plan.title_variants[0]`, always mandatory) and direct download (`grouped_title_variants`) are not touched; Task 3 Step 5 runs their suites.
