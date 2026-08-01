# Shelfmark fork — issues and feature proposals

## Overview

**Purpose**: capture three changes worth making in a fork of
[calibrain/shelfmark](https://github.com/calibrain/shelfmark), with the evidence behind each
so the work can start without re-deriving it.
**Audience**: whoever picks up the fork (human or agent).
**Prerequisites**: familiarity with the codebase. Every claim below was measured against a
live deployment (Audiobookshelf + Prowlarr + qBittorrent/SABnzbd on k3s), not inferred from
reading alone.

**You are reading this inside the fork.** `origin` is `DrNgo/shelfmark-fork`; `upstream` is
`calibrain/shelfmark`, so `git fetch upstream` still pulls any future upstream commits.

All line references are against commit **`cdd156e`**, the fork point, and were read from the
tree rather than recalled. Behavioural claims were measured against a live deployment on
**2026-07-31** — each is labelled where it matters, so nothing here rests on inference alone.

> **Building the fork:** upstream publishes `ghcr.io/calibrain/shelfmark:latest` (multi-arch,
> amd64 + arm64). A fork has to build and publish its own image and repoint whatever deploys
> it. If the target is an ARM board (RK3588 here), the build **must** produce an arm64
> manifest or it fails to pull with "no match for platform in manifest".

### Why a fork at all

Upstream states it is **"in a stable state as of May 2026 but is not under active
maintenance."** Two of the three items below are things upstream would plausibly never ship:
one is a bug fix with a short shelf life (#2), the other is an integration only useful to
people running Audiobookshelf (#3). Item #1 is a genuine feature gap.

### Effort / value summary

| # | Item | Type | Effort | Value here |
|---|---|---|---|---|
| 2 | AudioBook Bay ingestion is broken | bug | **~4 lines** + a guard | restores a source that is otherwise dead |
| 3 | No awareness of what's already in the library | feature | medium | stops re-downloading owned books |
| 1 | One destination, five ABS libraries | feature | medium–large | routes each approval to the right library |
| 4 | Audiobook search uses print-book metadata | feature | medium | narrator, runtime and ASINs for audiobook search |

Suggested order: ~~2 → 3 → 1~~ **superseded — see Locked decisions below.**

### Locked decisions (2026-07-31 review)

Every open question below was resolved in a design review on 2026-07-31. Where a decision
below contradicts the original proposal text, **the decision wins.**

**Build order: #2 → shared ABS client → #1 → #3.** The ABS client became a shared
foundation once #1 went hybrid, and #3's matching needed rethinking (see below), so #1 —
deterministic plumbing once the client exists — lands before #3.

- **#2**: implement in fork now, then open an **upstream PR** (fix + fixture tests).
  `tests/audiobookbay/` already exists to host the fixtures.
- **#1 is hybrid, seerr-style**: the ABS client (`GET /api/libraries`) fetches and names
  libraries in settings, but each library row still gets a locally-confirmed writable path —
  ABS reports paths as *its* container sees them; Shelfmark writes files, seerr never does.
  Selector on the approve dialog and admin-initiated downloads only (**admin-only**, no
  requester-side picker in v1; the schema doesn't foreclose adding it later).
- **#1 scope: audiobooks only.** The ebook lane (`DESTINATION`/`INGEST_DIR`) is untouched.
- **#1 precedence**: explicit approval choice > per-user override > global default.
  `{User}` expansion applies inside whichever path wins. A dangling `destination_key`
  falls back down the chain with a WARNING — never an error, never a wrong write.
- **#1 mechanics**: the destination map is stored config — the approve dialog never
  queries ABS live, so ABS being down can't break approvals. Podcasts are excluded by
  simply never configuring a path for them. Selector hides when ≤1 destination.
  Nullable `destination_key` on requests; admin direct downloads pass the key straight
  through `queue_release`. Each entry gets the `check_audiobook_destination` hardlink test.
- **#3 matching: strict, not fuzzy — and ASIN-first is dead on arrival.** Verified:
  no metadata provider surfaces an ASIN (`grep -rni asin shelfmark/metadata/` = zero hits),
  so normalised title+author is the *only* matcher, not the fallback. v1 uses exact match
  after normalisation (casefold, strip punctuation/diacritics/leading articles/bracketed
  edition noise; **keep subtitles** — the four *Housemaid* titles differ only by suffix).
  Rationale: the badge is advisory, so a miss costs today's status quo, but a false
  "in library" silently suppresses a legitimate request. Store the matched ABS item id +
  ASIN so the badge names the edition and library.
- **#3 index scope: ALL book-type ABS libraries**, not just destination-mapped ones — an
  owned book in an unmapped library is still owned. Hourly refresh + manual "sync now".
- **Publishing**: keep the inherited workflow (already multi-arch amd64+arm64) and the
  `ghcr.io/<owner>/shelfmark` image name; version tags are `v<upstream>-fork.N`; **delete
  the `create-aliases` legacy job** (namespace pollution in a fork); pin the k3s manifest
  to the semver tag, never `latest`. GitHub disables cron on inactive forks after 60 days —
  tag-push and manual dispatch are the real triggers.

---

## 1. Multiple library support when approving requests

### Problem

Shelfmark has exactly **one** audiobook destination. Audiobookshelf here has **five**
libraries — `fiction`, `nonfiction`, `smutty`, `light-novels`, `podcasts` — each a separate
mount. Every approved audiobook therefore lands in whichever single library
`DESTINATION_AUDIOBOOK` points at (currently `fiction`), and anything that belongs elsewhere
has to be moved afterwards with a separate tool that preserves listening progress.

An admin approving a request is exactly the person who knows which library it belongs in, and
exactly the moment the information is available — but the approval path has nowhere to put it.

### Evidence

- `shelfmark/core/utils.py:209` — `get_destination(*, is_audiobook, user_id, username)`. The
  only branch is the boolean `is_audiobook`; there is no notion of *which* audiobook library.
- `shelfmark/core/requests_service.py:499` — `fulfil_request(...)` takes `release_data`,
  `admin_note`, `manual_approval`. No destination parameter.
- `shelfmark/core/requests_service.py:587` — the hand-off:
  ```python
  success, error = queue_release(
      queued_release_data, 0,
      user_id=request_row["user_id"],
      username=requester.get("username"),
  )
  ```
  Identity is threaded through; destination is not.

### The seam that already exists

`get_destination` resolves through `config.get("DESTINATION_AUDIOBOOK", "", user_id=user_id)`
— i.e. the setting is **already per-user overridable**, and it already supports a `{User}`
placeholder expanded by `_expand_user_destination_placeholder`. So the plumbing for "this
download resolves its destination from context rather than a global" is in place; it is keyed
on user rather than on a chosen target.

That makes the change smaller than it first looks: add a second dimension to the same lookup
rather than inventing a new mechanism.

### Proposed design

1. **A named-destination map.** New setting `DESTINATIONS_AUDIOBOOK`, a list of
   `{key, label, path}` (a `TableField`, the same shape `PROWLARR_REMOTE_PATH_MAPPINGS`
   already uses — see `shelfmark/config/settings.py:1830` for a working precedent).
   `DESTINATION_AUDIOBOOK` stays as the default/fallback so existing installs are unaffected.
2. **Carry a key on the request.** Add a nullable `destination_key` column to the requests
   table, set at approval time.
3. **Thread it through**: `fulfil_request(..., destination_key=...)` → `queue_release(...)` →
   the download task → `get_destination(..., destination_key=...)`, which resolves the map
   first and falls back to today's behaviour when the key is absent or unknown.
4. **UI**: a select on the approve dialog, defaulting to the configured default. When only one
   destination is configured, hide it entirely so nothing changes for single-library users.

### Risks / notes

- **Resolve unknown keys to the default, never to an error or an empty path.** A deleted
  destination must not strand a queued request or write to `/`.
- Each destination must satisfy the same hardlink constraint as today: it has to be on the
  **same mount** as the download client's completed dir, or the transfer silently falls back
  to copying. A "Test Destination" action already exists (`check_audiobook_destination`) and
  should be run per entry.
- Per-user overrides and per-destination routing can conflict. Decide precedence explicitly
  and write it down — suggested: explicit approval choice > per-user override > global default.

---

## 2. Fix AudioBook Bay ingestion

### Problem

The bundled AudioBook Bay source returns **zero results for every query**, even though the
site is up and well stocked. It is currently disabled in this deployment (`ABB_ENABLED=false`)
because an enabled source that cannot return rows is pure latency — it costs a rate-limited
fetch per page for nothing.

### Root cause (measured, not inferred)

AudioBook Bay now serves post content **base64-encoded inside a hidden div**, decoded
client-side by JavaScript:

```html
<div class="post re-ab" style="display:none;">PGRpdiBjbGFzcz0icG9zdFRpdGxlIj48aDI+PGEg…</div>
```

Decoding one of those payloads by hand yields the *original* markup intact:

```html
<div class="postTitle"><h2><a href="/abss/the-divorce-freida-mcfadden/">The Divorce - Freida McFadden</a></h2></div><div class="postInfo">Category: Crime…
```

The scraper looks for that markup in the live DOM:

- `shelfmark/release_sources/audiobookbay/scraper.py:233` — `posts = soup.select(".post")`
  → still matches (the wrapper keeps `class="post re-ab"`), so it finds 9 "posts"…
- `shelfmark/release_sources/audiobookbay/scraper.py:241` —
  `title_elem = post.select_one(".postTitle > h2 > a")` → matches **nothing**, because
  `.postTitle` only exists inside the base64 blob. Every post is skipped via the
  `if not title_elem: continue` guard, so the function returns `[]` with no error.

`grep -rn "b64decode\|base64" shelfmark/` confirms the ABB scraper has **no base64 handling**
anywhere (the hits are cover-URL encoding in `core/utils.py` and NZBGet payloads).

Measured 2026-07-31 against `audiobookbay.lu`: a search for *freida mcfadden* returned HTTP
200, 38,183 bytes, `9x class="post re-ab"`, 11 mentions of "mcfadden", and **0 parsed
results**. Decoding the 9 payloads by hand produced 9 real titles (*The Divorce*, *The
Intruder*, *The Tenant*, *The Gift*, …), several of which no other configured indexer carries.

> The same bug kills the standalone `audiobookbay-automated` project, which uses the identical
> `.post` → `.postTitle > h2 > a` selector pair, and is broken the same way.

### Proposed fix

Decode each `.post` element's text before parsing it, falling back to the element itself when
it is not base64. Roughly, at `scraper.py:233`:

```python
import base64
from bs4 import BeautifulSoup

def _decode_post(post):
    """ABB serves post markup base64-encoded in a hidden div; decode when present."""
    raw = post.get_text(strip=True)
    if not raw or len(raw) < 32:
        return post                       # plain markup, nothing to decode
    try:
        html = base64.b64decode(raw, validate=True).decode("utf-8", "replace")
    except Exception:
        return post                       # not base64 — leave as-is
    if "postTitle" not in html:
        return post                       # decoded to something unexpected
    return BeautifulSoup(html, "html.parser")

posts = [_decode_post(p) for p in soup.select(".post")]
```

Everything downstream (`.postTitle > h2 > a`, `.postInfo`, `.postContent`, the cover, the
info-hash page) then works unchanged, because the decoded payload *is* the old markup.

### Risks / notes

- **This is an anti-scraping measure and will change again.** Treat the fix as disposable.
- **Add a loud signal for "site reachable but nothing parsed."** The failure mode that cost
  real time here was silence: 200 OK, posts found, zero results, no error. Log
  `found N .post elements, parsed 0` at WARNING — that single line turns the next breakage
  from an investigation into a glance.
- A regression test with a **saved fixture** of one base64 page (and one plain page, as a
  negative control) is worth more than the fix itself, since the fix will need redoing.
- Keep the decode defensive: a plain-markup mirror must keep working, so never assume base64.

---

## 3. Track books/audiobooks already in the library

### Problem

Shelfmark has **no idea what you already own**. It will happily present, and let a user
request, a book that is already on the shelf. In this library that is not hypothetical: nine
Freida McFadden audiobooks were already present, and every one of them was still offered as a
fresh download.

Contrast Jellyseerr — the app whose workflow Shelfmark is otherwise imitating — which reads
Jellyfin and marks matching items **Available**, so users request only what is missing.

### Evidence

- `grep -rni "audiobookshelf" shelfmark/` returns **one** hit:
  `shelfmark/config/settings.py:400` — a `placeholder="http://audiobookshelf:8080"` for a
  cosmetic **nav-link button**. There is no client, no sync, no import of library state.
- The only de-duplication that exists is:
  - `shelfmark/core/queue.py` — de-dupes queue entries by task id;
  - `shelfmark/core/requests_service.py:111` — `_find_duplicate_pending_request(...)`, which
    blocks a second *pending request* for the same title/author/content_type.

  Both are about not doing the same thing twice **inside Shelfmark**. Neither looks at a
  library.

### Current mitigation (and why it isn't enough)

Requests here default to `request_book`, so a human approves every one — the operator *is* the
duplicate check. That works while the operator remembers the library, and fails quietly as it
grows. It also puts the work in the wrong place: the requesting user should see "you already
have this" before asking.

### Proposed design

1. **A read-only Audiobookshelf client.** Base URL + API token settings, mirroring the
   existing Prowlarr integration (`release_sources/prowlarr/` is the model to copy — it already
   does URL normalisation, token handling, and a connection test).
2. **A periodic index**, not a live query per card. `GET /api/libraries` then
   `GET /api/libraries/<id>/items` — note that endpoint is **minified and omits `series`**, so
   pass `?expanded=1` or use `/filterdata` if series matters. Cache to the local DB with a
   configurable refresh (hourly is plenty) plus a manual "sync now".
3. **Match on ASIN first, then normalised title+author.** ASIN is exact when present; fall
   back to a case/punctuation-normalised title plus a normalised primary author. Do **not**
   match on title alone — this library holds four distinct *Housemaid* titles that differ only
   by suffix.
4. **Surface it in three places**: an "In library" badge on search results; a soft warning on
   the request form; and the same badge on the admin approve dialog.
5. **Never hard-block.** Re-acquiring a better edition is legitimate. Warn, don't refuse.

### Risks / notes

- **Do not conflate "in library" with "same recording."** A 2021 rip and a 2024 re-recording
  are both *The Locked Door*; treating them as one hides a real upgrade. Store the matched
  ASIN so the badge can say *which* edition is held.
- Multi-library ABS means the badge should say **which** library, which is also exactly what
  feature #1 needs — build the client once and let both use it.
- Handle the ABS token being absent or wrong by degrading to today's behaviour with a visible
  warning, not by failing search.

---

## 4. Audiobook search runs on print-book metadata

### Problem

Shelfmark ships three metadata providers — Hardcover, Open Library, Google Books — and all
three catalogue **books**. Set as the audiobook provider, they cannot supply a narrator, a
runtime, an abridgement flag, or the recording's own cover art, because none of those exist
in a print catalogue. The "editions" they offer are paper editions.

### Evidence

`grep -rni asin shelfmark/metadata_providers/` returns zero hits: no provider surfaces an
Audible identifier, which is why proposal #3's matcher had to fall back to normalised
title+author as its *only* key rather than as a fallback.

### The service ABS actually uses

Audiobookshelf does this in two calls, and only the first is load-bearing:

1. `https://api.audible.{tld}/1.0/catalog/products?title=&author=&num_results=&products_sort_by=`
   — search, returning ASINs. Undocumented, unauthenticated, and relied on by ABS, Readarr
   and Plex's agent alike.
2. `https://api.audnex.us/books/{asin}?region=` — enrichment for a *known* ASIN.

**Audnexus cannot replace step 1.** Its entire surface is `/authors?name=`, `/authors/{ASIN}`,
`/books/{ASIN}` and `/books/{ASIN}/chapters` — there is no book search, so it can never turn
a title into an ASIN.

### Proposed design

An `audible` metadata provider registered through the existing `@register_provider` system:
region-selectable storefront, narrator/runtime/rating/abridgement as display fields, square
cover art, series taken from the numbered entry rather than the first, and optional audnexus
enrichment for genres and ISBN on the detail view only — never once per search result.

`BookMetadata` gains an `asin` field, which flows into the #3 badge as an **additional exact
match key**.

### Risks / notes

- **ASIN confirms a match; it never rules one out.** Regional editions, re-recordings and
  abridgements each get their own ASIN, and direct-mode results (ABB, Anna's Archive,
  Prowlarr) carry none at all. Title+author must keep carrying the general case.
- An ASIN is only valid in the storefront it came from, so anything cached by ASIN has to be
  keyed by region too.
- The catalog API is undocumented; Amazon can change it. audnex.us is a free community
  service, so enrichment must be best-effort and never load-bearing.
- Audible is audiobooks only. Leave the ebook provider alone.

---

## Deployment context these were found in

Useful when judging whether a proposal generalises or is specific to one setup:

- Shelfmark driving **Prowlarr** (4 usenet + 4 torrent indexers) into **qBittorrent** and
  **SABnzbd**, hardlinking into **Audiobookshelf**.
- A single `/media` mount covering both the download dir and the library, because hardlinking
  across separate bind mounts of the same filesystem returns `EXDEV` and silently degrades to
  copying.
- Five separate ABS libraries, which is what motivates proposal #1.
- An existing external filing pipeline (hardlink + tag-based worklist + an m4b normaliser) that
  a fork should complement rather than duplicate.
