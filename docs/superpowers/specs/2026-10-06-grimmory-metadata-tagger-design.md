# Grimmory Metadata Tagger — Design (Project 1 of 3)

**Date:** 2026-10-06
**Status:** Approved in conversation; revised after Codex adversarial review; awaiting
written-spec review

## Primary Goal & Intent

Give every book in Grimmory correct, verified metadata — the **ISBN above all**, plus
the **Hardcover ID** — through a repeatable, reviewable tool.

Two goals, both in scope:

1. **Library hygiene for its own sake.** The ISBN stored is the ebook edition held,
   useful to Grimmory, readers and any other tool.
2. **Reliable Shelfmark "In library" badges.** A Hardcover ID on each Grimmory book
   gives Shelfmark an edition-agnostic key to match on (consumed in Project 3).

The governing rule throughout: **a wrong value is worse than an empty one.** Anything
the tool cannot positively establish is reported, not written.

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
   tagger's `hook` entry point (including finding the uploaded book), and a
   fleet-infra dispatcher sharing `CUSTOM_SCRIPT` with the ABS enrich hook.
3. **Shelfmark matcher (later spec).** Hardcover ID stored in the library index and
   used as a match key; reversed author order for comma-less two-token names;
   `Vol. N: subtitle` truncation gated on a volume marker; `volume`→`vol`.

Project 1 leaves seams for Project 2: the identity/scoring/planning core is callable
for a single book given hints, and the queue file schema is defined here.

## Current State (measured 2026-10-06)

- Grimmory v3.5.0 (`ghcr.io/grimmory-tools/grimmory:v3.5.0`), 148 books: Light Novels
  139, Fiction 8, Nonfiction 1; EPUB 133, MOBI 15; none metadata-locked.
- 82 carry a valid ISBN-13; 0 carry a Hardcover ID; 4 have an email address as author;
  one ISBN (`9781718310421`) is shared by two books.
- Grimmory's default fetch priority is `GoodReads → Google` for every field —
  Hardcover is absent, which is why no book has a Hardcover ID despite the API key
  being configured.
- Shelfmark's Hardcover **text-search** results carry no ISBN. `get_book` carries the
  default **physical** edition's ISBN; `search_by_isbn` echoes the matched edition's.
  A stored ebook ISBN therefore cannot be the primary Shelfmark match key — the
  Hardcover ID is.

## Key Decisions

### Drive Grimmory's own providers, resolve the ISBN via Hardcover editions (B + C)

Candidates come from Grimmory's `prospective` endpoint (Hardcover, RanobeDB, Goodreads,
Google). The tagger does the scoring itself rather than trusting Grimmory's auto-match,
because this library is dominated by light-novel series where the manga adaptation,
box sets and the novel share a title and volume number.

The ISBN is then chosen from Hardcover's edition list for the identified work.
Rejected: settings-only bulk auto-fetch (no review, no edition control) and
Hardcover-only direct querying (duplicates Grimmory's provider plumbing and loses
RanobeDB/Goodreads).

### Store the ebook ISBN; match on Hardcover ID

The ISBN in Grimmory describes the file held. Cross-edition matching is Project 3's
Hardcover-ID key, not an ISBN compromise. **A physical ISBN is never written.**

### Preserve verified data; prove before replacing

An existing ISBN that verifies as an ebook edition of the identified work is kept,
even if Hardcover would rank another ebook edition higher — the tool cannot tell
which digital release is on disk better than the file's own metadata did. Writes
fill empty fields; replacing a populated value requires an explicit, recorded human
approval and a verified field-specific write protocol (see *Writes*).

### Dry run by default; apply replays an explicitly named, reviewed report

`--apply` executes decisions recorded in a named report rather than re-scoring, so
what was reviewed is what is written.

### Reuse Shelfmark's Grimmory account

The tool authenticates with the credentials Shelfmark already holds
(`BOOKLORE_USERNAME` / `BOOKLORE_PASSWORD`, the `shelfmark` account, id 2). On
2026-10-06 that account was granted `canEditMetadata` + `canBulkLockUnlockMetadata`
on top of `canUpload` + `canManageLibrary`; it is assigned all three libraries.

Rejected: a dedicated `shelfmark-tagger` account. It would have kept the password
Shelfmark stores at rest (`/config/plugins/grimmory.json`) unable to rewrite metadata,
but the user preferred one credential over a second secret to manage. Accepted
consequence: that stored password can now rewrite and lock metadata on every book.
Each run checks coverage (see *Inventory*).

### Notifications reuse Shelfmark's Apprise routes

Same channel and rule as the ABS enrich hook: alert only when a human must act;
successes stay silent; no new secret.

## Architecture

New package `shelfmark/tools/grimmory_tagger/`, run as
`python -m shelfmark.tools.grimmory_tagger <command>`. It ships in the Shelfmark image
but is never imported by the server process.

| Unit | Purpose | Depends on |
|---|---|---|
| `grimmory_api.py` | Login, list libraries/books, get book, stream `prospective` candidates (SSE), update metadata, toggle field locks. Typed errors, never `None`-for-failure | `requests`, `shelfmark.grimmory.client` login |
| `hardcover_api.py` | Tagger-owned GraphQL client: ISBN → editions (all hits), work → full edition list, work → series/authors. Typed errors | Hardcover token from Shelfmark config |
| `evidence.py` | Pure: extract hints from a Grimmory book or candidate — volume, series, adaptation type from **raw** title markers, language, people | — |
| `identity.py` | Pure: compare evidence tri-state, score candidates, return a `Decision` | `evidence` |
| `isbn_choice.py` | Pure: classify the existing ISBN pair and pick an ebook ISBN | ISBN validation |
| `plan.py` | Pure: turn a `Decision` + current metadata into per-field actions | — |
| `report.py` | Write/read `report-<id>.json` and `.html` | — |
| `apply.py` | Replay a report: preconditions, write, verify, lock, verify | `grimmory_api` |
| `queue.py` | Read/rewrite the hook queue under a file lock | — |
| `notify.py` | Summary alert via a new synchronous helper in `shelfmark/core/notifications.py` | Shelfmark Apprise |
| `__main__.py` | CLI parsing, wiring, exit codes | all |

The pure units take plain data and do no I/O, so they test exhaustively from fixtures
and are reused unchanged by Project 2's hook.

**Not reused from Shelfmark, deliberately:**

- `HardcoverProvider._execute_query` / `search_by_isbn` — they return `None` for
  timeouts, HTTP errors and GraphQL errors alike, so a provider failure would read as
  "no such ISBN"; `search_by_isbn` also takes one edition and no series. The tagger's
  own client distinguishes *absent* from *failed*.
- `shelfmark.library.matching` title/author normalizers — they keep subtitles,
  distinguish `volume` from `vol`, and delete bracketed markers such as `(Manga)`
  before anything can read them; reusing them would send every Overlord volume to
  review and erase the adaptation evidence. The tagger defines its own comparison.
  ISBN check-digit validation is reused, with an added Bookland (978/979) prefix check.

## Grimmory API (v3.5.0, read from `app.jar` controllers)

- `GET /api/v1/libraries`; paged `GET /api/v1/books/page`; `GET /api/v1/books/{id}`
  (full metadata including provider IDs and per-field locks; the paged list omits IDs).
- `POST /api/v1/books/{id}/metadata/prospective` — `text/event-stream`; body
  `{bookId, providers[], isbn, title, author, asin}`; needs edit-metadata or admin.
- `PUT /api/v1/books/{id}/metadata?replaceMode=…&mergeCategories=false` — body
  `{metadata, clearFlags}`; `replaceMode` **defaults to `REPLACE_ALL`**.
- `PUT /api/v1/books/metadata/toggle-field-locks` — `{bookIds, fieldActions}`.

**Unverified, resolved by the Implementation step 1 probe:** the SSE framing and
completion/error events; which IDs, ISBNs and format/edition markers each provider's
candidates carry; whether a `PUT` touches fields absent from the body under each
`replaceMode`; how `clearFlags` and existing field locks interact with writes; and
whether any conditional-write mechanism exists. Until each is verified, the design
takes the conservative branch noted where it applies.

## Inventory

- The report header records the libraries and book count the account can see (three
  libraries, 148 books at time of writing). If a run sees fewer libraries than the
  previous report, it stops as a setup failure (an account lost a library
  assignment); fewer books is reported, not fatal (books can be deleted).
- Duplicate-ISBN and duplicate-Hardcover-ID detection always runs over the **full
  accessible inventory**, before `--library`/`--book` filters narrow what is scored.
  Two books sharing an identity are a `conflict` unless both are distinct files of the
  same edition (e.g. EPUB and MOBI of one release), which is reported as a duplicate
  holding, not an error.

## Evidence Model

Every comparison is **tri-state: agree / disagree / unknown.** Normalization rules:

- **Volume:** `seriesNumber`, else parsed from the raw title: `Vol. N`, `Vol.N`,
  `Volume N`, `Book N`, `#N`; decimals (`5.5`) and Roman numerals (`II`) supported.
  Title and `seriesNumber` disagreeing on one side is itself a `review` reason.
- **Series:** casefolded, punctuation-stripped, leading article dropped.
- **Title:** compared on the portion before a `:` subtitle; `volume` ≡ `vol`; bracketed
  markers removed for comparison only after being read as evidence.
- **Adaptation type:** read from raw markers *before* normalization — `(Manga)`,
  `(Light Novel)`, `Box Set`, `Omnibus`, `Collection`, graphic/comic formats — and from
  provider format fields where present. Values: `novel`, `manga`, `box-set`, `unknown`.
- **Language:** mapped to ISO 639-1 from names and 2/3-letter codes (`English`, `en`,
  `eng` → `en`).
- **People:** each author compared individually; a two-token name also compared
  reversed. Junk entries (containing `@`, empty, `Unknown`, a bare URL) are dropped
  from comparison but not from the record.

## Identity Resolution

Strongest evidence first. Both paths end at the same **common gates**.

1. **Existing valid ISBN.** Query Hardcover editions by ISBN (all hits). More than
   one distinct work → `conflict`. One work → hydrate it (series, position, authors,
   language) and run the common gates against the Grimmory book. Pass → identified.
   Fail → `conflict` (the ISBN points at something else). Provider failure → `error`,
   never "not found".
2. **Candidates.** Stream `prospective` candidates; deduplicate by Hardcover work ID.
   A stream that ends without its completion event, or with a provider-error event,
   marks the book `error` rather than scoring a partial set.

**Common gates** — any `disagree` eliminates a candidate:

- Volume, series, language.
- Adaptation type, interpreted by library: in `Light Novels`, the candidate must be
  `novel`; `manga` or `box-set` eliminates, and `unknown` cannot be accepted
  automatically (→ `review`).

**Minimum positive evidence to accept** — beyond passing every gate, a candidate must
have *agree* on:

- Series-bearing books: series **and** volume **and** at least one of (author, title).
- Standalone books (no series on either side): title **and** at least one
  non-junk author.

A book whose only authors are junk can still be accepted on series + volume + title.

**Outcome:** exactly one Hardcover-backed candidate meeting gates and minimum
evidence, with no soft disagreement (publisher, secondary author) → `accepted`.
Multiple such candidates, unknown adaptation type, or a soft disagreement → `review`
with every surviving candidate listed and numbered. None → `no-match`.

## ISBN Selection

Hardcover edition query for the identified work returns, per edition: edition ID,
reading format, ISBN-10/13, publisher, language, release date, users count. Paginated
until exhausted; ties broken deterministically (users count, then release date, then
edition ID).

Classify the existing ISBN pair first:

| Existing state | Action |
|---|---|
| Valid, an ebook edition of the identified work | **keep** — even if another ebook edition ranks higher |
| Valid, a physical edition of the identified work | propose swap → `review` |
| Valid, an edition of a different work, or held by another book | `conflict` |
| Valid but unknown to Hardcover | keep; report `isbn: unverified` |
| Populated but invalid (bad check digit, non-978/979 ISBN-13) | `review` (proposed clear-and-fill) |
| ISBN-10 and ISBN-13 disagree with each other | `review` |
| Empty | choose (below) |

Choosing for an empty field:

1. Ebook editions in the book's language. If Grimmory records a publisher, keep only
   that publisher's editions; if that leaves exactly one ISBN → use it.
2. If several ebook ISBNs remain and no evidence distinguishes them → `review` with
   the options listed; nothing is written.
3. RanobeDB candidate ISBN only if the candidate itself marks the release digital
   **and** it passed the common gates for this book. If the probe shows RanobeDB does
   not expose format, this fallback is dropped.
4. Otherwise leave empty; report `isbn: none-found` (an unresolved goal, see
   *Notifications*).

ISBN-10 is written only alongside a `978` ISBN-13 and only when the ISBN-10 field is
empty. Replacing an ISBN-13 with a `979` one also requires clearing a stale ISBN-10,
which is part of the same approved replacement.

## Fields and Per-Field Status

Every planned field carries its own status: `keep`, `fill`, `replace` (approval
required), `lock-only`, `skip`, with a reason. A book's overall outcome is derived
after all fields — including the Hardcover ID — are evaluated; `unchanged` means
**every** field is `keep` and already locked.

- `hardcoverId`: `fill` when identified and empty.
- `isbn13`, `isbn10`: per *ISBN Selection*.
- `seriesName`, `seriesNumber`: `fill` when empty.
- `authors`: only junk entries are replaced. Valid co-authors are kept in place;
  replacement names come from the identified Hardcover work's contributors with the
  `Author` role only (never illustrators/translators), with provenance recorded.
  Name order (`Maruyama Kugane`) is left alone — Project 3 handles it in the matcher.

## Review Decisions

Review rows are not applied by a flag alone. The human records decisions in a small
decisions file next to the report:

```json
{"report_id": "…", "decisions": {"<book_id>": {"candidate": 2, "fields": ["isbn13", "hardcoverId"]}}}
```

- Each decision names one of the row's numbered candidates and the fields approved.
- Rows in the report with no decision are never written.
- A `replace` field is applied only if it appears in that book's approved `fields`.

## CLI

```
python -m shelfmark.tools.grimmory_tagger backfill
    [--library NAME ...] [--book ID ...]
    [--report-dir /config/grimmory-tagger]                   # dry run: score, write report

python -m shelfmark.tools.grimmory_tagger apply --report REPORT_ID
    [--decisions FILE]                                       # replay; never re-scores
```

- `backfill` never writes to Grimmory. It prints the report ID and paths.
- `apply` requires an explicit report ID. It writes `accepted` rows, plus `review`
  rows that have a recorded decision. It never scores and never reads new queue entries.
- Credentials: Shelfmark's own config — `BOOKLORE_HOST`, `BOOKLORE_USERNAME`,
  `BOOKLORE_PASSWORD` for Grimmory, and the Hardcover token. No new secret.
- Exit codes: `0` completed (per-book problems live in the report); non-zero only for
  setup failures — bad credentials, missing permission, Grimmory unreachable,
  coverage regression.

## Writes, Verification and Recovery

**Preconditions.** The report stores, per book, a snapshot of every planned field's
value and lock state. Before writing, `apply` re-reads the book; if any snapshotted
value or lock differs, the book is `stale` and skipped.

**Write protocol.**

- `fill` fields: one `PUT` with `replaceMode=REPLACE_MISSING` and only those fields.
- `replace` fields: only via a field-specific protocol the probe has verified leaves
  every other field untouched (including under existing locks and `clearFlags`).
  Until verified, `replace` actions are reported as manual steps and not executed.

**Verification.** After the `PUT`, re-read and confirm each written field holds the
planned value. Then lock exactly those fields, then re-read and confirm the locks.
Metadata and lock results are recorded separately (`written`, `write-failed`,
`locked`, `lock-failed`).

**Recovery.** Locking is idempotent. On a later `apply` of the same report, a book
whose fields already hold the planned values but are unlocked is treated as
`lock-only`, not `stale`. A lost `PUT` response is resolved the same way: re-read
decides whether the write landed.

**Concurrency limitation (accepted).** Grimmory exposes no conditional write
(pending probe confirmation), so a hand edit landing between the precondition read
and the `PUT` can be lost for fill fields. This is a single-user library and the
window is one request; the post-write verification reports any mismatch.

## Hook Queue (contract with Project 2)

`/config/grimmory-tag-queue.jsonl`, one JSON object per line, versioned:

```json
{"v": 1, "entry_id": "<uuid>", "ts": "…", "task_id": "…",
 "book_id": null, "library_id": 3, "path_id": 3, "filename": "…", "size": 123,
 "title": "…", "author": "…", "series_name": "…", "series_position": 1.0,
 "language": "en", "provider": "hardcover", "provider_id": "…",
 "isbn_13": null, "asin": null, "reason": "…"}
```

- One entry per uploaded file. A payload `provider_id` from Hardcover is the
  strongest hint, then `isbn_13`, then title/author/series.
- `backfill` resolves `book_id: null` entries by library + filename + size; zero or
  multiple matches → `unresolved` / `ambiguous` in the report (entry kept).
- `backfill` scores queue entries into the report keyed by `entry_id`; it never
  edits the queue. `apply` removes an entry only when its report row reached
  `written`+`locked` or `unchanged`. Removal rewrites the file under an exclusive
  `fcntl` lock that the hook also takes when appending; an entry appended after the
  report was made is untouched because removal is by `entry_id`.

## Error Handling

- Per-book isolation: one book's failure marks it `error` and the run continues.
- Grimmory candidate fetches throttled to ~1/s. Hardcover: honor rate limits; on
  `429` back off and retry (bounded); on `401` abort the run as a setup failure.
- GraphQL responses with HTTP 200 and an `errors` array are failures, not empty results.

## Notifications

- New public **synchronous** helper in `shelfmark/core/notifications.py` (the existing
  `notify_admin` dispatches on an executor thread, which a short-lived CLI can exit
  before). It reuses the existing route resolution and Apprise dispatch.
- Routes: admin routes subscribed to `download_failed`; fall back to all admin routes
  when none match. No new `NotificationEvent`.
- An **unresolved goal** is any book with outcome `review`, `conflict`, `no-match`,
  `error`, `unresolved`, `ambiguous`, or with `isbn: none-found` / `isbn: unverified`.
- `backfill`: one summary only when there is at least one unresolved goal.
- `apply`: one summary whenever anything is `stale`, `write-failed`, `lock-failed`,
  a manual `replace` step, or unresolved goals remain in the report.
- Never raises into the CLI. Route URLs carry the Pushover token: the helper must not
  emit them through its own logs, exception messages, or captured Apprise records —
  verified by tests that inject secret-bearing URLs and failing plugins.

## Testing

- **Evidence/identity (pytest):** fixtures from real probe output — Overlord light
  novel vs manga vs box set; email author; `Volume` vs `Vol.`; subtitle on one side;
  standalone Fiction title; missing volume on one/both sides; decimal and Roman
  volumes; language name vs code; multi-author with illustrator/translator; mixed
  valid/junk author list.
- **ISBN choice:** every row of the classification table; 978 vs 979 and ISBN-10
  handling; never-physical; several indistinguishable ebook ISBNs → review; RanobeDB
  print-only ISBN rejected; invalid prefix rejected.
- **Plan:** per-field statuses; book-level `unchanged` only when all fields keep+locked;
  Hardcover-ID fill on a book whose ISBN is already correct.
- **Apply:** stale snapshot; lost `PUT` response; write ok + lock failure, then re-run
  → `lock-only`; post-write verification mismatch; review row without decision not
  written; decision limited to approved fields.
- **Queue:** removal by `entry_id` with a concurrent append; repeated entries for one
  book; unresolved/ambiguous filename matches.
- **API layer:** recorded responses via `requests` mocks — SSE truncated stream and
  provider-error event; GraphQL 200-with-errors; 429 then success; 401.
- **Notifications:** route selection and fallback; no secret in logs or exceptions.

## Implementation Order (for the plan)

1. **Live probe** with the `shelfmark` account. Read-only against Overlord
   vol 1 and 3 and a Fiction title: capture `prospective` SSE output and Hardcover
   edition data; record per-provider fields. Then **one write experiment on a
   throwaway test book** to determine `PUT` semantics for absent fields, `clearFlags`,
   locks and any conditional write. Save everything as fixtures; update this spec's
   "unverified" items.
2. Pure units from those fixtures, test-first.
3. API layer, report, apply, queue, notifications, CLI.
4. **Acceptance:** dry run over all 148 books, reviewed by the user; `apply` on the
   Overlord rows first; verify through `GET /api/v1/books/{id}` that the nine volumes
   carry Hardcover IDs and verified ISBNs; run a Shelfmark index sync and confirm the
   index's ISBN count rose; then the rest. (Hardcover-ID matching in the index is
   Project 3.)

## Operational Follow-ups (not code)

- ~~Grant the account metadata permissions~~ — done 2026-10-06 (`shelfmark` now
  holds `canEditMetadata` + `canBulkLockUnlockMetadata`; recorded in fleet-infra
  `clusters/my-cluster/media/CLAUDE.md`).
- Put `Hardcover` first in Grimmory's default fetch priority, so manual and future
  bulk fetches populate Hardcover IDs too.
- Create a throwaway test book for the step-1 write experiment.
