# Release Search Query Ladder

**Date:** 2026-10-07
**Status:** Draft, revised after Codex review (see end)
**Scope:** Shelfmark release search, items #1–#3 of the search-quality list, plus per-request
query logging (#7).
- **Out of scope:**
  - result ranking (#6)
  - Torznab `title=`/`author=` parameters (#4)
  - alternate or romanised titles (#5)
- **Fallbacks apply to ebook searches through Prowlarr and Newznab only.**

## Problem

Release search sends one title-shaped query per language variant. The base title is
`book.search_title or book.title` (`shelfmark/core/search_plan.py`). For Hardcover,
`search_title` comes from `_compute_search_title` (`hardcover.py:958`), a set of provider-specific
heuristics:
- prefers a non-positional subtitle as the book name
- strips series prefixes and trailing parentheses
- otherwise returns nothing, so the full title is used

Hardcover data is inconsistent, so the resulting query often finds nothing:

- **DxD vol 5/6:** no subtitle, so the query is the full title "High School DxD (Light Novel),
  Vol. 5: Hellcat…". No release name contains "(Light Novel)".
- **Overlord vol 2/5:** the query is the subtitle ("The Dark Warrior"), which no release uses.
- **Mushoku Tensei:** a junk subtitle reduces every volume's query to "Jobless
  Reincarnation", so the volume number is lost.

## Evidence (measurement, 2026-10-07)

19 books (16 series volumes across 6 light-novel series, plus 3 standalone novels) were each
searched against the live Prowlarr with 4–7 query shapes. A hit is a non-video release whose
title names the right volume and series (or the title, for standalones).

| Query shape | Books found |
|---|---|
| current (today's query) | 12/19 |
| `<Series> vNN` | 15/16 series books |
| `<Series> Vol. N` | 14/16 series books |
| cleaned full title | 13/19 |
| book name after "Vol. N:" | 5/7 that have one |
| `<Series> Volume NN` | 7/16 series books |
| any shape | **19/19** |

This measures the union of the shapes; production acceptance is defined below. Standalone novels
were found by today's query.

## Design

### 1. Query ladder (new pure module `shelfmark/core/search_queries.py`)

`build_fallback_queries(*, title, current_query, series_name, series_position) -> list[str]`

- `current_query` is today's base query, `book.search_title or book.title`, passed in as is.
- It returns **fallback** queries only, deduplicated against `current_query` and each other,
  in this order:
  1. `<Series> Vol. N`
  2. `<Series> vNN` (zero-padded to 2 digits)
  3. `<Name>`: the text after `Vol. N:` / `Volume N:` in the title
  4. `<Series> Volume NN`
  5. the cleaned full title

Rules:
- **Cleaning** applies to every fallback query. Remove `(Light Novel)`, `(Novel)`, `(LN)`
  anywhere (case-insensitive), replace `:` and `,` with spaces, and collapse whitespace. Other
  parentheses, such as `(Manga)`, are kept.
- **Series name:** `series_name`, cleaned, if non-blank after cleaning. Otherwise the series is
  parsed from a title of the shape `<Series>(,| ) Vol(.|ume) N[: <Name>]`. A parse must consume
  the whole position token.
- **Position:** use a finite, non-negative, integral number, including `0`. Integral floats and
  numeric strings are normalized without truncation. Reject booleans, negatives, NaN and
  infinity.
  - If `series_position` and the title's parsed volume disagree, no series rungs (1, 2, 4) are
    produced.
  - When only one of name and position is present, the missing one comes from the title parse,
    and only a complete, consistent pair produces series rungs.
- **Distinguishing identities:** fractional positions (1.5), ranges (`Vol. 1–3`), omnibus or
  collected editions, and `Part I`/`Part II` attached to the volume token (`Vol. 5 Part 1`) or
  in a title with no volume number produce no single-volume series rungs. A `Part` in the book
  name after a single integral volume (`Overlord, Vol. 5: The Men of the Kingdom Part I`) does
  not suppress them, since the volume number already identifies the book. Their cleaned full title (rung 5) keeps the distinguishing text. Roman-numeral volumes
  are not parsed, and produce only rung 5.
- **Standalone books** (no usable series and no volume in the title) get no fallbacks.

### 2. Search plan

`build_release_search_plan` gains `content_type`, passed by the release endpoint.

- **Order:** variants are the mandatory base variant (today's query), then localized variants,
  as today, then the fallback queries. Fallbacks are marked `fallback=True`, a new field on
  `ReleaseSearchVariant` defaulting to `False`. Duplicate titles keep their mandatory status.
- **Who gets fallbacks:** only when `content_type` is ebook. Audiobook searches get none.
- **Unchanged:** `primary_query` (the first variant's title-plus-author), IRC, AudiobookBay
  and direct download (`grouped_title_variants`) behave exactly as today. Fallbacks are only
  consumed by Prowlarr and Newznab.
- **Manual queries:** an explicit manual query keeps today's trimming and 256-character
  limit, and gets no fallbacks. Manual-provider searches without an explicit query follow
  today's metadata path, also without fallbacks.

### 3. Stopping on real hits only (identity predicate)

`is_identity_hit(release_title, *, series_key, position, title_tokens, content_type) -> bool`
lives in the same module.

**For a series volume, it is true when the release title:**
- names the volume, as `Vol. N`, `Volume N`, `vN`, `[N]` or `- N`, zero-padded or not
- names no other volume number
- contains the series key tokens
- is not video (1080p, 720p, x264, BD, mkv, episode markers and the like)
- is not a manga or comic edition (`manga`, `comic(s)`, `graphic novel`) unless the book's
  own title or series names it as manga or comic

**For other books,** it is true when the release title contains the book's significant title
tokens and is not video.

It decides **only** whether fallbacks may stop; it never filters or reorders results, so ranking
stays out of scope.

### 4. Sources run fallbacks only while no real hit exists

**Prowlarr** loops variants, then indexers.
- **Mandatory variants:** run exactly as today, including auto-expand.
- **Fallback variants:** run in order, but only while no result so far (from any variant)
  passes `is_identity_hit`. They stop at the first that yields one.
- **Failed indexers:** an indexer that failed during this search, by error or timeout, gets no
  further fallback or expansion requests. Healthy indexers that answered empty stay eligible.
  Failure accounting and the existing unavailable-error behaviour stay as they are.
- **Request cap:** fallbacks are capped at **4 requests per indexer per search, auto-expanded
  calls included**. A rate-limit response counts as a failure for that indexer.

**Newznab** loops connections, then queries.
- **Per-connection ladder:** ladder state is kept per connection. A hit on connection A does not
  suppress fallbacks on connection B. Deduplication stays global.
- **What counts:** only results kept after `plan.indexers` filtering count as hits.
- **Failures:** `NewznabClient.search` must report failure separately from an empty success,
  through an exception or an explicit outcome; today a failure returns `[]`. A failed connection
  gets no further fallback or expansion requests during this search, and the same 4-request
  fallback cap applies per connection.

**Deadlines:**
- Fallbacks share the existing source and endpoint deadlines; nothing resets per rung.
- Each fallback request is skipped if the remaining source budget is below that request's
  timeout.
- Accumulated results are always kept.
- A search cut short by the deadline is reported as incomplete, never as a completed
  "no releases".

**Request bound:** with `I` indexers or connections and `L` localized variants, mandatory
requests stay as today (at most `2 × I × (1 + L)`). Fallbacks add at most `4 × I`.

### 5. Query logging (#7)

Prowlarr and Newznab log each request at INFO, recording:
- the query
- the indexer or connection
- the category scope
- mandatory or fallback rung number
- whether it was expanded
- the outcome (ok, empty, failed or rate-limited) and the result count

Each search also logs a final line: whether fallbacks ran and why they stopped (hit, cap,
deadline, exhausted). The existing aggregate summaries stay.

## Error handling

- `build_fallback_queries` and `is_identity_hit` are pure and total. Junk input drops rungs or
  returns `False`, and never raises.
- An empty title leads to ISBN queries, as today.
- Failures and deadlines follow §4.

## Testing

- **`tests/core/test_search_queries.py`:**
  - the 19 measured books as fixtures, modelled on the release endpoint's actual input: the
    `get_book` output with series fields, with the endpoint's title override applied
    (`main.py:3226-3236`); each asserts its exact fallback list
  - cleaning
  - numeric rules: 0, 1.5, `"3"`, `3.0`, booleans, negatives, NaN
  - conflicting positions, ranges, `Part I/II`, Roman numerals
  - standalones with no fallbacks
- **Identity predicate:** right volume, wrong volume (`Vol. 15` vs 5), video, another series,
  standalones.
- **Providers:** OpenLibrary and Google Books (no series fields), Moly (display-only series),
  Audible (audiobook, so no fallbacks).
- **`test_search_plan.py`:**
  - order: mandatory, then localized, then fallbacks
  - ebook only
  - manual query untouched
  - `primary_query` and `grouped_title_variants` unchanged
- **Prowlarr source:**
  - fallbacks are skipped when a mandatory result is a real hit
  - a wrong-volume or video result does not stop fallbacks
  - the first real hit stops them
  - localized variants still run
  - failed or rate-limited indexers are excluded from fallbacks while healthy ones continue
  - the cap counts expanded calls
  - a deadline keeps results and reports an incomplete search
  - logs as specified
- **Newznab source:**
  - per-connection ladders
  - failure distinguished from empty
  - only `plan.indexers`-filtered hits count
  - deadline behaviour
- **Acceptance:**
  - run the production plan and source path for the 19 books against live Prowlarr (through the
    release endpoint, or the same functions in-process with a port-forward)
  - require correct-book hits under real stopping, category and expansion behaviour
  - record requests per search, elapsed time and false positives; target **≥ 18/19** with
    standalones unchanged
  - then check in the UI that DxD vol 5 and Shield Hero vol 8 return releases

## Rollout

Shelfmark release (`scripts/release-local.sh`) and the fleet-infra image bump, each push with
the user's OK.

## Revisions after Codex review (2026-10-07)

All 14 findings were adopted. The main changes:

| # | Change |
|---|---|
| 1 | Stopping requires an identity-qualified hit, not any result |
| 2 | Newznab distinguishes failure from empty success |
| 3 | Fallbacks are ebook-only and Prowlarr/Newznab-only; today's query stays first, so `primary_query`, IRC, ABB and audiobooks are unchanged |
| 4 | Fixtures model `get_book` output plus the endpoint's title override |
| 5 | The builder takes `current_query` explicitly |
| 6 | Series metadata is a candidate: conflicts produce no series rungs |
| 7 | Numeric and multi-part rules are specified |
| 8 | Failed and rate-limited indexers are excluded per search |
| 9 | Newznab ladders run per connection, and only filtered results count |
| 10 | Mandatory and localized variants run before fallbacks; a fallback hit stops only the fallbacks |
| 11 | Request bound and a per-indexer cap that counts expansion |
| 12 | Deadline semantics |
| 13 | Acceptance runs the production path |
| 14 | Manual and logging claims corrected |
| 15 | (plan review) `Part` in the book name no longer suppresses series rungs; manga/comic releases are not identity hits for a light novel — live data showed `Overlord Vol. 5` returning only manga |
