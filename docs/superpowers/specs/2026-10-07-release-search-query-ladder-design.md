# Release Search Query Ladder

**Date:** 2026-10-07
**Status:** Draft for review
**Scope:** Shelfmark release search (items #1–#3 of the search-quality list; #7 query logging
folded in). Result ranking (#6), Torznab structured params (#4) and alternate/romanised titles
(#5) are out of scope.

## Problem

Release search sends one title-shaped query per language variant, built from the metadata
title (`book.search_title or book.title`, `shelfmark/core/search_plan.py`). For Hardcover the
"search title" is the subtitle when present, else the full title. Hardcover data is
inconsistent, so the query is often unusable:

- **DxD vol 5/6:** no subtitle, so the query is the full title "High School DxD (Light
  Novel), Vol. 5: Hellcat…". No release name contains "(Light Novel)", so nothing is found.
- **Overlord vol 2/5:** the query is the subtitle ("The Dark Warrior"), which no release uses.
- **Mushoku Tensei:** a junk subtitle ("Jobless Reincarnation (Light Novel)") reduces every
  volume's query to "Jobless Reincarnation", so the volume number is lost.

## Evidence (measurement, 2026-10-07)

19 books (16 series volumes across 6 light-novel series, 3 standalone novels), each searched
against the live Prowlarr with 4–7 query shapes. A hit is a non-video release whose title names
the right volume and series (or the title, for standalones).

| Query shape | Books found |
|---|---|
| current (today's query) | 12/19 |
| `<Series> vNN` | 15/16 series books |
| `<Series> Vol. N` | 14/16 series books |
| cleaned full title | 13/19 |
| book name after "Vol. N:" | 5/7 that have one |
| `<Series> Volume NN` | 7/16 series books |
| any shape, as a ladder | **19/19** |

Standalone novels were found by today's query; the ladder must not change them.

## Design

### 1. Query ladder (new pure module)

`shelfmark/core/search_queries.py` exposes
`build_query_ladder(title, subtitle, series_name, series_position) -> list[str]`, returning
deduplicated queries in this order:

1. `<Series> Vol. N`
2. `<Series> vNN` (zero-padded to 2 digits)
3. `<Name>`: the text after "Vol. N:" / "Volume N:" in the title, when present
4. `<Series> Volume NN`
5. the cleaned full title
6. today's query (`search_title or title`), cleaned, if not already present

Order follows the measurement: specific series+volume shapes first, so a generic book name
(e.g. "The Dark Warrior") is only tried after they fail.

- **Series and position:** taken from `series_name`/`series_position`. If either is missing,
  parse them from the title (`<Series>(, | )Vol(.|ume) N(:…)`). Only whole-number positions
  produce rungs 1, 2 and 4; a fractional position (1.5) skips them.
- **Cleaning (applies to every rung):** remove the medium labels `(Light Novel)`, `(Novel)`
  and `(LN)` anywhere (case-insensitive); replace `:` and `,` with spaces; collapse whitespace.
  `(Manga)` and other parentheses are kept: they narrow results.
- **Books with no series and no "Vol. N" in the title:** the ladder is just today's query,
  cleaned. Standalones are unchanged.

### 2. Search plan

`build_release_search_plan` builds the base-language variants from the ladder instead of a
single title.

- **Ladder variants:** the first rung is a normal variant. Later rungs are marked
  `fallback=True` (new field on `ReleaseSearchVariant`, default `False`).
- **Localized variants:** language variants from `titles_by_language` are unchanged and never
  fallbacks.
- **`primary_query`:** the first rung. IRC and AudiobookBay, which use only `primary_query`,
  therefore get the stronger first query with no other change.
- **Manual queries:** a manual query stays exactly as typed, with no ladder.

### 3. Sources run fallbacks only while nothing is found

Prowlarr and Newznab iterate variants as today, with one change. A `fallback` variant runs only
if every variant before it, auto-expand included, returned no results. They stop at the first
fallback that returns any result. Non-fallback variants still always run and merge, as today.

- The worst case is a genuinely missing book: every rung runs, about 5–6 queries instead of 1.
- The common case stops at rung 1 or 2.

Direct download (Anna's Archive etc.) uses `grouped_title_variants` and is unchanged in this
project.

### 4. Query logging (#7)

Prowlarr and Newznab log each query they send, with its result count and whether it was a
fallback or auto-expanded, at INFO. Today only "no results" is logged, so a successful search
leaves no trace.

## Error handling

- The ladder is pure and total: missing or junk fields drop rungs, never raise.
- An empty ladder (no title at all) falls back to ISBN queries, as today.
- Indexer failures behave as today: a failed search is not "no results" and does not trigger
  fallbacks, so rungs don't stack on a failing indexer.

## Testing

- **`tests/core/test_search_queries.py`:**
  - the 19 measured books as fixtures (real Hardcover title/subtitle/series/position), each
    asserting its exact ladder
  - medium-label cleaning; `(Manga)` and `(Unabridged)` kept
  - title parsing when series fields are missing; fractional positions
  - standalones unchanged
- **`tests/core/test_search_plan.py`:** ladder variants appear before localized ones; only
  rung 1 is non-fallback; `primary_query` is rung 1; manual query untouched.
- **Prowlarr and Newznab source tests:**
  - fallbacks skipped when an earlier variant has results; run in order when not
  - stop at the first fallback with results
  - an indexer failure doesn't trigger fallbacks
  - the INFO log lines
- **Acceptance:** re-run the measurement (`build_query_ladder` against the same 19 books and
  live Prowlarr). The ladder's first non-empty rung must find 19/19, with standalones unchanged.
  Then a manual check in the UI: DxD vol 5 and Shield Hero vol 8 return releases.

## Rollout

Shelfmark release (`scripts/release-local.sh`, next `1.3.15-fork.N`) and the fleet-infra image
bump, each push with the user's OK.

## Out of scope

- Result ranking (#6): wrong-volume, video and manga demotion.
- Torznab `title=`/`author=` parameters (#4).
- Romanised and alternate titles (#5).
- Direct-download sources.
