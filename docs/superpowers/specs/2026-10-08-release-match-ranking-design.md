# Release Match Ranking (volume and format)

**Date:** 2026-10-08
**Status:** Draft, revised after Codex review (see end)
**Scope:** Item #1 of the search-quality list.
- **Ebook searches only.** Audiobook searches are unchanged; an audiobook measurement is deferred.
- **Out of scope:**
  - edition guessing (official vs fan translation) beyond an explicit name marker; that is #2
  - spin-off detection
  - Roman-numeral volumes
  - localized-title identities
  - changing what is searched or sent to indexers

## Problem

The release list for a series volume mixes the right book with other volumes, manga
and comic editions, and audiobooks. Today the frontend's default "best match" sort
(`src/frontend/src/utils/releaseScoring.ts`, `sortReleasesByBookMatch`) scores title
similarity plus an author bonus, so:

- **Wrong volumes rank high:** "High School DxD - Volume 25" shares every title word with vol 5.
- **Other formats rank high:** for Overlord vol 2, manga releases ("Overlord.Vol.02.Manga…Comic.eBook") rank as well as the light novel.

**Edition is not detectable before download.** A live probe on 2026-10-08 found:
- **No edition data from Prowlarr:** it returns only title, indexer, size, upload date, categories and peers per release, with no publisher, description or tags.
- **A clean name, a fan file:** the MyAnonamouse release `High School DxD, Vol. 5: … by Ichiei Ishibumi [ENG / EPUB]` reads as official but is the Baka-Tsuki fan translation, and so is the NZBgeek copy.

## Principle

**Ranking acts only on strong, explicit evidence.**
- **Promote:** a release moves up only when it names this volume in explicit volume syntax.
- **Demote:** it moves down only when a declared format or category says it is another medium, or explicit volume syntax names another volume.
- **Otherwise:** no tier, and today's order applies. A wrong "unknown" costs nothing compared with today, while a wrong top or bottom tier misleads, so ambiguity resolves to unknown.

This classifier is separate from the ladder's stop predicate (`is_identity_hit`), which keeps
its own, deliberately broader rules. The two share helpers but not decisions.

## Design

### 1. Evidence a release carries

- **Release name:** the original name as the indexer gave it.
  - **Prowlarr:** it currently replaces MyAnonamouse titles with `bookTitle` (`release_sources/prowlarr/source.py:573`). It must keep the original in `extra["release_name"]` whenever it replaces the title.
  - **Other sources:** they keep `release.title` as the name.
  - **Classification:** uses `extra["release_name"]` when present, else `release.title`.
- **Declared format:** `release.format` and `extra["formats"]`. IRC and direct download carry the format separately from the title.
- **Declared content type:** `release.content_type`, detected from indexer categories (audiobook categories become `audiobook`).
- **Structured author:** `extra["author"]` when the source provides one.

**Precedence:** declared format or content type beats name words. Name words count only as technical markers (below), never as ordinary title words.

### 2. Classifying a release (`shelfmark/core/search_queries.py`)

```python
@dataclass(frozen=True)
class ReleaseMatch:
    volume: Literal["match", "other", "unknown"]
    other_volume: int | None          # set only when volume == "other"
    medium: Literal["ebook", "comic", "audio", "video", "unknown"]
    compatible: bool                  # medium suits the requested book
    fan_marker: bool                  # the name explicitly says fan translation

def classify_release(
    *, name: object, formats: Sequence[object], content_type: object,
    release_author: object, identity: RankingIdentity | None,
) -> ReleaseMatch
```

It is pure and total: junk input gives `ReleaseMatch("unknown", None, "unknown", True, False)`.

**Medium**, first rule that applies:
1. **Declared audio:** `content_type == "audiobook"`, or a declared format in m4b, mp3, m4a, flac or aac, gives `audio`.
2. **Declared comic:** a declared format in cbz, cbr or cb7 gives `comic`.
3. **Video:** technical video markers in the name: resolutions (2160p/1080p/720p/480p), codecs (x264/x265/h264/h265/hevc), containers (mkv/mp4/avi), BDRip/WEB-DL/WEBRip, `SxxEyy`. These give `video`. Plain words like "episode" do not count.
4. **Audio by name:** `m4b`, `mp3` or `audiobook` as a separate token, when the word is not one of the requested book's own title tokens, gives `audio`.
5. **Comic by name:** `manga`, `comic`/`comics` or `graphic novel` as a separate token, under the same own-title exclusion, gives `comic`.
6. **Ebook:** a declared ebook format (epub, mobi, azw3, pdf) or an `ebook` content type gives `ebook`.
7. **Otherwise:** `unknown`.

**Compatibility:**
- `compatible` is true for `ebook` and `unknown`.
- `comic` is compatible only when the requested book is itself a comic.
- `audio` and `video` are never compatible.
- The requested book counts as a comic when its title or series names it as such with word boundaries: `manga`, `comic(s)` or `graphic novel`. This replaces the unbounded `_COMIC_BOOK_RE` for ranking.

**Volume**, decided only when the identity has a series key and position:
- **Explicit volume syntax:** only these forms, with `.`, `_`, space or `-` as separators:
  - `Vol N`, `Vol. N`, `Volume N`, `Vols`
  - `vNN`
  - `#N`
  - `Book N`
  - `[<series> NN]` and `[<series> - Volume NN]`
  - `<series> NN` immediately followed by ` - `, `]`, `(`, a 4-digit year, a format token or the end of the name

  Here `<series>` means the last token of the series key, and N is 1–3 digits with no fraction or letter suffix. Bare `[N]`, `- N` and page or edition numbers are not volume syntax for ranking.
- **Short series key:** when the series name has a colon, the part before it ("Mushoku Tensei" of "Mushoku Tensei: Jobless Reincarnation") is a second key, if it has a significant token and differs from the full key; a name carrying either key's tokens carries the series key, and `<series>` may be either key's last token.
- **Collection evidence:** checked before `match` or `other`, and gives `unknown`:
  - `omnibus`, `box set`/`boxed set`, `complete series`, `collection`, `trilogy`/`duology`/`quartet`, `books N-M`
  - any range or list of volume numbers
  - more than one explicit volume number
  - Contributor separators (`&`, `/`, `+`, `&amp;`) are **not** collection evidence.
- **`match`:** the name carries the series key tokens and exactly one explicit volume number, and it is this position.
- **`other`:** the same conditions, with a different number. That number becomes `other_volume`.
- **Natural-title books:** a series book whose title names no volume (e.g. Leviathan Wakes). A release is `match` when all its significant title tokens appear, at least one of them is not a series word, and no explicit volume syntax names another number. Other numbers in the name (years, "2nd edition", "451") do not veto it.
- **Otherwise:** `unknown`.

**Author conflict:** when the release carries a structured author and it shares no token with any
requested author, a `match` is downgraded to `unknown`. A missing author is neutral.

**Fan marker:** the name contains, case-insensitively and on word boundaries, `baka-tsuki`,
`baka tsuki`, `fan tl`, `fan translation`, `fan-translated` or `scanlation`. There is no "retail" marker.

### 3. Identity (`RankingIdentity`)

Built once per request in the endpoint from the book after the release endpoint's title override
(`main.py:3237`), independent of the search plan:

```python
RankingIdentity(
    series_key=<build_search_identity(title=book.title,
                                      current_query=book.search_title or book.title,
                                      series_name=book.series_name,
                                      series_position=book.series_position)>.series_key,
    position=<same>.position,
    title_tokens=<same>.title_tokens,
    title_names_volume=<same>.title_names_volume,
    book_is_comic=<bounded rule above>,
    authors=tuple(book.authors),
)
```

Construction reuses `build_search_identity`, so series and position resolution (conflicts,
fractions, Part rules) match the ladder.

### 4. Endpoint (`shelfmark/main.py`, `/api/releases`)

- **When it applies:** for an ebook search with a book and no manual query, after all sources have been searched.
- **What it adds:** to every release from every source,
  `extra["release_match"] = {"v": 1, "volume", "other_volume", "medium", "compatible", "fan_marker"}`.
- **Audiobook searches and manual queries:** they get no `release_match`.
- **Failures:** classification runs per release inside `try`/`except`. A failure is logged at DEBUG and leaves the key absent; it never fails the request.
- **Downstream:** the key is informational. Download payloads may carry it inside `extra`, and nothing downstream reads or persists it.

### 5. Ranking (`src/frontend/src/utils/releaseScoring.ts`)

**One parser:** a shared `ReleaseMatch` TypeScript type and one runtime parser, `parseReleaseMatch(extra)`. It returns `null` on a wrong version or unknown enum values. An `other_volume` that is not a positive integer (or one set when `volume` is not `other`) does not discard the payload: the volume becomes `unknown` and `medium`, `compatible` and `fan_marker` still apply. The backend never emits `other_volume` 0 (a volume-0 `other` is `unknown`). Scoring and badges both use it.

Only the default "best match" sort changes, and the score is still computed once per release, outside the comparator:

| Tier | Condition | Bonus |
|---|---|---|
| Top | `volume == "match"` and `compatible` | +20000 |
| Middle | no parsed match, or `volume == "unknown"` and `compatible` | 0 |
| Bottom | `volume == "other"`, or not `compatible` | −20000 |

- **Tier dominance:** the bonuses exceed today's maximum score of about 11500 (exact title 10000 plus author 1500), so the tier decides. Within a tier, today's score and order apply.
- **Empty title candidates:** when there are none (today's sorter returns the input unchanged), the tier still orders, with today's order inside each tier.
- **No filtering:** nothing is filtered.
- **Unchanged:** column sorts, the format sort, saved sorts and all filters work as today.

### 6. Badges

**Mismatch badges:** shown on their own line below the title, outside the two-line title clamp, using existing badge styles:

| Badge | When |
|---|---|
| `Vol N` | `volume == "other"` |
| `Manga/Comic` | `medium == "comic"` and not compatible |
| `Audiobook` | `medium == "audio"` |
| `Video` | `medium == "video"` |

**Fan marker:** shown as a secondary `Fan TL?` badge with the tooltip "The release name says this is a fan translation".

**No positive badge:** a matching volume gets no badge. In the compact mobile layout, badges render as plain text, like other badges.

### 7. Cache and expanded searches

- **Problem:** the frontend release cache is keyed by provider, book, source and content type (`releaseCache.ts:14`). Expanded searches keep existing rows and drop duplicate incoming IDs (`useReleaseSearchSession.ts:281-290`).
- **Cache:** a manual-query response is not written to the book's normal cache entry, so reopening the book never shows unannotated manual results.
- **Merging:** when an expanded response returns a release ID already shown, the incoming `extra.release_match` replaces the old one.

## Error handling

- `classify_release` never raises.
- **Endpoint failures:** a per-release failure means no `release_match` for that release.
- **Frontend:** a missing or invalid `release_match` means no tier and no badge, which is today's behaviour.

## Testing

- **Classifier (`tests/core/test_search_queries.py`).** Paired right and wrong cases, using live-run names where they exist:
  - **Match:** DxD 5 `match`; "Volume 25" `other` (25); Overlord `[Overlord 02]` `match`; `Overlord #3 EPUB` and `Overlord_Vol_02_2018_Retail_EPUB` against vol 2 (`other` and `match`); `The Expanse Book 2 …` `other` for Leviathan Wakes.
  - **Unknown:** `Overlord Vol. 2 Omnibus EPUB`, `The Expanse Leviathan Wakes [320] EPUB` and `… - 451 pages EPUB`.
  - **Natural titles:** `Leviathan Wakes 2nd edition EPUB` and `Leviathan Wakes James S. A. Corey & Daniel Abraham EPUB` are both `match`.
  - **Medium:** `Overlord Vol. 2: Episodes of the Kingdom EPUB` is `ebook`, not video; Overlord manga names are `comic` and not compatible; a CBZ-declared clean title is `comic`; an audiobook-category clean title is `audio`; a 1080p name is `video`.
  - **Comic flag:** a `(Graphic Novel)` request is a comic; "Comical" is not.
  - **Other:** an author conflict downgrades `match` to `unknown`; the fan marker; junk input.
- **Source conversion:**
  - Prowlarr keeps `release_name` when it substitutes `bookTitle`.
  - An endpoint test converts a raw MyAnonamouse `… [ENG / M4B]` result and gets `audio`.
  - An IRC result with format `epub` and a clean title.
- **Endpoint:**
  - an ebook search annotates releases from two sources
  - audiobook and manual-query searches carry no `release_match`
  - a per-release classifier failure keeps the request successful
- **Frontend:**
  - `parseReleaseMatch` rejects malformed payloads
  - tier ordering beats title score, and within-tier order is unchanged
  - no match data gives today's order, and empty title candidates still use tiers
  - a saved column sort is unaffected
  - manual search, then reopen, then expand shows no stale or unannotated mix
  - large-list smoke test: 2000 releases sort without recomputing scores in the comparator
- **Badges:** mismatch badges render outside the title clamp, nothing renders for a match, and the fan marker shows with its tooltip.
- **Acceptance:** after release, open DxD vol 5 and Overlord vol 2 with the Default sort and no format, language or indexer filter.
  - The right volume ranks above other volumes and manga, which carry badges.
  - A saved column sort still applies when chosen.

## Rollout

Ships with #4 (already on local main, 496e2f7) in one Shelfmark release via
`scripts/release-local.sh`, plus the fleet-infra image bump. Each push needs the user's OK.

## Revisions after Codex review (2026-10-08)

16 findings. 15 were adopted; #10, localized identities, is declared out of scope. The main changes:

| # | Change |
|---|---|
| 1 | Original release name kept (`extra.release_name`); declared format, content type and author used, with precedence over name words |
| 2 | Collection evidence is checked before match/other |
| 3, 8 | Explicit volume syntax only (adds `#N`, `Book N`, underscores, bare series numbers in context); bare `[N]`/`- N` excluded |
| 4, 5 | Natural-title rule for ranking without the number veto; contributor separators are not bundles, but a conjunction (`&`, `and`, `/`, `+`, `&amp;`) joining the requested title to further title words makes a natural-title release `unknown`, unless those words are a requested author or the conjunction sits in an author segment before ` - ` (amended after the plan review) |
| 6, 7 | Technical markers only; bounded comic rule; medium separated from compatibility |
| 9 | Author conflict blocks top tier |
| 10 | Localized identities: out of scope, unmatched releases keep today's order |
| 11 | Manual responses not cached under the book; incoming match data refreshes duplicate rows |
| 12 | Exact identity arguments defined |
| 13 | Acceptance with Default sort and no filters; saved sorts verified unchanged |
| 14 | Versioned `release_match` key, typed parser, informational downstream |
| 15 | No positive badges, badges outside the title clamp, `Fan TL?` with tooltip, no `Retail` badge |
| 16 | Source-to-UI pipeline tests, counterexamples, cache flow, large-list smoke test |

## Revisions after the plan review (2026-10-08)

| Finding | Change |
|---|---|
| Natural-title bundles | A conjunction joining the requested title to further title words ("Leviathan Wakes & Caliban's War") is `unknown`; "Leviathan Wakes James S. A. Corey & Daniel Abraham" stays `match` (revision 5 amended) |
| Invalid `other_volume` | The backend never emits `other_volume` 0; the frontend parser downgrades an invalid `other_volume` to an `unknown` volume and keeps medium, compatibility and fan marker (§5 amended) |
| Final review: pre-colon short series key accepted | The part of a series name before a colon is a second series key for ranking ("Mushoku Tensei Vol. 3" matches "Mushoku Tensei: Jobless Reincarnation" vol 3); a series name without a colon gets no shortened key (§2 amended) |
