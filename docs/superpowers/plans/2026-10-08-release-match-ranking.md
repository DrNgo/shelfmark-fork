# Release Match Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For ebook searches, the release modal's default "best match" sort puts the requested volume first and other volumes, manga/comic editions, audiobooks and video last, with small mismatch badges, acting only on strong explicit evidence so anything ambiguous keeps today's order.

**Architecture:** A pure classifier, `classify_release`, in `shelfmark/core/search_queries.py` (separate from the ladder's `is_identity_hit`) reads each release's original name, declared formats, content type and structured author against a `RankingIdentity` built once per request with `build_search_identity`. `/api/releases` adds a versioned `extra["release_match"]` to every release of an ebook, metadata-provider, non-manual search (Prowlarr first keeps MAM's original name in `extra["release_name"]`; IRC is classified on its original result line). The frontend parses it once (`parseReleaseMatch`), adds a ±20000 tier bonus in `sortReleasesByBookMatch` (reached through one extracted sort-path function), renders mismatch badges below the title clamp, and tracks each tab's query context so manual-query responses never enter the book's cache, expansions merge only into a list from the same context, and superseded responses are discarded.

**Tech Stack:** Python 3.14 (Flask), uv, pytest (+xdist), Ruff 0.16.5 (pre-commit hook: ruff 0.15.10 via prek), BasedPyright, Vulture; React 19 + TypeScript 7, Vitest 4 (`react-dom/server` static rendering, no DOM environment), oxlint, oxfmt, knip; npm (`src/frontend/package-lock.json`).

**Spec:** `docs/superpowers/specs/2026-10-08-release-match-ranking-design.md` — read it fully, including both revisions tables ("Revisions after Codex review" and "Revisions after the plan review"), before any task. The spec is binding; the rulings below only fill its gaps.

## Global Constraints

- **Ebook searches only.** Audiobook searches, manual queries (`manual_query`), the `manual` provider and source-browse flows get no `release_match` and keep today's order.
- **Out of scope:** edition guessing beyond the explicit fan marker, spin-off detection, Roman-numeral volumes, localized-title identities, and any change to what is searched or sent to indexers. `is_identity_hit` and its tests do not change.
- **Ranking acts only on strong, explicit evidence.** Promote only on explicit volume syntax naming this volume; demote only on a declared format/content type for another medium, a technical video marker, a non-title medium word, or explicit volume syntax naming another volume. Otherwise `unknown`.
- `classify_release` is pure and total: junk `name` gives `ReleaseMatch("unknown", None, "unknown", True, False)`; it never raises.
- Payload, exactly: `extra["release_match"] = {"v": 1, "volume", "other_volume", "medium", "compatible", "fan_marker"}`; `volume` in `match|other|unknown`, `medium` in `ebook|comic|audio|video|unknown`, `other_volume` set only for `other`.
- Endpoint failures: classification runs per release inside `try`/`except`; a failure is logged at DEBUG and leaves the key absent; it never fails the request. The key is informational: nothing downstream reads or persists it.
- Frontend tiers (default sort only): top `+20000` (`match` and `compatible`), middle `0` (no parsed match, or `unknown` and `compatible`), bottom `-20000` (`other`, or not `compatible`). Scores are computed once per release, outside the comparator. Nothing is filtered; column sorts, the format sort, saved sorts and filters are unchanged.
- Badges: `Vol N` (other), `Manga/Comic` (comic and not compatible), `Audiobook` (audio), `Video` (video); secondary `Fan TL?` with the tooltip "The release name says this is a fan translation". No positive badge. Badges sit on their own line below the title, outside the two-line clamp; compact (mobile) badges are plain text.
- Cache: a manual-query response is never written to the book's cache entry; an expanded response's `extra.release_match` replaces the old one on duplicate release IDs; an expansion merges only into a list from the same query context.
- Python: bare `except A, B:` (PEP 758) is valid here — do not "fix" it.
- Commits run the repo's prek hooks (ruff-check 0.15.10, ruff-format, oxfmt). Never pass `--no-verify`. Never push from a plan step.
- Never contact Prowlarr, Hardcover or the cluster from a plan step except the user-gated Task 9.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Rulings on spec ambiguities

Binding for this plan; each is pinned by a test in the owning task.

1. **The spec's "Unknown" page-count names** (`The Expanse Leviathan Wakes [320] EPUB`, `… - 451 pages EPUB`) are checked against *Caliban's War* (The Expanse 2), where they are `unknown` — a bare `[N]` or `- N` is not volume syntax, so they are not `other` 320/451. Against *Leviathan Wakes* the same names are `match` by the natural-title rule ("451" does not veto). Task 1 `test_a_page_count_is_not_another_volume`.
2. **Medium needs declared evidence to be `ebook`.** `Overlord Vol. 2: Episodes of the Kingdom EPUB` is tested with `formats=["epub"]` (spec rule 6: name words never make `ebook`). Without declared evidence the medium is `unknown`, which is equally compatible and shows no badge. Task 1 `test_episodes_in_a_light_novel_title_is_not_video`, `test_ebook_evidence`.
3. **Ebook content type** is `"ebook"` or `"book"` (Prowlarr and Newznab label their ebook categories `"book"`); `"audiobook"` is audio; any other value (`"other"`, direct download's `"book (fiction)"`) is ignored and the declared format decides. Declared ebook formats are exactly `epub`, `mobi`, `azw3`, `pdf`.
4. **Explicit volume syntax details.** `Vol`/`Volume`/`Vols` take `.`, space, `-` or (normalised) `_` separators; `vNN`, `#N`, `Book N`; `[<series> NN]` and `<series> NN` use **one** separator (a whitespace run, `.` or `-`), so `High School DxD - 5` is a bare `- N`, not syntax; `<series>` is the last significant token of the series key. N is 1–3 digits (0 allowed).
5. **A partial number** (`Vol. 5.5`, `Vol 2.125`, `Vol. 5a`, `v05.5`, `#5.5`) makes the whole release's volume `unknown` rather than being ignored — ambiguity resolves to unknown. A dot followed by digits of any length is a decimal, except a four-digit year (`19xx`/`20xx`): `Vol.02.2016` and `Vol 2 2016` stay volume 2. Task 1 Review Focus 3, `TestRankingReviewFindings::test_any_decimal_suffix_is_not_a_whole_volume` / `::test_a_year_after_the_volume_keeps_it_whole`.
6. **"More than one explicit volume number"** means distinct numbers; `Overlord Vol. 2 [Overlord 02]` is one volume.
7. **Range/list collection evidence counts only in volume context:** a second number joined by `-`, `–`, `—`, `~`, `&`, `+`, `,`, `/`, `to`, `and` or `through` right after a volume marker (`Vol. 2, 3`, `Vol 2/3`, `Vol 2 / 3`, `v02-v03`), after the series name (`Overlord 1-3`, `The Expanse 1-3 …`) or after `Books` (`Books 1-3`). Before any volume parsing the name is masked of ISBNs (`978…`/`979…`, 13 digits with optional separators), dates (`YYYY-M-D`) and sizes (`1-2 MB`, `620.5MB`), so those never read as ranges. Separators between *names* are not evidence (`Kugane Maruyama / so-bin`). The three Codex example names were not available to this plan; `test_isbns_dates_and_sizes_are_not_volume_ranges` uses one name per kind.
7a. **Natural-title bundles** (spec revision 5, amended): a conjunction (`&`, `and`, `/`, `+`, `&amp;`) right after the requested title's words, followed by further title-like words, makes a natural-title release `unknown` (`Leviathan Wakes & Calibans War EPUB`). It stays `match` when those words are one requested author's tokens (`Leviathan Wakes & James S. A. Corey`), when nothing title-like follows (format words and numbers do not count), or when a ` - ` closes the segment (an author segment). A conjunction elsewhere is harmless (`Leviathan Wakes James S. A. Corey & Daniel Abraham EPUB` → `match`).
7b. **Volume 0 is never `other`:** a release naming volume 0 for a request of another volume is `unknown` (its medium still counts: `Overlord Vol 0 [MP3]` → audio, unknown volume); a request for volume 0 still matches `Vol. 0`.
8. **Own-title exclusion** (medium rules 4–5) uses `RankingIdentity.title_tokens`; a matched phrase counts only if one of its words is not a title word (`The Manga Guide to Physics` is not a comic release of itself). With no identity there is no exclusion.
9. **Author conflict compares contributors one by one.** The release author field is split on `,`, `;`, `&`, `+`, `/` and `and`; each part's surname candidates are its first and last words of two or more characters (initials ignored), which covers `Surname, Given` and `Surname Given`. Each requested author's surname is its last word of two or more characters. The release agrees when any candidate is any requested surname; otherwise, with a real release author and at least one requested surname, it conflicts. A shared given name alone is not agreement (`James Patterson` vs `James S. A. Corey` → conflict; `Corey, James S A` → agree; `Maruyama Kugane` vs `Kugane Maruyama` → agree). Placeholder authors (`Unknown`, `Various`, `Anonymous`, `N/A`, `NA`, `None`) and an author field made only of the book's own title or series words (IRC's series-prefix layout puts `Overlord` where the author goes) are missing, not conflicting. A conflict only downgrades `match`. Task 1 Review Focus 1 and `TestRankingReviewFindings`.
10. **Fan marker**, matched after `_` becomes a space: `fan[\s.-]?tl`, `fan[\s.-]translation`, `fan[\s.-]translated`, `baka[\s.-]?tsuki`, `scanlation`, case-insensitive on word boundaries (so `FanTL` and `BakaTsuki` count; plurals and `fantastic translation` do not).
11. **Junk input.** A `name` that is not a non-blank string gives the spec's all-unknown result even if formats are declared; junk `formats`, `content_type` or `identity` are tolerated one by one (an `identity` that is not a `RankingIdentity` decides no volume and makes a comic incompatible). A `RankingIdentity` with malformed fields is sanitised first: a non-string `series_key` becomes `""`, a non-integer, boolean or negative `position` becomes `None`, non-string tokens and authors are dropped (a non-sequence becomes `()`), `title_names_volume` is `False` only when it is exactly `False`, `book_is_comic` is `True` only when exactly `True`.
12. **Identity construction** lives in `build_ranking_identity(*, title, current_query, series_name, series_position, authors)` in `search_queries.py`, which calls `build_search_identity` with exactly the spec §3 arguments; `book_is_comic` is the bounded rule (`manga`, `comic(s)`, `graphic novel(s)` on word boundaries over title + series). `authors` keeps non-blank strings only.
13. **Endpoint scope.** Annotation runs only in the metadata-provider branch (a registered provider, not `manual`, not a source-browse provider, not a query browse), when `content_type == "ebook"` and `manual_query` is empty; the identity is built right after `book.title = title_param`. Task 4 Review Focus 4.
14. **Annotation is applied to the serialized dicts** (`asdict` copies), never to the `Release` objects a source may cache. A release whose serialized `extra` is not a dict gets `{}` only when it is annotated.
15. **Failure logging** is `logger.debug("Release match classification failed for %s: %s", source_id, exc)` under `except Exception as exc:  # noqa: BLE001 - …`. With `exc_info=True` the project ruff (0.16.5) reports the `noqa` as unused (RUF100) while the commit hook's ruff (0.15.10) still reports BLE001 without it; this form passes both.
16. **Prowlarr `release_name`** is always present in Prowlarr's `extra`: the raw indexer title when `bookTitle` replaced it, otherwise `None`. The endpoint uses it when it is a non-blank string, else `release.title`. Newznab never substitutes and is unchanged.
16a. **IRC evidence** (`irc.parser.ranking_evidence(full_line)`): for an `irc` release with an `extra.full_line` result line, the name is that line without the leading `!Bot ` command and the trailing `::INFO::`/`::HASH::` metadata (the file extension stays; it is a format token), and the author is the parser's author only when the detailed `Author - Title.format` pattern matched; a fallback split is a guess and counts as missing. `!Bsk Overlord - Volume 2.epub` is therefore `match` for Overlord 2 (the parser's "author" `Overlord` is the book's own words, ruling 9).
17. **Frontend parser** (spec §5, amended): `v` must be the number `1`, `volume` and `medium` known values, `compatible` and `fan_marker` booleans, else `null`. An invalid `other_volume` (not a positive integer for `other`, or not `null`/absent for another volume) downgrades `volume` to `unknown` (`other_volume: null`) and keeps `medium`, `compatible` and `fan_marker`.
18. **Tier precedence.** Bottom wins: `match` but not `compatible` (the right volume of the manga) is bottom. `unknown`+`compatible` and no parsed match are middle.
19. **Empty title candidates:** the score is the tier alone, and ties keep input order.
20. **Badge rendering.** The `Fan TL?` tooltip is a native `title` attribute (the mechanism the existing "Unsupported" format badge uses), in both layouts. Mismatch badges use the amber "Unsupported" style, `Fan TL?` the gray fallback style. Order: `Vol N`, `Manga/Comic`, `Audiobook`, `Video`, `Fan TL?`. "List and card rows" are `ReleaseRow`'s desktop grid and mobile layout (the only release-row renderings); compact badges are plain text separated by `·`, like the mobile info line. `ReleaseRow` is exported for the component test.
21. **Query context.** Each search has a context: `''` for the automatic search, else the *applied* manual query — the one last submitted with "Search" (`runManualSearch`), never the draft text in the field. Filters, tab switches and expansion use the applied query (until now they read the draft); the applied query starts as the default manual query only when `defaultShowManualQuery` is on (today's initial search) and resets with the book. A search reads and writes the book's cache entry only in the automatic context (`usesBookReleaseCache`).
22. **Responses.** Per tab the hook keeps the context of the list on screen and a request sequence number. A response is discarded when a newer request for the tab started or the applied context changed while it was in flight (loading and errors are cleared only by the newest request); a book or content-type change supersedes every in-flight request. An expansion merges only when its context equals the displayed one; otherwise its response replaces the list.
23. **Merge replacement is literal.** On a duplicate `source_id` the existing row keeps its place and data, but its `extra.release_match` becomes the incoming one, and is removed when the incoming row has none; new rows are appended in response order (as today). A non-object `extra` on either side is treated as `{}`.
24. **Tested where it runs.** The hook calls `queryContext`, `usesBookReleaseCache`, `releaseResponseAction` and `applyReleaseResponse` from `releaseSearchSession.helpers.ts` (the repo's helpers-module pattern; the frontend has no DOM test environment), and a source check (`readFileSync` of the hook, as `mobileHomepageLayout.test.ts` does for CSS) fails if the hook stops calling them or reimplements the merge. `ReleaseModal` picks its sort path through `sortReleasesForDisplay` (new `utils/releaseDisplaySort.ts`), which the saved-sort tests exercise.

## Review Focus

1. **A release whose author field is a placeholder or written differently** (IRC's `Unknown`, `Maruyama Kugane`, `Kugane Maruyama, so-bin`) → still the right volume, never demoted by the author-conflict rule. Tests: `TestRankingReviewFocus::test_a_placeholder_author_is_missing_not_a_conflict` and `::test_name_order_and_extra_contributors_are_not_a_conflict` in Task 1.
2. **Indexer escaping and file version tags** (`&amp;`, `(v1.1)`, `[v2.0]`) → the volume is still read correctly, never `unknown` or another volume. Test: `TestRankingReviewFocus::test_escaped_names_and_version_tags_make_no_volume_or_bundle` in Task 1.
3. **Half volumes and lettered volumes** (`Vol. 5.5`, `v05.5`, `Vol. 5a`) → never top tier for vol 5. Test: `TestRankingReviewFocus::test_a_fractional_or_lettered_volume_is_unknown` in Task 1.
4. **The manual provider** (a user-typed title with no metadata) → no `release_match`, today's order. Test: `TestNoAnnotation::test_the_manual_provider_carries_no_release_match` in Task 4.
5. **A mobile row with several mismatches** (another volume of the manga, fan-translated) → one readable plain-text line with no orphan separators. Test: `ReleaseMatchBadges > separates several compact badges without orphan separators` in Task 6.

## File Structure

| File | Change |
|---|---|
| `shelfmark/core/search_queries.py` | `RankingIdentity`, `ReleaseMatch`, `RELEASE_MATCH_VERSION`, `is_comic_book`, `build_ranking_identity`, `classify_release` and private helpers, appended after the ladder code (Task 1) |
| `shelfmark/release_sources/prowlarr/source.py` | `_prowlarr_result_to_release` keeps `extra["release_name"]` when `bookTitle` replaces the title (Task 2) |
| `shelfmark/release_sources/irc/parser.py` | `ranking_evidence(full_line)` — the original line and a trusted author for ranking (Task 3) |
| `shelfmark/main.py` | `_release_match_payload` (IRC evidence, Prowlarr `release_name`), `_annotate_release_matches`; `/api/releases` builds the identity after the title override and annotates serialized releases (Task 4) |
| `src/frontend/src/types/index.ts` | `ReleaseMatch` interface (Task 5) |
| `src/frontend/src/utils/releaseMatch.ts` | **new** — `parseReleaseMatch` (Task 5) |
| `src/frontend/src/utils/releaseScoring.ts` | tier bonus in `sortReleasesByBookMatch`, empty-candidates path (Task 5) |
| `src/frontend/src/utils/releaseDisplaySort.ts` | **new** — `sortReleasesForDisplay`, the sort-path choice `ReleaseModal` used inline (Task 5) |
| `src/frontend/src/components/ReleaseModal.tsx` | sorts through `sortReleasesForDisplay` (Task 5); `ReleaseRow` exported, badges below the title in both layouts (Task 6) |
| `src/frontend/src/components/ReleaseMatchBadges.tsx` | **new** — mismatch and `Fan TL?` badges (Task 6) |
| `src/frontend/src/hooks/releaseModal/releaseSearchSession.helpers.ts` | **new** — `queryContext`, `usesBookReleaseCache`, `releaseResponseAction`, `applyReleaseResponse`, `mergeExpandedReleases` (Task 7) |
| `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts` | applied manual query, per-tab displayed context and request sequence; uses the helpers for cache, discard, merge and replace (Task 7) |
| `shelfmark/release_sources/irc/source.py`, `direct_download.py`, `newznab/*`, `is_identity_hit`, `releaseSort.ts`, `releaseCache.ts`, `releasePayload.ts` | **unchanged** |
| Backend tests | `tests/core/test_search_queries.py` (Task 1), `tests/prowlarr/test_source.py` (Task 2), `tests/irc/test_parser.py` (Task 3), `tests/core/test_releases_api_release_match.py` (new, Task 4) |
| Frontend tests | `src/frontend/src/tests/releaseMatch.test.ts`, `releaseScoring.test.ts`, `releaseDisplaySort.test.ts` (new, Task 5), `releaseMatchBadges.test.tsx` (new, Task 6), `releaseSearchSession.test.ts` (new, Task 7) |

**Test-run notes (environment, not this feature):**
- Set up once: `uv sync --all-extras` and `cd src/frontend && npm ci`.
- `pytest` runs with `-n auto` by default (`pyproject.toml`). Run endpoint tests (`tests/core/test_releases_api_*.py`) without `tests/newznab` in the same invocation (`tests/newznab/conftest.py` stubs `flask_socketio`).
- On a sandboxed macOS host `tests/core/test_search_deadline.py::test_html_get_page_will_not_start_a_bypass_on_a_spent_budget` can hang (it reaches the network); deselect it where noted.
- Known pre-existing noise: 9 failures in `tests/config/test_entrypoint_permissions.py` on macOS; 4 BasedPyright errors at `shelfmark/main.py:2305-2308` on `main` (the same four lines move to `2353-2356` after Task 4 adds 48 lines above them); `npm run knip` exits 1 on `main` with 2 unused exports (`SEARCH_MODE`, `DISCOVER_ROWS_BY_PROVIDER`) and 27 unused exported types — record that list first (`cd src/frontend && npm run knip > /tmp/knip-main.txt`); after each frontend task the list must be identical apart from line numbers in `src/types/index.ts`.
- Dry-run baseline on `main`: backend 3922 passed / 9 failed; frontend 363 tests.

---
### Task 1: Release classifier — `RankingIdentity`, `classify_release`, bounded comic rule, fan marker

**Files:**
- Modify: `shelfmark/core/search_queries.py` (imports at lines 21 and 24; new section appended after `any_identity_hit`, the end of the file)
- Test: `tests/core/test_search_queries.py` (import block at lines 10-23; new classes appended at the end)

**Interfaces:**
- Consumes: `build_search_identity`, `significant_tokens`, `_tokens`, `_VERSION_TAG_RE` (all existing, same module).
- Produces:
  - `RELEASE_MATCH_VERSION = 1`
  - `@dataclass(frozen=True) class RankingIdentity: series_key: str = ""; position: int | None = None; title_tokens: tuple[str, ...] = (); title_names_volume: bool = True; book_is_comic: bool = False; authors: tuple[str, ...] = ()`
  - `@dataclass(frozen=True) class ReleaseMatch: volume: Literal["match", "other", "unknown"]; other_volume: int | None; medium: Literal["ebook", "comic", "audio", "video", "unknown"]; compatible: bool; fan_marker: bool` with `to_payload() -> dict[str, object]` returning `{"v": 1, "volume", "other_volume", "medium", "compatible", "fan_marker"}`
  - `is_comic_book(title: object, series_name: object) -> bool`
  - `build_ranking_identity(*, title: object, current_query: object, series_name: object, series_position: object, authors: object) -> RankingIdentity`
  - `classify_release(*, name: object, formats: Sequence[object], content_type: object, release_author: object, identity: RankingIdentity | None) -> ReleaseMatch` (sanitises `identity`; never raises)

- [ ] **Step 1: Write the failing tests**

**Replace** in `tests/core/test_search_queries.py`:

```python
import math

import pytest

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

with:

```python
import math
from typing import Any

import pytest

from shelfmark.core.search_queries import (
    RankingIdentity,
    ReleaseMatch,
    SearchIdentity,
    any_identity_hit,
    build_fallback_queries,
    build_ranking_identity,
    build_search_identity,
    classify_release,
    clean_query,
    is_identity_hit,
    normalize_position,
)
```

**Append** to the end of `tests/core/test_search_queries.py` (after two blank lines):

```python
# --- Release ranking (classify_release) ----------------------------------------------


def _ranking(
    title: str, series: str | None, position: object, authors: tuple[str, ...] = ()
) -> RankingIdentity:
    return build_ranking_identity(
        title=title,
        current_query=title,
        series_name=series,
        series_position=position,
        authors=list(authors),
    )


DXD5_RANK = _ranking(
    f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp", DXD, 5, ("Ichiei Ishibumi",)
)
OVERLORD2_RANK = _ranking(f"{OL}, Vol. 2: The Dark Warrior", OL, 2, ("Kugane Maruyama",))
LEVIATHAN_RANK = _ranking("Leviathan Wakes", "The Expanse", 1, ("James S. A. Corey",))
CALIBAN_RANK = _ranking("Caliban's War", "The Expanse", 2, ("James S. A. Corey",))


def _classify(
    name: object,
    identity: Any,
    *,
    formats: Any = (),
    content_type: object = None,
    author: object = None,
) -> ReleaseMatch:
    """identity and formats are Any so the junk-input tests can pass junk."""
    return classify_release(
        name=name,
        formats=formats,
        content_type=content_type,
        release_author=author,
        identity=identity,
    )


def _volume(name: str, identity: RankingIdentity) -> tuple[str, int | None]:
    match = _classify(name, identity)
    return match.volume, match.other_volume


class TestRankingIdentity:
    def test_it_resolves_series_and_position_like_the_ladder(self):
        assert DXD5_RANK == RankingIdentity(
            series_key="High School DxD",
            position=5,
            title_tokens=(
                "high",
                "school",
                "dxd",
                "5",
                "hellcat",
                "underworld",
                "training",
                "camp",
            ),
            title_names_volume=True,
            book_is_comic=False,
            authors=("Ichiei Ishibumi",),
        )

    def test_a_natural_title_series_book(self):
        assert LEVIATHAN_RANK.series_key == "The Expanse"
        assert LEVIATHAN_RANK.position == 1
        assert LEVIATHAN_RANK.title_names_volume is False

    def test_a_graphic_novel_request_is_a_comic(self):
        assert _ranking("Watchmen (Graphic Novel)", None, None).book_is_comic is True
        assert _ranking("Overlord (Manga), Vol. 2", "Overlord (Manga)", 2).book_is_comic is True

    def test_comical_is_not_a_comic(self):
        assert _ranking("The Comical Adventures", None, None).book_is_comic is False
        assert _ranking("Mangarama", "Comicality", 1).book_is_comic is False

    def test_junk_authors_are_dropped(self):
        identity = build_ranking_identity(
            title="Dune",
            current_query="Dune",
            series_name=None,
            series_position=None,
            authors=["Frank Herbert", None, "  ", 5],
        )
        assert identity.authors == ("Frank Herbert",)
        assert _ranking("Dune", None, None).authors == ()


class TestRankingVolume:
    def test_the_requested_volume_is_a_match(self):
        name = (
            "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp "
            "by Ichiei Ishibumi [ENG / EPUB]"
        )
        assert _volume(name, DXD5_RANK) == ("match", None)

    def test_volume_25_is_another_volume(self):
        name = "High School DxD - Volume 25 by Ichiei Ishibumi [ENG / EPUB]"
        assert _volume(name, DXD5_RANK) == ("other", 25)

    def test_a_bracketed_series_number_is_a_match(self):
        name = "Kugane Maruyama - [Overlord 02] - The Dark Warrior (epub)"
        assert _volume(name, OVERLORD2_RANK) == ("match", None)

    def test_a_hash_number_names_another_volume(self):
        assert _volume("Overlord #3 EPUB", OVERLORD2_RANK) == ("other", 3)

    def test_underscore_scene_names_are_explicit_volume_syntax(self):
        assert _volume("Overlord_Vol_02_2018_Retail_EPUB", OVERLORD2_RANK) == ("match", None)
        assert _volume("Overlord_Vol_03_2018_Retail_EPUB", OVERLORD2_RANK) == ("other", 3)

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("Overlord v02 (2016) (Digital) (danke-Empire)", ("match", None)),
            ("Yen.Press-Overlord.Vol.02.2016.Retail.eBook-BitBook", ("match", None)),
            ("Overlord 02 - The Dark Warrior (epub)", ("match", None)),
            ("Overlord 02 (2016)", ("match", None)),
            ("Overlord 02 2016 epub", ("match", None)),
            ("Overlord 02 epub", ("match", None)),
            ("Overlord 02", ("match", None)),
            ("Overlord Book 3 EPUB", ("other", 3)),
            ("Overlord Volume 10", ("other", 10)),
        ],
    )
    def test_every_explicit_form(self, name, expected):
        assert _volume(name, OVERLORD2_RANK) == expected

    def test_another_numbered_expanse_book_is_another_volume(self):
        name = "The Expanse Book 2 Caliban's War by James S. A. Corey EPUB"
        assert _volume(name, LEVIATHAN_RANK) == ("other", 2)

    @pytest.mark.parametrize(
        "name",
        [
            "High School DxD [5] (epub)",
            "High School DxD - 5 (epub)",
            "High School DxD (epub)",
            "Overlord 02 The Dark Warrior",
        ],
    )
    def test_a_bare_number_is_not_volume_syntax(self, name):
        identity = DXD5_RANK if "DxD" in name else OVERLORD2_RANK
        assert _volume(name, identity) == ("unknown", None)

    def test_a_page_count_is_not_another_volume(self):
        for name in (
            "The Expanse Leviathan Wakes [320] EPUB",
            "The Expanse Leviathan Wakes - 451 pages EPUB",
        ):
            assert _volume(name, CALIBAN_RANK) == ("unknown", None)
            # The page count does not veto the natural-title match either.
            assert _volume(name, LEVIATHAN_RANK) == ("match", None)

    def test_a_standalone_book_has_no_volume(self):
        standalone = _ranking("The Housemaid", None, None)
        assert _volume("The Housemaid Vol. 2 (epub)", standalone) == ("unknown", None)


class TestRankingCollections:
    @pytest.mark.parametrize(
        "name",
        [
            "Overlord Vol. 2 Omnibus EPUB",
            "Overlord Box Set Vol. 2",
            "Overlord Boxed Set v02",
            "Overlord The Complete Series Vol. 2",
            "Overlord Collection Vol. 2",
            "Overlord Trilogy Vol. 2",
            "Overlord Duology v02",
            "Overlord Quartet v02",
            "Overlord Books 1-3",
            "Overlord Vol. 1-3",
            "Overlord Vols 2-4",
            "Overlord Vol. 2 & 3",
            "Overlord Vol. 2 and 3",
            "Overlord v02-v03",
            "Overlord Vol. 2, 3",
            "Overlord Vol. 2 Vol. 3",
            "Overlord 1-3 (epub)",
        ],
    )
    def test_collection_evidence_is_unknown(self, name):
        assert _volume(name, OVERLORD2_RANK) == ("unknown", None)

    def test_contributor_separators_are_not_a_bundle(self):
        for name in (
            "Leviathan Wakes 2nd edition EPUB",
            "Leviathan Wakes James S. A. Corey & Daniel Abraham EPUB",
            "Leviathan Wakes James S. A. Corey &amp; Daniel Abraham EPUB",
            "Leviathan Wakes Corey / Abraham + Bonus EPUB",
        ):
            assert _volume(name, LEVIATHAN_RANK) == ("match", None)

    def test_the_same_number_twice_is_one_volume(self):
        assert _volume("Overlord Vol. 2 [Overlord 02]", OVERLORD2_RANK) == ("match", None)


class TestRankingNaturalTitles:
    def test_the_title_words_name_the_book(self):
        assert _volume("Leviathan Wakes (The Expanse #1) epub", LEVIATHAN_RANK) == (
            "match",
            None,
        )
        assert _volume("James S A Corey - Leviathan Wakes (epub)", LEVIATHAN_RANK) == (
            "match",
            None,
        )

    def test_explicit_syntax_naming_another_number_is_not_the_book(self):
        assert _volume("Leviathan Wakes #2 epub", LEVIATHAN_RANK) == ("unknown", None)

    def test_another_title_in_the_series_is_not_the_book(self):
        assert _volume("The Expanse - Calibans War (epub)", LEVIATHAN_RANK) == ("unknown", None)

    def test_a_title_made_of_series_words_never_matches_by_title(self):
        hunger = _ranking("The Hunger Games", "The Hunger Games", 1)
        assert _volume("The Hunger Games (epub)", hunger) == ("unknown", None)
        assert _volume("The Hunger Games #1 (epub)", hunger) == ("match", None)


class TestRankingMedium:
    def test_episodes_in_a_light_novel_title_is_not_video(self):
        match = _classify(
            "Overlord Vol. 2: Episodes of the Kingdom EPUB", OVERLORD2_RANK, formats=["epub"]
        )
        assert (match.medium, match.compatible) == ("ebook", True)

    @pytest.mark.parametrize(
        "name",
        [
            "Yen.Press-Overlord.Vol.02.Manga.2022.Hybrid.Comic.eBook-BitBook",
            "Yen.Press-Overlord.The.Undead.King.Oh.Vol.02.2022.Hybrid.Comic.eBook-BitBook",
            "Overlord Vol. 2 (Graphic Novel) (epub)",
        ],
    )
    def test_manga_names_are_an_incompatible_comic(self, name):
        match = _classify(name, OVERLORD2_RANK)
        assert (match.medium, match.compatible) == ("comic", False)

    def test_a_comic_is_compatible_with_a_comic_request(self):
        manga2 = _ranking("Overlord (Manga), Vol. 2", "Overlord (Manga)", 2)
        match = _classify("Yen.Press-Overlord.Vol.02.Manga.2022.Hybrid.Comic.eBook", manga2)
        assert (match.medium, match.compatible, match.volume) == ("comic", True, "match")

    def test_a_cbz_declared_clean_title_is_a_comic(self):
        match = _classify("Overlord v02", OVERLORD2_RANK, formats=["cbz"])
        assert (match.medium, match.compatible) == ("comic", False)

    def test_an_audiobook_category_clean_title_is_audio(self):
        match = _classify("Overlord v02", OVERLORD2_RANK, content_type="audiobook")
        assert (match.medium, match.compatible) == ("audio", False)

    def test_a_declared_audio_format_is_audio(self):
        for fmt in ("m4b", "MP3", "m4a", "flac", "aac"):
            assert _classify("Overlord v02", OVERLORD2_RANK, formats=[fmt]).medium == "audio"

    def test_a_declared_format_beats_name_words(self):
        match = _classify("Overlord Manga Vol. 2", OVERLORD2_RANK, formats=["m4b"])
        assert match.medium == "audio"

    @pytest.mark.parametrize(
        "name",
        [
            "Overlord S02E05 1080p WEB-DL x264",
            "Overlord.2160p.BDRip.HEVC",
            "Overlord 720p h.264 mkv",
            "Overlord.480p.WEBRip.avi",
            "Overlord x265 mp4",
        ],
    )
    def test_technical_video_markers_are_video(self, name):
        match = _classify(name, OVERLORD2_RANK)
        assert (match.medium, match.compatible) == ("video", False)

    def test_audio_words_in_the_name(self):
        for name in ("Overlord Vol 2 [ENG / M4B]", "Overlord v02 MP3", "Overlord Audiobook v02"):
            assert _classify(name, OVERLORD2_RANK).medium == "audio"

    def test_a_medium_word_that_is_the_books_own_title_word_does_not_count(self):
        manga_guide = _ranking("The Manga Guide to Physics", None, None)
        assert _classify("The Manga Guide to Physics (epub)", manga_guide).medium == "unknown"
        mp3_book = _ranking("MP3 Players For Dummies", None, None)
        assert _classify("MP3 Players For Dummies", mp3_book).medium == "unknown"

    def test_ebook_evidence(self):
        assert _classify("Overlord v02", OVERLORD2_RANK, formats=["epub"]).medium == "ebook"
        assert _classify("Overlord v02", OVERLORD2_RANK, content_type="book").medium == "ebook"
        assert _classify("Overlord v02", OVERLORD2_RANK, content_type="ebook").medium == "ebook"
        assert _classify("Overlord v02", OVERLORD2_RANK).medium == "unknown"
        assert _classify("Overlord v02", OVERLORD2_RANK).compatible is True


class TestRankingAuthorAndFanMarker:
    def test_an_author_conflict_downgrades_a_match(self):
        name = "Overlord v02 (epub)"
        assert _classify(name, OVERLORD2_RANK, author="Kugane Maruyama").volume == "match"
        assert _classify(name, OVERLORD2_RANK, author="Someone Else").volume == "unknown"

    def test_an_author_conflict_leaves_another_volume_alone(self):
        match = _classify("Overlord v03 (epub)", OVERLORD2_RANK, author="Someone Else")
        assert (match.volume, match.other_volume) == ("other", 3)

    def test_a_missing_author_is_neutral(self):
        for author in (None, "", "   ", 42):
            assert _classify("Overlord v02", OVERLORD2_RANK, author=author).volume == "match"

    @pytest.mark.parametrize(
        "name",
        [
            "High School DxD Vol 5 Baka-Tsuki",
            "High School DxD Vol 5 (Baka Tsuki)",
            "High School DxD Vol 5 [Fan TL]",
            "High School DxD Vol 5 fan translation",
            "High School DxD Vol 5 fan-translated",
            "High School DxD Vol 5 Scanlation",
        ],
    )
    def test_the_fan_marker(self, name):
        assert _classify(name, DXD5_RANK).fan_marker is True

    @pytest.mark.parametrize(
        "name",
        [
            "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp by Ichiei Ishibumi [ENG / EPUB]",
            "High School DxD Vol 5 Retail",
            "High School DxD Vol 5 fantastic translation",
        ],
    )
    def test_no_fan_marker(self, name):
        assert _classify(name, DXD5_RANK).fan_marker is False


class TestRankingJunk:
    @pytest.mark.parametrize("name", [None, "", "   ", 5, object(), ["Overlord v02"]])
    def test_a_junk_name_is_fully_unknown(self, name):
        assert _classify(name, OVERLORD2_RANK, formats=["m4b"]) == ReleaseMatch(
            "unknown", None, "unknown", compatible=True, fan_marker=False
        )

    @pytest.mark.parametrize("formats", [None, 5, "epub", [None, 3, object()]])
    def test_junk_formats_are_ignored(self, formats):
        match = _classify("Overlord v02", OVERLORD2_RANK, formats=formats)
        assert (match.volume, match.medium) == ("match", "unknown")

    @pytest.mark.parametrize("identity", [None, "Overlord", 5])
    def test_no_usable_identity_decides_no_volume(self, identity):
        match = _classify("Overlord v02 (epub)", identity, content_type=7)
        assert match.volume == "unknown"
        assert match.medium == "unknown"

    def test_the_payload_is_versioned(self):
        assert _classify("Overlord v03", OVERLORD2_RANK).to_payload() == {
            "v": 1,
            "volume": "other",
            "other_volume": 3,
            "medium": "unknown",
            "compatible": True,
            "fan_marker": False,
        }

    def test_classify_release_never_raises_on_a_hostile_identity(self):
        hostile = RankingIdentity(series_key="(", position=2, title_tokens=("(",))
        assert _classify("Overlord ( v02", hostile).volume in {"match", "unknown"}


class TestRankingReviewFocus:
    """Inputs the spec implies but its test list does not name (plan Review Focus)."""

    @pytest.mark.parametrize("author", ["Unknown", "unknown", "Various", "Anonymous", "N/A"])
    def test_a_placeholder_author_is_missing_not_a_conflict(self, author):
        # IRC's parser sets "Unknown" when a line has no "Author - Title" split.
        assert _classify("Overlord v02 (epub)", OVERLORD2_RANK, author=author).volume == "match"

    @pytest.mark.parametrize(
        "author", ["Maruyama Kugane", "Kugane Maruyama, so-bin", "MARUYAMA, Kugane", "K. Maruyama"]
    )
    def test_name_order_and_extra_contributors_are_not_a_conflict(self, author):
        assert _classify("Overlord v02 (epub)", OVERLORD2_RANK, author=author).volume == "match"

    def test_initials_alone_do_not_count_as_a_shared_author(self):
        corey = _ranking("Leviathan Wakes", "The Expanse", 1, ("James S. A. Corey",))
        match = _classify("Leviathan Wakes (epub)", corey, author="S. A. Smith")
        assert match.volume == "unknown"

    def test_escaped_names_and_version_tags_make_no_volume_or_bundle(self):
        assert _volume("Overlord Vol. 2 (v1.1) (epub)", OVERLORD2_RANK) == ("match", None)
        assert _volume("Overlord Vol. 2 [v2.0] Kugane &amp; so-bin", OVERLORD2_RANK) == (
            "match",
            None,
        )

    @pytest.mark.parametrize(
        "name",
        [
            "High School DxD Vol. 5.5 (epub)",
            "High School DxD Vol. 5a (epub)",
            "High School DxD v05.5 (epub)",
            "High School DxD #5.5",
        ],
    )
    def test_a_fractional_or_lettered_volume_is_unknown(self, name):
        assert _volume(name, DXD5_RANK) == ("unknown", None)

    def test_a_year_after_the_volume_is_not_a_fraction(self):
        name = "Seven.Seas-High.School.DxD.Vol.05.2016.Retail.eBook-BitBook"
        assert _volume(name, DXD5_RANK) == ("match", None)


class TestRankingReviewFindings:
    """Cases from the Codex review of the plan (2026-10-08)."""

    @pytest.mark.parametrize("name", ["Overlord Vol 2.125 (epub)", "Overlord Vol 3.141 (epub)"])
    def test_any_decimal_suffix_is_not_a_whole_volume(self, name):
        assert _volume(name, OVERLORD2_RANK) == ("unknown", None)

    @pytest.mark.parametrize("name", ["Overlord Vol.02.2016 (epub)", "Overlord Vol 2 2016 (epub)"])
    def test_a_year_after_the_volume_keeps_it_whole(self, name):
        assert _volume(name, OVERLORD2_RANK) == ("match", None)

    @pytest.mark.parametrize("name", ["Overlord Vol 2/3 (epub)", "Overlord Vol 2 / 3 (epub)"])
    def test_a_slash_between_volume_numbers_is_a_collection(self, name):
        assert _volume(name, OVERLORD2_RANK) == ("unknown", None)

    def test_a_slash_between_contributors_is_harmless(self):
        name = "Overlord Vol. 2 Kugane Maruyama / so-bin (epub)"
        assert _volume(name, OVERLORD2_RANK) == ("match", None)

    @pytest.mark.parametrize(
        "name",
        [
            "Leviathan Wakes & Calibans War EPUB",
            "Leviathan Wakes and Calibans War EPUB",
            "Leviathan Wakes / Calibans War EPUB",
            "Leviathan Wakes + Calibans War EPUB",
            "Leviathan Wakes &amp; Calibans War EPUB",
            "Corey & Abraham - Leviathan Wakes & Calibans War (epub)",
        ],
    )
    def test_a_conjunction_joining_another_title_is_unknown(self, name):
        assert _volume(name, LEVIATHAN_RANK) == ("unknown", None)

    @pytest.mark.parametrize(
        "name",
        [
            "Leviathan Wakes James S. A. Corey & Daniel Abraham EPUB",
            "Leviathan Wakes & James S. A. Corey (epub)",
            "Daniel Abraham & James S. A. Corey - Leviathan Wakes (epub)",
            "Leviathan Wakes & EPUB",
        ],
    )
    def test_a_conjunction_before_a_contributor_or_nothing_is_harmless(self, name):
        assert _volume(name, LEVIATHAN_RANK) == ("match", None)

    def test_volume_zero_is_never_another_volume(self):
        match = _classify("Overlord Vol 0 [MP3]", OVERLORD2_RANK)
        assert (match.volume, match.other_volume, match.medium) == ("unknown", None, "audio")
        assert match.to_payload()["other_volume"] is None

    def test_a_requested_volume_zero_still_matches(self):
        prequel = _ranking("Overlord (Light Novel), Vol. 0: Prologue", OL, 0)
        assert _volume("Overlord Vol. 0 Prologue (epub)", prequel) == ("match", None)

    @pytest.mark.parametrize(
        "name",
        [
            "Overlord Vol. 2 ISBN 978-1-9753-0123-4 (epub)",
            "Overlord v02 (2016-05-24) (epub)",
            "Overlord Vol. 2 [1-2 MB] (epub)",
        ],
    )
    def test_isbns_dates_and_sizes_are_not_volume_ranges(self, name):
        assert _volume(name, OVERLORD2_RANK) == ("match", None)

    def test_a_series_number_range_is_still_a_collection(self):
        assert _volume("The Expanse 1-3 Leviathan Wakes (epub)", LEVIATHAN_RANK) == (
            "unknown",
            None,
        )

    def test_a_shared_given_name_is_not_the_same_author(self):
        match = _classify("Leviathan Wakes (epub)", LEVIATHAN_RANK, author="James Patterson")
        assert match.volume == "unknown"

    @pytest.mark.parametrize(
        "author", ["Corey, James S A", "James S. A. Corey", "J. S. A. Corey & Daniel Abraham"]
    )
    def test_the_same_surname_is_the_same_author(self, author):
        assert _classify("Leviathan Wakes (epub)", LEVIATHAN_RANK, author=author).volume == "match"

    def test_reordered_names_are_the_same_author(self):
        reordered = _classify("Overlord v02 (epub)", OVERLORD2_RANK, author="Maruyama Kugane")
        assert reordered.volume == "match"

    def test_an_author_field_holding_the_series_name_is_not_an_author(self):
        assert _classify("Overlord - Volume 2.epub", OVERLORD2_RANK, author="Overlord").volume == (
            "match"
        )

    @pytest.mark.parametrize("sep", [" ", ".", "-", "_", ""])
    def test_fan_tl_and_baka_tsuki_with_any_separator(self, sep):
        assert _classify(f"DxD Vol 5 [Fan{sep}TL]", DXD5_RANK).fan_marker is True
        assert _classify(f"DxD Vol 5 Baka{sep}Tsuki", DXD5_RANK).fan_marker is True

    @pytest.mark.parametrize("sep", [" ", ".", "-", "_"])
    def test_fan_translation_with_any_separator(self, sep):
        assert _classify(f"DxD Vol 5 fan{sep}translation", DXD5_RANK).fan_marker is True
        assert _classify(f"DxD Vol 5 fan{sep}translated", DXD5_RANK).fan_marker is True

    @pytest.mark.parametrize(
        "identity",
        [
            RankingIdentity(series_key=None, position="2", title_tokens=None, authors=None),  # type: ignore[arg-type]
            RankingIdentity(
                series_key="Overlord",
                position=True,  # type: ignore[arg-type]
                title_tokens=("overlord", None, 3),  # type: ignore[arg-type]
                authors=(None, 5, "Kugane Maruyama"),  # type: ignore[arg-type]
                title_names_volume=None,  # type: ignore[arg-type]
                book_is_comic="yes",  # type: ignore[arg-type]
            ),
            RankingIdentity(series_key="Overlord", position=-1, title_tokens="overlord"),  # type: ignore[arg-type]
        ],
    )
    def test_a_malformed_identity_never_raises(self, identity):
        for author in (None, "Someone Else", "Kugane Maruyama"):
            match = _classify(
                "Yen.Press-Overlord.Vol.02.Manga.2022.Hybrid.Comic.eBook-BitBook",
                identity,
                author=author,
            )
            assert match.volume == "unknown"
            assert (match.medium, match.compatible) == ("comic", False)

    def test_malformed_tokens_and_authors_are_dropped_not_fatal(self):
        identity = RankingIdentity(
            series_key="The Expanse",
            position=1,
            title_tokens=("leviathan", None, "wakes", 7),  # type: ignore[arg-type]
            title_names_volume=False,
            authors=("James S. A. Corey", None),  # type: ignore[arg-type]
        )
        match = _classify("Leviathan Wakes (epub)", identity, author="Corey, James")
        assert match.volume == "match"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_search_queries.py -q -n0`
Expected: FAIL — collection error `ImportError: cannot import name 'RankingIdentity' from 'shelfmark.core.search_queries'`.

- [ ] **Step 3: Implement the classifier**

**Replace** in `shelfmark/core/search_queries.py`:

```python
from typing import TYPE_CHECKING
```

with:

```python
from typing import TYPE_CHECKING, Literal
```

**Replace** in `shelfmark/core/search_queries.py`:

```python
    from collections.abc import Iterable
```

with:

```python
    from collections.abc import Iterable, Sequence
```

**Append** to the end of `shelfmark/core/search_queries.py` (after two blank lines):

```python
# --- Release ranking --------------------------------------------------------------------
#
# A classifier separate from ``is_identity_hit``: that predicate only decides when the
# fallback ladder may stop, and keeps its own, deliberately broader rules. This one decides
# how the release list is ordered, so it acts only on strong, explicit evidence: a release
# moves up only when it names this volume in explicit volume syntax, and down only when a
# declared format or category says it is another medium, or explicit volume syntax names
# another volume. Anything ambiguous is "unknown", which keeps today's order.

RELEASE_MATCH_VERSION = 1

type Volume = Literal["match", "other", "unknown"]
type Medium = Literal["ebook", "comic", "audio", "video", "unknown"]

_AUDIO_FORMATS = frozenset({"m4b", "mp3", "m4a", "flac", "aac"})
_COMIC_FORMATS = frozenset({"cbz", "cbr", "cb7"})
_EBOOK_FORMATS = frozenset({"epub", "mobi", "azw3", "pdf"})
# Prowlarr and Newznab report an ebook category as "book"; the other sources say "ebook".
_EBOOK_CONTENT_TYPES = frozenset({"ebook", "book"})
_ALL_FORMATS = _AUDIO_FORMATS | _COMIC_FORMATS | _EBOOK_FORMATS
_FORMAT_TOKENS = "|".join(sorted(_ALL_FORMATS))

# Technical video markers only: plain words such as "episode" say nothing about the medium
# ("Overlord Vol. 2: Episodes of the Kingdom" is a light novel).
_RANK_VIDEO_RE = re.compile(
    r"\b(?:2160p|1080p|720p|480p|x264|x265|h\.?264|h\.?265|hevc|mkv|mp4|avi"
    r"|bdrip|web-?dl|webrip|s\d{1,2}e\d{1,3})\b",
    re.IGNORECASE,
)
_RANK_AUDIO_WORD_RE = re.compile(r"\b(?:m4b|mp3|audiobook)\b", re.IGNORECASE)
_RANK_COMIC_WORD_RE = re.compile(r"\b(?:manga|comics?|graphic[\s.-]+novels?)\b", re.IGNORECASE)
# Matched after "_" became a space, so "Fan_TL" and "Baka_Tsuki" count too.
_FAN_MARKER_RE = re.compile(
    r"\b(?:fan[\s.-]?tl|fan[\s.-]translation|fan[\s.-]translated|baka[\s.-]?tsuki"
    r"|scanlation)\b",
    re.IGNORECASE,
)

# Numbers that are never volumes, masked before any volume parsing: ISBNs, dates and file
# sizes ("978-1-9753-0...", "2016-05-24", "1-2 MB", "620.5 MB").
_RANK_NOISE_RES = (
    re.compile(r"\b97[89](?:[\s-]?\d){10}\b"),
    re.compile(r"\b(?:19|20)\d{2}-\d{1,2}-\d{1,2}\b"),
    re.compile(
        r"\b\d+(?:[.,]\d+)?(?:\s*(?:-|–|to)\s*\d+(?:[.,]\d+)?)?\s*(?:[kmgt]i?b|bytes?)\b",
        re.IGNORECASE,
    ),
)

# Explicit volume syntax: "Vol N", "Vol. N", "Volume N", "Vols N", "vNN", "#N", "Book N"
# (with ".", "_", " " or "-" as separators; "_" is a space by the time these run). N is
# one to three digits; bare "[N]" and "- N" are not volume syntax here.
_RANK_VOLUME_RES = (
    re.compile(r"\bvol(?:ume)?s?\b\.?[\s.-]*(\d{1,3})(?!\d)", re.IGNORECASE),
    re.compile(r"\bv(\d{1,3})(?!\d)", re.IGNORECASE),
    re.compile(r"#(\d{1,3})(?!\d)"),
    re.compile(r"\bbook[\s.-]+(\d{1,3})(?!\d)", re.IGNORECASE),
)
# A number that is not a whole volume: a decimal suffix of any length ("2.5", "2.125") or
# a letter suffix ("5a"). A four-digit year after a dot ("Vol.02.2016") is not a decimal.
# Ambiguous, so the release's volume is unknown.
_RANK_PARTIAL_VOLUME_RE = re.compile(r"\.(?!(?:19|20)\d{2}(?!\d))\d+|[^\W\d_]")
# A range or list separator between two volume numbers: "5-6", "5 & 6", "5 to 7", "2/3".
_RANK_RANGE_SEPARATOR = r"\s*(?:[-–—~&+,/]|\bto\b|\band\b|\bthrough\b)\s*"
# A second volume right after the first: "5-6", "5 & 6", "v05-v07", "1, 2", "2 / 3".
_RANK_VOLUME_LIST_RE = re.compile(
    _RANK_RANGE_SEPARATOR + r"(?:vol(?:ume)?s?\b\.?\s*|v|#|book\s+)?\d{1,3}(?![\d.]|[^\W\d_])",
    re.IGNORECASE,
)
# Collection evidence: several books in one release. Separators between names ("Corey &
# Abraham", "Author / Illustrator") are not; numbers count only in volume context.
_RANK_COLLECTION_RE = re.compile(
    r"\b(?:omnibus|box(?:ed)?[\s.-]*set|complete[\s.-]+series|collection|trilogy|duology"
    r"|quartet)\b"
    r"|\bbooks[\s.-]*\d{1,3}" + _RANK_RANGE_SEPARATOR + r"\d{1,3}(?!\d)",
    re.IGNORECASE,
)
# A conjunction right after the requested title: "Leviathan Wakes & Caliban's War".
_RANK_CONJUNCTION_RE = re.compile(r"\s*(?:&|\+|/|\band\b)\s*", re.IGNORECASE)
# Where a run of title-like words ends: a bracket, a parenthesis or " - ".
_RANK_SEGMENT_END_RE = re.compile(r"[\[\](){}]|\s-\s")
# Author names that say nothing about who wrote the book.
_PLACEHOLDER_AUTHORS = frozenset({"unknown", "various", "anonymous", "n/a", "na", "none"})
# Separators between contributors in one author field ("Corey, James S A" is split too:
# each side is then compared on its own).
_AUTHOR_SPLIT_RE = re.compile(r"\s*(?:[,;&+/]|\band\b)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class RankingIdentity:
    """The requested book, as the release ranking sees it (one per request)."""

    series_key: str = ""
    position: int | None = None
    title_tokens: tuple[str, ...] = ()
    title_names_volume: bool = True
    book_is_comic: bool = False
    authors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseMatch:
    """How one release relates to the requested book (see ``classify_release``)."""

    volume: Volume
    other_volume: int | None  # set only when volume == "other"; never 0
    medium: Medium
    compatible: bool  # the medium suits the requested book
    fan_marker: bool  # the name explicitly says fan translation

    def to_payload(self) -> dict[str, object]:
        """The versioned ``extra["release_match"]`` value the frontend parses."""
        return {
            "v": RELEASE_MATCH_VERSION,
            "volume": self.volume,
            "other_volume": self.other_volume,
            "medium": self.medium,
            "compatible": self.compatible,
            "fan_marker": self.fan_marker,
        }


_UNKNOWN_MATCH = ReleaseMatch("unknown", None, "unknown", compatible=True, fan_marker=False)


def is_comic_book(title: object, series_name: object) -> bool:
    """Whether the requested book is itself a manga or comic (word-bounded, so not "Comical")."""
    title_text = title if isinstance(title, str) else ""
    series_text = series_name if isinstance(series_name, str) else ""
    return _RANK_COMIC_WORD_RE.search(f"{title_text} {series_text}") is not None


def _clean_strings(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(v for v in values if isinstance(v, str) and v.strip())


def build_ranking_identity(
    *,
    title: object,
    current_query: object,
    series_name: object,
    series_position: object,
    authors: object,
) -> RankingIdentity:
    """The ranking identity, resolved exactly as the ladder resolves series and position."""
    search_identity = build_search_identity(
        title=title,
        current_query=current_query,
        series_name=series_name,
        series_position=series_position,
    )
    return RankingIdentity(
        series_key=search_identity.series_key,
        position=search_identity.position,
        title_tokens=search_identity.title_tokens,
        title_names_volume=search_identity.title_names_volume,
        book_is_comic=is_comic_book(title, series_name),
        authors=_clean_strings(authors),
    )


def _sanitize_identity(identity: object) -> RankingIdentity:
    """A well-typed copy of ``identity``: junk fields become their empty defaults."""
    if not isinstance(identity, RankingIdentity):
        return RankingIdentity()
    position = identity.position
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        position = None
    series_key = identity.series_key if isinstance(identity.series_key, str) else ""
    return RankingIdentity(
        series_key=series_key,
        position=position,
        title_tokens=tuple(t.casefold() for t in _clean_strings(identity.title_tokens)),
        title_names_volume=identity.title_names_volume is not False,
        book_is_comic=identity.book_is_comic is True,
        authors=_clean_strings(identity.authors),
    )


def _declared_formats(formats: object) -> set[str]:
    if not isinstance(formats, (list, tuple)):
        return set()
    return {f.strip().casefold() for f in formats if isinstance(f, str) and f.strip()}


def _name_word_counts(pattern: re.Pattern[str], text: str, own_tokens: set[str]) -> bool:
    """Whether ``pattern`` finds a word in ``text`` that is not one of the book's own words."""
    for match in pattern.finditer(text):
        words = set(_tokens(match.group(0)))
        if not words <= own_tokens:
            return True
    return False


def _medium(text: str, formats: set[str], content_type: str, own_tokens: set[str]) -> Medium:
    if content_type == "audiobook" or formats & _AUDIO_FORMATS:
        return "audio"
    if formats & _COMIC_FORMATS:
        return "comic"
    if _RANK_VIDEO_RE.search(text):
        return "video"
    if _name_word_counts(_RANK_AUDIO_WORD_RE, text, own_tokens):
        return "audio"
    if _name_word_counts(_RANK_COMIC_WORD_RE, text, own_tokens):
        return "comic"
    if formats & _EBOOK_FORMATS or content_type in _EBOOK_CONTENT_TYPES:
        return "ebook"
    return "unknown"


def _series_volume_res(series_tokens: tuple[str, ...]) -> list[re.Pattern[str]]:
    if not series_tokens:
        return []
    last = re.escape(series_tokens[-1])
    return [
        # "[Overlord 02]" (and "[Overlord - Volume 02]", which "Volume" already covers).
        re.compile(rf"\b{last}(?:\s+|[.-])(\d{{1,3}})\s*\]"),
        # "Overlord 02" followed by " - ", "]", "(", a year, a format or the end. One
        # separator only: "High School DxD - 5" is a bare "- N", not volume syntax.
        re.compile(
            rf"\b{last}(?:\s+|[.-])(\d{{1,3}})(?=\s+-\s|\s*\]|\s*\(|[\s.-]+(?:19|20)\d{{2}}(?!\d)"
            rf"|[\s.-]+(?:{_FORMAT_TOKENS})\b|\s*$)"
        ),
    ]


def _explicit_volumes(text: str, series_tokens: tuple[str, ...]) -> set[int] | None:
    """Volume numbers ``text`` names in explicit syntax; None when one is not a whole volume."""
    numbers: set[int] = set()
    for pattern in [*_RANK_VOLUME_RES, *_series_volume_res(series_tokens)]:
        for match in pattern.finditer(text):
            if _RANK_PARTIAL_VOLUME_RE.match(text, match.end(1)):
                return None
            numbers.add(int(match.group(1)))
    return numbers


def _has_volume_list(text: str, series_tokens: tuple[str, ...]) -> bool:
    """A range or list of volume numbers right after a volume marker or the series name."""
    patterns = list(_RANK_VOLUME_RES)
    if series_tokens:
        last = re.escape(series_tokens[-1])
        patterns.append(re.compile(rf"\b{last}(?:\s+|[.-])(\d{{1,3}})(?!\d)"))
    for pattern in patterns:
        for match in pattern.finditer(text):
            if _RANK_VOLUME_LIST_RE.match(text, match.end(1)):
                return True
    return False


def _token_spans(text: str) -> list[tuple[str, int]]:
    return [(m.group(0), m.end()) for m in _TOKEN_RE.finditer(text)]


def _title_joined_to_more(text: str, title_tokens: list[str], authors: tuple[str, ...]) -> bool:
    """Whether a conjunction joins the requested title to further title-like words.

    "Leviathan Wakes & Caliban's War" names two books. Not when the words after the
    conjunction are a requested author ("Leviathan Wakes & James S. A. Corey"), or when the
    conjunction sits in an author segment that a " - " closes.
    """
    wanted = set(title_tokens)
    seen: set[str] = set()
    end = None
    for token, token_end in _token_spans(text):
        if token in wanted:
            seen.add(token)
            if seen == wanted:
                end = token_end
                break
    if end is None:
        return False
    conjunction = _RANK_CONJUNCTION_RE.match(text, end)
    if conjunction is None:
        return False
    rest = text[conjunction.end() :]
    segment_end = _RANK_SEGMENT_END_RE.search(rest)
    if segment_end is not None and segment_end.group(0).strip() == "-":
        return False
    segment = rest[: segment_end.start()] if segment_end is not None else rest
    words = [t for t in _tokens(segment) if t not in _ALL_FORMATS and not t.isdigit()]
    if not words:
        return False
    author_tokens = [set(_tokens(author)) for author in authors]
    return not any(set(words) <= tokens for tokens in author_tokens)


def _volume(text: str, identity: RankingIdentity) -> tuple[Volume, int | None]:
    if not identity.series_key or identity.position is None:
        return "unknown", None
    key_tokens = significant_tokens(identity.series_key)
    if _RANK_COLLECTION_RE.search(text) or _has_volume_list(text, key_tokens):
        return "unknown", None
    numbers = _explicit_volumes(text, key_tokens)
    if numbers is None or len(numbers) > 1:
        return "unknown", None

    present = set(_tokens(text))
    has_key = bool(key_tokens) and all(token in present for token in key_tokens)
    if numbers and has_key:
        (number,) = numbers
        if number == identity.position:
            return "match", None
        # A volume 0 is a prequel or an index page as often as a volume: not evidence.
        return ("other", number) if number > 0 else ("unknown", None)

    # A series book whose title names no volume ("Leviathan Wakes", The Expanse 1) is
    # also named by its own title words, as long as no explicit volume names another
    # number and no conjunction joins it to another title. Other numbers ("2nd edition",
    # "451", a year) do not veto it.
    if identity.title_names_volume or not numbers <= {identity.position}:
        return "unknown", None
    series_words = set(key_tokens)
    title_tokens = [t for t in identity.title_tokens if t]
    if (
        title_tokens
        and all(token in present for token in title_tokens)
        and any(token not in series_words for token in title_tokens)
        and not _title_joined_to_more(text, title_tokens, identity.authors)
    ):
        return "match", None
    return "unknown", None


def _surname_candidates(name: str) -> set[str]:
    """Words of one contributor that may be a surname: the last and the first non-initial.

    "Kugane Maruyama" and "Maruyama Kugane" both give {"kugane", "maruyama"}; initials
    ("S. A.") never count.
    """
    words = [t for t in _tokens(name) if len(t) > 1]
    return {words[0], words[-1]} if words else set()


def _author_conflicts(release_author: object, identity: RankingIdentity) -> bool:
    """True only when the release names a real author who is none of the requested ones.

    Contributors are compared one by one: a release author agrees with a requested author
    when one of its surname candidates is that author's surname (the last non-initial
    word). A shared given name alone ("James Patterson" vs "James S. A. Corey") is not
    agreement. An author field made only of the book's own words (an IRC "Overlord -
    Volume 2" line puts the series where the author goes) is not an author.
    """
    if not isinstance(release_author, str):
        return False
    text = html.unescape(release_author)
    if " ".join(text.split()).casefold() in _PLACEHOLDER_AUTHORS:
        return False
    own_words = set(identity.title_tokens) | set(significant_tokens(identity.series_key))
    release_words = {t for t in _tokens(text) if len(t) > 1}
    if not release_words or release_words <= own_words:
        return False
    surnames = set()
    for author in identity.authors:
        words = [t for t in _tokens(author) if len(t) > 1]
        if words:
            surnames.add(words[-1])
    if not surnames:
        return False
    candidates = set().union(*(_surname_candidates(p) for p in _AUTHOR_SPLIT_RE.split(text)))
    return not candidates & surnames


def _ranking_text(name: str) -> str:
    # Indexers send "&amp;" for "&"; a file version tag "(v2.0)" is not a volume; "_" is
    # a separator in scene names; ISBNs, dates and sizes are never volume numbers.
    text = _VERSION_TAG_RE.sub(" ", html.unescape(name)).replace("_", " ").casefold()
    for pattern in _RANK_NOISE_RES:
        text = pattern.sub(" ", text)
    return text


def classify_release(
    *,
    name: object,
    formats: Sequence[object],
    content_type: object,
    release_author: object,
    identity: RankingIdentity | None,
) -> ReleaseMatch:
    """Classify one release for the default "best match" sort. Pure and total.

    ``name`` is the release name as the indexer gave it; ``formats`` the formats the
    source declared (``release.format`` plus ``extra["formats"]``); ``content_type`` the
    release's content type. Declared format and content type beat words in the name.
    """
    if not isinstance(name, str) or not name.strip():
        return _UNKNOWN_MATCH
    safe_identity = _sanitize_identity(identity)
    text = _ranking_text(name)
    declared = _declared_formats(formats)
    kind = content_type.strip().casefold() if isinstance(content_type, str) else ""

    medium = _medium(text, declared, kind, set(safe_identity.title_tokens))
    compatible = medium in {"ebook", "unknown"} or (
        medium == "comic" and safe_identity.book_is_comic
    )
    volume, other_volume = _volume(text, safe_identity)
    if volume == "match" and _author_conflicts(release_author, safe_identity):
        volume = "unknown"
    return ReleaseMatch(
        volume=volume,
        other_volume=other_volume,
        medium=medium,
        compatible=compatible,
        fan_marker=_FAN_MARKER_RE.search(text) is not None,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_search_queries.py -q -n0`
Expected: PASS (340 passed — 189 existing ladder/predicate tests unchanged plus 151 new).

- [ ] **Step 5: Lint and typecheck**

```bash
uv run ruff check shelfmark tests
uv run ruff format --check shelfmark/core/search_queries.py tests/core/test_search_queries.py
uv run basedpyright shelfmark/core/search_queries.py
uv run basedpyright tests/core/test_search_queries.py --skipunannotated
uv run vulture shelfmark
```
Expected: `All checks passed!`; `2 files already formatted`; `0 errors` twice; vulture prints nothing.

- [ ] **Step 6: Commit**

```bash
git add shelfmark/core/search_queries.py \
  tests/core/test_search_queries.py
git commit -m "feat(search): classify releases by volume and medium for ranking

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Prowlarr keeps the indexer's release name when `bookTitle` replaces it

**Files:**
- Modify: `shelfmark/release_sources/prowlarr/source.py` (`_prowlarr_result_to_release`, ~lines 565-574 and the `extra` dict ~line 625)
- Test: `tests/prowlarr/test_source.py` (new class before `class TestProwlarrStaleIndexerSelection:`, ~line 1482)

**Interfaces:**
- Consumes: nothing new.
- Produces: every Prowlarr `Release.extra` has `"release_name": str | None` — the raw indexer title when format detection replaced `title` with `bookTitle`, otherwise `None`. Task 4 classifies on it.

- [ ] **Step 1: Write the failing test**

**Insert before** the line `class TestProwlarrStaleIndexerSelection:` in `tests/prowlarr/test_source.py` (followed by two blank lines):

```python
class TestReleaseNameKeptWhenBookTitleReplacesIt:
    """Ranking reads the name the indexer gave, which MAM's bookTitle would otherwise hide."""

    RAW = "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp by Ichiei Ishibumi [ENG / M4B]"
    BOOK_TITLE = "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp"

    def _result(self, **overrides) -> dict:
        result = {
            "title": self.RAW,
            "bookTitle": self.BOOK_TITLE,
            "guid": "https://www.myanonamouse.net/t/1",
            "indexer": "MyAnonamouse",
            "indexerId": 1,
            "protocol": "torrent",
            "size": 1000,
            "seeders": 5,
            "leechers": 0,
            "categories": [{"id": 7020}],
        }
        result.update(overrides)
        return result

    def test_the_raw_name_is_kept_when_the_title_is_substituted(self):
        from shelfmark.release_sources.prowlarr.source import _prowlarr_result_to_release

        release = _prowlarr_result_to_release(self._result(), "ebook", enable_format_detection=True)

        assert release.title == self.BOOK_TITLE
        assert release.extra["release_name"] == self.RAW

    def test_no_release_name_when_the_title_is_not_substituted(self):
        from shelfmark.release_sources.prowlarr.source import _prowlarr_result_to_release

        plain = _prowlarr_result_to_release(self._result(), "ebook")
        no_book_title = _prowlarr_result_to_release(
            self._result(bookTitle="  "), "ebook", enable_format_detection=True
        )

        assert plain.title == self.RAW
        assert plain.extra["release_name"] is None
        assert no_book_title.title == self.RAW
        assert no_book_title.extra["release_name"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/prowlarr/test_source.py -q -n0 -k ReleaseNameKept`
Expected: FAIL — 2 failed with `KeyError: 'release_name'`.

- [ ] **Step 3: Keep the name**

**Replace** in `shelfmark/release_sources/prowlarr/source.py`:

```python
    format_detected: str | None = None
    formats: list[str] = []
    unrecognized_formats: list[str] = []
    formats_display: str | None = None
    language_detected: str | None = None
    if enable_format_detection:
        book_title = str(result.get("bookTitle") or "").strip()
        if book_title:
            title = book_title
```

with:

```python
    format_detected: str | None = None
    formats: list[str] = []
    unrecognized_formats: list[str] = []
    formats_display: str | None = None
    language_detected: str | None = None
    # The indexer's own name for the release, kept when bookTitle replaces it: release
    # ranking reads volume and format words from the name the indexer gave.
    release_name: str | None = None
    if enable_format_detection:
        book_title = str(result.get("bookTitle") or "").strip()
        if book_title:
            title = book_title
            release_name = str(raw_title)
```

**Replace** in `shelfmark/release_sources/prowlarr/source.py`:

```python
            "book_title": result.get("bookTitle"),
            "indexer_flags": indexer_flags,
            "vip": is_vip,
```

with:

```python
            "book_title": result.get("bookTitle"),
            "release_name": release_name,
            "indexer_flags": indexer_flags,
            "vip": is_vip,
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/prowlarr/test_source.py -q -n0 -k ReleaseNameKept`
Expected: PASS (2 passed)

Run: `uv run pytest tests/prowlarr -q`
Expected: PASS (602 passed, 42 skipped)

- [ ] **Step 5: Lint and typecheck**

```bash
uv run ruff check shelfmark tests
uv run ruff format --check shelfmark tests
uv run basedpyright shelfmark/release_sources/prowlarr/source.py
```
Expected: clean; `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add shelfmark/release_sources/prowlarr/source.py \
  tests/prowlarr/test_source.py
git commit -m "fix(prowlarr): keep the indexer's release name when bookTitle replaces it

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: IRC ranking evidence — the original result line and a trusted author

**Files:**
- Modify: `shelfmark/release_sources/irc/parser.py` (new code before the comment `# Words that mark an archive as holding an audiobook rather than an ebook. Multi-file`, ~line 195)
- Test: `tests/irc/test_parser.py` (new tests appended at the end)

**Interfaces:**
- Consumes: `RESULT_LINE_REGEX` (existing, same module).
- Produces: `ranking_evidence(full_line: object) -> tuple[str, str | None] | None` — `(name, author)` for a `!Bot …` result line: the line without the command and the trailing `::INFO::`/`::HASH::` metadata, and the detailed pattern's author or `None`; `None` for anything that is not a result line. Task 4 uses it for `irc` releases.

- [ ] **Step 1: Write the failing tests**

**Append** to the end of `tests/irc/test_parser.py` (after two blank lines):

```python
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "!Bsk Kugane Maruyama - Overlord 02 - The Dark Warrior.epub ::INFO:: 1.1MB",
            ("Kugane Maruyama - Overlord 02 - The Dark Warrior.epub", "Kugane Maruyama"),
        ),
        # Series-prefix layout: the parser's "author" is really the series.
        ("!Bsk Overlord - Volume 2.epub", ("Overlord - Volume 2.epub", "Overlord")),
        # Authorless layout: only the fallback pattern matches, so there is no author.
        ("!Bsk Overlord Vol 2.epub ::INFO:: 1.1MB", ("Overlord Vol 2.epub", None)),
        (
            "!Ook Andy Weir - Project Hail Mary (2021) Audiobook ::INFO:: 620.5MB",
            ("Andy Weir - Project Hail Mary (2021) Audiobook", None),
        ),
        (
            "!Bsk Ichiei Ishibumi - DxD v05.epub ::INFO:: 1.2MB ::HASH:: abc123",
            ("Ichiei Ishibumi - DxD v05.epub", "Ichiei Ishibumi"),
        ),
    ],
)
def test_ranking_evidence_is_the_line_without_command_and_metadata(line, expected):
    assert parser.ranking_evidence(line) == expected


@pytest.mark.parametrize("line", [None, 5, "", "   ", "no command here", "!Bsk", "!Bsk   "])
def test_ranking_evidence_needs_a_result_line(line):
    assert parser.ranking_evidence(line) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/irc/test_parser.py -q -n0`
Expected: FAIL — 12 failed with `AttributeError: module 'shelfmark.release_sources.irc.parser' has no attribute 'ranking_evidence'`; the 16 existing tests pass.

- [ ] **Step 3: Implement `ranking_evidence`**

**Insert before** the line `# Words that mark an archive as holding an audiobook rather than an ebook. Multi-file` in `shelfmark/release_sources/irc/parser.py` (followed by two blank lines):

```python
# What release ranking reads from a result line: the line without the "!Bot" command and
# the trailing "::INFO::"/"::HASH::" metadata.
_RANKING_COMMAND_RE = re.compile(r"^!\S+\s+")
_RANKING_TRAILER_RE = re.compile(r"\s+::(?:INFO|HASH)::.*$", re.IGNORECASE | re.DOTALL)


def ranking_evidence(full_line: object) -> tuple[str, str | None] | None:
    """The release name and author release ranking should use for a result line.

    The name is the original line minus the bot command and the trailing metadata, so the
    words the parser split off as "author" (often the series: "!Bot Overlord - Volume
    2.epub") still count. The author is trusted only when the detailed
    "Author - Title.format" pattern matched; the fallback split is a guess, so it is
    reported as missing. None when ``full_line`` is not a result line.
    """
    if not isinstance(full_line, str):
        return None
    line = full_line.strip()
    command = _RANKING_COMMAND_RE.match(line)
    if command is None:
        return None
    name = _RANKING_TRAILER_RE.sub("", line[command.end() :]).strip()
    if not name:
        return None
    detailed = RESULT_LINE_REGEX.match(line)
    return name, detailed.group(2).strip() if detailed else None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/irc -q`
Expected: PASS (64 passed)

- [ ] **Step 5: Lint and typecheck**

```bash
uv run ruff check shelfmark tests
uv run ruff format --check shelfmark tests
uv run basedpyright shelfmark/release_sources/irc/parser.py
```
Expected: clean; `0 errors`.

- [ ] **Step 6: Commit**

```bash
git add shelfmark/release_sources/irc/parser.py \
  tests/irc/test_parser.py
git commit -m "feat(irc): ranking evidence from the original result line

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `/api/releases` annotates ebook releases with `extra.release_match`

**Files:**
- Modify: `shelfmark/main.py` (imports ~lines 92 and 102-110; new helpers after `_serialize_release` ~line 1047; `api_releases` ~lines 3174, 3237 and 3285)
- Test: `tests/core/test_releases_api_release_match.py` (new)

**Interfaces:**
- Consumes: `build_ranking_identity`, `classify_release`, `RankingIdentity`, `ReleaseMatch.to_payload()` (Task 1); `extra["release_name"]` (Task 2); `irc.parser.ranking_evidence` (Task 3); `_prowlarr_result_to_release`, `IRCReleaseSource._convert_to_releases`, `parse_result_line` (tests only).
- Produces: `_release_match_payload(release: Release, identity: RankingIdentity) -> dict[str, object]`; `_annotate_release_matches(releases_data: list[dict], releases: list[Release], identity: RankingIdentity) -> None`. Response contract for the frontend: each release dict's `extra.release_match` (Task 1's payload) on ebook, metadata-provider, non-manual searches only.

- [ ] **Step 1: Write the failing tests**

**Create** `tests/core/test_releases_api_release_match.py`:

```python
"""/api/releases annotates ebook releases with how they match the requested book.

Each release from every source gets ``extra["release_match"]`` (version 1) for an ebook
search of a metadata-provider book with no manual query. Audiobook searches, manual
queries and the manual provider get none, and a classifier failure on one release only
leaves that release unannotated.
"""

from __future__ import annotations

import importlib
from unittest.mock import patch

import pytest

from shelfmark.metadata_providers import BookMetadata
from shelfmark.release_sources import Release
from shelfmark.release_sources.irc.parser import parse_result_line
from shelfmark.release_sources.irc.source import IRCReleaseSource
from shelfmark.release_sources.prowlarr.source import _prowlarr_result_to_release

DXD = "High School DxD (Light Novel)"
DXD5_TITLE = f"{DXD}, Vol. 5: Hellcat of the Underworld Training Camp"
MAM_M4B_NAME = (
    "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp "
    "by Ichiei Ishibumi [ENG / M4B]"
)
IRC_LINE = (
    "!Bsk Ichiei Ishibumi - High School DxD Vol 5 Hellcat of the Underworld Training Camp.epub"
    " ::INFO:: 1.2MB"
)


@pytest.fixture(scope="module")
def main_module():
    with patch("shelfmark.download.orchestrator.start"):
        import shelfmark.main as main

        importlib.reload(main)
        return main


@pytest.fixture
def client(main_module):
    test_client = main_module.app.test_client()
    with test_client.session_transaction() as sess:
        sess["user_id"] = "alice"
        sess["is_admin"] = False
        sess["db_user_id"] = 7
    return test_client


def _mam_m4b_release() -> Release:
    """A raw MyAnonamouse result in an ebook category, converted the way Prowlarr does."""
    return _prowlarr_result_to_release(
        {
            "title": MAM_M4B_NAME,
            "bookTitle": "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp",
            "guid": "https://www.myanonamouse.net/t/555",
            "indexer": "MyAnonamouse",
            "indexerId": 1,
            "protocol": "torrent",
            "size": 300_000_000,
            "seeders": 10,
            "leechers": 0,
            "categories": [{"id": 7020}],
        },
        "ebook",
        enable_format_detection=True,
    )


def _other_volume_release() -> Release:
    return Release(
        source="prowlarr",
        source_id="p-25",
        title="High School DxD - Volume 25 (epub)",
        content_type="book",
    )


def _irc_release(line: str = IRC_LINE) -> Release:
    source = IRCReleaseSource()
    source._online_servers = set()
    result = parse_result_line(line)
    assert result is not None
    return source._convert_to_releases([result], content_type="ebook")[0]


class _Source:
    def __init__(self, releases: list[Release]) -> None:
        self._releases = releases

    def search(self, book, plan, *, expand_search=False, content_type="ebook"):
        return list(self._releases)

    def get_column_config(self):
        from shelfmark.release_sources import _default_column_config

        return _default_column_config()


class _Provider:
    def __init__(self, book: BookMetadata) -> None:
        self._book = book

    def get_book(self, book_id):
        return self._book


def _dxd5_book(title: str = DXD5_TITLE) -> BookMetadata:
    return BookMetadata(
        provider="hardcover",
        provider_id="dxd5",
        title=title,
        search_title="Hellcat of the Underworld Training Camp",
        authors=["Ichiei Ishibumi"],
        series_name=DXD,
        series_position=5,
    )


def _search(
    client,
    main_module,
    query: dict[str, str],
    book: BookMetadata | None = None,
    irc_releases: list[Release] | None = None,
):
    sources = {
        "prowlarr": _Source([_mam_m4b_release(), _other_volume_release()]),
        "irc": _Source(irc_releases if irc_releases is not None else [_irc_release()]),
    }
    with (
        patch.object(main_module, "get_auth_mode", return_value="none"),
        patch("shelfmark.metadata_providers.is_provider_registered", return_value=True),
        patch("shelfmark.metadata_providers.get_provider_kwargs", return_value={}),
        patch(
            "shelfmark.metadata_providers.get_provider",
            return_value=_Provider(book or _dxd5_book()),
        ),
        patch(
            "shelfmark.release_sources.list_available_sources",
            return_value=[
                {"name": "prowlarr", "enabled": True},
                {"name": "irc", "enabled": True},
            ],
        ),
        patch("shelfmark.release_sources.get_source", side_effect=sources.__getitem__),
        patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
    ):
        response = client.get(
            "/api/releases", query_string={"provider": "hardcover", "book_id": "dxd5", **query}
        )
    assert response.status_code == 200
    return {release["source_id"]: release for release in response.get_json()["releases"]}


class TestEbookSearchAnnotates:
    def test_releases_from_two_sources_are_annotated(self, client, main_module):
        releases = _search(client, main_module, {"content_type": "ebook", "title": DXD5_TITLE})
        mam = next(r for r in releases.values() if r["indexer"] == "MyAnonamouse")

        # The raw MAM name says M4B: audio, whatever bookTitle and the ebook category say.
        assert mam["title"] == "High School DxD, Vol. 5: Hellcat of the Underworld Training Camp"
        assert mam["extra"]["release_name"] == MAM_M4B_NAME
        assert mam["extra"]["release_match"] == {
            "v": 1,
            "volume": "match",
            "other_volume": None,
            "medium": "audio",
            "compatible": False,
            "fan_marker": False,
        }
        assert releases["p-25"]["extra"]["release_match"] == {
            "v": 1,
            "volume": "other",
            "other_volume": 25,
            "medium": "ebook",
            "compatible": True,
            "fan_marker": False,
        }
        # IRC: a clean title with the format declared separately.
        assert releases[IRC_LINE]["extra"]["release_match"] == {
            "v": 1,
            "volume": "match",
            "other_volume": None,
            "medium": "ebook",
            "compatible": True,
            "fan_marker": False,
        }

    def test_the_identity_comes_from_the_book_after_the_title_override(self, client, main_module):
        provider_book = _dxd5_book(title="Hellcat of the Underworld Training Camp")
        seen: list[dict[str, object]] = []
        real_build = main_module.build_ranking_identity

        def _spy(**kwargs):
            seen.append(kwargs)
            return real_build(**kwargs)

        with patch.object(main_module, "build_ranking_identity", _spy):
            _search(
                client,
                main_module,
                {"content_type": "ebook", "title": DXD5_TITLE},
                book=provider_book,
            )

        assert seen == [
            {
                "title": DXD5_TITLE,
                "current_query": "Hellcat of the Underworld Training Camp",
                "series_name": DXD,
                "series_position": 5,
                "authors": ["Ichiei Ishibumi"],
            }
        ]


class TestIrcEvidence:
    """IRC is classified on its original line, and only a detailed-pattern author counts."""

    OVERLORD2 = "Overlord (Light Novel), Vol. 2: The Dark Warrior"

    def _overlord_book(self) -> BookMetadata:
        return BookMetadata(
            provider="hardcover",
            provider_id="ol2",
            title=self.OVERLORD2,
            search_title="The Dark Warrior",
            authors=["Kugane Maruyama"],
            series_name="Overlord (Light Novel)",
            series_position=2,
        )

    @pytest.mark.parametrize(
        "line",
        [
            # Series-prefix layout: the parser splits "Overlord" off as the author.
            "!Bsk Overlord - Volume 2.epub",
            # Authorless layout: only the fallback pattern matches.
            "!Bsk Overlord Vol 2.epub ::INFO:: 1.1MB",
            "!Bsk Kugane Maruyama - Overlord 02 - The Dark Warrior.epub ::INFO:: 1.1MB",
        ],
    )
    def test_the_requested_volume_matches_in_every_layout(self, client, main_module, line):
        releases = _search(
            client,
            main_module,
            {"content_type": "ebook", "title": self.OVERLORD2},
            book=self._overlord_book(),
            irc_releases=[_irc_release(line)],
        )

        assert releases[line]["extra"]["release_match"]["volume"] == "match"

    def test_a_detailed_author_still_conflicts(self, client, main_module):
        line = "!Bsk James Patterson - Overlord 02.epub ::INFO:: 1.1MB"
        releases = _search(
            client,
            main_module,
            {"content_type": "ebook", "title": self.OVERLORD2},
            book=self._overlord_book(),
            irc_releases=[_irc_release(line)],
        )

        assert releases[line]["extra"]["release_match"]["volume"] == "unknown"


class TestNoAnnotation:
    def test_an_audiobook_search_carries_no_release_match(self, client, main_module):
        releases = _search(client, main_module, {"content_type": "audiobook"})

        assert releases
        assert all("release_match" not in r["extra"] for r in releases.values())

    def test_a_manual_query_carries_no_release_match(self, client, main_module):
        releases = _search(
            client, main_module, {"content_type": "ebook", "manual_query": "dxd volume 5"}
        )

        assert releases
        assert all("release_match" not in r["extra"] for r in releases.values())

    def test_the_manual_provider_carries_no_release_match(self, client, main_module):
        source = _Source([_other_volume_release()])
        with (
            patch.object(main_module, "get_auth_mode", return_value="none"),
            patch(
                "shelfmark.release_sources.list_available_sources",
                return_value=[{"name": "prowlarr", "enabled": True}],
            ),
            patch("shelfmark.release_sources.get_source", return_value=source),
            patch("shelfmark.release_sources.source_results_are_releases", return_value=False),
        ):
            response = client.get(
                "/api/releases",
                query_string={
                    "provider": "manual",
                    "book_id": "abc",
                    "title": "High School DxD Vol. 5",
                },
            )

        body = response.get_json()
        assert response.status_code == 200
        assert [r["extra"].get("release_match") for r in body["releases"]] == [None]


class TestFailureTolerance:
    def test_a_classifier_failure_leaves_only_that_release_unannotated(self, client, main_module):
        real_classify = main_module.classify_release

        def _flaky(**kwargs):
            if "Volume 25" in str(kwargs["name"]):
                raise RuntimeError("boom")
            return real_classify(**kwargs)

        with patch.object(main_module, "classify_release", _flaky):
            releases = _search(client, main_module, {"content_type": "ebook", "title": DXD5_TITLE})

        assert "release_match" not in releases["p-25"]["extra"]
        assert releases[IRC_LINE]["extra"]["release_match"]["volume"] == "match"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/core/test_releases_api_release_match.py -q -n0`
Expected: FAIL — 7 failed: `test_releases_from_two_sources_are_annotated` and the 4 `TestIrcEvidence` tests with `KeyError: 'release_match'`, `test_the_identity_comes_from_the_book_after_the_title_override` with `AttributeError: module 'shelfmark.main' has no attribute 'build_ranking_identity'`, `test_a_classifier_failure_leaves_only_that_release_unannotated` with `... has no attribute 'classify_release'`. The 3 `TestNoAnnotation` tests already pass on `main` and stay as pins.

- [ ] **Step 3: Annotate in the endpoint**

**Replace** in `shelfmark/main.py`:

```python
    sync_delivery_states_from_queue_status,
)
from shelfmark.core.user_db import UserDB
```

with:

```python
    sync_delivery_states_from_queue_status,
)
from shelfmark.core.search_queries import build_ranking_identity, classify_release
from shelfmark.core.user_db import UserDB
```

**Replace** in `shelfmark/main.py`:

```python
from shelfmark.release_sources import (
    BrowseRecord,
    Release,
    SourceUnavailableError,
    get_source_display_name,
)

if TYPE_CHECKING:
```

with:

```python
from shelfmark.release_sources import (
    BrowseRecord,
    Release,
    SourceUnavailableError,
    get_source_display_name,
)
from shelfmark.release_sources.irc.parser import ranking_evidence as irc_ranking_evidence

if TYPE_CHECKING:
    from shelfmark.core.search_queries import RankingIdentity
```

**Replace** in `shelfmark/main.py`:

```python
            extra["preview"] = transform_cover_url(preview, release.source_id)
            result["extra"] = extra

    return result
```

with:

```python
            extra["preview"] = transform_cover_url(preview, release.source_id)
            result["extra"] = extra

    return result


def _release_match_payload(release: Release, identity: RankingIdentity) -> dict[str, object]:
    """Classify one release against the requested book for the default sort and badges."""
    extra = release.extra if isinstance(release.extra, dict) else {}
    # The indexer's own name when a source replaced the title (Prowlarr's MAM bookTitle).
    name: object = extra.get("release_name")
    release_author: object = extra.get("author")
    # IRC: the original result line, whose "author" may really be the series; the author
    # counts only when the detailed "Author - Title.format" pattern matched.
    irc = irc_ranking_evidence(extra.get("full_line")) if release.source == "irc" else None
    if irc is not None:
        name, release_author = irc
    elif not isinstance(name, str) or not name.strip():
        name = release.title
    extra_formats = extra.get("formats")
    formats = [release.format, *(extra_formats if isinstance(extra_formats, list) else ())]
    return classify_release(
        name=name,
        formats=formats,
        content_type=release.content_type,
        release_author=release_author,
        identity=identity,
    ).to_payload()


def _annotate_release_matches(
    releases_data: list[dict], releases: list[Release], identity: RankingIdentity
) -> None:
    """Add ``extra["release_match"]`` to each serialized release.

    Informational only: nothing downstream reads or persists it. A release whose
    classification fails is left without the key; it never fails the request.
    """
    for data, release in zip(releases_data, releases, strict=True):
        try:
            payload = _release_match_payload(release, identity)
        except Exception as exc:  # noqa: BLE001 - one release must never fail the search
            logger.debug("Release match classification failed for %s: %s", release.source_id, exc)
            continue
        extra = data.get("extra")
        if not isinstance(extra, dict):
            extra = {}
            data["extra"] = extra
        extra["release_match"] = payload
```

**Replace** in `shelfmark/main.py`:

```python
        source_query_filters = None
        is_source_provider = bool(provider) and source_results_are_releases(provider)

        book: BookMetadata
```

with:

```python
        source_query_filters = None
        is_source_provider = bool(provider) and source_results_are_releases(provider)

        book: BookMetadata
        # Set only for an ebook search of a metadata-provider book with no manual query.
        ranking_identity: RankingIdentity | None = None
```

**Replace** in `shelfmark/main.py`:

```python
            if title_param:
                book.title = title_param
```

with:

```python
            if title_param:
                book.title = title_param

            # Ebook searches rank releases by volume and medium; audiobook searches and
            # manual queries keep today's order. Built after the title override, so the
            # identity is the book the modal asked about.
            if content_type == "ebook" and not manual_query:
                ranking_identity = build_ranking_identity(
                    title=book.title,
                    current_query=book.search_title or book.title,
                    series_name=book.series_name,
                    series_position=book.series_position,
                    authors=book.authors,
                )
```

**Replace** in `shelfmark/main.py`:

```python
        # Convert Release objects to dicts
        releases_data = [_serialize_release(release) for release in all_releases]
```

with:

```python
        # Convert Release objects to dicts
        releases_data = [_serialize_release(release) for release in all_releases]
        if ranking_identity is not None:
            _annotate_release_matches(releases_data, all_releases, ranking_identity)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/core/test_releases_api_release_match.py -q -n0`
Expected: PASS (10 passed)

Run: `uv run pytest tests/core/test_releases_api_*.py -q`
Expected: PASS (28 passed)

- [ ] **Step 5: Lint and typecheck**

```bash
uv run ruff check shelfmark tests
uv run ruff format --check shelfmark tests
uv run basedpyright
uv run basedpyright tests --skipunannotated
uv run vulture shelfmark
```
Expected: ruff clean; BasedPyright only the 4 known `reportOptionalSubscript` errors, now at `shelfmark/main.py:2353-2356`; tests `0 errors`; vulture prints nothing.

- [ ] **Step 6: Commit**

```bash
git add shelfmark/main.py \
  tests/core/test_releases_api_release_match.py
git commit -m "feat(releases): annotate ebook releases with how they match the book

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Frontend — `ReleaseMatch` type, `parseReleaseMatch`, tiered default sort, one sort-path function

All paths below are relative to `src/frontend/`; run commands from there.

**Files:**
- Modify: `src/types/index.ts` (after `interface Release`, ~line 460), `src/utils/releaseScoring.ts` (imports; `sortReleasesByBookMatch`, end of file), `src/components/ReleaseModal.tsx` (imports ~lines 47-64; `filteredReleases` ~lines 1179 and 1215-1227)
- Create: `src/utils/releaseMatch.ts`, `src/utils/releaseDisplaySort.ts`
- Test: `src/tests/releaseMatch.test.ts`, `src/tests/releaseScoring.test.ts`, `src/tests/releaseDisplaySort.test.ts` (all new)

**Interfaces:**
- Consumes: the Task 4 payload `extra.release_match = {v: 1, volume, other_volume, medium, compatible, fan_marker}`; `isRecord` from `utils/objectHelpers`; `sortReleases`, `sortReleasesByFormat`, `FORMAT_SORT_KEY`, `SortState` from `utils/releaseSort` (unchanged).
- Produces: `export interface ReleaseMatch { volume: 'match' | 'other' | 'unknown'; other_volume: number | null; medium: 'ebook' | 'comic' | 'audio' | 'video' | 'unknown'; compatible: boolean; fan_marker: boolean }` in `types/index.ts`; `parseReleaseMatch(extra: unknown): ReleaseMatch | null` in `utils/releaseMatch.ts` (Task 6 uses it); `sortReleasesByBookMatch(releases, titleCandidates, authorCandidates)` keeps its signature; `sortReleasesForDisplay(releases: Release[], currentSort: SortState | null, hasSortOptions: boolean, uiBook: Book | null, responseBook: ReleasesResponse['book'] | undefined): Release[]` in `utils/releaseDisplaySort.ts`.

- [ ] **Step 1: Write the failing tests**

**Create** `src/frontend/src/tests/releaseMatch.test.ts`:

```ts
import { describe, expect, it } from 'vitest';

import { parseReleaseMatch } from '../utils/releaseMatch';

const valid = {
  v: 1,
  volume: 'other',
  other_volume: 25,
  medium: 'ebook',
  compatible: true,
  fan_marker: false,
};

describe('parseReleaseMatch', () => {
  it('parses a version-1 payload', () => {
    expect(parseReleaseMatch({ release_match: valid })).toEqual({
      volume: 'other',
      other_volume: 25,
      medium: 'ebook',
      compatible: true,
      fan_marker: false,
    });
    expect(
      parseReleaseMatch({
        author: 'Ichiei Ishibumi',
        release_match: { ...valid, volume: 'match', other_volume: null, fan_marker: true },
      }),
    ).toEqual({
      volume: 'match',
      other_volume: null,
      medium: 'ebook',
      compatible: true,
      fan_marker: true,
    });
  });

  it('returns null without a payload', () => {
    for (const extra of [undefined, null, 'x', 5, [], {}, { release_match: null }]) {
      expect(parseReleaseMatch(extra)).toBeNull();
    }
  });

  it('rejects a wrong or missing version', () => {
    for (const v of [2, 0, '1', null, undefined]) {
      expect(parseReleaseMatch({ release_match: { ...valid, v } })).toBeNull();
    }
  });

  it('rejects unknown enum values', () => {
    expect(parseReleaseMatch({ release_match: { ...valid, volume: 'maybe' } })).toBeNull();
    expect(parseReleaseMatch({ release_match: { ...valid, medium: 'tape' } })).toBeNull();
    expect(parseReleaseMatch({ release_match: { ...valid, medium: 'Ebook' } })).toBeNull();
  });

  it('rejects non-boolean flags', () => {
    expect(parseReleaseMatch({ release_match: { ...valid, compatible: 'true' } })).toBeNull();
    expect(parseReleaseMatch({ release_match: { ...valid, fan_marker: 1 } })).toBeNull();
  });

  it('downgrades an invalid other volume to unknown and keeps the rest', () => {
    for (const otherVolume of [0, -1, 2.5, '3', null, undefined, Number.NaN]) {
      expect(
        parseReleaseMatch({
          release_match: { ...valid, other_volume: otherVolume, medium: 'audio', fan_marker: true },
        }),
      ).toEqual({
        volume: 'unknown',
        other_volume: null,
        medium: 'audio',
        compatible: true,
        fan_marker: true,
      });
    }
  });

  it('downgrades an other volume set on a release that is not another volume', () => {
    expect(
      parseReleaseMatch({ release_match: { ...valid, volume: 'match', other_volume: 3 } }),
    ).toEqual({
      volume: 'unknown',
      other_volume: null,
      medium: 'ebook',
      compatible: true,
      fan_marker: false,
    });
  });
});
```

**Create** `src/frontend/src/tests/releaseScoring.test.ts`:

```ts
import { describe, expect, it } from 'vitest';

import type { Release } from '../types';
import { sortReleasesByBookMatch } from '../utils/releaseScoring';

type Volume = 'match' | 'other' | 'unknown';

function matchPayload(
  volume: Volume,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    v: 1,
    volume,
    other_volume: volume === 'other' ? 25 : null,
    medium: 'ebook',
    compatible: true,
    fan_marker: false,
    ...overrides,
  };
}

function release(
  id: string,
  title: string,
  releaseMatch?: Record<string, unknown>,
  extra: Record<string, unknown> = {},
): Release {
  return {
    source: 'prowlarr',
    source_id: id,
    title,
    extra: releaseMatch ? { ...extra, release_match: releaseMatch } : extra,
  };
}

const ids = (releases: Release[]): string[] => releases.map((r) => r.source_id);

const CANDIDATES = ['high school dxd vol 5'];

describe('sortReleasesByBookMatch tiers', () => {
  it('lets the tier beat the title score', () => {
    const releases = [
      release('exact-other', 'High School DxD Vol 5', matchPayload('other')),
      release('comic', 'High School DxD Vol 5 Manga', matchPayload('match', { compatible: false })),
      release('exact-unknown', 'High School DxD Vol 5', matchPayload('unknown')),
      release('no-data', 'High School DxD Vol 5 epub'),
      release('weak-match', 'DxD v05 Hellcat', matchPayload('match')),
    ];

    expect(ids(sortReleasesByBookMatch(releases, CANDIDATES, []))).toEqual([
      'weak-match',
      'exact-unknown',
      'no-data',
      'exact-other',
      'comic',
    ]);
  });

  it('puts an incompatible unknown volume in the bottom tier', () => {
    const releases = [
      release(
        'audio',
        'High School DxD Vol 5',
        matchPayload('unknown', { medium: 'audio', compatible: false }),
      ),
      release('plain', 'Unrelated'),
    ];

    expect(ids(sortReleasesByBookMatch(releases, CANDIDATES, []))).toEqual(['plain', 'audio']);
  });

  it("keeps today's order inside a tier", () => {
    const plain = [
      release('prefix', 'High School DxD Vol 5 Hellcat'),
      release('unrelated', 'Something Else Entirely'),
      release('exact', 'High School DxD Vol 5'),
      release('author', 'High School DxD Vol 5 Hellcat', undefined, { author: 'Ichiei Ishibumi' }),
    ];
    const tiered = plain.map((r) => ({
      ...r,
      extra: { ...r.extra, release_match: matchPayload('unknown') },
    }));

    const expected = ['exact', 'author', 'prefix', 'unrelated'];
    expect(ids(sortReleasesByBookMatch(plain, CANDIDATES, ['ichiei ishibumi']))).toEqual(expected);
    expect(ids(sortReleasesByBookMatch(tiered, CANDIDATES, ['ichiei ishibumi']))).toEqual(expected);
  });

  it("keeps today's order when no release has match data", () => {
    const releases = [
      release('b', 'Other Book'),
      release('a', 'High School DxD Vol 5'),
      release('c', 'Other Book'),
    ];

    expect(ids(sortReleasesByBookMatch(releases, CANDIDATES, []))).toEqual(['a', 'b', 'c']);
  });

  it('ignores a malformed payload', () => {
    const releases = [
      release('bad', 'Unrelated', { ...matchPayload('match'), v: 2 }),
      release('good', 'High School DxD Vol 5'),
    ];

    expect(ids(sortReleasesByBookMatch(releases, CANDIDATES, []))).toEqual(['good', 'bad']);
  });

  it('still orders by tier without title candidates', () => {
    const releases = [
      release('unknown', 'A', matchPayload('unknown')),
      release('other', 'B', matchPayload('other')),
      release('match', 'C', matchPayload('match')),
      release('none', 'D'),
      release('match-2', 'E', matchPayload('match')),
    ];

    expect(ids(sortReleasesByBookMatch(releases, [], []))).toEqual([
      'match',
      'match-2',
      'unknown',
      'none',
      'other',
    ]);
  });

  it('keeps input order without title candidates or match data', () => {
    const releases = [release('z', 'Z'), release('a', 'High School DxD Vol 5'), release('m', 'M')];

    expect(ids(sortReleasesByBookMatch(releases, [], []))).toEqual(['z', 'a', 'm']);
  });

  it('scores 2000 releases once each, outside the comparator', () => {
    const reads = { match: 0, title: 0, author: 0 };
    const volumes: Volume[] = ['match', 'other', 'unknown'];
    const releases: Release[] = Array.from({ length: 2000 }, (_, index) => {
      const payload = matchPayload(volumes[index % 3]);
      const extra: Record<string, unknown> = {};
      Object.defineProperty(extra, 'release_match', {
        enumerable: true,
        get: () => {
          reads.match += 1;
          return payload;
        },
      });
      Object.defineProperty(extra, 'author', {
        enumerable: true,
        get: () => {
          reads.author += 1;
          return 'Ichiei Ishibumi';
        },
      });
      const item: Release = { source: 'prowlarr', source_id: `r-${index}`, title: '', extra };
      Object.defineProperty(item, 'title', {
        enumerable: true,
        get: () => {
          reads.title += 1;
          return `High School DxD Vol ${index % 30}`;
        },
      });
      return item;
    });

    // One title candidate and one author candidate: each release's title and author are
    // read exactly once by the scoring, however many comparisons the sort makes.
    const sorted = sortReleasesByBookMatch(releases, CANDIDATES, ['ichiei ishibumi']);

    expect(reads).toEqual({ match: 2000, title: 2000, author: 2000 });
    expect(sorted).toHaveLength(2000);
    expect(sorted.slice(0, 667).every((r) => Number(r.source_id.slice(2)) % 3 === 0)).toBe(true);
    expect(sorted.slice(-667).every((r) => Number(r.source_id.slice(2)) % 3 === 1)).toBe(true);
  });
});
```

**Create** `src/frontend/src/tests/releaseDisplaySort.test.ts`:

```ts
import { describe, expect, it } from 'vitest';

import type { Book, Release } from '../types';
import { sortReleasesForDisplay } from '../utils/releaseDisplaySort';

const book: Book = {
  id: 'dxd5',
  title: 'High School DxD Vol 5',
  author: 'Ichiei Ishibumi',
  provider: 'hardcover',
  provider_id: 'dxd5',
};

function release(id: string, volume: 'match' | 'other', sizeBytes: number, format = 'epub') {
  const payload = {
    v: 1,
    volume,
    other_volume: volume === 'other' ? 25 : null,
    medium: 'ebook',
    compatible: true,
    fan_marker: false,
  };
  const r: Release = {
    source: 'prowlarr',
    source_id: id,
    title: 'High School DxD Vol 5',
    format,
    size_bytes: sizeBytes,
    extra: { release_match: payload },
  };
  return r;
}

const releases = [
  release('small-match', 'match', 10, 'pdf'),
  release('big-other', 'other', 30),
  release('mid-match', 'match', 20),
];
const ids = (list: Release[]): string[] => list.map((r) => r.source_id);

describe('sortReleasesForDisplay', () => {
  it('uses the tiered best-match sort without a chosen sort', () => {
    expect(ids(sortReleasesForDisplay(releases, null, true, book, undefined))).toEqual([
      'small-match',
      'mid-match',
      'big-other',
    ]);
  });

  it('applies a saved column sort and ignores the tiers', () => {
    const saved = { key: 'size_bytes', direction: 'desc' as const };

    expect(ids(sortReleasesForDisplay(releases, saved, true, book, undefined))).toEqual([
      'big-other',
      'mid-match',
      'small-match',
    ]);
  });

  it('applies the format sort', () => {
    const formatSort = { key: '_format_priority', direction: 'asc' as const, value: 'pdf' };

    expect(ids(sortReleasesForDisplay(releases, formatSort, true, book, undefined))[0]).toBe(
      'small-match',
    );
  });

  it('falls back to best match when the source has no sortable columns', () => {
    const saved = { key: 'size_bytes', direction: 'desc' as const };

    expect(ids(sortReleasesForDisplay(releases, saved, false, book, undefined))).toEqual([
      'small-match',
      'mid-match',
      'big-other',
    ]);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/tests/releaseMatch.test.ts src/tests/releaseScoring.test.ts src/tests/releaseDisplaySort.test.ts`
Expected: FAIL — `releaseMatch.test.ts`: `Cannot find module '../utils/releaseMatch'`; `releaseDisplaySort.test.ts`: `Cannot find module '../utils/releaseDisplaySort'`; `releaseScoring.test.ts`: 4 failed (`lets the tier beat the title score`, `puts an incompatible unknown volume in the bottom tier`, `still orders by tier without title candidates`, and `scores 2000 releases once each, outside the comparator` with `expected { Object (match, title, ...) } to deeply equal { match: 2000, title: 2000, …(1) }`), 4 passed (today's order, input order and the malformed payload already hold and stay as pins).

- [ ] **Step 3: Implement the type, the parser and the tiers**

**Replace** in `src/frontend/src/types/index.ts`:

```ts
  content_type?: string; // "ebook", "audiobook", or "book"
  extra?: Record<string, unknown>; // Source-specific metadata
}
```

with:

```ts
  content_type?: string; // "ebook", "audiobook", or "book"
  extra?: Record<string, unknown>; // Source-specific metadata
}

// How a release matches the requested book: `extra.release_match` (version 1), set by
// /api/releases for ebook searches. Read it only through `parseReleaseMatch`.
export interface ReleaseMatch {
  volume: 'match' | 'other' | 'unknown';
  other_volume: number | null; // set only when volume is 'other'
  medium: 'ebook' | 'comic' | 'audio' | 'video' | 'unknown';
  compatible: boolean; // the medium suits the requested book
  fan_marker: boolean; // the release name says fan translation
}
```

**Create** `src/frontend/src/utils/releaseMatch.ts`:

```ts
import type { ReleaseMatch } from '../types';
import { isRecord } from './objectHelpers';

// The version of `extra.release_match` this parser understands (backend RELEASE_MATCH_VERSION).
const RELEASE_MATCH_VERSION = 1;

const VOLUMES: ReadonlySet<unknown> = new Set(['match', 'other', 'unknown']);
const MEDIUMS: ReadonlySet<unknown> = new Set(['ebook', 'comic', 'audio', 'video', 'unknown']);

const isVolume = (value: unknown): value is ReleaseMatch['volume'] => VOLUMES.has(value);
const isMedium = (value: unknown): value is ReleaseMatch['medium'] => MEDIUMS.has(value);
const isPositiveInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 1;

/**
 * The release's `extra.release_match`, or null when it is missing or malformed.
 *
 * The one parser for ranking and badges. A wrong version, an unknown volume or medium, or
 * non-boolean flags give null, which means today's behaviour. An invalid `other_volume`
 * (not a positive integer for another volume, or set on a release that is not another
 * volume) only makes the volume unknown: the medium and flags still stand.
 */
export function parseReleaseMatch(extra: unknown): ReleaseMatch | null {
  if (!isRecord(extra)) return null;
  const raw = extra.release_match;
  if (!isRecord(raw) || raw.v !== RELEASE_MATCH_VERSION) return null;

  const { volume, medium, compatible } = raw;
  const otherVolume = raw.other_volume;
  const fanMarker = raw.fan_marker;
  if (!isVolume(volume) || !isMedium(medium)) return null;
  if (typeof compatible !== 'boolean' || typeof fanMarker !== 'boolean') return null;

  if (volume === 'other') {
    if (isPositiveInteger(otherVolume)) {
      return { volume, other_volume: otherVolume, medium, compatible, fan_marker: fanMarker };
    }
  } else if (otherVolume == null) {
    return { volume, other_volume: null, medium, compatible, fan_marker: fanMarker };
  }
  return { volume: 'unknown', other_volume: null, medium, compatible, fan_marker: fanMarker };
}
```

**Replace** in `src/frontend/src/utils/releaseScoring.ts`:

```ts
import type { Book, Release, ReleasesResponse } from '../types';
import { isRecord } from './objectHelpers';
```

with:

```ts
import type { Book, Release, ReleasesResponse } from '../types';
import { isRecord } from './objectHelpers';
import { parseReleaseMatch } from './releaseMatch';
```

**Replace** in `src/frontend/src/utils/releaseScoring.ts`:

```ts
export function sortReleasesByBookMatch(
  releases: Release[],
  titleCandidates: string[],
  authorCandidates: string[],
): Release[] {
  if (titleCandidates.length === 0) {
    return releases;
  }

  return releases
    .map((release, index) => ({
      release,
      index,
      score:
        titleCandidates.reduce(
          (best, candidate) => Math.max(best, getTitleMatchScore(release.title, candidate)),
          0,
        ) + (hasAuthorMatch(release, authorCandidates) ? 1500 : 0),
    }))
    .toSorted((a, b) => {
      const scoreDiff = b.score - a.score;
      if (scoreDiff !== 0) {
        return scoreDiff;
      }
      return a.index - b.index;
    })
    .map(({ release }) => release);
}
```

with:

```ts
// Tier bonuses exceed today's best score (exact title 10000 plus author 1500), so the
// tier decides and today's score orders releases within a tier.
const MATCH_TIER_BONUS = 20000;

function getMatchTierBonus(release: Release): number {
  const match = parseReleaseMatch(release.extra);
  if (!match) return 0;
  if (match.volume === 'other' || !match.compatible) return -MATCH_TIER_BONUS;
  if (match.volume === 'match') return MATCH_TIER_BONUS;
  return 0;
}

function getBookMatchScore(
  release: Release,
  titleCandidates: string[],
  authorCandidates: string[],
): number {
  // Without title candidates today's order stands (inside each tier).
  if (titleCandidates.length === 0) return 0;
  return (
    titleCandidates.reduce(
      (best, candidate) => Math.max(best, getTitleMatchScore(release.title, candidate)),
      0,
    ) + (hasAuthorMatch(release, authorCandidates) ? 1500 : 0)
  );
}

export function sortReleasesByBookMatch(
  releases: Release[],
  titleCandidates: string[],
  authorCandidates: string[],
): Release[] {
  // Each score is computed once per release, never inside the comparator.
  return releases
    .map((release, index) => ({
      release,
      index,
      score:
        getMatchTierBonus(release) + getBookMatchScore(release, titleCandidates, authorCandidates),
    }))
    .toSorted((a, b) => {
      const scoreDiff = b.score - a.score;
      if (scoreDiff !== 0) {
        return scoreDiff;
      }
      return a.index - b.index;
    })
    .map(({ release }) => release);
}
```

**Create** `src/frontend/src/utils/releaseDisplaySort.ts`:

```ts
import type { Book, Release, ReleasesResponse } from '../types';
import {
  getBookAuthorCandidates,
  getBookTitleCandidates,
  sortReleasesByBookMatch,
} from './releaseScoring';
import type { SortState } from './releaseSort';
import { FORMAT_SORT_KEY, sortReleases, sortReleasesByFormat } from './releaseSort';

/**
 * The order the release modal shows: an explicit format sort, else an explicit (saved or
 * chosen) column sort when the source has sortable columns, else the default best-match
 * sort with its volume and medium tiers.
 */
export function sortReleasesForDisplay(
  releases: Release[],
  currentSort: SortState | null,
  hasSortOptions: boolean,
  uiBook: Book | null,
  responseBook: ReleasesResponse['book'] | undefined,
): Release[] {
  if (currentSort?.key === FORMAT_SORT_KEY && currentSort.value) {
    return sortReleasesByFormat(releases, currentSort.value, currentSort.direction);
  }
  if (currentSort && hasSortOptions) {
    return sortReleases(releases, currentSort.key, currentSort.direction);
  }
  return sortReleasesByBookMatch(
    releases,
    getBookTitleCandidates(uiBook, responseBook),
    getBookAuthorCandidates(uiBook, responseBook),
  );
}
```

Then route `ReleaseModal`'s sort through it:

**Replace** in `src/frontend/src/components/ReleaseModal.tsx`:

```tsx
import { getReleaseFormats } from '../utils/releaseFormats';
```

with:

```tsx
import { sortReleasesForDisplay } from '../utils/releaseDisplaySort';
import { getReleaseFormats } from '../utils/releaseFormats';
```

**Replace** in `src/frontend/src/components/ReleaseModal.tsx`:

```tsx
import { buildReleaseDownloadPayload, type ReleaseDownloadOptions } from '../utils/releasePayload';
import {
  getBookTitleCandidates,
  getBookAuthorCandidates,
  sortReleasesByBookMatch,
} from '../utils/releaseScoring';
import type { SortState } from '../utils/releaseSort';
import {
  getSavedSort,
  saveSort,
  clearSort,
  inferDefaultDirection,
  sortReleases,
  FORMAT_SORT_KEY,
  sortReleasesByFormat,
} from '../utils/releaseSort';
```

with:

```tsx
import { buildReleaseDownloadPayload, type ReleaseDownloadOptions } from '../utils/releasePayload';
import type { SortState } from '../utils/releaseSort';
import {
  getSavedSort,
  saveSort,
  clearSort,
  inferDefaultDirection,
  FORMAT_SORT_KEY,
} from '../utils/releaseSort';
```

**Replace** in `src/frontend/src/components/ReleaseModal.tsx`:

```tsx
    // First, filter
    let filtered = releases.filter((r) => {
```

with:

```tsx
    // First, filter
    const filtered = releases.filter((r) => {
```

**Replace** in `src/frontend/src/components/ReleaseModal.tsx`:

```tsx
    // Then, sort by explicit column/format, or default to book-title relevance with exact author boost
    if (currentSort?.key === FORMAT_SORT_KEY && currentSort.value) {
      filtered = sortReleasesByFormat(filtered, currentSort.value, currentSort.direction);
    } else if (currentSort && allSortOptions.length > 0) {
      filtered = sortReleases(filtered, currentSort.key, currentSort.direction);
    } else {
      const responseBook = releasesBySource[activeTab]?.book;
      const titleCandidates = getBookTitleCandidates(book, responseBook);
      const authorCandidates = getBookAuthorCandidates(book, responseBook);
      filtered = sortReleasesByBookMatch(filtered, titleCandidates, authorCandidates);
    }

    return filtered;
```

with:

```tsx
    // Then, sort by explicit column/format, or default to the tiered best-match sort
    return sortReleasesForDisplay(
      filtered,
      currentSort,
      allSortOptions.length > 0,
      book,
      releasesBySource[activeTab]?.book,
    );
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/tests/releaseMatch.test.ts src/tests/releaseScoring.test.ts src/tests/releaseDisplaySort.test.ts`
Expected: PASS (19 passed)

- [ ] **Step 5: Typecheck, lint, format, knip, full suite**

```bash
npm run typecheck && npm run lint && npm run format:check
npm run knip > /tmp/knip-task5.txt; diff /tmp/knip-main.txt /tmp/knip-task5.txt
npm run test:unit
```
Expected: typecheck, lint and format clean; the knip diff shows only the `SourceSearchInfo` line moving from `src/types/index.ts:463` to `:473`; vitest 382 passed.

- [ ] **Step 6: Commit** (from the repository root)

```bash
git add src/frontend/src/types/index.ts \
  src/frontend/src/utils/releaseMatch.ts \
  src/frontend/src/utils/releaseScoring.ts \
  src/frontend/src/utils/releaseDisplaySort.ts \
  src/frontend/src/components/ReleaseModal.tsx \
  src/frontend/src/tests/releaseMatch.test.ts \
  src/frontend/src/tests/releaseScoring.test.ts \
  src/frontend/src/tests/releaseDisplaySort.test.ts
git commit -m "feat(releases): rank the default sort by volume and medium tiers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Mismatch badges and `Fan TL?` below the title, outside the clamp

All paths below are relative to `src/frontend/`; run commands from there.

**Files:**
- Create: `src/components/ReleaseMatchBadges.tsx`
- Modify: `src/components/ReleaseModal.tsx` (imports ~line 70; `ReleaseRow` ~line 479; desktop title ~line 581; mobile title ~line 633)
- Test: `src/tests/releaseMatchBadges.test.tsx` (new)

**Interfaces:**
- Consumes: `parseReleaseMatch` (Task 5).
- Produces: `ReleaseMatchBadges({ release, compact? }: { release: Release; compact?: boolean })` (renders `null` when there is nothing to flag); `FAN_TL_TOOLTIP = 'The release name says this is a fan translation'`; `ReleaseRow` becomes a named export of `ReleaseModal.tsx` (props unchanged).

- [ ] **Step 1: Write the failing tests**

**Create** `src/frontend/src/tests/releaseMatchBadges.test.tsx`:

```tsx
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { ReleaseMatchBadges, FAN_TL_TOOLTIP } from '../components/ReleaseMatchBadges';
import { ReleaseRow } from '../components/ReleaseModal';
import type { Release } from '../types';

function release(releaseMatch?: Record<string, unknown>): Release {
  return {
    source: 'prowlarr',
    source_id: 'r-1',
    title: 'High School DxD - Volume 25',
    extra: releaseMatch ? { release_match: releaseMatch } : {},
  };
}

function payload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    v: 1,
    volume: 'unknown',
    other_volume: null,
    medium: 'ebook',
    compatible: true,
    fan_marker: false,
    ...overrides,
  };
}

const render = (r: Release, compact = false): string =>
  renderToStaticMarkup(<ReleaseMatchBadges release={r} compact={compact} />);

describe('ReleaseMatchBadges', () => {
  it('names another volume', () => {
    expect(render(release(payload({ volume: 'other', other_volume: 25 })))).toContain('Vol 25');
  });

  it('flags an incompatible comic, an audiobook and a video', () => {
    expect(render(release(payload({ medium: 'comic', compatible: false })))).toContain(
      'Manga/Comic',
    );
    expect(render(release(payload({ medium: 'audio', compatible: false })))).toContain('Audiobook');
    expect(render(release(payload({ medium: 'video', compatible: false })))).toContain('Video');
  });

  it('renders nothing for a match, a compatible comic, an unknown or no data', () => {
    expect(render(release(payload({ volume: 'match' })))).toBe('');
    expect(render(release(payload({ medium: 'comic', compatible: true })))).toBe('');
    expect(render(release(payload()))).toBe('');
    expect(render(release())).toBe('');
    expect(render(release({ ...payload({ volume: 'other', other_volume: 25 }), v: 2 }))).toBe('');
  });

  it('shows the fan marker as a secondary badge with its tooltip', () => {
    const markup = render(release(payload({ volume: 'match', fan_marker: true })));

    expect(FAN_TL_TOOLTIP).toBe('The release name says this is a fan translation');
    expect(markup).toContain('Fan TL?');
    expect(markup).toContain(`title="${FAN_TL_TOOLTIP}"`);
  });

  it('renders plain text in the compact layout', () => {
    const markup = render(
      release(payload({ volume: 'other', other_volume: 25, fan_marker: true })),
      true,
    );

    expect(markup).toContain('Vol 25');
    expect(markup).toContain(`title="${FAN_TL_TOOLTIP}"`);
    expect(markup).not.toContain('rounded-lg');
  });

  it('separates several compact badges without orphan separators', () => {
    const markup = render(
      release(
        payload({
          volume: 'other',
          other_volume: 25,
          medium: 'comic',
          compatible: false,
          fan_marker: true,
        }),
      ),
      true,
    );
    const text = markup.replaceAll(/<[^>]+>/g, '');

    expect(text).toBe('Vol 25·Manga/Comic·Fan TL?');
  });
});

const renderRow = (r: Release): string =>
  renderToStaticMarkup(
    <ReleaseRow
      release={r}
      index={0}
      onDownload={async () => undefined}
      buttonState={{ text: 'Download', state: 'download' }}
      columns={[]}
      gridTemplate="minmax(0,2fr)"
      leadingCell={{ type: 'none' }}
      showReleaseSourceLinks={false}
    />,
  );

const clampedTitles = (markup: string): string[] =>
  [...markup.matchAll(/<p class="line-clamp-2[^"]*"[^>]*>.*?<\/p>/g)].map((m) => m[0]);

describe('ReleaseRow mismatch badges', () => {
  it('renders the badges outside the two-line title clamp in both layouts', () => {
    const markup = renderRow(release(payload({ volume: 'other', other_volume: 25 })));
    const titles = clampedTitles(markup);

    expect(titles).toHaveLength(2);
    expect(titles.every((title) => !title.includes('Vol 25'))).toBe(true);
    expect(markup.match(/Vol 25/g)).toHaveLength(2);
  });

  it('renders no badge line for a matching release', () => {
    const markup = renderRow(release(payload({ volume: 'match' })));

    expect(markup).not.toContain('Vol ');
    expect(markup).not.toContain('Fan TL?');
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/tests/releaseMatchBadges.test.tsx`
Expected: FAIL — `Cannot find module '../components/ReleaseMatchBadges'`.

- [ ] **Step 3: Implement the badges and place them**

**Create** `src/frontend/src/components/ReleaseMatchBadges.tsx`:

```tsx
import type { Release } from '../types';
import { parseReleaseMatch } from '../utils/releaseMatch';

export const FAN_TL_TOOLTIP = 'The release name says this is a fan translation';

interface MatchBadge {
  label: string;
  secondary: boolean;
  title?: string;
}

function getMatchBadges(release: Release): MatchBadge[] {
  const match = parseReleaseMatch(release.extra);
  if (!match) return [];

  const badges: MatchBadge[] = [];
  if (match.volume === 'other' && match.other_volume !== null) {
    badges.push({ label: `Vol ${match.other_volume}`, secondary: false });
  }
  if (match.medium === 'comic' && !match.compatible) {
    badges.push({ label: 'Manga/Comic', secondary: false });
  }
  if (match.medium === 'audio') {
    badges.push({ label: 'Audiobook', secondary: false });
  }
  if (match.medium === 'video') {
    badges.push({ label: 'Video', secondary: false });
  }
  if (match.fan_marker) {
    badges.push({ label: 'Fan TL?', secondary: true, title: FAN_TL_TOOLTIP });
  }
  return badges;
}

/**
 * Mismatch badges for a release row: another volume, a medium the book is not, and a
 * secondary "Fan TL?" marker. Rendered on their own line below the title, outside the
 * two-line title clamp; a matching release gets nothing. `compact` renders plain text for
 * the mobile layout, like other compact badges.
 */
export function ReleaseMatchBadges({
  release,
  compact = false,
}: {
  release: Release;
  compact?: boolean;
}) {
  const badges = getMatchBadges(release);
  if (badges.length === 0) return null;

  if (compact) {
    return (
      <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px]">
        {badges.map((badge, idx) => (
          <span key={badge.label} className="flex items-center gap-1.5">
            {idx > 0 && <span className="text-zinc-300 dark:text-zinc-600">·</span>}
            <span
              className={
                badge.secondary
                  ? 'text-zinc-500 dark:text-zinc-400'
                  : 'font-semibold text-amber-600 dark:text-amber-400'
              }
              title={badge.title}
            >
              {badge.label}
            </span>
          </span>
        ))}
      </p>
    );
  }

  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      {badges.map((badge) => (
        <span
          key={badge.label}
          className={`rounded-lg px-1.5 py-0.5 text-[10px] font-semibold tracking-wide whitespace-nowrap sm:px-2 sm:text-[11px] ${
            badge.secondary
              ? 'bg-gray-500/20 text-gray-700 dark:text-gray-300'
              : 'bg-amber-500/20 text-amber-700 dark:text-amber-400'
          }`}
          title={badge.title}
        >
          {badge.label}
        </span>
      ))}
    </div>
  );
}
```

**Replace** in `src/frontend/src/components/ReleaseModal.tsx`:

```tsx
import { ReleaseCell } from './ReleaseCell';
```

with:

```tsx
import { ReleaseCell } from './ReleaseCell';
import { ReleaseMatchBadges } from './ReleaseMatchBadges';
```

**Replace** in `src/frontend/src/components/ReleaseModal.tsx`:

```tsx
// Release row component with dynamic columns
const ReleaseRow = ({
```

with:

```tsx
// Release row component with dynamic columns
export const ReleaseRow = ({
```

**Replace** in `src/frontend/src/components/ReleaseModal.tsx`:

```tsx
            ) : (
              release.title
            )}
          </p>
          {author && <p className="truncate text-xs text-zinc-500 dark:text-zinc-400">{author}</p>}
```

with:

```tsx
            ) : (
              release.title
            )}
          </p>
          <ReleaseMatchBadges release={release} />
          {author && <p className="truncate text-xs text-zinc-500 dark:text-zinc-400">{author}</p>}
```

**Replace** in `src/frontend/src/components/ReleaseModal.tsx`:

```tsx
            {author && (
              <span className="font-normal text-zinc-500 dark:text-zinc-400"> — {author}</span>
            )}
          </p>
```

with:

```tsx
            {author && (
              <span className="font-normal text-zinc-500 dark:text-zinc-400"> — {author}</span>
            )}
          </p>
          <ReleaseMatchBadges release={release} compact />
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/tests/releaseMatchBadges.test.tsx`
Expected: PASS (8 passed)

- [ ] **Step 5: Typecheck, lint, format, knip, full suite**

```bash
npm run typecheck && npm run lint && npm run format:check
npm run knip > /tmp/knip-task6.txt; diff /tmp/knip-main.txt /tmp/knip-task6.txt
npm run test:unit
```
Expected: clean (oxlint's `unicorn(consistent-function-scoping)` is why the test helpers live at module scope); knip diff only the `SourceSearchInfo` line number; vitest 390 passed.

- [ ] **Step 6: Commit** (from the repository root)

```bash
git add src/frontend/src/components/ReleaseMatchBadges.tsx \
  src/frontend/src/components/ReleaseModal.tsx \
  src/frontend/src/tests/releaseMatchBadges.test.tsx
git commit -m "feat(releases): mismatch and fan-translation badges below the release title

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Query context — manual results stay out of the book cache, expansions merge only within a context, superseded responses are dropped

All paths below are relative to `src/frontend/`; run commands from there.

**Files:**
- Create: `src/hooks/releaseModal/releaseSearchSession.helpers.ts`
- Modify: `src/hooks/releaseModal/useReleaseSearchSession.ts` (imports ~line 22; refs after `showManualQuery` ~line 158; `fetchReleaseResults` ~lines 225-310; the reset effect ~line 345; `runManualSearch` ~line 520)
- Test: `src/tests/releaseSearchSession.test.ts` (new)

**Interfaces:**
- Consumes: `getCachedReleases`, `setCachedReleases`, `invalidateCachedReleases` (`utils/releaseCache.ts`, unchanged); `isRecord` (`utils/objectHelpers`).
- Produces: `queryContext(appliedManualQuery: string | undefined): string`; `usesBookReleaseCache(context: string): boolean`; `releaseResponseAction(options: { expandSearch: boolean; requestContext: string; currentContext: string; displayedContext: string | undefined; isLatestRequest: boolean }): 'discard' | 'merge' | 'replace'`; `applyReleaseResponse(existing: ReleasesResponse | null | undefined, response: ReleasesResponse, action: 'merge' | 'replace'): ReleasesResponse`; `mergeExpandedReleases(existing: ReleasesResponse, incoming: ReleasesResponse): ReleasesResponse`. The hook's public return value is unchanged.

- [ ] **Step 1: Write the failing tests**

**Create** `src/frontend/src/tests/releaseSearchSession.test.ts`:

```ts
import { readFileSync } from 'node:fs';

import { afterEach, describe, expect, it } from 'vitest';

import {
  applyReleaseResponse,
  mergeExpandedReleases,
  queryContext,
  releaseResponseAction,
  usesBookReleaseCache,
} from '../hooks/releaseModal/releaseSearchSession.helpers';
import type { Release, ReleasesResponse } from '../types';
import {
  getCachedReleases,
  invalidateCachedReleases,
  setCachedReleases,
} from '../utils/releaseCache';

function matchPayload(volume: 'match' | 'other' | 'unknown', otherVolume: number | null = null) {
  return {
    v: 1,
    volume,
    other_volume: otherVolume,
    medium: 'ebook',
    compatible: true,
    fan_marker: false,
  };
}

function annotated(id: string, payload: Record<string, unknown>, title = id): Release {
  return {
    source: 'prowlarr',
    source_id: id,
    title,
    extra: { author: 'A', release_match: payload },
  };
}

function plain(id: string): Release {
  return { source: 'prowlarr', source_id: id, title: id, extra: { author: 'A' } };
}

function response(releases: Release[], title = 'High School DxD, Vol. 5'): ReleasesResponse {
  return {
    releases,
    book: { provider: 'hardcover', provider_id: 'dxd5', title },
    sources_searched: ['prowlarr'],
    column_config: null,
  };
}

// A release whose `extra` is not an object, as an older or broken backend could send.
function malformed(id: string, extra: unknown): Release {
  const release: Release = { source: 'prowlarr', source_id: id, title: id };
  Reflect.set(release, 'extra', extra);
  return release;
}

const KEY = ['hardcover', 'dxd5', 'prowlarr', 'ebook'] as const;

afterEach(() => {
  invalidateCachedReleases(...KEY);
});

describe('query context and the book cache', () => {
  it('is the applied manual query, trimmed, or empty for the automatic search', () => {
    expect(queryContext(undefined)).toBe('');
    expect(queryContext('  ')).toBe('');
    expect(queryContext(' dxd volume 5 ')).toBe('dxd volume 5');
  });

  it('uses the book cache only for the automatic search', () => {
    expect(usesBookReleaseCache('')).toBe(true);
    expect(usesBookReleaseCache('dxd volume 5')).toBe(false);
  });
});

describe('releaseResponseAction', () => {
  const base = {
    expandSearch: false,
    requestContext: '',
    currentContext: '',
    displayedContext: '',
    isLatestRequest: true,
  };

  it('replaces the list with a normal search response', () => {
    expect(releaseResponseAction(base)).toBe('replace');
  });

  it('merges an expansion only into a list from the same context', () => {
    expect(releaseResponseAction({ ...base, expandSearch: true })).toBe('merge');
    expect(
      releaseResponseAction({
        ...base,
        expandSearch: true,
        requestContext: 'dxd 5',
        currentContext: 'dxd 5',
        displayedContext: 'dxd 5',
      }),
    ).toBe('merge');
  });

  it('replaces instead of mixing automatic and manual rows', () => {
    // Automatic list on screen, expansion of the applied manual query.
    expect(
      releaseResponseAction({
        ...base,
        expandSearch: true,
        requestContext: 'dxd 5',
        currentContext: 'dxd 5',
        displayedContext: '',
      }),
    ).toBe('replace');
    // Manual list on screen, expansion of the automatic search (after a reopen).
    expect(releaseResponseAction({ ...base, expandSearch: true, displayedContext: 'dxd 5' })).toBe(
      'replace',
    );
    // Nothing on screen yet.
    expect(
      releaseResponseAction({ ...base, expandSearch: true, displayedContext: undefined }),
    ).toBe('replace');
  });

  it('discards a response superseded by a newer request or another context', () => {
    expect(releaseResponseAction({ ...base, isLatestRequest: false })).toBe('discard');
    expect(releaseResponseAction({ ...base, currentContext: 'dxd 5' })).toBe('discard');
  });
});

describe('out-of-order completion', () => {
  it('shows only the newest request, whatever order the responses arrive in', () => {
    // The hook's bookkeeping for one tab: a sequence number per request, the applied
    // context, and what is on screen.
    let seq = 0;
    let applied = '';
    let shown: ReleasesResponse | undefined;
    let displayed: string | undefined;
    const start = (context: string) => ({ id: ++seq, context });
    const arrive = (request: { id: number; context: string }, data: ReleasesResponse) => {
      const action = releaseResponseAction({
        expandSearch: false,
        requestContext: request.context,
        currentContext: applied,
        displayedContext: displayed,
        isLatestRequest: request.id === seq,
      });
      if (action === 'discard') return;
      displayed = request.context;
      shown = applyReleaseResponse(shown, data, action);
    };

    const automatic = start('');
    applied = 'dxd 5';
    const manual = start('dxd 5');
    arrive(manual, response([plain('m')]));
    arrive(automatic, response([annotated('a', matchPayload('match'))]));

    expect(shown?.releases.map((r) => r.source_id)).toEqual(['m']);
    expect(displayed).toBe('dxd 5');
  });
});

describe('applyReleaseResponse', () => {
  it('replaces, merges, and merges into nothing as a replace', () => {
    const existing = response([plain('a')]);
    const incoming = response([plain('b')]);

    expect(applyReleaseResponse(existing, incoming, 'replace')).toBe(incoming);
    expect(
      applyReleaseResponse(existing, incoming, 'merge').releases.map((r) => r.source_id),
    ).toEqual(['a', 'b']);
    expect(applyReleaseResponse(undefined, incoming, 'merge')).toBe(incoming);
  });
});

describe('mergeExpandedReleases', () => {
  it('keeps existing rows in place and appends new ones', () => {
    const merged = mergeExpandedReleases(
      response([plain('a'), plain('b')]),
      response([plain('c'), plain('a')], 'ignored'),
    );

    expect(merged.releases.map((r) => r.source_id)).toEqual(['a', 'b', 'c']);
    expect(merged.book.title).toBe('High School DxD, Vol. 5');
  });

  it("refreshes a duplicate row's release match from the incoming response", () => {
    const merged = mergeExpandedReleases(
      response([annotated('a', matchPayload('unknown'), 'kept title')]),
      response([annotated('a', matchPayload('other', 25), 'new title')]),
    );

    expect(merged.releases[0].title).toBe('kept title');
    expect(merged.releases[0].extra).toEqual({
      author: 'A',
      release_match: matchPayload('other', 25),
    });
  });

  it('drops a stale release match the incoming row no longer carries', () => {
    const merged = mergeExpandedReleases(
      response([annotated('a', matchPayload('match'))]),
      response([plain('a')]),
    );

    expect(merged.releases[0].extra).toEqual({ author: 'A' });
  });

  it('leaves rows the response does not return untouched', () => {
    const kept = annotated('a', matchPayload('match'));
    const merged = mergeExpandedReleases(response([kept]), response([plain('b')]));

    expect(merged.releases[0]).toBe(kept);
  });

  it('survives malformed extras on either side', () => {
    const merged = mergeExpandedReleases(
      response([malformed('a', ['x']), malformed('b', null), malformed('c', undefined)]),
      response([
        annotated('a', matchPayload('match')),
        malformed('b', ['release_match']),
        malformed('c', 'release_match'),
      ]),
    );

    expect(merged.releases.map((r) => r.extra)).toEqual([
      { release_match: matchPayload('match') },
      {},
      {},
    ]);
  });
});

describe('manual search, reopen, expand', () => {
  it('shows no stale or unannotated mix', () => {
    const store = (context: string, data: ReleasesResponse) => {
      if (usesBookReleaseCache(context)) setCachedReleases(...KEY, data);
    };

    // 1. Open the book: an annotated response is cached under the book.
    store('', response([annotated('a', matchPayload('match'))]));
    // 2. Manual search: every tab's entry is invalidated, and the unannotated manual
    //    response is not written back under the book.
    invalidateCachedReleases(...KEY);
    store('dxd volume 5', response([plain('a'), plain('m')]));
    // 3. Reopen: no manual results come back from the cache, so the modal searches again.
    expect(getCachedReleases(...KEY)).toBeNull();
    const reopened = response([
      annotated('a', matchPayload('match')),
      annotated('b', matchPayload('other', 6)),
    ]);
    store('', reopened);
    expect(getCachedReleases(...KEY)).toBe(reopened);
    // 4. Expand: duplicates take the incoming match data; new rows arrive annotated.
    const action = releaseResponseAction({
      expandSearch: true,
      requestContext: '',
      currentContext: '',
      displayedContext: '',
      isLatestRequest: true,
    });
    const expanded = applyReleaseResponse(
      reopened,
      response([annotated('b', matchPayload('other', 7)), annotated('c', matchPayload('unknown'))]),
      action === 'discard' ? 'replace' : action,
    );

    expect(action).toBe('merge');
    expect(expanded.releases.map((r) => [r.source_id, r.extra?.release_match])).toEqual([
      ['a', matchPayload('match')],
      ['b', matchPayload('other', 7)],
      ['c', matchPayload('unknown')],
    ]);
  });
});

describe('useReleaseSearchSession uses the helpers', () => {
  const hook = readFileSync(
    new URL('../hooks/releaseModal/useReleaseSearchSession.ts', import.meta.url),
    'utf8',
  );

  it('calls the extracted decisions instead of reimplementing them', () => {
    for (const call of [
      'queryContext(',
      'usesBookReleaseCache(requestContext)',
      'releaseResponseAction(',
      'applyReleaseResponse(',
    ]) {
      expect(hook).toContain(call);
    }
    expect(hook).not.toContain('seenIds');
    expect(hook).not.toMatch(/new Set\(existing\.releases/);
  });

  it('writes the cache only behind the cache decision', () => {
    const writes = hook.match(/setCachedReleases\(/g) ?? [];
    expect(writes).toHaveLength(1);
    expect(hook).toMatch(
      /if \(!expandSearch && usesBookReleaseCache\(requestContext\)\) \{\s*setCachedReleases\(/,
    );
  });

  it('sends the applied manual query, never the draft text', () => {
    expect(hook).toContain('queryContext(manualQueryOverride ?? appliedManualQueryRef.current)');
    expect(hook).not.toMatch(/manualQueryOverride \?\? manualQuery\)/);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/tests/releaseSearchSession.test.ts`
Expected: FAIL — `Cannot find module '../hooks/releaseModal/releaseSearchSession.helpers'`.

- [ ] **Step 3: Implement the helpers and use them in the hook**

**Create** `src/frontend/src/hooks/releaseModal/releaseSearchSession.helpers.ts`:

```ts
import type { Release, ReleasesResponse } from '../../types';
import { isRecord } from '../../utils/objectHelpers';

/**
 * The query context of a search: `''` for the automatic search, otherwise the applied
 * manual query (the one last submitted, never the draft in the text field).
 */
export function queryContext(appliedManualQuery: string | undefined): string {
  return appliedManualQuery?.trim() ?? '';
}

/**
 * Whether a search in this context may read and write the book's normal cache entry.
 *
 * A manual query's results are the user's own words and carry no `release_match`, so
 * they are never stored under (or served from) the book's entry: reopening the book
 * always shows a normal, annotated search.
 */
export function usesBookReleaseCache(context: string): boolean {
  return context === '';
}

type ReleaseResponseAction = 'discard' | 'merge' | 'replace';

/**
 * What to do with a search response when it arrives.
 *
 * - `discard`: a newer request for the tab was started, or the query context changed
 *   while this one was in flight.
 * - `merge`: an expanded search whose context is the one the list is showing.
 * - `replace`: anything else, including an expanded search over a list from another
 *   context (it must not mix manual and automatic rows).
 */
export function releaseResponseAction(options: {
  expandSearch: boolean;
  requestContext: string;
  currentContext: string;
  displayedContext: string | undefined;
  isLatestRequest: boolean;
}): ReleaseResponseAction {
  const { expandSearch, requestContext, currentContext, displayedContext, isLatestRequest } =
    options;
  if (!isLatestRequest || requestContext !== currentContext) return 'discard';
  if (expandSearch && displayedContext === requestContext) return 'merge';
  return 'replace';
}

/** The list to show after a `merge` or `replace` action. */
export function applyReleaseResponse(
  existing: ReleasesResponse | null | undefined,
  response: ReleasesResponse,
  action: 'merge' | 'replace',
): ReleasesResponse {
  return action === 'merge' && existing ? mergeExpandedReleases(existing, response) : response;
}

function withIncomingReleaseMatch(existing: Release, incoming: Release): Release {
  const extra: Record<string, unknown> = isRecord(existing.extra) ? { ...existing.extra } : {};
  if (isRecord(incoming.extra) && 'release_match' in incoming.extra) {
    extra.release_match = incoming.extra.release_match;
  } else {
    delete extra.release_match;
  }
  return { ...existing, extra };
}

/**
 * Merge an expanded search into the rows already shown.
 *
 * Existing rows keep their place and data, except that a row the response returns again
 * takes the response's `extra.release_match` (or loses a stale one the response no longer
 * carries). Rows the existing list does not have are appended in response order.
 */
export function mergeExpandedReleases(
  existing: ReleasesResponse,
  incoming: ReleasesResponse,
): ReleasesResponse {
  const incomingById = new Map(incoming.releases.map((release) => [release.source_id, release]));
  const existingIds = new Set(existing.releases.map((release) => release.source_id));

  const refreshed = existing.releases.map((release) => {
    const fresh = incomingById.get(release.source_id);
    return fresh ? withIncomingReleaseMatch(release, fresh) : release;
  });
  const added = incoming.releases.filter((release) => !existingIds.has(release.source_id));

  return { ...existing, releases: [...refreshed, ...added] };
}
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
import { useDependencyEffect, useMountEffect } from '../useMountEffect';
```

with:

```ts
import { useDependencyEffect, useMountEffect } from '../useMountEffect';
import {
  applyReleaseResponse,
  queryContext,
  releaseResponseAction,
  usesBookReleaseCache,
} from './releaseSearchSession.helpers';
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
  const [showManualQuery, setShowManualQuery] = useState(defaultShowManualQuery);
```

with:

```ts
  const [showManualQuery, setShowManualQuery] = useState(defaultShowManualQuery);
  // The manual query last submitted ('' for the automatic search), and per tab the query
  // context of the list on screen and the newest request. Refs, because a response is
  // checked against the values current when it arrives, not when it was requested.
  const appliedManualQueryRef = useRef(defaultShowManualQuery ? defaultManualQuery : '');
  const displayedContextRef = useRef<Record<string, string>>({});
  const requestSeqRef = useRef<Record<string, number>>({});
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
      const currentManualQuery = (manualQueryOverride ?? manualQuery).trim() || undefined;
```

with:

```ts
      const requestContext = queryContext(manualQueryOverride ?? appliedManualQueryRef.current);
      const currentManualQuery = requestContext || undefined;
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
      if (!expandSearch) {
        const cached = getCachedReleases(provider, bookId, tabName, contentType);
        if (cached) {
          setReleasesBySource((prev) => ({ ...prev, [tabName]: cached }));
```

with:

```ts
      if (!expandSearch && usesBookReleaseCache(requestContext)) {
        const cached = getCachedReleases(provider, bookId, tabName, contentType);
        if (cached) {
          displayedContextRef.current[tabName] = requestContext;
          setReleasesBySource((prev) => ({ ...prev, [tabName]: cached }));
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
      setLoadingBySource((prev) => ({ ...prev, [tabName]: true }));
      setErrorBySource((prev) => ({ ...prev, [tabName]: null }));

      try {
```

with:

```ts
      const requestSeq = (requestSeqRef.current[tabName] ?? 0) + 1;
      requestSeqRef.current[tabName] = requestSeq;
      const isLatestRequest = () => requestSeqRef.current[tabName] === requestSeq;

      setLoadingBySource((prev) => ({ ...prev, [tabName]: true }));
      setErrorBySource((prev) => ({ ...prev, [tabName]: null }));

      try {
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
        if (expandSearch) {
          setReleasesBySource((prev) => {
            const existing = prev[tabName];
            if (!existing) {
              return { ...prev, [tabName]: response };
            }

            const seenIds = new Set(existing.releases.map((release) => release.source_id));
            const mergedReleases = response.releases.filter(
              (release) => !seenIds.has(release.source_id),
            );

            return {
              ...prev,
              [tabName]: {
                ...existing,
                releases: [...existing.releases, ...mergedReleases],
              },
            };
          });
        } else {
          setCachedReleases(provider, bookId, tabName, contentType, response);
          setReleasesBySource((prev) => ({ ...prev, [tabName]: response }));
        }

        initializeIndexerFilterForTab(tabName, response);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to fetch releases';
        setErrorBySource((prev) => ({ ...prev, [tabName]: message }));
      } finally {
        setLoadingBySource((prev) => ({ ...prev, [tabName]: false }));
        clearSearchStatusForTab(tabName);
      }
```

with:

```ts
        const action = releaseResponseAction({
          expandSearch,
          requestContext,
          currentContext: queryContext(appliedManualQueryRef.current),
          displayedContext: displayedContextRef.current[tabName],
          isLatestRequest: isLatestRequest(),
        });
        if (action === 'discard') {
          return;
        }
        if (!expandSearch && usesBookReleaseCache(requestContext)) {
          setCachedReleases(provider, bookId, tabName, contentType, response);
        }
        displayedContextRef.current[tabName] = requestContext;
        setReleasesBySource((prev) => ({
          ...prev,
          [tabName]: applyReleaseResponse(prev[tabName], response, action),
        }));

        initializeIndexerFilterForTab(tabName, response);
      } catch (err) {
        if (isLatestRequest()) {
          const message = err instanceof Error ? err.message : 'Failed to fetch releases';
          setErrorBySource((prev) => ({ ...prev, [tabName]: message }));
        }
      } finally {
        if (isLatestRequest()) {
          setLoadingBySource((prev) => ({ ...prev, [tabName]: false }));
          clearSearchStatusForTab(tabName);
        }
      }
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
      loadingBySource,
      manualQuery,
      releasesBySource,
    ],
  );
  useMountEffect(() => {
```

with:

```ts
      loadingBySource,
      releasesBySource,
    ],
  );
  useMountEffect(() => {
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
    indexerFilterInitializedRef.current = new Set<string>();
    const nextInitialActiveTab
```

with:

```ts
    indexerFilterInitializedRef.current = new Set<string>();
    // A new book or content type: back to the automatic search, and every response still
    // in flight is superseded.
    appliedManualQueryRef.current = defaultShowManualQuery ? defaultManualQuery : '';
    displayedContextRef.current = {};
    for (const tab of Object.keys(requestSeqRef.current)) {
      requestSeqRef.current[tab] += 1;
    }
    const nextInitialActiveTab
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
    setExpandedBySource({});
    setErrorBySource({});
    setReleasesBySource({});

    void fetchReleaseResults(activeTab, {
      force: true,
      manualQueryOverride: manualSearchQuery,
    });
```

with:

```ts
    setExpandedBySource({});
    setErrorBySource({});
    setReleasesBySource({});

    // Submitting makes this the applied query: filters, tabs and expansion use it from now
    // on, whatever the text field holds later.
    appliedManualQueryRef.current = manualSearchQuery;
    void fetchReleaseResults(activeTab, {
      force: true,
      manualQueryOverride: manualSearchQuery,
    });
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/tests/releaseSearchSession.test.ts`
Expected: PASS (17 passed)

- [ ] **Step 5: Typecheck, lint, format, knip, full suite**

```bash
npm run typecheck && npm run lint && npm run format:check
npm run knip > /tmp/knip-task7.txt; diff /tmp/knip-main.txt /tmp/knip-task7.txt
npm run test:unit
```
Expected: clean; knip diff only the `SourceSearchInfo` line number; vitest 407 passed.

- [ ] **Step 6: Commit** (from the repository root)

```bash
git add src/frontend/src/hooks/releaseModal/releaseSearchSession.helpers.ts \
  src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts \
  src/frontend/src/tests/releaseSearchSession.test.ts
git commit -m "fix(releases): track the query context of release results; refresh match data on expand

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Full gates

**Files:** none (verification only).

**Interfaces:**
- Consumes: everything above.
- Produces: a green branch ready for review and the user-gated release.

- [ ] **Step 1: Backend gates** (repository root)

```bash
uv run pytest tests/ -q -m "not integration and not e2e" --deselect tests/core/test_search_deadline.py::test_html_get_page_will_not_start_a_bypass_on_a_spent_budget
uv run ruff check shelfmark tests
uv run ruff format --check $(git ls-files '*.py')
uv run basedpyright
uv run basedpyright tests --skipunannotated
uv run vulture shelfmark
```
Expected: pytest — only the 9 known failures in `tests/config/test_entrypoint_permissions.py` (dry run: 4097 passed, 175 more than `main`); ruff clean; BasedPyright only the 4 known errors, at `shelfmark/main.py:2353-2356`; tests `0 errors`; vulture prints nothing. (Off a sandboxed host, also run without the `--deselect`.)

- [ ] **Step 2: Frontend gates** (`src/frontend`)

```bash
npm run typecheck && npm run lint && npm run format:check && npm run test:unit
npm run knip > /tmp/knip-final.txt; diff /tmp/knip-main.txt /tmp/knip-final.txt
```
Expected: typecheck, lint, format clean; vitest 407 passed (44 more than `main`); knip exits 1 with exactly `main`'s findings — the diff shows only `SourceSearchInfo` at `src/types/index.ts:473` instead of `:463`.

- [ ] **Step 3: Contract spot-check**

```bash
grep -n '"release_match"\|"release_name"\|irc_ranking_evidence(' shelfmark/main.py shelfmark/release_sources/prowlarr/source.py
grep -n 'RELEASE_MATCH_VERSION = 1' shelfmark/core/search_queries.py src/frontend/src/utils/releaseMatch.ts
git diff main --stat -- shelfmark/release_sources/irc/source.py shelfmark/release_sources/newznab shelfmark/release_sources/direct_download.py src/frontend/src/utils/releaseSort.ts src/frontend/src/utils/releaseCache.ts
```
Expected: the first prints the annotation, IRC-evidence and Prowlarr lines; the second prints one line per file; the third prints nothing (those files are unchanged).

---

### Task 9: Release and acceptance — USER-GATED, text only

Do not run any of this without the user's explicit OK for each push.

1. **Shelfmark release:** `scripts/release-local.sh` (the canonical deploy; never rebuild a published tag). It ships together with #4 (`496e2f7`, already on local `main`).
2. **fleet-infra:** bump the Shelfmark image tag, push with the user's OK, then `flux reconcile` as that repo documents.
3. **UI acceptance** (spec "Acceptance") after the deploy: open High School DxD vol 5 and Overlord vol 2 with the **Default** sort and no format, language or indexer filter.
   - The right volume ranks above other volumes and manga, and those carry `Vol N` / `Manga/Comic` badges; a matching release carries none.
   - Choose a saved column sort (e.g. Size): it still applies, and badges still show.
   - Run a manual search, close and reopen the book, expand the search: no unannotated manual rows reappear; run a manual search and expand it: the manual rows are replaced or merged, never mixed with automatic rows.

---

## Self-review

- **Spec coverage:** §1 evidence (original name, declared format, content type, structured author; precedence) → Tasks 1-4 (`release_name` Task 2; IRC line Task 3; `_release_match_payload` Task 4); §2 classifier (medium rules 1-7, compatibility, bounded comic rule, explicit volume syntax, collection evidence, match/other, natural titles incl. the amended bundle rule, author conflict, fan marker, totality) → Task 1; §3 identity → Task 1 `build_ranking_identity`, Task 4 `test_the_identity_comes_from_the_book_after_the_title_override`; §4 endpoint (when it applies, payload, audiobook/manual absent, per-release failure, informational) → Task 4; §5 ranking (one parser incl. the amended `other_volume` rule, tiers, dominance, empty candidates, no filtering, sorts unchanged, scores precomputed) → Task 5; §6 badges (outside the clamp, Fan TL? tooltip, no positive badge, compact text) → Task 6; §7 cache and merge → Task 7; Error handling → Tasks 1, 4, 5; Testing list → every named case has a test in Tasks 1-7 (source conversion: Task 2, Task 3 and Task 4's MAM M4B and IRC cases); Acceptance and Rollout → Task 9.
- **Plan-review findings (Codex, on 507e053):** decimals and years → ruling 5, Task 1; `/` between volume numbers → ruling 7, Task 1; natural-title conjunctions → ruling 7a, Task 1 (spec revision 5 amended); volume 0 and the parser downgrade → rulings 7b and 17, Tasks 1 and 5 (spec §5 amended); volume-context ranges and masking → ruling 7, Task 1; per-contributor authors → ruling 9, Task 1; fan-marker separators → ruling 10, Task 1; malformed identities → ruling 11, Task 1; query context, applied query, discard → rulings 21-22, Task 7; malformed extras in the merge → ruling 23, Task 7; extracted orchestration and sort path → ruling 24, Tasks 5 and 7; IRC original line → ruling 16a, Tasks 3-4; scoring-boundary instrumentation → Task 5 `scores 2000 releases once each, outside the comparator`.
- **Unchanged predicate:** `is_identity_hit`, `SearchIdentity` and their 189 tests are untouched (Task 1 adds code only after `any_identity_hit`).
- **Type consistency:** `RankingIdentity`, `ReleaseMatch.to_payload()`, `build_ranking_identity`, `classify_release` (Task 1) and `ranking_evidence` (Task 3) are used with the same names and keywords in Task 4; the payload keys in Task 4 equal those `parseReleaseMatch` reads in Task 5; `parseReleaseMatch` feeds Tasks 5-6; the Task 7 helpers are defined and used in Task 7 only.
- **Dry run:** every code block above was applied verbatim to a throwaway worktree of `main` (with this plan and the amended spec); each Step 2 failed for the stated reason and each Step 4-5 passed with the stated counts.
