# Release Match Ranking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For ebook searches, the release modal's default "best match" sort puts the requested volume first and other volumes, manga/comic editions, audiobooks and video last, with small mismatch badges, acting only on strong explicit evidence so anything ambiguous keeps today's order.

**Architecture:** A pure classifier, `classify_release`, in `shelfmark/core/search_queries.py` (separate from the ladder's `is_identity_hit`) reads each release's original name, declared formats, content type and structured author against a `RankingIdentity` built once per request with `build_search_identity`. `/api/releases` adds a versioned `extra["release_match"]` to every release of an ebook, metadata-provider, non-manual search (Prowlarr first keeps MAM's original name in `extra["release_name"]`). The frontend parses it once (`parseReleaseMatch`), adds a ±20000 tier bonus in `sortReleasesByBookMatch`, renders mismatch badges below the title clamp, keeps manual-query responses out of the book's cache, and refreshes match data on duplicate rows when an expanded search merges.

**Tech Stack:** Python 3.14 (Flask), uv, pytest (+xdist), Ruff 0.16.5 (pre-commit hook: ruff 0.15.10 via prek), BasedPyright, Vulture; React 19 + TypeScript 7, Vitest 4 (`react-dom/server` static rendering, no DOM environment), oxlint, oxfmt, knip; npm (`src/frontend/package-lock.json`).

**Spec:** `docs/superpowers/specs/2026-10-08-release-match-ranking-design.md` (commit 571c879) — read it fully, including the "Revisions after Codex review" table, before any task. The spec is binding; the rulings below only fill its gaps.

## Global Constraints

- **Ebook searches only.** Audiobook searches, manual queries (`manual_query`), the `manual` provider and source-browse flows get no `release_match` and keep today's order.
- **Out of scope:** edition guessing beyond the explicit fan marker, spin-off detection, Roman-numeral volumes, localized-title identities, and any change to what is searched or sent to indexers. `is_identity_hit` and its tests do not change.
- **Ranking acts only on strong, explicit evidence.** Promote only on explicit volume syntax naming this volume; demote only on a declared format/content type for another medium, a technical video marker, a non-title medium word, or explicit volume syntax naming another volume. Otherwise `unknown`.
- `classify_release` is pure and total: junk `name` gives `ReleaseMatch("unknown", None, "unknown", True, False)`; it never raises.
- Payload, exactly: `extra["release_match"] = {"v": 1, "volume", "other_volume", "medium", "compatible", "fan_marker"}`; `volume` in `match|other|unknown`, `medium` in `ebook|comic|audio|video|unknown`, `other_volume` set only for `other`.
- Endpoint failures: classification runs per release inside `try`/`except`; a failure is logged at DEBUG and leaves the key absent; it never fails the request. The key is informational: nothing downstream reads or persists it.
- Frontend tiers (default sort only): top `+20000` (`match` and `compatible`), middle `0` (no parsed match, or `unknown` and `compatible`), bottom `-20000` (`other`, or not `compatible`). Scores are computed once per release, outside the comparator. Nothing is filtered; column sorts, the format sort, saved sorts and filters are unchanged.
- Badges: `Vol N` (other), `Manga/Comic` (comic and not compatible), `Audiobook` (audio), `Video` (video); secondary `Fan TL?` with the tooltip "The release name says this is a fan translation". No positive badge. Badges sit on their own line below the title, outside the two-line clamp; compact (mobile) badges are plain text.
- Cache: a manual-query response is never written to the book's cache entry; an expanded response's `extra.release_match` replaces the old one on duplicate release IDs.
- Python: bare `except A, B:` (PEP 758) is valid here — do not "fix" it.
- Commits run the repo's prek hooks (ruff-check 0.15.10, ruff-format, oxfmt). Never pass `--no-verify`. Never push from a plan step.
- Never contact Prowlarr, Hardcover or the cluster from a plan step except the user-gated Task 8.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Rulings on spec ambiguities

Binding for this plan; each is pinned by a test in the owning task.

1. **The spec's "Unknown" page-count names** (`The Expanse Leviathan Wakes [320] EPUB`, `… - 451 pages EPUB`) are checked against *Caliban's War* (The Expanse 2), where they are `unknown` — a bare `[N]` or `- N` is not volume syntax, so they are not `other` 320/451. Against *Leviathan Wakes* the same names are `match` by the natural-title rule ("451" does not veto). Task 1 `test_a_page_count_is_not_another_volume`.
2. **Medium needs declared evidence to be `ebook`.** `Overlord Vol. 2: Episodes of the Kingdom EPUB` is tested with `formats=["epub"]` (spec rule 6: name words never make `ebook`). Without declared evidence the medium is `unknown`, which is equally compatible and shows no badge. Task 1 `test_episodes_in_a_light_novel_title_is_not_video`, `test_ebook_evidence`.
3. **Ebook content type** is `"ebook"` or `"book"` (Prowlarr and Newznab label their ebook categories `"book"`); `"audiobook"` is audio; any other value (`"other"`, direct download's `"book (fiction)"`) is ignored and the declared format decides. Declared ebook formats are exactly `epub`, `mobi`, `azw3`, `pdf`.
4. **Explicit volume syntax details.** `Vol`/`Volume`/`Vols` take `.`, space, `-` or (normalised) `_` separators; `vNN`, `#N`, `Book N`; `[<series> NN]` and `<series> NN` use **one** separator (a whitespace run, `.` or `-`), so `High School DxD - 5` is a bare `- N`, not syntax; `<series>` is the last significant token of the series key. N is 1–3 digits (0 allowed).
5. **A partial number** (`Vol. 5.5`, `Vol. 5a`, `v05.5`, `#5.5`) makes the whole release's volume `unknown` rather than being ignored — ambiguity resolves to unknown. A dot followed by 1–2 digits is a fraction; `Vol.05.2016` (a year) is not. Task 1 Review Focus 3.
6. **"More than one explicit volume number"** means distinct numbers; `Overlord Vol. 2 [Overlord 02]` is one volume.
7. **Range/list collection evidence** also covers a range of two 1–3 digit numbers anywhere in the name (`-`, `–`, `—`, `~`, `&`, `+`, `to`, `and`), so `Overlord 1-3 (epub)` and `The Expanse 1-3 …` are `unknown`, and a list right after explicit syntax (`Vol. 2, 3`, `v02-v03`). Separators between *names* (no numbers on both sides) are not evidence, so per spec revision 5 `Leviathan Wakes & Caliban's War` can natural-match for ranking (the ladder still treats it as a bundle).
8. **Own-title exclusion** (medium rules 4–5) uses `RankingIdentity.title_tokens`; a matched phrase counts only if one of its words is not a title word (`The Manga Guide to Physics` is not a comic release of itself). With no identity there is no exclusion.
9. **Author conflict** compares word tokens of two or more characters (initials ignored). Placeholder authors (`Unknown`, `Various`, `Anonymous`, `N/A`, `NA`, `None`) are missing, not conflicting — IRC's parser writes `Unknown`. A conflict only downgrades `match`. Task 1 Review Focus 1.
10. **Fan marker** words may be separated by space, `.`, `-` or `_`; `fan translated` (space) also counts; plurals do not.
11. **Junk input.** A `name` that is not a non-blank string gives the spec's all-unknown result even if formats are declared; junk `formats`, `content_type` or `identity` are tolerated one by one (an `identity` that is not a `RankingIdentity` decides no volume and makes a comic incompatible).
12. **Identity construction** lives in `build_ranking_identity(*, title, current_query, series_name, series_position, authors)` in `search_queries.py`, which calls `build_search_identity` with exactly the spec §3 arguments; `book_is_comic` is the bounded rule (`manga`, `comic(s)`, `graphic novel(s)` on word boundaries over title + series). `authors` keeps non-blank strings only.
13. **Endpoint scope.** Annotation runs only in the metadata-provider branch (a registered provider, not `manual`, not a source-browse provider, not a query browse), when `content_type == "ebook"` and `manual_query` is empty; the identity is built right after `book.title = title_param`. Task 3 Review Focus 4.
14. **Annotation is applied to the serialized dicts** (`asdict` copies), never to the `Release` objects a source may cache. A release whose serialized `extra` is not a dict gets `{}` only when it is annotated.
15. **Failure logging** is `logger.debug("Release match classification failed for %s: %s", source_id, exc)` under `except Exception as exc:  # noqa: BLE001 - …`. With `exc_info=True` the project ruff (0.16.5) reports the `noqa` as unused (RUF100) while the commit hook's ruff (0.15.10) still reports BLE001 without it; this form passes both.
16. **Prowlarr `release_name`** is always present in Prowlarr's `extra`: the raw indexer title when `bookTitle` replaced it, otherwise `None`. The endpoint uses it when it is a non-blank string, else `release.title`. Newznab never substitutes and is unchanged.
17. **Frontend parser.** `v` must be the number `1`; when `volume` is `other`, `other_volume` must be a positive integer; otherwise it must be `null` (or absent). Consequence of the spec's "positive integer": a backend `other` with volume 0 parses as `null` (no tier, no badge).
18. **Tier precedence.** Bottom wins: `match` but not `compatible` (the right volume of the manga) is bottom. `unknown`+`compatible` and no parsed match are middle.
19. **Empty title candidates:** the score is the tier alone, and ties keep input order.
20. **Badge rendering.** The `Fan TL?` tooltip is a native `title` attribute (the mechanism the existing "Unsupported" format badge uses), in both layouts. Mismatch badges use the amber "Unsupported" style, `Fan TL?` the gray fallback style. Order: `Vol N`, `Manga/Comic`, `Audiobook`, `Video`, `Fan TL?`. "List and card rows" are `ReleaseRow`'s desktop grid and mobile layout (the only release-row renderings); compact badges are plain text separated by `·`, like the mobile info line. `ReleaseRow` is exported for the component test.
21. **Cache reads too.** A manual-query search neither reads nor writes the book's cache entry (`usesBookReleaseCache`), so it can never show the book's cached results either. The hook's decisions are tested through extracted pure helpers (`releaseSearchSession.helpers.ts`), the repo's pattern for hooks (`useRequests.helpers.ts`), since the frontend has no DOM test environment.
22. **Merge replacement is literal.** On a duplicate `source_id` the existing row keeps its place and data, but its `extra.release_match` becomes the incoming one, and is removed when the incoming row has none; new rows are appended in response order (as today).

## Review Focus

1. **A release whose author field is a placeholder or written differently** (IRC's `Unknown`, `Maruyama Kugane`, `Kugane Maruyama, so-bin`) → still the right volume, never demoted by the author-conflict rule. Tests: `TestRankingReviewFocus::test_a_placeholder_author_is_missing_not_a_conflict` and `::test_name_order_and_extra_contributors_are_not_a_conflict` in Task 1.
2. **Indexer escaping and file version tags** (`&amp;`, `(v1.1)`, `[v2.0]`) → the volume is still read correctly, never `unknown` or another volume. Test: `TestRankingReviewFocus::test_escaped_names_and_version_tags_make_no_volume_or_bundle` in Task 1.
3. **Half volumes and lettered volumes** (`Vol. 5.5`, `v05.5`, `Vol. 5a`) → never top tier for vol 5. Test: `TestRankingReviewFocus::test_a_fractional_or_lettered_volume_is_unknown` in Task 1.
4. **The manual provider** (a user-typed title with no metadata) → no `release_match`, today's order. Test: `TestNoAnnotation::test_the_manual_provider_carries_no_release_match` in Task 3.
5. **A mobile row with several mismatches** (another volume of the manga, fan-translated) → one readable plain-text line with no orphan separators. Test: `ReleaseMatchBadges > separates several compact badges without orphan separators` in Task 5.

## File Structure

| File | Change |
|---|---|
| `shelfmark/core/search_queries.py` | `RankingIdentity`, `ReleaseMatch`, `RELEASE_MATCH_VERSION`, `is_comic_book`, `build_ranking_identity`, `classify_release` and private helpers, appended after the ladder code (Task 1) |
| `shelfmark/release_sources/prowlarr/source.py` | `_prowlarr_result_to_release` keeps `extra["release_name"]` when `bookTitle` replaces the title (Task 2) |
| `shelfmark/main.py` | `_release_match_payload`, `_annotate_release_matches`; `/api/releases` builds the identity after the title override and annotates serialized releases (Task 3) |
| `src/frontend/src/types/index.ts` | `ReleaseMatch` interface (Task 4) |
| `src/frontend/src/utils/releaseMatch.ts` | **new** — `parseReleaseMatch` (Task 4) |
| `src/frontend/src/utils/releaseScoring.ts` | tier bonus in `sortReleasesByBookMatch`, empty-candidates path (Task 4) |
| `src/frontend/src/components/ReleaseMatchBadges.tsx` | **new** — mismatch and `Fan TL?` badges (Task 5) |
| `src/frontend/src/components/ReleaseModal.tsx` | `ReleaseRow` exported; badges below the title in both layouts (Task 5) |
| `src/frontend/src/hooks/releaseModal/releaseSearchSession.helpers.ts` | **new** — `usesBookReleaseCache`, `mergeExpandedReleases` (Task 6) |
| `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts` | uses the helpers for cache reads/writes and expanded merges (Task 6) |
| `shelfmark/release_sources/irc/*`, `direct_download.py`, `newznab/*`, `is_identity_hit`, `releaseSort.ts`, `releaseCache.ts`, `releasePayload.ts` | **unchanged** |
| Backend tests | `tests/core/test_search_queries.py` (Task 1), `tests/prowlarr/test_source.py` (Task 2), `tests/core/test_releases_api_release_match.py` (new, Task 3) |
| Frontend tests | `src/frontend/src/tests/releaseMatch.test.ts`, `releaseScoring.test.ts` (new, Task 4), `releaseMatchBadges.test.tsx` (new, Task 5), `releaseSearchSession.test.ts` (new, Task 6) |

**Test-run notes (environment, not this feature):**
- Set up once: `uv sync --all-extras` and `cd src/frontend && npm ci`.
- `pytest` runs with `-n auto` by default (`pyproject.toml`). Run endpoint tests (`tests/core/test_releases_api_*.py`) without `tests/newznab` in the same invocation (`tests/newznab/conftest.py` stubs `flask_socketio`).
- On a sandboxed macOS host `tests/core/test_search_deadline.py::test_html_get_page_will_not_start_a_bypass_on_a_spent_budget` can hang (it reaches the network); deselect it where noted.
- Known pre-existing noise: 9 failures in `tests/config/test_entrypoint_permissions.py` on macOS; 4 BasedPyright errors at `shelfmark/main.py:2305-2308` on `main` (the same four lines move to `2346-2349` after Task 3 adds 41 lines above them); `npm run knip` exits 1 on `main` with 2 unused exports (`SEARCH_MODE`, `DISCOVER_ROWS_BY_PROVIDER`) and 27 unused exported types — record that list first (`cd src/frontend && npm run knip > /tmp/knip-main.txt`); after each frontend task the list must be identical apart from line numbers in `src/types/index.ts`.
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
  - `classify_release(*, name: object, formats: Sequence[object], content_type: object, release_author: object, identity: RankingIdentity | None) -> ReleaseMatch`

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
_FORMAT_TOKENS = "|".join(sorted(_AUDIO_FORMATS | _COMIC_FORMATS | _EBOOK_FORMATS))

# Technical video markers only: plain words such as "episode" say nothing about the medium
# ("Overlord Vol. 2: Episodes of the Kingdom" is a light novel).
_RANK_VIDEO_RE = re.compile(
    r"\b(?:2160p|1080p|720p|480p|x264|x265|h\.?264|h\.?265|hevc|mkv|mp4|avi"
    r"|bdrip|web-?dl|webrip|s\d{1,2}e\d{1,3})\b",
    re.IGNORECASE,
)
_RANK_AUDIO_WORD_RE = re.compile(r"\b(?:m4b|mp3|audiobook)\b", re.IGNORECASE)
_RANK_COMIC_WORD_RE = re.compile(r"\b(?:manga|comics?|graphic[\s.-]+novels?)\b", re.IGNORECASE)
_FAN_MARKER_RE = re.compile(
    r"\b(?:baka[\s.-]tsuki|fan[\s.-]tl|fan[\s.-]translation|fan-translated|scanlation)\b",
    re.IGNORECASE,
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
# A number that is not a whole volume: a fraction ("5.5" - not "05.2016", a year) or a
# letter suffix ("5a"). Ambiguous, so the release's volume is unknown.
_RANK_PARTIAL_VOLUME_RE = re.compile(r"\.\d{1,2}(?!\d)|[^\W\d_]")
# A second volume right after the first: "5-6", "5 & 6", "5 to 7", "v05-v07", "1, 2".
_RANK_VOLUME_LIST_RE = re.compile(
    r"\s*(?:[-–—~&+,]|\bto\b|\band\b|\bthrough\b)\s*"
    r"(?:vol(?:ume)?s?\b\.?\s*|v|#|book\s+)?\d{1,3}(?![\d.]|[^\W\d_])",
    re.IGNORECASE,
)
# Collection evidence: several books in one release. Contributor separators on their own
# ("Corey & Abraham", "Author / Illustrator") are not.
_RANK_COLLECTION_RE = re.compile(
    r"\b(?:omnibus|box(?:ed)?[\s.-]*set|complete[\s.-]+series|collection|trilogy|duology"
    r"|quartet)\b"
    r"|\bbooks[\s.-]*\d{1,3}\s*(?:[-–—~&+,]|\bto\b|\band\b|\bthrough\b)\s*\d{1,3}(?!\d)"
    r"|(?<![\d.])\d{1,3}\s*(?:[-–—~&+]|\bto\b|\band\b)\s*\d{1,3}(?![\d.]|[^\W\d_])",
    re.IGNORECASE,
)
# Author names that say nothing about who wrote the book.
_PLACEHOLDER_AUTHORS = frozenset({"unknown", "various", "anonymous", "n/a", "na", "none"})


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
    other_volume: int | None  # set only when volume == "other"
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
    author_list = authors if isinstance(authors, (list, tuple)) else ()
    return RankingIdentity(
        series_key=search_identity.series_key,
        position=search_identity.position,
        title_tokens=search_identity.title_tokens,
        title_names_volume=search_identity.title_names_volume,
        book_is_comic=is_comic_book(title, series_name),
        authors=tuple(a for a in author_list if isinstance(a, str) and a.strip()),
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


def _explicit_volumes(text: str, series_tokens: tuple[str, ...]) -> set[int] | None:
    """Volume numbers ``text`` names in explicit syntax; None when one is not a whole volume."""
    patterns = list(_RANK_VOLUME_RES)
    if series_tokens:
        last = re.escape(series_tokens[-1])
        # "[Overlord 02]" (and "[Overlord - Volume 02]", which "Volume" already covers).
        patterns.append(re.compile(rf"\b{last}(?:\s+|[.-])(\d{{1,3}})\s*\]"))
        # "Overlord 02" followed by " - ", "]", "(", a year, a format or the end. One
        # separator only: "High School DxD - 5" is a bare "- N", not volume syntax.
        patterns.append(
            re.compile(
                rf"\b{last}(?:\s+|[.-])(\d{{1,3}})(?=\s+-\s|\s*\]|\s*\(|[\s.-]+(?:19|20)\d{{2}}(?!\d)"
                rf"|[\s.-]+(?:{_FORMAT_TOKENS})\b|\s*$)"
            )
        )
    numbers: set[int] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            if _RANK_PARTIAL_VOLUME_RE.match(text, match.end(1)):
                return None
            numbers.add(int(match.group(1)))
    return numbers


def _has_volume_list(text: str) -> bool:
    for pattern in _RANK_VOLUME_RES:
        for match in pattern.finditer(text):
            if _RANK_VOLUME_LIST_RE.match(text, match.end(1)):
                return True
    return False


def _volume(text: str, identity: RankingIdentity) -> tuple[Volume, int | None]:
    if not identity.series_key or identity.position is None:
        return "unknown", None
    if _RANK_COLLECTION_RE.search(text) or _has_volume_list(text):
        return "unknown", None
    key_tokens = significant_tokens(identity.series_key)
    numbers = _explicit_volumes(text, key_tokens)
    if numbers is None or len(numbers) > 1:
        return "unknown", None

    present = set(_tokens(text))
    has_key = bool(key_tokens) and all(token in present for token in key_tokens)
    if numbers and has_key:
        (number,) = numbers
        return ("match", None) if number == identity.position else ("other", number)

    # A series book whose title names no volume ("Leviathan Wakes", The Expanse 1) is
    # also named by its own title words, as long as no explicit volume names another
    # number. Other numbers ("2nd edition", "451", a year) do not veto it.
    if identity.title_names_volume or not numbers <= {identity.position}:
        return "unknown", None
    series_words = set(key_tokens)
    title_tokens = [t for t in identity.title_tokens if t]
    if (
        title_tokens
        and all(token in present for token in title_tokens)
        and any(token not in series_words for token in title_tokens)
    ):
        return "match", None
    return "unknown", None


def _author_tokens(text: str) -> set[str]:
    return {token for token in _tokens(html.unescape(text)) if len(token) > 1}


def _author_conflicts(release_author: object, authors: tuple[str, ...]) -> bool:
    """True only when the release names a real author sharing no word with the book's."""
    if not isinstance(release_author, str):
        return False
    if " ".join(release_author.split()).casefold() in _PLACEHOLDER_AUTHORS:
        return False
    release_tokens = _author_tokens(release_author)
    wanted = set().union(*(_author_tokens(author) for author in authors)) if authors else set()
    if not release_tokens or not wanted:
        return False
    return not release_tokens & wanted


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
    if not isinstance(identity, RankingIdentity):
        identity = RankingIdentity()
    # Indexers send "&amp;" for "&"; a file version tag "(v2.0)" is not a volume; "_" is
    # a separator in scene names.
    text = _VERSION_TAG_RE.sub(" ", html.unescape(name)).replace("_", " ").casefold()
    declared = _declared_formats(formats)
    kind = content_type.strip().casefold() if isinstance(content_type, str) else ""

    medium = _medium(text, declared, kind, set(identity.title_tokens))
    compatible = medium in {"ebook", "unknown"} or (medium == "comic" and identity.book_is_comic)
    volume, other_volume = _volume(text, identity)
    if volume == "match" and _author_conflicts(release_author, identity.authors):
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
Expected: PASS (298 passed — 189 existing ladder/predicate tests unchanged plus 109 new).

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
- Produces: every Prowlarr `Release.extra` has `"release_name": str | None` — the raw indexer title when format detection replaced `title` with `bookTitle`, otherwise `None`. Task 3 classifies on it.

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

### Task 3: `/api/releases` annotates ebook releases with `extra.release_match`

**Files:**
- Modify: `shelfmark/main.py` (imports ~line 92 and the `TYPE_CHECKING` block ~line 109; new helpers after `_serialize_release` ~line 1047; `api_releases` ~lines 3174, 3237 and 3285)
- Test: `tests/core/test_releases_api_release_match.py` (new)

**Interfaces:**
- Consumes: `build_ranking_identity`, `classify_release`, `RankingIdentity`, `ReleaseMatch.to_payload()` (Task 1); `extra["release_name"]` (Task 2); `_prowlarr_result_to_release`, `IRCReleaseSource._convert_to_releases`, `parse_result_line` (tests only).
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


def _irc_epub_release() -> Release:
    source = IRCReleaseSource()
    source._online_servers = set()
    result = parse_result_line(IRC_LINE)
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


def _search(client, main_module, query: dict[str, str], book: BookMetadata | None = None):
    sources = {
        "prowlarr": _Source([_mam_m4b_release(), _other_volume_release()]),
        "irc": _Source([_irc_epub_release()]),
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
Expected: FAIL — 3 failed: `test_releases_from_two_sources_are_annotated` with `KeyError: 'release_match'`, `test_the_identity_comes_from_the_book_after_the_title_override` with `AttributeError: module 'shelfmark.main' has no attribute 'build_ranking_identity'`, `test_a_classifier_failure_leaves_only_that_release_unannotated` with `... has no attribute 'classify_release'`. The 3 `TestNoAnnotation` tests already pass on `main` and stay as pins.

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
if TYPE_CHECKING:
    from shelfmark.metadata_providers import BookMetadata, MetadataProvider
```

with:

```python
if TYPE_CHECKING:
    from shelfmark.core.search_queries import RankingIdentity
    from shelfmark.metadata_providers import BookMetadata, MetadataProvider
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
    name = extra.get("release_name")
    if not isinstance(name, str) or not name.strip():
        name = release.title
    extra_formats = extra.get("formats")
    formats = [release.format, *(extra_formats if isinstance(extra_formats, list) else ())]
    return classify_release(
        name=name,
        formats=formats,
        content_type=release.content_type,
        release_author=extra.get("author"),
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
Expected: PASS (6 passed)

Run: `uv run pytest tests/core/test_releases_api_*.py -q`
Expected: PASS (24 passed)

- [ ] **Step 5: Lint and typecheck**

```bash
uv run ruff check shelfmark tests
uv run ruff format --check shelfmark tests
uv run basedpyright
uv run basedpyright tests --skipunannotated
uv run vulture shelfmark
```
Expected: ruff clean; BasedPyright only the 4 known `reportOptionalSubscript` errors, now at `shelfmark/main.py:2346-2349`; tests `0 errors`; vulture prints nothing.

- [ ] **Step 6: Commit**

```bash
git add shelfmark/main.py \
  tests/core/test_releases_api_release_match.py
git commit -m "feat(releases): annotate ebook releases with how they match the book

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Frontend — `ReleaseMatch` type, `parseReleaseMatch`, tiered default sort

All paths below are relative to `src/frontend/`; run commands from there.

**Files:**
- Modify: `src/types/index.ts` (after `interface Release`, ~line 460), `src/utils/releaseScoring.ts` (imports; `sortReleasesByBookMatch`, end of file)
- Create: `src/utils/releaseMatch.ts`
- Test: `src/tests/releaseMatch.test.ts` (new), `src/tests/releaseScoring.test.ts` (new)

**Interfaces:**
- Consumes: the Task 3 payload `extra.release_match = {v: 1, volume, other_volume, medium, compatible, fan_marker}`; `isRecord` from `utils/objectHelpers`.
- Produces: `export interface ReleaseMatch { volume: 'match' | 'other' | 'unknown'; other_volume: number | null; medium: 'ebook' | 'comic' | 'audio' | 'video' | 'unknown'; compatible: boolean; fan_marker: boolean }` in `types/index.ts`; `parseReleaseMatch(extra: unknown): ReleaseMatch | null` in `utils/releaseMatch.ts` (Task 5 uses it); `sortReleasesByBookMatch(releases, titleCandidates, authorCandidates)` keeps its signature.

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

  it('rejects an other volume that is not a positive integer', () => {
    for (const otherVolume of [0, -1, 2.5, '3', null, undefined, Number.NaN]) {
      expect(
        parseReleaseMatch({ release_match: { ...valid, other_volume: otherVolume } }),
      ).toBeNull();
    }
  });

  it('rejects an other volume on a release that is not another volume', () => {
    expect(
      parseReleaseMatch({ release_match: { ...valid, volume: 'match', other_volume: 3 } }),
    ).toBeNull();
  });
});
```

**Create** `src/frontend/src/tests/releaseScoring.test.ts`:

```ts
import { describe, expect, it } from 'vitest';

import type { Release } from '../types';
import { sortReleasesByBookMatch } from '../utils/releaseScoring';
import { sortReleases } from '../utils/releaseSort';

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

  it('leaves a saved column sort alone', () => {
    const releases = [
      { ...release('small-match', 'A', matchPayload('match')), size_bytes: 10 },
      { ...release('big-other', 'B', matchPayload('other')), size_bytes: 30 },
      { ...release('mid-none', 'C'), size_bytes: 20 },
    ];

    expect(ids(sortReleases(releases, 'size_bytes', 'desc'))).toEqual([
      'big-other',
      'mid-none',
      'small-match',
    ]);
  });

  it('sorts 2000 releases reading each match payload once', () => {
    let reads = 0;
    const volumes: Volume[] = ['match', 'other', 'unknown'];
    const releases: Release[] = Array.from({ length: 2000 }, (_, index) => {
      const payload = matchPayload(volumes[index % 3]);
      const extra: Record<string, unknown> = {};
      Object.defineProperty(extra, 'release_match', {
        enumerable: true,
        get: () => {
          reads += 1;
          return payload;
        },
      });
      return {
        source: 'prowlarr',
        source_id: `r-${index}`,
        title: `High School DxD Vol ${index % 30}`,
        extra,
      };
    });

    const sorted = sortReleasesByBookMatch(releases, CANDIDATES, []);

    expect(reads).toBe(2000);
    expect(sorted).toHaveLength(2000);
    expect(sorted.slice(0, 667).every((r) => Number(r.source_id.slice(2)) % 3 === 0)).toBe(true);
    expect(sorted.slice(-667).every((r) => Number(r.source_id.slice(2)) % 3 === 1)).toBe(true);
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npx vitest run src/tests/releaseMatch.test.ts src/tests/releaseScoring.test.ts`
Expected: FAIL — `releaseMatch.test.ts`: `Cannot find module '../utils/releaseMatch'`; `releaseScoring.test.ts`: 4 failed (`lets the tier beat the title score`, `puts an incompatible unknown volume in the bottom tier`, `still orders by tier without title candidates`, `sorts 2000 releases reading each match payload once` — `expected +0 to be 2000`), 5 passed (today's order, malformed payload and saved sort already hold and stay as pins).

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

/**
 * The release's `extra.release_match`, or null when it is missing or malformed.
 *
 * The one parser for ranking and badges: a wrong version, an unknown volume or medium,
 * non-boolean flags, or an `other_volume` that is not a positive integer (or is set on a
 * release that is not another volume) all give null, which means today's behaviour.
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
    if (typeof otherVolume !== 'number' || !Number.isInteger(otherVolume) || otherVolume < 1) {
      return null;
    }
    return { volume, other_volume: otherVolume, medium, compatible, fan_marker: fanMarker };
  }
  if (otherVolume != null) return null;
  return { volume, other_volume: null, medium, compatible, fan_marker: fanMarker };
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

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/tests/releaseMatch.test.ts src/tests/releaseScoring.test.ts`
Expected: PASS (16 passed)

- [ ] **Step 5: Typecheck, lint, format, knip, full suite**

```bash
npm run typecheck && npm run lint && npm run format:check
npm run knip > /tmp/knip-task4.txt; diff /tmp/knip-main.txt /tmp/knip-task4.txt
npm run test:unit
```
Expected: typecheck, lint and format clean; the knip diff shows only the `SourceSearchInfo` line moving from `src/types/index.ts:463` to `:473`; vitest 379 passed.

- [ ] **Step 6: Commit** (from the repository root)

```bash
git add src/frontend/src/types/index.ts \
  src/frontend/src/utils/releaseMatch.ts \
  src/frontend/src/utils/releaseScoring.ts \
  src/frontend/src/tests/releaseMatch.test.ts \
  src/frontend/src/tests/releaseScoring.test.ts
git commit -m "feat(releases): rank the default sort by volume and medium tiers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Mismatch badges and `Fan TL?` below the title, outside the clamp

All paths below are relative to `src/frontend/`; run commands from there.

**Files:**
- Create: `src/components/ReleaseMatchBadges.tsx`
- Modify: `src/components/ReleaseModal.tsx` (imports ~line 70; `ReleaseRow` ~line 479; desktop title ~line 581; mobile title ~line 633)
- Test: `src/tests/releaseMatchBadges.test.tsx` (new)

**Interfaces:**
- Consumes: `parseReleaseMatch` (Task 4).
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
npm run knip > /tmp/knip-task5.txt; diff /tmp/knip-main.txt /tmp/knip-task5.txt
npm run test:unit
```
Expected: clean (oxlint's `unicorn(consistent-function-scoping)` is why the test helpers live at module scope); knip diff only the `SourceSearchInfo` line number; vitest 387 passed.

- [ ] **Step 6: Commit** (from the repository root)

```bash
git add src/frontend/src/components/ReleaseMatchBadges.tsx \
  src/frontend/src/components/ReleaseModal.tsx \
  src/frontend/src/tests/releaseMatchBadges.test.tsx
git commit -m "feat(releases): mismatch and fan-translation badges below the release title

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Manual results stay out of the book cache; expanded searches refresh match data

All paths below are relative to `src/frontend/`; run commands from there.

**Files:**
- Create: `src/hooks/releaseModal/releaseSearchSession.helpers.ts`
- Modify: `src/hooks/releaseModal/useReleaseSearchSession.ts` (imports ~line 22; `fetchReleaseResults` cache read ~line 241 and response handling ~lines 268-295)
- Test: `src/tests/releaseSearchSession.test.ts` (new)

**Interfaces:**
- Consumes: `getCachedReleases`, `setCachedReleases`, `invalidateCachedReleases` (`utils/releaseCache.ts`, unchanged).
- Produces: `usesBookReleaseCache(manualQuery: string | undefined): boolean`; `mergeExpandedReleases(existing: ReleasesResponse, incoming: ReleasesResponse): ReleasesResponse`.

- [ ] **Step 1: Write the failing tests**

**Create** `src/frontend/src/tests/releaseSearchSession.test.ts`:

```ts
import { afterEach, describe, expect, it } from 'vitest';

import {
  mergeExpandedReleases,
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

const KEY = ['hardcover', 'dxd5', 'prowlarr', 'ebook'] as const;

afterEach(() => {
  invalidateCachedReleases(...KEY);
});

describe('usesBookReleaseCache', () => {
  it('is true only without a manual query', () => {
    expect(usesBookReleaseCache(undefined)).toBe(true);
    expect(usesBookReleaseCache('')).toBe(true);
    expect(usesBookReleaseCache('dxd volume 5')).toBe(false);
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

  it('annotates a previously unannotated duplicate row', () => {
    const merged = mergeExpandedReleases(
      response([plain('a')]),
      response([annotated('a', matchPayload('match'))]),
    );

    expect(merged.releases[0].extra?.release_match).toEqual(matchPayload('match'));
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
});

describe('manual search, reopen, expand', () => {
  it('shows no stale or unannotated mix', () => {
    // The hook's decisions, in the order the modal makes them.
    const store = (manualQuery: string | undefined, data: ReleasesResponse) => {
      if (usesBookReleaseCache(manualQuery)) setCachedReleases(...KEY, data);
    };

    // 1. Open the book: an annotated response is cached under the book.
    store(undefined, response([annotated('a', matchPayload('match'))]));
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
    store(undefined, reopened);
    expect(getCachedReleases(...KEY)).toBe(reopened);
    // 4. Expand: duplicates take the incoming match data; new rows arrive annotated.
    const expanded = mergeExpandedReleases(
      reopened,
      response([annotated('b', matchPayload('other', 7)), annotated('c', matchPayload('unknown'))]),
    );

    expect(expanded.releases.map((r) => [r.source_id, r.extra?.release_match])).toEqual([
      ['a', matchPayload('match')],
      ['b', matchPayload('other', 7)],
      ['c', matchPayload('unknown')],
    ]);
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

/**
 * Whether a search may read and write the book's normal release-cache entry.
 *
 * A manual query's results are the user's own words and carry no `release_match`, so
 * they are never stored under the book: reopening the book always shows a normal,
 * annotated search.
 */
export function usesBookReleaseCache(manualQuery: string | undefined): boolean {
  return !manualQuery;
}

function withIncomingReleaseMatch(existing: Release, incoming: Release): Release {
  const extra: Record<string, unknown> = { ...existing.extra };
  if (incoming.extra !== undefined && 'release_match' in incoming.extra) {
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
import { mergeExpandedReleases, usesBookReleaseCache } from './releaseSearchSession.helpers';
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
      if (!expandSearch) {
        const cached = getCachedReleases(provider, bookId, tabName, contentType);
```

with:

```ts
      if (!expandSearch && usesBookReleaseCache(currentManualQuery)) {
        const cached = getCachedReleases(provider, bookId, tabName, contentType);
```

**Replace** in `src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts`:

```ts
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
```

with:

```ts
          setReleasesBySource((prev) => {
            const existing = prev[tabName];
            if (!existing) {
              return { ...prev, [tabName]: response };
            }
            return { ...prev, [tabName]: mergeExpandedReleases(existing, response) };
          });
        } else {
          if (usesBookReleaseCache(currentManualQuery)) {
            setCachedReleases(provider, bookId, tabName, contentType, response);
          }
          setReleasesBySource((prev) => ({ ...prev, [tabName]: response }));
        }
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npx vitest run src/tests/releaseSearchSession.test.ts`
Expected: PASS (7 passed)

- [ ] **Step 5: Typecheck, lint, format, knip, full suite**

```bash
npm run typecheck && npm run lint && npm run format:check
npm run knip > /tmp/knip-task6.txt; diff /tmp/knip-main.txt /tmp/knip-task6.txt
npm run test:unit
```
Expected: clean; knip diff only the `SourceSearchInfo` line number; vitest 394 passed.

- [ ] **Step 6: Commit** (from the repository root)

```bash
git add src/frontend/src/hooks/releaseModal/releaseSearchSession.helpers.ts \
  src/frontend/src/hooks/releaseModal/useReleaseSearchSession.ts \
  src/frontend/src/tests/releaseSearchSession.test.ts
git commit -m "fix(releases): keep manual results out of the book cache; refresh match data on expand

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Full gates

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
Expected: pytest — only the 9 known failures in `tests/config/test_entrypoint_permissions.py` (dry run: 4039 passed, 117 more than `main`); ruff clean (`418 files already formatted`); BasedPyright only the 4 known errors, at `shelfmark/main.py:2346-2349`; tests `0 errors`; vulture prints nothing. (Off a sandboxed host, also run without the `--deselect`.)

- [ ] **Step 2: Frontend gates** (`src/frontend`)

```bash
npm run typecheck && npm run lint && npm run format:check && npm run test:unit
npm run knip > /tmp/knip-final.txt; diff /tmp/knip-main.txt /tmp/knip-final.txt
```
Expected: typecheck, lint, format clean; vitest 394 passed (31 more than `main`); knip exits 1 with exactly `main`'s findings — the diff shows only `SourceSearchInfo` at `src/types/index.ts:473` instead of `:463`.

- [ ] **Step 3: Contract spot-check**

```bash
grep -n '"release_match"\|"release_name"' shelfmark/main.py shelfmark/release_sources/prowlarr/source.py
grep -n 'RELEASE_MATCH_VERSION = 1' shelfmark/core/search_queries.py src/frontend/src/utils/releaseMatch.ts
git diff main --stat -- shelfmark/release_sources/irc shelfmark/release_sources/newznab shelfmark/release_sources/direct_download.py src/frontend/src/utils/releaseSort.ts src/frontend/src/utils/releaseCache.ts
```
Expected: the first prints the annotation and Prowlarr lines; the second prints one line per file; the third prints nothing (those files are unchanged).

---

### Task 8: Release and acceptance — USER-GATED, text only

Do not run any of this without the user's explicit OK for each push.

1. **Shelfmark release:** `scripts/release-local.sh` (the canonical deploy; never rebuild a published tag). It ships together with #4 (`496e2f7`, already on local `main`).
2. **fleet-infra:** bump the Shelfmark image tag, push with the user's OK, then `flux reconcile` as that repo documents.
3. **UI acceptance** (spec "Acceptance") after the deploy: open High School DxD vol 5 and Overlord vol 2 with the **Default** sort and no format, language or indexer filter.
   - The right volume ranks above other volumes and manga, and those carry `Vol N` / `Manga/Comic` badges; a matching release carries none.
   - Choose a saved column sort (e.g. Size): it still applies, and badges still show.
   - Run a manual search, close and reopen the book, expand the search: no unannotated manual rows reappear.

---

## Self-review

- **Spec coverage:** §1 evidence (original name, declared format, content type, structured author; precedence) → Tasks 1-3 (`release_name` Task 2; `_release_match_payload` Task 3); §2 classifier (medium rules 1-7, compatibility, bounded comic rule, explicit volume syntax, collection evidence, match/other, natural titles, author conflict, fan marker, totality) → Task 1; §3 identity → Task 1 `build_ranking_identity`, Task 3 `test_the_identity_comes_from_the_book_after_the_title_override`; §4 endpoint (when it applies, payload, audiobook/manual absent, per-release failure, informational) → Task 3; §5 ranking (one parser, tiers, dominance, empty candidates, no filtering, sorts unchanged, scores precomputed) → Task 4; §6 badges (outside the clamp, Fan TL? tooltip, no positive badge, compact text) → Task 5; §7 cache and merge → Task 6; Error handling → Tasks 1, 3, 4; Testing list → every named case has a test in Tasks 1-6 (source conversion: Task 2 and Task 3's MAM M4B and IRC cases); Acceptance and Rollout → Task 8.
- **Unchanged predicate:** `is_identity_hit`, `SearchIdentity` and their 189 tests are untouched (Task 1 adds code only after `any_identity_hit`).
- **Type consistency:** `RankingIdentity`, `ReleaseMatch.to_payload()`, `build_ranking_identity`, `classify_release` (Task 1) are used with the same names and keywords in Task 3; the payload keys in Task 3 equal those `parseReleaseMatch` reads in Task 4; `parseReleaseMatch` feeds Tasks 4-5; `usesBookReleaseCache`/`mergeExpandedReleases` are defined and used in Task 6.
- **Dry run:** every code block above was applied verbatim to a throwaway worktree of `main` at 571c879; each Step 2 failed for the stated reason and each Step 4-5 passed with the stated counts.
