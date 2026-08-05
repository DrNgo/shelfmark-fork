"""Pure parsing and lookup helpers for Audible's category taxonomy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

MAX_TAXONOMY_DEPTH = 8
MAX_TAXONOMY_NODES = 5_000


@dataclass(frozen=True)
class AudibleTopicNode:
    """An immutable Audible category and its complete storefront path."""

    name: str
    path: tuple[str, ...]
    category_id: str
    children: tuple[AudibleTopicNode, ...] = ()


@dataclass(frozen=True)
class CoreTopicDefinition:
    """A permanent Shelfmark topic independent of Audible storefront wording."""

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


def parse_audible_topic_tree(payload: object) -> tuple[AudibleTopicNode, ...] | None:
    """Parse an Audible category response, ignoring malformed category entries."""
    if not isinstance(payload, dict) or not isinstance(categories := payload.get("categories"), list):
        return None

    accepted_nodes = 0

    def parse_nodes(raw_nodes: list[Any], parent_path: tuple[str, ...]) -> tuple[AudibleTopicNode, ...]:
        nonlocal accepted_nodes
        parsed: list[AudibleTopicNode] = []
        for raw_node in raw_nodes:
            if accepted_nodes >= MAX_TAXONOMY_NODES:
                break
            if not isinstance(raw_node, dict):
                continue

            category_id = raw_node.get("id")
            name = raw_node.get("name")
            children = raw_node.get("children", [])
            if (
                not isinstance(category_id, str)
                or not category_id.isdigit()
                or not isinstance(name, str)
                or not name
                or not isinstance(children, list)
            ):
                continue

            path = (*parent_path, name)
            accepted_nodes += 1
            parsed_children = ()
            if len(path) < MAX_TAXONOMY_DEPTH:
                parsed_children = parse_nodes(children, path)
            parsed.append(AudibleTopicNode(name, path, category_id, parsed_children))
        return tuple(parsed)

    return parse_nodes(categories, ())


def find_audible_topic(
    nodes: Sequence[AudibleTopicNode], path: Sequence[str]
) -> AudibleTopicNode | None:
    """Find a category only when its full path exactly matches."""
    target_path = tuple(path)
    for node in nodes:
        if node.path == target_path:
            return node
        if found := find_audible_topic(node.children, target_path):
            return found
    return None


def core_topic_path(region: str, key: str) -> tuple[str, ...] | None:
    """Return the localized Audible path for a permanent topic key."""
    return CORE_TOPIC_PATHS_BY_REGION.get(region, {}).get(key)


def matching_core_topic_key(region: str, path: Sequence[str]) -> str | None:
    """Return the permanent topic key whose localized path matches exactly."""
    target_path = tuple(path)
    for key, mapped_path in CORE_TOPIC_PATHS_BY_REGION.get(region, {}).items():
        if mapped_path == target_path:
            return key
    return None


def public_topic_nodes(nodes: Sequence[AudibleTopicNode]) -> list[dict[str, Any]]:
    """Serialize nodes for public clients without exposing Audible category IDs."""
    return [
        {
            "name": node.name,
            "path": list(node.path),
            "children": public_topic_nodes(node.children),
        }
        for node in nodes
    ]


def preferred_topic_label(path: Sequence[str]) -> str:
    """Build a concise label from a category's root and most-specific names."""
    if not path:
        return ""
    if len(path) == 1:
        return path[0]
    return f"{path[0]} — {path[-1]}"
