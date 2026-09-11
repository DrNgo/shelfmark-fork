# Audible Topic Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add regional Audible topic recommendation rows and a per-user cascading broad-topic/subgenre preference that pins one topic on the homepage.

**Architecture:** Pure taxonomy parsing and localized permanent-topic definitions live beside the Audible provider; a core service owns regional taxonomy caching and validation. The existing discover service resolves permanent and preferred categories server-side, while one shared React topic selector serves global and per-user settings and pure frontend helpers determine row order.

**Tech Stack:** Python 3.14, Flask, requests, pytest, React 19, TypeScript 7, Tailwind CSS, Vitest, existing `CacheService` and settings registry. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-05-audible-topic-discovery-design.md`

## Global Constraints

- Permanent rows are Fantasy, Romance, Mystery, Thriller & Suspense, Science Fiction, Historical Fiction, and Horror, in that order after Best Sellers and New Releases.
- Audible topic products use `products_sort_by=BestSellers`, a maximum of two pages, and `ROW_LIMIT = 20`.
- Topic rows render only in Audiobook and Combined views when Audible is the effective audiobook metadata provider.
- Combined mode resolves ordinary rows from the combined provider and topic rows from the audiobook provider.
- `DEFAULT_DISCOVER_TOPIC` is `list[str]`, defaults to `[]`, is user-overridable, and is not environment-backed.
- The browser stores and submits taxonomy paths only; it never controls an Audible category ID used for discovery.
- Taxonomy fresh TTL is 24 hours; topic-row fresh TTL is 6 hours; both last-good TTLs are 7 days.
- `None` means provider/taxonomy failure and activates stale fallback; `[]` means a successful empty or unsupported category and must be cached as success.
- Full-path matching is exact after trimming segment edges; preserve case, punctuation, accents, and ordering.
- Taxonomy parsing accepts at most 8 levels and 5,000 valid nodes.
- Cache keys include storefront and resolved category ID; preferred paths use deterministic JSON plus a bounded SHA-256 digest.
- Unsupported topics are hidden. Do not fall back to keywords or broader categories.
- Preserve the current discover tile visuals, square Audible art, badges, library lookup, request state, download state, and details behavior.

---

## File Structure

- Create `shelfmark/metadata_providers/audible_taxonomy.py`: immutable taxonomy model, defensive parser, exact-path lookup, localized permanent-topic paths, public serialization helpers.
- Modify `shelfmark/metadata_providers/audible.py`: categories HTTP fetch and category-filtered discover browse.
- Create `shelfmark/core/audible_topics.py`: taxonomy cache, single-flight, resolution, normalization, validation, and path hashing.
- Modify `shelfmark/core/discover.py`: split standard/topic dispatch and reuse dual-entry row caching.
- Modify `shelfmark/main.py`: topic-tree endpoint, discover row validation, and effective preference config fields.
- Modify `shelfmark/config/settings.py`: custom settings field and global save validation.
- Modify `shelfmark/config/users_settings.py`, `shelfmark/core/admin_settings_routes.py`, `shelfmark/core/user_settings_overrides.py`: per-user validation and nested backing-field exposure.
- Create `src/frontend/src/utils/audibleTopics.ts`: taxonomy/path and cascading-option helpers.
- Create `src/frontend/src/components/settings/AudibleTopicSelector.tsx`: shared cascading selector.
- Create `src/frontend/src/components/settings/customFields/AudibleTopicSelectorField.tsx`: generic settings adapter.
- Modify the custom-field registry and `UserSearchPreferencesSection.tsx`: render the shared control globally and per-user.
- Modify `src/frontend/src/utils/discoverRows.ts`, `DiscoverSection.tsx`, and `App.tsx`: deterministic mixed-provider row definitions and ordering.
- Extend focused backend/frontend test modules; do not add a DOM test dependency.

---

### Task 1: Pure Audible taxonomy model and localized permanent topics

**Files:**
- Create: `shelfmark/metadata_providers/audible_taxonomy.py`
- Create: `tests/metadata/test_audible_taxonomy.py`

**Interfaces:**
- Produces: `AudibleTopicNode`, `CORE_TOPIC_DEFINITIONS`, `CORE_TOPIC_PATHS_BY_REGION`, `parse_audible_topic_tree(payload)`, `find_audible_topic(nodes, path)`, `core_topic_path(region, key)`, `matching_core_topic_key(region, path)`, `public_topic_nodes(nodes)`, and `preferred_topic_label(path)`.
- Consumers: Tasks 2, 3, 4, 5, and 7.

- [ ] **Step 1: Write failing parser and mapping tests**

```python
from shelfmark.metadata_providers.audible_taxonomy import (
    CORE_TOPIC_DEFINITIONS,
    CORE_TOPIC_PATHS_BY_REGION,
    find_audible_topic,
    matching_core_topic_key,
    parse_audible_topic_tree,
    preferred_topic_label,
)
from shelfmark.metadata_providers.audible import REGION_TLDS


def test_parser_preserves_full_paths_and_skips_malformed_siblings():
    payload = {
        "categories": [
            {
                "id": "10",
                "name": "Science Fiction & Fantasy",
                "children": [
                    {"id": "11", "name": "Fantasy", "children": []},
                    {"id": "bad", "name": "Broken", "children": []},
                ],
            }
        ]
    }
    nodes = parse_audible_topic_tree(payload)
    assert nodes is not None
    fantasy = find_audible_topic(nodes, ["Science Fiction & Fantasy", "Fantasy"])
    assert fantasy is not None
    assert fantasy.category_id == "11"
    assert fantasy.path == ("Science Fiction & Fantasy", "Fantasy")


def test_repeated_leaf_names_require_complete_path():
    payload = {
        "categories": [
            {"id": "1", "name": "Romance", "children": [{"id": "2", "name": "Fantasy"}]},
            {"id": "3", "name": "Science Fiction & Fantasy", "children": [{"id": "4", "name": "Fantasy"}]},
        ]
    }
    nodes = parse_audible_topic_tree(payload)
    assert nodes is not None
    assert find_audible_topic(nodes, ["Romance", "Fantasy"]).category_id == "2"
    assert find_audible_topic(nodes, ["Science Fiction & Fantasy", "Fantasy"]).category_id == "4"


def test_every_region_defines_every_permanent_topic():
    expected_keys = {definition.key for definition in CORE_TOPIC_DEFINITIONS}
    assert set(CORE_TOPIC_PATHS_BY_REGION) == set(REGION_TLDS)
    assert all(set(paths) == expected_keys for paths in CORE_TOPIC_PATHS_BY_REGION.values())


def test_localized_path_matches_core_key_and_builds_label():
    path = ["SF・ファンタジー", "ファンタジー"]
    assert matching_core_topic_key("jp", path) == "topic_fantasy"
    assert preferred_topic_label(["SF・ファンタジー", "ファンタジー", "ダークファンタジー"]) == "SF・ファンタジー — ダークファンタジー"


def test_parser_stops_after_maximum_depth():
    child = {"id": "9", "name": "Too deep", "children": []}
    for depth in range(8, 0, -1):
        child = {"id": str(depth), "name": f"Level {depth}", "children": [child]}
    nodes = parse_audible_topic_tree({"categories": [child]})
    assert nodes is not None
    current = nodes[0]
    accepted_depth = 1
    while current.children:
        current = current.children[0]
        accepted_depth += 1
    assert accepted_depth == MAX_TAXONOMY_DEPTH


def test_parser_stops_after_maximum_node_count():
    categories = [
        {"id": str(index), "name": f"Topic {index}", "children": []}
        for index in range(MAX_TAXONOMY_NODES + 25)
    ]
    nodes = parse_audible_topic_tree({"categories": categories})
    assert nodes is not None
    assert len(nodes) == MAX_TAXONOMY_NODES
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/metadata/test_audible_taxonomy.py -x --tb=short`

Expected: collection fails because `shelfmark.metadata_providers.audible_taxonomy` does not exist.

- [ ] **Step 3: Implement the model, parser, and exact regional mappings**

Use immutable tuples so cached trees cannot be mutated by consumers:

```python
from dataclasses import dataclass
from typing import Any

MAX_TAXONOMY_DEPTH = 8
MAX_TAXONOMY_NODES = 5_000


@dataclass(frozen=True)
class AudibleTopicNode:
    name: str
    path: tuple[str, ...]
    category_id: str
    children: tuple["AudibleTopicNode", ...] = ()


@dataclass(frozen=True)
class CoreTopicDefinition:
    key: str
    label: str


CORE_TOPIC_DEFINITIONS = (
    CoreTopicDefinition("topic_fantasy", "Fantasy"),
    CoreTopicDefinition("topic_romance", "Romance"),
    CoreTopicDefinition("topic_mystery_thriller", "Mystery, Thriller & Suspense"),
    CoreTopicDefinition("topic_science_fiction", "Science Fiction"),
    CoreTopicDefinition("topic_historical_fiction", "Historical Fiction"),
    CoreTopicDefinition("topic_horror", "Horror"),
)

_ENGLISH_PATHS = {
    "topic_fantasy": ("Science Fiction & Fantasy", "Fantasy"),
    "topic_romance": ("Romance",),
    "topic_mystery_thriller": ("Mystery, Thriller & Suspense",),
    "topic_science_fiction": ("Science Fiction & Fantasy", "Science Fiction"),
    "topic_historical_fiction": ("Literature & Fiction", "Historical Fiction"),
    "topic_horror": ("Literature & Fiction", "Horror"),
}

CORE_TOPIC_PATHS_BY_REGION = {
    region: dict(_ENGLISH_PATHS) for region in ("us", "ca", "uk", "au")
}
CORE_TOPIC_PATHS_BY_REGION["in"] = {
    **_ENGLISH_PATHS,
    "topic_historical_fiction": ("Literature & Fiction", "Historical"),
}
CORE_TOPIC_PATHS_BY_REGION["de"] = {
    "topic_fantasy": ("Science Fiction & Fantasy", "Fantasy"),
    "topic_romance": ("Liebesromane",),
    "topic_mystery_thriller": ("Krimis & Thriller",),
    "topic_science_fiction": ("Science Fiction & Fantasy", "Science Fiction"),
    "topic_historical_fiction": ("Literatur & Belletristik", "Historische Romane"),
    "topic_horror": ("Literatur & Belletristik", "Horror"),
}
CORE_TOPIC_PATHS_BY_REGION["fr"] = {
    "topic_fantasy": ("Science-Fiction et fantasy", "Fantasy"),
    "topic_romance": ("Romance",),
    "topic_mystery_thriller": ("Policier, thrillers et œuvres à suspense",),
    "topic_science_fiction": ("Science-Fiction et fantasy", "Science-fiction"),
    "topic_historical_fiction": ("Littérature, romans et fiction", "Fiction historique"),
    "topic_horror": ("Littérature, romans et fiction", "Horreur"),
}
CORE_TOPIC_PATHS_BY_REGION["it"] = {
    "topic_fantasy": ("Fantascienza e fantasy", "Fantasy"),
    "topic_romance": ("Romanzo d'amore",),
    "topic_mystery_thriller": ("Poliziesco, thriller e suspense",),
    "topic_science_fiction": ("Fantascienza e fantasy", "Fantascienza"),
    "topic_historical_fiction": ("Letteratura e narrativa", "Narrativa storica"),
    "topic_horror": ("Letteratura e narrativa", "Horror"),
}
CORE_TOPIC_PATHS_BY_REGION["es"] = {
    "topic_fantasy": ("Ciencia ficción y fantasía", "Fantasía"),
    "topic_romance": ("Romántica",),
    "topic_mystery_thriller": ("Policíaca, negra y suspense",),
    "topic_science_fiction": ("Ciencia ficción y fantasía", "Ciencia ficción"),
    "topic_historical_fiction": ("Literatura y ficción", "Novela histórica"),
    "topic_horror": ("Literatura y ficción", "Terror"),
}
CORE_TOPIC_PATHS_BY_REGION["br"] = {
    "topic_fantasy": ("Ficção Científica e Fantasia", "Fantasia"),
    "topic_romance": ("Romance",),
    "topic_mystery_thriller": ("Mistério, Intriga e Suspense",),
    "topic_science_fiction": ("Ficção Científica e Fantasia", "Ficção Científica"),
    "topic_historical_fiction": ("Literatura e Ficção", "Ficção Histórica"),
    "topic_horror": ("Literatura e Ficção", "Terror"),
}
CORE_TOPIC_PATHS_BY_REGION["jp"] = {
    "topic_fantasy": ("SF・ファンタジー", "ファンタジー"),
    "topic_romance": ("官能・ロマンス",),
    "topic_mystery_thriller": ("ミステリー・スリラー・サスペンス",),
    "topic_science_fiction": ("SF・ファンタジー", "SF"),
    "topic_historical_fiction": ("文学・フィクション", "歴史小説"),
    "topic_horror": ("文学・フィクション", "ホラー"),
}
```

Implement recursive parsing with a shared accepted-node counter, stop descending at depth 8, and stop accepting nodes after 5,000. Return `None` when the top-level payload or `categories` shape is invalid; return `()` for a valid empty list. `public_topic_nodes` must recursively omit `category_id`.

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/metadata/test_audible_taxonomy.py -q`

Expected: all taxonomy tests pass.

- [ ] **Step 5: Commit**

```bash
git add shelfmark/metadata_providers/audible_taxonomy.py tests/metadata/test_audible_taxonomy.py
git commit -m "feat(discover): model Audible topic taxonomy"
```

### Task 2: Audible taxonomy HTTP and category-filtered browse

**Files:**
- Modify: `shelfmark/metadata_providers/audible.py:52-371`
- Modify: `tests/metadata/test_audible_discover.py`
- Create: `tests/metadata/test_audible_topics.py`

**Interfaces:**
- Consumes: `parse_audible_topic_tree` and `AudibleTopicNode` from Task 1.
- Produces: `AudibleProvider.fetch_topic_tree() -> tuple[AudibleTopicNode, ...] | None` and `AudibleProvider.discover_topic(category_id: str, limit: int = 20) -> list[BookMetadata] | None`.

- [ ] **Step 1: Write failing provider tests**

```python
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.metadata_providers.audible import AudibleProvider


def _response(payload: object) -> MagicMock:
    response = MagicMock()
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    return response


def _product(asin: str) -> dict:
    return {
        "asin": asin,
        "title": f"Title {asin}",
        "is_listenable": True,
        "content_delivery_type": "SinglePartBook",
    }


@pytest.fixture
def provider() -> AudibleProvider:
    return AudibleProvider(region="us")


def test_fetch_topic_tree_uses_genres_root(provider):
    response = _response({"categories": [{"id": "10", "name": "Romance"}]})
    with patch.object(provider.session, "get", return_value=response) as mock_get:
        nodes = provider.fetch_topic_tree()
    assert nodes is not None and nodes[0].path == ("Romance",)
    assert mock_get.call_args.kwargs["params"] == {
        "root": "Genres",
        "categories_num_levels": 8,
        "response_groups": "category_metadata",
    }


def test_discover_topic_passes_category_and_best_sellers(provider):
    products = [_product("B000000000")]
    with patch.object(provider.session, "get", return_value=_response({"products": products})) as mock_get:
        books = provider.discover_topic("18580607011", limit=1)
    assert books is not None and books[0].provider_id == "B000000000"
    params = mock_get.call_args.kwargs["params"]
    assert params["category_id"] == "18580607011"
    assert params["products_sort_by"] == "BestSellers"


def test_discover_topic_rejects_non_numeric_internal_id(provider):
    assert provider.discover_topic("not-an-id") == []
```

Keep these helpers local to the new provider-topic test module.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/metadata/test_audible_topics.py tests/metadata/test_audible_discover.py -x --tb=short`

Expected: `AttributeError` for `fetch_topic_tree` or `discover_topic`.

- [ ] **Step 3: Implement provider methods and optional category parameter**

Change `_discover_fetch_page` and `_discover_browse` to accept keyword-only `category_id: str | None = None`. Build the request params first and add `category_id` only when present. Add:

```python
def fetch_topic_tree(self) -> tuple[AudibleTopicNode, ...] | None:
    try:
        response = self.session.get(
            f"{self.base_url}/1.0/catalog/categories",
            params={
                "root": "Genres",
                "categories_num_levels": MAX_TAXONOMY_DEPTH,
                "response_groups": "category_metadata",
            },
            timeout=15,
            verify=get_ssl_verify(self.base_url),
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, TypeError, ValueError):
        logger.warning("Audible topic taxonomy fetch failed for region %s", self.region)
        return None
    return parse_audible_topic_tree(payload)


def discover_topic(self, category_id: str, limit: int = 20) -> list[BookMetadata] | None:
    normalized_id = str(category_id or "").strip()
    if not normalized_id.isdigit():
        return []
    return self._discover_browse(
        "BestSellers",
        limit,
        category_id=normalized_id,
    )
```

Retain existing behavior for Best Sellers and New Releases calls with no category.

- [ ] **Step 4: Run provider tests**

Run: `uv run pytest tests/metadata/test_audible_topics.py tests/metadata/test_audible_discover.py -q`

Expected: both modules pass, including existing pagination/filtering tests.

- [ ] **Step 5: Commit**

```bash
git add shelfmark/metadata_providers/audible.py tests/metadata/test_audible_topics.py tests/metadata/test_audible_discover.py
git commit -m "feat(discover): browse Audible categories"
```

### Task 3: Taxonomy cache service and authenticated topic-tree endpoint

**Files:**
- Create: `shelfmark/core/audible_topics.py`
- Modify: `shelfmark/main.py:45-60,2580-2670`
- Create: `tests/core/test_audible_topic_service.py`
- Create: `tests/core/test_audible_topics_endpoint.py`

**Interfaces:**
- Consumes: provider methods and taxonomy helpers from Tasks 1-2.
- Produces: `AudibleTopicTree`, `AudibleTopicResolution`, `get_audible_topic_tree()`, `resolve_audible_topic(path)`, `normalize_audible_topic_path(value)`, `validate_audible_topic_path(value)`, and `audible_topic_path_digest(path)`.
- HTTP: `GET /api/metadata/audible/topics`.

- [ ] **Step 1: Write failing service and endpoint tests**

Test the matrix explicitly:

```python
from unittest.mock import MagicMock, patch

import pytest

from shelfmark.core import audible_topics as topic_service
from shelfmark.core.cache import get_metadata_cache
from shelfmark.metadata_providers.audible_taxonomy import AudibleTopicNode


@pytest.fixture
def nodes() -> tuple[AudibleTopicNode, ...]:
    return (
        AudibleTopicNode(
            name="Romance",
            path=("Romance",),
            category_id="10",
        ),
    )


@pytest.fixture
def provider() -> MagicMock:
    result = MagicMock()
    result.region = "us"
    result.tld = "com"
    return result


def test_success_writes_fresh_and_last_good_with_exact_ttls(provider, nodes):
    provider.fetch_topic_tree.return_value = nodes
    with patch.object(topic_service, "get_provider", return_value=provider), patch.object(
        get_metadata_cache(), "set"
    ) as cache_set:
        result = topic_service.get_audible_topic_tree()
    assert result is not None and result.stale is False
    ttls = {call.args[0]: call.args[2] for call in cache_set.call_args_list}
    assert ttls["audible:topics:com:fresh"] == 24 * 3600
    assert ttls["audible:topics:com:last_good"] == 7 * 24 * 3600


def test_failure_serves_last_good(provider, nodes):
    get_metadata_cache().set("audible:topics:com:last_good", nodes, 600)
    provider.fetch_topic_tree.return_value = None
    with patch.object(topic_service, "get_provider", return_value=provider):
        result = topic_service.get_audible_topic_tree()
    assert result is not None and result.stale is True


def test_resolution_distinguishes_missing_from_failure(nodes):
    with patch.object(topic_service, "get_audible_topic_tree", return_value=None):
        assert topic_service.resolve_audible_topic(["Romance"]).failed is True
    tree = topic_service.AudibleTopicTree(region="us", tld="com", topics=nodes, stale=False)
    with patch.object(topic_service, "get_audible_topic_tree", return_value=tree):
        result = topic_service.resolve_audible_topic(["Missing"])
    assert result.failed is False and result.node is None
```

Also add `test_concurrent_cold_taxonomy_requests_fetch_once`: use two threads, a barrier inside the mocked provider fetch, and a shared result list; assert both callers receive the same tree and `provider.fetch_topic_tree.call_count == 1`. This locks in per-region single-flight behavior rather than merely documenting it.

Endpoint tests must cover auth-required 401, successful public tree with no `category_id`, stale flag, and HTTP 503 when the service returns `None`.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/core/test_audible_topic_service.py tests/core/test_audible_topics_endpoint.py -x --tb=short`

Expected: import or route failures.

- [ ] **Step 3: Implement service contracts and endpoint**

Use these immutable results:

```python
@dataclass(frozen=True)
class AudibleTopicTree:
    region: str
    tld: str
    topics: tuple[AudibleTopicNode, ...]
    stale: bool = False


@dataclass(frozen=True)
class AudibleTopicResolution:
    node: AudibleTopicNode | None
    failed: bool
    stale: bool = False
```

`get_audible_topic_tree()` constructs Audible with `get_provider("audible", **get_provider_kwargs("audible"))`, uses keys `audible:topics:{tld}:fresh|last_good`, double-checks fresh inside a per-key lock, and caches valid empty tuples. `normalize_audible_topic_path` accepts only `list`/`tuple`, at most 8 non-empty trimmed strings, and returns `tuple[str, ...] | None`. `validate_audible_topic_path` returns `([], None)` for empty input, a normalized list for a resolved path, and a concrete retry/error message for malformed, missing, or unverifiable paths.

Endpoint body:

```python
@app.route("/api/metadata/audible/topics", methods=["GET"])
@login_required
def api_audible_topics() -> Response | tuple[Response, int]:
    tree = get_audible_topic_tree_service()
    if tree is None:
        return jsonify({"error": "Audible topics are temporarily unavailable"}), 503
    return jsonify(
        {
            "region": tree.region,
            "stale": tree.stale,
            "topics": public_topic_nodes(tree.topics),
        }
    )
```

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/core/test_audible_topic_service.py tests/core/test_audible_topics_endpoint.py -q`

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add shelfmark/core/audible_topics.py shelfmark/main.py tests/core/test_audible_topic_service.py tests/core/test_audible_topics_endpoint.py
git commit -m "feat(discover): cache and expose Audible topics"
```

### Task 4: Global and per-user topic preference

**Files:**
- Modify: `shelfmark/config/settings.py:1-575`
- Modify: `shelfmark/config/users_settings.py:70-230`
- Modify: `shelfmark/core/admin_settings_routes.py:35-135`
- Modify: `shelfmark/core/user_settings_overrides.py:20-95`
- Modify: `shelfmark/main.py:1130-1205`
- Modify: `tests/config/test_search_mode_settings.py`
- Modify: `tests/config/test_users_settings.py`
- Modify: `tests/core/test_config_user_overrides.py`
- Modify: `tests/core/test_admin_users_api.py`
- Modify: `tests/core/test_self_user_routes.py`
- Modify: `tests/core/test_config_api.py`

**Interfaces:**
- Consumes: `validate_audible_topic_path` and `matching_core_topic_key`.
- Produces: registered `DEFAULT_DISCOVER_TOPIC: list[str]`, `default_discover_topic`, and `default_discover_topic_core_key` in `/api/config`.

- [ ] **Step 1: Write failing registry, validation, and config tests**

Add assertions that the search tab contains a `CustomComponentField` with component `audible_topic_selector`, whose only backing field is a `TagListField` with `key == "DEFAULT_DISCOVER_TOPIC"`, `default == []`, `env_supported is False`, and `user_overridable is True`.

Add preference tests:

```python
def test_search_mode_settings_include_audible_topic_selector():
    fields = {field.key: field for field in search_mode_settings()}
    selector = fields["audible_topic_selector"]
    assert selector.component == "audible_topic_selector"
    assert len(selector.value_fields) == 1
    backing = selector.value_fields[0]
    assert isinstance(backing, TagListField)
    assert backing.key == "DEFAULT_DISCOVER_TOPIC"
    assert backing.default == []
    assert backing.env_supported is False
    assert backing.user_overridable is True


def test_search_preferences_include_custom_backing_topic_field(user_db):
    user = user_db.create_user(username="topic-reader")
    payload = build_user_preferences_payload(user_db, user["id"], "search_mode")
    assert "DEFAULT_DISCOVER_TOPIC" in payload["keys"]
    assert payload["globalValues"]["DEFAULT_DISCOVER_TOPIC"] == []


def test_validate_user_settings_normalizes_topic_path():
    with patch(
        "shelfmark.config.users_settings.validate_audible_topic_path",
        return_value=(["Science Fiction & Fantasy", "Fantasy"], None),
    ):
        valid, errors = validate_user_settings(
            {"DEFAULT_DISCOVER_TOPIC": [" Science Fiction & Fantasy ", "Fantasy"]}
        )
    assert errors == []
    assert valid["DEFAULT_DISCOVER_TOPIC"] == ["Science Fiction & Fantasy", "Fantasy"]
```

Extend config API expectations with the user-scoped call and core-key result.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/config/test_search_mode_settings.py tests/config/test_users_settings.py tests/core/test_config_user_overrides.py tests/core/test_self_user_routes.py tests/core/test_config_api.py -x --tb=short`

Expected: missing field/key/config assertions fail.

- [ ] **Step 3: Register and validate the preference**

Add after `SHOW_DISCOVER_ROWS` in `search_mode_settings()`:

```python
CustomComponentField(
    key="audible_topic_selector",
    component="audible_topic_selector",
    label="Default Discover Topic",
    description="Choose an Audible topic or subgenre to pin first on the home page.",
    wrap_in_field_wrapper=True,
    show_when={"field": "SEARCH_MODE", "value": "universal"},
    value_fields=[
        TagListField(
            key="DEFAULT_DISCOVER_TOPIC",
            label="Default Discover Topic",
            default=[],
            env_supported=False,
            user_overridable=True,
        )
    ],
),
```

Change `get_ordered_user_overridable_fields()` to iterate `settings_registry.iter_value_fields(tab)` rather than `tab.fields`. Add `DEFAULT_DISCOVER_TOPIC` to `_SEARCH_PREFERENCE_VALIDATABLE_KEYS` and the normalized-key set in `validate_user_settings`; delegate its branch to `validate_audible_topic_path`.

Register a `search_mode` on-save handler that validates the topic only when present and returns the normalized values. Preserve all unrelated values.

In `/api/config`, read with `user_id=db_user_id`, normalize to `list[str]` or `[]`, and return:

```python
"default_discover_topic": default_discover_topic,
"default_discover_topic_core_key": matching_core_topic_key(
    str(app_config.get("AUDIBLE_REGION", "us") or "us"),
    default_discover_topic,
),
```

- [ ] **Step 4: Run preference tests**

Run the command from Step 2, then run: `uv run pytest tests/core/test_admin_users_api.py -q`

Expected: all preference, admin, self-service, and config tests pass.

- [ ] **Step 5: Commit**

```bash
git add shelfmark/config/settings.py shelfmark/config/users_settings.py shelfmark/core/admin_settings_routes.py shelfmark/core/user_settings_overrides.py shelfmark/main.py tests/config/test_search_mode_settings.py tests/config/test_users_settings.py tests/core/test_config_user_overrides.py tests/core/test_admin_users_api.py tests/core/test_self_user_routes.py tests/core/test_config_api.py
git commit -m "feat(settings): add preferred Audible topic"
```

### Task 5: Topic-aware discover dispatch and row caching

**Files:**
- Modify: `shelfmark/core/discover.py`
- Modify: `shelfmark/main.py:2620-2675`
- Modify: `tests/core/test_discover_service.py`
- Modify: `tests/core/test_discover_endpoint.py`

**Interfaces:**
- Consumes: `resolve_audible_topic`, `core_topic_path`, `preferred_topic_label`, `audible_topic_path_digest`, and effective user config.
- Produces: `AUDIBLE_TOPIC_ROWS`, `PREFERRED_TOPIC_ROW_KEY`, `ALL_DISCOVER_ROW_KEYS`, and topic-capable `get_discover_row(content_type, row_key, user_id=None)`.

- [ ] **Step 1: Write failing dispatch/cache tests**

Cover these exact cases:

```python
from unittest.mock import MagicMock, patch

from shelfmark.core.audible_topics import AudibleTopicResolution
from shelfmark.metadata_providers.audible_taxonomy import AudibleTopicNode


def _topic(category_id: str, *path: str) -> AudibleTopicNode:
    return AudibleTopicNode(
        name=path[-1],
        path=tuple(path),
        category_id=category_id,
    )


def _audible_mock(topic_books: list[BookMetadata]) -> MagicMock:
    provider = MagicMock()
    provider.region = "us"
    provider.tld = "com"
    provider.is_available.return_value = True
    provider.discover_topic.return_value = topic_books
    return provider


def test_combined_topic_uses_audiobook_provider_and_category():
    provider = _audible_mock(topic_books=_books(1))
    resolution = AudibleTopicResolution(
        node=_topic("99", "Science Fiction & Fantasy", "Fantasy"),
        failed=False,
    )
    with (
        patch.object(discover, "get_configured_provider_name", return_value="audible") as configured,
        patch.object(discover, "get_provider", return_value=provider),
        patch.object(discover, "get_provider_kwargs", return_value={}),
        patch.object(discover, "is_provider_enabled", return_value=True),
        patch.object(discover, "resolve_audible_topic", return_value=resolution),
    ):
        row = discover.get_discover_row("combined", "topic_fantasy", user_id=7)
    assert row is not None and row.label == "Fantasy"
    configured.assert_called_once_with("audiobook", user_id=7)
    provider.discover_topic.assert_called_once_with("99", discover.ROW_LIMIT)


def test_book_view_never_resolves_topic():
    with patch.object(discover, "resolve_audible_topic") as resolve:
        row = discover.get_discover_row("ebook", "topic_fantasy", user_id=7)
    assert row is None
    resolve.assert_not_called()


def test_preferred_topic_reads_user_scoped_setting():
    provider = _audible_mock(topic_books=_books(1))
    resolution = AudibleTopicResolution(
        node=_topic("101", "Romance", "Historical"),
        failed=False,
    )
    with (
        patch.object(discover, "get_configured_provider_name", return_value="audible"),
        patch.object(discover, "get_provider", return_value=provider),
        patch.object(discover, "get_provider_kwargs", return_value={}),
        patch.object(discover, "is_provider_enabled", return_value=True),
        patch.object(discover, "resolve_audible_topic", return_value=resolution),
        patch.object(
            discover.app_config,
            "get",
            return_value=["Romance", "Historical"],
        ) as config_get,
    ):
        row = discover.get_discover_row("audiobook", "preferred_topic", user_id=8)
    assert row is not None and row.label == "Romance — Historical"
    config_get.assert_called_once_with("DEFAULT_DISCOVER_TOPIC", [], user_id=8)
```

Also assert topic TTL is 6 hours, missing category caches `[]`, failure serves last-good, category ID changes produce a different cache key, and two users with the same preferred path share a cache entry.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/core/test_discover_service.py tests/core/test_discover_endpoint.py -x --tb=short`

Expected: new row keys are unknown and topic dispatch assertions fail.

- [ ] **Step 3: Refactor discover resolution without changing standard rows**

Define permanent rows from `CORE_TOPIC_DEFINITIONS`, set `PREFERRED_TOPIC_ROW_KEY = "preferred_topic"`, and export an immutable `ALL_DISCOVER_ROW_KEYS` used by `main.py` validation.

For topic rows:

1. reject `ebook`;
2. resolve the configured `audiobook` provider with `user_id`;
3. require enabled/available Audible;
4. derive a permanent path or read the user-scoped preferred path;
5. resolve the path through the topic service;
6. use an `unsupported` category token for successful misses and the numeric ID for hits;
7. return an uncached empty row for taxonomy failure with no resolution;
8. call `provider.discover_topic(category_id, ROW_LIMIT)` for hits;
9. pass both standard and topic fetchers through one existing dual-entry cache helper.

Build preferred cache identity with:

```python
path_key = audible_topic_path_digest(path)
base_key = f"discover:audible:{provider.tld}:preferred:{path_key}:{category_token}"
```

Permanent keys use `discover:audible:{tld}:{row_key}:{category_token}`. `_row_ttl` returns 24 hours only for `new_releases`; every other valid row gets 6 hours.

- [ ] **Step 4: Run discover tests**

Run: `uv run pytest tests/core/test_discover_service.py tests/core/test_discover_endpoint.py -q`

Expected: old and new tests pass.

- [ ] **Step 5: Commit**

```bash
git add shelfmark/core/discover.py shelfmark/main.py tests/core/test_discover_service.py tests/core/test_discover_endpoint.py
git commit -m "feat(discover): serve Audible topic rows"
```

### Task 6: Cascading topic selector and personal preference UI

**Files:**
- Modify: `src/frontend/src/services/api.ts`
- Create: `src/frontend/src/utils/audibleTopics.ts`
- Create: `src/frontend/src/tests/audibleTopics.test.ts`
- Create: `src/frontend/src/components/settings/AudibleTopicSelector.tsx`
- Create: `src/frontend/src/components/settings/customFields/AudibleTopicSelectorField.tsx`
- Modify: `src/frontend/src/components/settings/customFields/index.tsx`
- Modify: `src/frontend/src/components/settings/users/UserSearchPreferencesSection.tsx`
- Modify: `src/frontend/src/components/settings/users/types.ts`

**Interfaces:**
- Produces: `AudibleTopicNode`, `AudibleTopicsResponse`, `getAudibleTopics()`, `flattenTopicDescendants`, `findTopicByPath`, `topicPathsEqual`, and shared `AudibleTopicSelector`.
- Consumers: Task 7 uses only config preference types; the selector is isolated from homepage rendering.

- [ ] **Step 1: Write failing pure helper tests**

```typescript
const tree: AudibleTopicNode[] = [
  {
    name: 'Science Fiction & Fantasy',
    path: ['Science Fiction & Fantasy'],
    children: [
      {
        name: 'Fantasy',
        path: ['Science Fiction & Fantasy', 'Fantasy'],
        children: [
          {
            name: 'Epic',
            path: ['Science Fiction & Fantasy', 'Fantasy', 'Epic'],
            children: [],
          },
        ],
      },
    ],
  },
];

it('starts descendants with All and uses relative breadcrumbs', () => {
  expect(flattenTopicDescendants(tree[0]!)).toEqual([
    { label: 'All Science Fiction & Fantasy', path: ['Science Fiction & Fantasy'] },
    { label: 'Fantasy', path: ['Science Fiction & Fantasy', 'Fantasy'] },
    { label: 'Fantasy → Epic', path: ['Science Fiction & Fantasy', 'Fantasy', 'Epic'] },
  ]);
});

it('restores an exact saved path and rejects a missing one', () => {
  expect(findTopicByPath(tree, ['Science Fiction & Fantasy', 'Fantasy'])?.name).toBe('Fantasy');
  expect(findTopicByPath(tree, ['Science Fiction & Fantasy', 'Missing'])).toBeUndefined();
});
```

- [ ] **Step 2: Run tests to verify failure**

Run: `cd src/frontend && npm run test:unit -- src/tests/audibleTopics.test.ts`

Expected: import failure for `../utils/audibleTopics`.

- [ ] **Step 3: Implement API, helpers, and shared control**

API types:

```typescript
export interface AudibleTopicNode {
  name: string;
  path: string[];
  children: AudibleTopicNode[];
}

export interface AudibleTopicsResponse {
  region: string;
  stale: boolean;
  topics: AudibleTopicNode[];
}

export const getAudibleTopics = async (): Promise<AudibleTopicsResponse> =>
  fetchJSON<AudibleTopicsResponse>(`${API_BASE}/metadata/audible/topics`);
```

`AudibleTopicSelector` accepts `value: string[]`, `onChange(path: string[]): void`, and `disabled?: boolean`. It fetches once per mount, shows two existing `SelectField` controls, and encodes option values as `JSON.stringify(path)` while retaining the real path arrays in a lookup map. Broad changes immediately select that broad topic's `All …` option; descendant changes replace the full path. Show stale copy non-blockingly and preserve an unavailable saved path with a reset prompt.

The custom-field adapter finds the bound `TagListField`, reads `values[boundField.key]`, computes the effective audiobook provider as `METADATA_PROVIDER_AUDIOBOOK || METADATA_PROVIDER`, and returns `null` unless Universal + Audible. Register it as `audible_topic_selector`.

In `UserSearchPreferencesSection`, add `DEFAULT_DISCOVER_TOPIC` to `SearchSettingKey`, calculate its override/reset state with `toComparableValue`, gate on Universal + effective Audible, and render the shared selector in `FieldWrapper`. Add `DEFAULT_DISCOVER_TOPIC?: string[]` to `PerUserSettings`.

- [ ] **Step 4: Run frontend selector checks**

Run:

```bash
cd src/frontend
npm run test:unit -- src/tests/audibleTopics.test.ts
npm run typecheck
npm run lint
npm run format:check
```

Expected: all pass with no new dependency.

- [ ] **Step 5: Commit**

```bash
git add src/frontend/src/services/api.ts src/frontend/src/utils/audibleTopics.ts src/frontend/src/tests/audibleTopics.test.ts src/frontend/src/components/settings/AudibleTopicSelector.tsx src/frontend/src/components/settings/customFields/AudibleTopicSelectorField.tsx src/frontend/src/components/settings/customFields/index.tsx src/frontend/src/components/settings/users/UserSearchPreferencesSection.tsx src/frontend/src/components/settings/users/types.ts
git commit -m "feat(settings): add Audible topic selector"
```

### Task 7: Mixed-provider homepage topic rows and preference ordering

**Files:**
- Modify: `src/frontend/src/types/index.ts:264-294`
- Modify: `src/frontend/src/utils/discoverRows.ts`
- Modify: `src/frontend/src/tests/discoverRows.test.ts`
- Modify: `src/frontend/src/components/DiscoverSection.tsx`
- Modify: `src/frontend/src/App.tsx:2580-2630`

**Interfaces:**
- Consumes: backend row keys and `/api/config` fields from Tasks 4-5.
- Produces: `buildDiscoverRowDefs(context) -> DiscoverRowDef[]` and the final homepage UI.

- [ ] **Step 1: Write failing row-definition matrix tests**

Update `DiscoverRowDef` with `provider: string` and `coverAspect: 'square' | 'portrait'`. Add:

```typescript
it('keeps ebook rows free of Audible topics', () => {
  expect(
    buildDiscoverRowDefs({
      contentType: 'ebook',
      standardProvider: 'hardcover',
      audiobookProvider: 'audible',
      hasPreferredTopic: true,
      preferredCoreKey: null,
    }).map((row) => row.key),
  ).toEqual(['trending', 'new_releases']);
});

it('adds preferred then standard and permanent rows in combined mode', () => {
  expect(
    buildDiscoverRowDefs({
      contentType: 'combined',
      standardProvider: 'hardcover',
      audiobookProvider: 'audible',
      hasPreferredTopic: true,
      preferredCoreKey: null,
    }).map((row) => row.key),
  ).toEqual([
    'preferred_topic',
    'trending',
    'new_releases',
    'topic_fantasy',
    'topic_romance',
    'topic_mystery_thriller',
    'topic_science_fiction',
    'topic_historical_fiction',
    'topic_horror',
  ]);
});

it('moves an exact permanent preference without duplicating it', () => {
  const keys = buildDiscoverRowDefs({
    contentType: 'audiobook',
    standardProvider: 'audible',
    audiobookProvider: 'audible',
    hasPreferredTopic: true,
    preferredCoreKey: 'topic_horror',
  }).map((row) => row.key);
  expect(keys[0]).toBe('topic_horror');
  expect(keys.filter((key) => key === 'topic_horror')).toHaveLength(1);
});
```

Also test no Audible provider, no preference, and square topic skeletons beside portrait Hardcover standard rows.

- [ ] **Step 2: Run tests to verify failure**

Run: `cd src/frontend && npm run test:unit -- src/tests/discoverRows.test.ts`

Expected: missing `buildDiscoverRowDefs` and new shape failures.

- [ ] **Step 3: Implement deterministic row definitions and wire App**

Add config fields:

```typescript
default_discover_topic: string[];
default_discover_topic_core_key?: string | null;
```

`buildDiscoverRowDefs` first gets standard rows for `standardProvider`. If content is not ebook and `audiobookProvider === 'audible'`, it prepends either `preferred_topic` or the matching permanent key, then appends standard rows and remaining permanent rows. Set Audible definitions to `provider: 'audible'` and `coverAspect: 'square'`; Hardcover rows are portrait.

Replace `DiscoverSection`'s `providerName` prop with:

```typescript
standardProviderName: string | null;
audiobookProviderName: string | null;
preferredTopicPath: string[];
preferredCoreTopicKey: string | null;
```

Use each row definition's `coverAspect` for its skeleton. Keep loaded tile aspect from `book.cover_aspect`.

In `App.tsx`, pass the current combined/audiobook/book standard provider exactly as today, pass `configuredAudiobookMetadataProvider ?? configuredMetadataProvider` separately, and pass the two effective config preference fields. Do not change search provider selection.

- [ ] **Step 4: Run frontend homepage checks**

Run:

```bash
cd src/frontend
npm run test:unit -- src/tests/discoverRows.test.ts src/tests/audibleTopics.test.ts
npm run typecheck
npm run lint
npm run format:check
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/frontend/src/types/index.ts src/frontend/src/utils/discoverRows.ts src/frontend/src/tests/discoverRows.test.ts src/frontend/src/components/DiscoverSection.tsx src/frontend/src/App.tsx
git commit -m "feat(discover): show preferred Audible topics"
```

### Task 8: User documentation and full verification

**Files:**
- Modify: `docs/configuration.md`
- Verify: all files changed in Tasks 1-7

**Interfaces:**
- Consumes: complete feature.
- Produces: user-facing setup instructions and a verified implementation branch.

- [ ] **Step 1: Document the user workflow**

Add an “Audible topic discovery” section explaining:

- enable Audible and select it as the audiobook metadata provider;
- enable Discover rows;
- open My Account → Search Preferences;
- choose a broad topic and optionally a subgenre;
- the selected topic is pinned only for that user;
- topic rows appear in Audiobook and Combined views;
- categories follow the selected Audible region and unsupported saved paths are hidden until reset/reselected.

- [ ] **Step 2: Run all focused backend tests**

```bash
uv run pytest tests/metadata/test_audible_taxonomy.py tests/metadata/test_audible_topics.py tests/metadata/test_audible_discover.py tests/core/test_audible_topic_service.py tests/core/test_audible_topics_endpoint.py tests/core/test_discover_service.py tests/core/test_discover_endpoint.py tests/config/test_search_mode_settings.py tests/config/test_users_settings.py tests/core/test_config_user_overrides.py tests/core/test_admin_users_api.py tests/core/test_self_user_routes.py tests/core/test_config_api.py -q
```

Expected: all pass.

- [ ] **Step 3: Run full automated verification**

```bash
make python-checks
make python-test
make frontend-checks
make frontend-test
```

Expected: every command exits 0. If the known pre-existing macOS `entrypoint.sh` test fails unchanged, record the exact test name and confirm none of this feature's files are in its traceback; do not weaken or skip it.

- [ ] **Step 4: Perform manual UI verification**

Run the existing development stack and verify:

1. US Audible taxonomy loads into both cascading selectors.
2. Selecting `Science Fiction & Fantasy → Fantasy → Epic` persists after reload.
3. The preferred row appears first in Audiobook and Combined views.
4. Selecting permanent Horror moves Horror first without duplication.
5. Book view has no Audible topic rows.
6. Combined Hardcover + Audible shows Hardcover standard rows and square Audible topic rows.
7. Reset removes the user override and restores inherited ordering.
8. Temporarily forcing the taxonomy endpoint to fail shows the retry state without breaking existing rows.

- [ ] **Step 5: Review diff and commit documentation**

```bash
git diff --check
git status --short
git add docs/configuration.md
git commit -m "docs: explain Audible topic discovery"
```

Expected: only intentional files remain changed; `.superpowers/` visual-companion artifacts are not staged.
