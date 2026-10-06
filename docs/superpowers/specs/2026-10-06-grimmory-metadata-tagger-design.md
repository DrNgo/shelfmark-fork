# Grimmory Metadata Tagger — Design (Project 1 of 3)

**Date:** 2026-10-06
**Status:** Approved in conversation; awaiting written-spec review

## Primary Goal & Intent

Give every book in Grimmory correct, verified metadata — the **ISBN above all**, plus
the **Hardcover ID** — through a repeatable, reviewable tool.

Two goals, both in scope:

1. **Library hygiene for its own sake.** The ISBN stored is the one belonging to the
   edition actually on disk (the ebook), useful to Grimmory, readers and any other tool.
2. **Reliable Shelfmark "In library" badges.** A Hardcover ID on each Grimmory book
   gives Shelfmark an edition-agnostic key to match on (consumed in Project 3).

Triggered by the Overlord light novels never badging as owned. Diagnosis on the live
index (2026-10-06):

| Grimmory index key | Hardcover search key |
|---|---|
| `overlord vol 1\|maruyama kugane` | `overlord vol 1 the undead king\|kugane maruyama` |
| `overlord volume 3\|ikirinec hotmailcom` | `overlord vol 3 the bloody valkyrie\|kugane maruyama` |

Three independent defects: surname-first author with no comma, a subtitle on one side
only, and junk metadata (an email address as author) on four volumes.

## Scope and Decomposition

This is Project 1 of three, each with its own spec → plan → implementation cycle:

1. **This spec — tagger core + `backfill` CLI.** No Shelfmark runtime changes beyond
   one synchronous notification helper. Fixes the 148 existing books.
2. **Shelfmark ingest (later spec).** Selectable Grimmory target library at download
   time (like the audiobook destination picker; **default Fiction `grimmory:3:3`**),
   upload direct to library instead of bookdrop, book identity (`provider`,
   `provider_id`, `isbn_13`, `asin`) carried into the custom-script payload, the
   tagger's `hook` entry point, and a fleet-infra dispatcher sharing `CUSTOM_SCRIPT`
   with the ABS enrich hook.
3. **Shelfmark matcher (later spec).** Hardcover-ID match key; reversed author order
   for comma-less two-token names; `Vol. N: subtitle` truncation gated on a volume
   marker; `volume`→`vol` normalization.

Project 1 must leave seams for Project 2: the identity/scoring/apply core is callable
for a single book given hints, and the queue file format is defined here.

## Current State (measured 2026-10-06)

- Grimmory v3.5.0 (`ghcr.io/grimmory-tools/grimmory:v3.5.0`), 148 books: Light Novels
  139, Fiction 8, Nonfiction 1; EPUB 133, MOBI 15; none metadata-locked.
- 82 carry a valid ISBN-13; 0 carry a Hardcover ID; 4 have an email address as author;
  one ISBN (`9781718310421`) is shared by two books.
- Grimmory's default fetch priority is `GoodReads → Google` for every field —
  Hardcover is absent, which is why no book has a Hardcover ID despite the API key
  being configured.
- Hardcover *search* results in Shelfmark carry no ISBN; only `get_book` does, and that
  is the default **physical** edition's. A stored ebook ISBN therefore cannot be the
  primary Shelfmark match key — the Hardcover ID is.

## Key Decisions

### Drive Grimmory's own providers, resolve the ISBN via Hardcover editions (B + C)

Candidates come from Grimmory's `prospective` endpoint (Hardcover, RanobeDB, Goodreads,
Google). The tagger does the scoring itself rather than trusting Grimmory's auto-match,
because this library is dominated by light-novel series where the manga adaptation,
box sets and the novel share a title and volume number.

The ISBN is then chosen from Hardcover's edition list for the identified book, picking
the ebook edition. Rejected: settings-only bulk auto-fetch (no review, no edition
control) and Hardcover-only direct querying (duplicates Grimmory's provider plumbing
and loses RanobeDB/Goodreads).

### Store the ebook ISBN; match on Hardcover ID

The ISBN in Grimmory describes the file held. Shelfmark's physical-edition ISBN would
miss it anyway, so cross-edition matching is Project 3's Hardcover-ID key, not an ISBN
compromise. **A physical ISBN is never stored as a fallback.**

### Fill missing, verify, then lock

Writes use `REPLACE_MISSING` (always passed explicitly — Grimmory's `PUT` defaults to
`REPLACE_ALL`). Fields the tool verified and wrote are then field-locked so a later
Grimmory bulk refresh cannot regress them. Overwriting an existing value happens only
for junk authors and for an approved print→ebook ISBN swap, and only for books cleared
in review.

### Dry run by default; apply replays a reviewed report

`--apply` executes the decisions recorded in the latest dry-run report rather than
re-scoring, so what was reviewed is what is written. A book whose Grimmory state
changed since the report is skipped as `stale` — a hand edit is never clobbered.

### Dedicated write account

The tool authenticates as a new Grimmory account (`shelfmark-tagger`, metadata-edit
permission only). The existing `shelfmark` account stays read-only.

### Notifications reuse Shelfmark's Apprise routes

Same channel and rule as the ABS enrich hook: alert only when a human must act;
successes stay silent; no new secret.

## Architecture

New package `shelfmark/tools/grimmory_tagger/`, run as
`python -m shelfmark.tools.grimmory_tagger <command>`. It ships in the Shelfmark image
but is never imported by the server process.

| Unit | Purpose | Depends on |
|---|---|---|
| `grimmory_api.py` | Login, list books (paged), get book, stream `prospective` candidates (SSE), update metadata, toggle field locks | `requests`, existing `shelfmark.grimmory.client` login |
| `hardcover_lookup.py` | ISBN → Hardcover book; Hardcover book → edition list (format, language, publisher, ISBNs) | existing `HardcoverProvider._execute_query` / `search_by_isbn` |
| `identity.py` | Pure: extract volume/series/format hints from a Grimmory book; score candidates; return `Decision` (outcome, chosen candidate, reasons, runner-up) | `shelfmark.library.matching` normalizers |
| `isbn_choice.py` | Pure: pick the ebook ISBN from an edition list + fallbacks; derive ISBN-10 | `shelfmark.library.matching.normalize_isbn` |
| `plan.py` | Pure: turn a `Decision` + current metadata into a field-level change set (fill-missing vs explicit replace, locks) | — |
| `report.py` | Write `report-<ts>.json` and `report-<ts>.html`; load latest report | — |
| `apply.py` | Replay a report: re-read each book, stale check, write, lock | `grimmory_api` |
| `notify.py` | Summary alert via a new synchronous helper in `shelfmark/core/notifications.py` | Shelfmark Apprise |
| `__main__.py` | CLI parsing, wiring, exit codes | all |

The pure units (`identity`, `isbn_choice`, `plan`) take plain data and do no I/O, so
they test exhaustively from fixtures and are reused unchanged by Project 2's hook.

## Grimmory API (v3.5.0, read from `app.jar` controllers)

- `POST /api/v1/books/{id}/metadata/prospective` — `text/event-stream`; body
  `{bookId, providers[], isbn, title, author, asin}`; needs edit-metadata or admin.
- `PUT /api/v1/books/{id}/metadata?replaceMode=REPLACE_MISSING&mergeCategories=false`
  — body `{metadata, clearFlags}`.
- `PUT /api/v1/books/metadata/toggle-field-locks` — `{bookIds, fieldActions}`.
- `GET /api/v1/books/{id}` — full metadata including provider IDs (the paged list
  omits them).
- Provider names: `Hardcover`, `Ranobedb`, `GoodReads`, `Google` (others unused).

The candidate payload shape (which IDs and ISBNs each provider returns, and how SSE
events are framed) is **not yet observed** — see Implementation step 1.

## Identity Resolution

Strongest evidence first:

1. **Existing valid ISBN.** `search_by_isbn` on Hardcover → book ID. Accept only if
   series and volume agree with Grimmory's; otherwise outcome `conflict`.
2. **Candidates.** Stream `prospective` candidates with title/author/ISBN hints.
   - **Hard gates** (any failure eliminates the candidate):
     - Volume number equal — from `seriesNumber`, else `Vol. N` / `Volume N` / `Book N`
       parsed from the title.
     - Series name equal after normalization (when both sides have one).
     - Format guard: in a library named `Light Novels`, reject candidates marked
       `(Manga)`, graphic/comic formats, box sets and omnibuses.
     - Language equal (when both sides have one).
   - **Soft signals:** author match in either token order (junk authors — containing
     `@`, empty, `Unknown` — are ignored, not counted against); title-key agreement
     via `shelfmark.library.matching`; publisher agreement.
   - **Accept** when exactly one Hardcover-backed candidate passes every gate and no
     soft signal disagrees. More than one survivor, or a survivor with a disagreeing
     soft signal → `review`. None → `no-match`.

## ISBN Selection

Given the identified Hardcover book:

1. Hardcover editions with an ebook reading format, in the book's language; prefer
   the publisher Grimmory already records, then most-held. Take its ISBN-13.
2. Else the RanobeDB candidate's ISBN, if it validates.
3. Else leave empty and report `isbn: none-found`.

Existing ISBN handling:

| Existing ISBN | Action |
|---|---|
| The chosen ebook ISBN | keep (`unchanged`) |
| Print edition of the same Hardcover book | propose swap → `review` |
| Belongs to a different Hardcover book, or duplicated across books | `conflict` |

ISBN-10 is set only when the ISBN-13 has the `978` prefix.

## Fields Written

`isbn13`, `isbn10`, `hardcoverId`, `seriesName`, `seriesNumber`; `authors` only when
the current value is junk. Author name order (`Maruyama Kugane`) is left alone —
Project 3 handles it in the matcher. All written fields are then locked.

## CLI

```
python -m shelfmark.tools.grimmory_tagger backfill
    [--library NAME ...] [--book ID ...]
    [--apply] [--include-review]
    [--report-dir /config/grimmory-tagger]
```

- Default: dry run — fetch, score, write a report, change nothing.
- `--apply`: replay the latest report's `accepted` decisions.
- `--include-review`: also apply `review` decisions; with `--book`, only those books.
- Always also consumes `/config/grimmory-tag-queue.jsonl` (written by Project 2's
  hook): queued books are scored in the same run. A dry run never edits the queue;
  `--apply` removes a line only once its book reaches `applied` or `unchanged`, so an
  unreviewed or failed book stays queued for the next run.

Credentials: `GRIMMORY_TAGGER_USERNAME`, `GRIMMORY_TAGGER_PASSWORD` (env). Grimmory
host from Shelfmark's `BOOKLORE_HOST`; Hardcover key from Shelfmark's config.

Exit codes: `0` run completed (per-book errors are in the report); non-zero only for
setup failures — bad credentials, missing permission, Grimmory unreachable.

### Queue file format (contract with Project 2)

One JSON object per line:
`{"ts", "book_id"|null, "filename", "library_id", "title", "author", "series_name",
"series_position", "language", "provider", "provider_id", "isbn_13", "asin", "reason"}`.

## Report

Per book: outcome (`accepted` / `review` / `conflict` / `no-match` / `unchanged` /
`error`; plus `stale` / `applied` / `write-failed` after apply), current → proposed
per field, chosen candidate and runner-up with reasons. Header: counts by outcome;
a dedicated "duplicates & conflicts" section. JSON is the machine record `--apply`
reads; HTML is for the human.

## Error Handling

- Per-book isolation: provider timeouts and 5xx mark that book `error`; the run
  continues.
- Throttle Grimmory candidate fetches to ~1/s; respect Hardcover rate limits; bounded
  retry with backoff on transient failures.
- Writes: a failed `PUT` marks the book `write-failed` and skips its lock.

## Notifications

- New public **synchronous** helper in `shelfmark/core/notifications.py` (the existing
  `notify_admin` dispatches on an executor thread, which a short-lived CLI can exit
  before). It reuses the existing route resolution and Apprise dispatch.
- Routes: admin routes subscribed to `download_failed`; fall back to all admin routes
  when none match. No new `NotificationEvent`.
- `backfill` dry run: one summary only when `review + conflict + no-match + error > 0`.
- `backfill --apply`: one result summary when anything was `stale` or `write-failed`,
  or review items remain.
- Never raises into the CLI; never logs route URLs (they carry the Pushover token).

## Testing

- **Unit (pytest):** `identity` against fixtures from real Overlord data (light novel,
  manga, box set, email author, `Volume` vs `Vol.`); `isbn_choice` including the
  978/979 ISBN-10 rule and the never-physical rule; `plan` fill-missing vs explicit
  replace; report → apply replay including the stale skip.
- **API layer:** recorded responses replayed through `requests` mocks, including SSE
  framing.
- **Notification helper:** route selection and fallback; no URL in logs.

## Implementation Order (for the plan)

1. **Live read-only probe** with the new `shelfmark-tagger` account against a handful
   of books (Overlord vol 1 and 3, a Fiction title): capture real `prospective` SSE
   output and Hardcover edition data; confirm whether Hardcover candidates carry
   `hardcoverId`/ISBN and whether RanobeDB returns ISBNs. Save as test fixtures.
2. Pure units from those fixtures, test-first.
3. API layer, report, apply, notifications, CLI.
4. **Acceptance:** dry run over all 148 books, reviewed by the user; `--apply` on
   Overlord first; Shelfmark index sync shows nine Overlord rows with ISBNs and
   Hardcover IDs; then the rest.

## Operational Follow-ups (not code)

- Create the `shelfmark-tagger` Grimmory account (edit-metadata only); store its
  credentials as a SealedSecret in fleet-infra, exposed to the Shelfmark pod.
- Put `Hardcover` first in Grimmory's default fetch priority, so manual and future
  bulk fetches populate Hardcover IDs too.
- Fix the four email-author books' authorship as part of the first reviewed apply.
