# Release Match Ranking (volume and format)

**Date:** 2026-10-08
**Status:** Draft
**Scope:** Item #1 of the search-quality list.
- **Ebook searches only.** Audiobook searches are unchanged; an audiobook measurement is deferred.
- **Out of scope:**
  - edition guessing (official vs fan translation) beyond explicit name markers; that is #2
  - spin-off detection
  - changing what is searched or sent to indexers

## Problem

The release list for a series volume mixes the right book with other volumes, manga
and comic editions, and audiobooks. Today the frontend's default "best match" sort
(`src/frontend/src/utils/releaseScoring.ts`, `sortReleasesByBookMatch`) scores title
similarity plus an author bonus, so:

- **Wrong volumes rank high:** "High School DxD - Volume 25" shares every title word with vol 5.
- **Other formats rank high:** for Overlord vol 2, manga releases ("Overlord.Vol.02.Manga…Comic.eBook") rank as well as the light novel.

The release query ladder (2026-10-07) already recognises volumes and formats in release
names, in `shelfmark/core/search_queries.py`, checked against the live data. Today it uses
that only to decide when to stop sending fallback queries.

**Edition is not detectable before download.** A live probe on 2026-10-08 found:
- **No edition data from Prowlarr:** it returns only title, indexer, size, upload date, categories and peers per release, with no publisher, description or tags.
- **A clean name, a fan file:** the MyAnonamouse release `High School DxD, Vol. 5: … by Ichiei Ishibumi [ENG / EPUB]` reads as official but is the Baka-Tsuki fan translation, and so is the NZBgeek copy.

Edition badges are therefore limited to names that say so explicitly.

## Design

### 1. Classifying a release (`shelfmark/core/search_queries.py`)

```python
@dataclass(frozen=True)
class ReleaseMatch:
    volume: Literal["match", "other", "unknown"]
    other_volume: int | None   # the single other volume named, when volume == "other"
    medium: Literal["ebook", "comic", "audio", "video"]
    marker: Literal["fan", "retail"] | None

def classify_release(release_title: object, identity: SearchIdentity | None) -> ReleaseMatch
```

It is pure and total: junk input gives `ReleaseMatch("unknown", None, "ebook", None)`. It reuses the module's
existing parsing (`html.unescape`, version-tag removal, `_release_volumes` with the series
key, `_VIDEO_RE`, `_AUDIO_RE`, `_COMIC_RELEASE_RE`), so it agrees with `is_identity_hit`.

- **medium:**
  - `video` if `_VIDEO_RE` matches; else `audio` if `_AUDIO_RE` matches; else `comic` if
    `_COMIC_RELEASE_RE` matches and the book is not itself a comic (`identity.book_is_comic`).
  - Otherwise `ebook`.
- **volume:** only when the identity has a series key and a position.
  - `match` when the release carries the series key tokens and names exactly this volume (the
    same rule as the series branch of `is_identity_hit`, without the medium checks).
  - `other` when it carries the series key tokens and names exactly one whole volume that is
    a different number. `other_volume` is that number.
  - `unknown` otherwise: no volume named, several volumes, ranges, fractions, or no series
    identity, as with standalones.
  - For a series book whose title names no volume (`title_names_volume` false, e.g. Leviathan
    Wakes), the title-token rule of `is_identity_hit` also yields `match`.
- **marker:** set only from explicit name tokens, case-insensitive, on word boundaries.
  - `fan`: `baka-tsuki`, `baka tsuki`, `fan tl`, `fan translation`, `fan-translated`,
    `scanlation`, `armaell`.
  - `retail`: `retail`.
  - If both match, `fan` wins. Scene group names are not markers.

`identity` comes from `build_search_identity(...)`. When the plan already carries one,
`plan.identity` is used; otherwise it is built for the book.

### 2. Endpoint (`shelfmark/main.py`, `/api/releases`)

- **When it applies:** for an ebook search with a book (not a manual query), after all sources have been searched and before serialisation.
- **What it adds:** `release.extra["match"] = {"volume", "other_volume", "medium", "marker"}` on every release from every source.
- **Identity:** built once per request from the same book and title override the plan uses.
- **Audiobook searches and manual queries:** they get no `match` key.
- **Failures:** a failure in classification is logged and leaves `match` absent. It never fails the request.

### 3. Ranking (`src/frontend/src/utils/releaseScoring.ts`)

Only the default "best match" sort changes. Column sorts and the format sort are untouched.
Nothing is filtered or hidden.

The tier is added to today's score:

| Tier | Releases | Bonus |
|---|---|---|
| Top | `volume == "match"` and `medium == "ebook"` | +20000 |
| Middle | `volume == "unknown"` and `medium == "ebook"` | 0 |
| Bottom | `volume == "other"`, or `medium` is comic, audio or video | −20000 |

The bonuses exceed today's maximum score of about 11500 (exact title 10000 plus author 1500), so the tier always decides.
Within a tier, today's score and order apply. Releases with no `match` key (audiobook
searches, old responses) score exactly as today.

### 4. Badges (`ReleaseModal` list and card rows)

Small badges next to the release title, reusing the existing badge styling in `ReleaseCell`:

| Badge | When | Style |
|---|---|---|
| `Vol N` | `volume == "other"` | muted warning |
| `Manga/Comic` | `medium == "comic"` | muted warning |
| `Audiobook` | `medium == "audio"` | muted warning |
| `Video` | `medium == "video"` | muted warning |
| `Fan TL` | `marker == "fan"` | amber |
| `Retail` | `marker == "retail"` | neutral |

A correct match gets no badge, to keep the list quiet. Badges render as plain text in the compact
mobile layout, like other badges.

## Error handling

- `classify_release` never raises.
- **Endpoint failures:** the endpoint wraps it per release; a failure means no `match` key for that release.
- **Frontend:** treats a missing or malformed `match` as no tier and no badge.

## Testing

- **`tests/core/test_search_queries.py`, `classify_release`:** using release names from the 2026-10-07 live run and
  the 2026-10-08 probe:
  - DxD 5: `match`
  - "Volume 25" and "Volume 15": `other` (25, 15)
  - Overlord manga names: `comic`
  - "[ENG / M4B]": `audio`
  - a 1080p name: `video`
  - Shield Hero "v03 … (v2.0)": `match`
  - Leviathan Wakes natural name: `match`
  - a standalone: `unknown` and `ebook`
  - "[Baka-Tsuki][Armaell]": `fan`
  - "Retail": `retail`
  - junk input
- **Endpoint test:**
  - an ebook search returns `extra.match` on releases from two sources
  - audiobook and manual-query searches return none
  - a classification failure leaves the request successful
- **Frontend unit tests (`releaseScoring`):** tier ordering beats title score; within-tier order unchanged;
  no `match` means today's order.
- **Component test:** badges render for `other`, comic, audio, video, fan and retail, and nothing for a match.
- **Acceptance:** after release, open DxD vol 5 and Overlord vol 2 in the UI.
  - The right volume is at the top.
  - Other volumes and manga are at the bottom, with badges.

## Rollout

Ships with #4 (already on local main, 496e2f7) in one Shelfmark release via
`scripts/release-local.sh`, plus the fleet-infra image bump. Each push needs the user's OK.
