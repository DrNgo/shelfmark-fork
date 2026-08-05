"""Cached Audible taxonomy access, exact-path resolution, and validation."""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from shelfmark.core.cache import get_metadata_cache
from shelfmark.core.logger import setup_logger
from shelfmark.metadata_providers import get_provider, get_provider_kwargs
from shelfmark.metadata_providers.audible_taxonomy import (
    MAX_TAXONOMY_DEPTH,
    AudibleTopicNode,
    find_audible_topic,
)

if TYPE_CHECKING:
    from shelfmark.metadata_providers.audible import AudibleProvider

logger = setup_logger(__name__)

FRESH_TTL = 24 * 3600
LAST_GOOD_TTL = 7 * 24 * 3600

_key_locks: dict[str, threading.Lock] = {}
_key_locks_guard = threading.Lock()


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
    region: str | None = None
    tld: str | None = None


def _lock_for(key: str) -> threading.Lock:
    """Return the shared single-flight lock for one storefront cache key."""
    with _key_locks_guard:
        return _key_locks.setdefault(key, threading.Lock())


def get_audible_topic_tree() -> AudibleTopicTree | None:
    """Return the current storefront taxonomy, falling back to last-good data."""
    provider = cast("AudibleProvider", get_provider("audible", **get_provider_kwargs("audible")))
    base_key = f"audible:topics:{provider.tld}"
    fresh_key = f"{base_key}:fresh"
    last_good_key = f"{base_key}:last_good"
    cache = get_metadata_cache()

    cached = cache.get(fresh_key)
    if cached is not None:
        return AudibleTopicTree(
            region=provider.region,
            tld=provider.tld,
            topics=cast("tuple[AudibleTopicNode, ...]", cached),
        )

    with _lock_for(base_key):
        cached = cache.get(fresh_key)
        if cached is not None:
            return AudibleTopicTree(
                region=provider.region,
                tld=provider.tld,
                topics=cast("tuple[AudibleTopicNode, ...]", cached),
            )

        topics = provider.fetch_topic_tree()
        if topics is not None:
            cache.set(fresh_key, topics, FRESH_TTL)
            cache.set(last_good_key, topics, LAST_GOOD_TTL)
            return AudibleTopicTree(
                region=provider.region,
                tld=provider.tld,
                topics=topics,
            )

        stale_topics = cache.get(last_good_key)
        if stale_topics is None:
            logger.warning("Audible topic taxonomy failed for %s without stale data", base_key)
            return None

        logger.warning("Audible topic taxonomy failed for %s; serving stale data", base_key)
        return AudibleTopicTree(
            region=provider.region,
            tld=provider.tld,
            topics=cast("tuple[AudibleTopicNode, ...]", stale_topics),
            stale=True,
        )


def normalize_audible_topic_path(value: object) -> tuple[str, ...] | None:
    """Normalize a bounded category path, or reject malformed input."""
    if not isinstance(value, (list, tuple)) or len(value) > MAX_TAXONOMY_DEPTH:
        return None

    normalized: list[str] = []
    for segment in value:
        if not isinstance(segment, str) or not (trimmed := segment.strip()):
            return None
        normalized.append(trimmed)
    return tuple(normalized)


def resolve_audible_topic(path: object) -> AudibleTopicResolution:
    """Resolve an exact path while distinguishing a miss from a fetch failure."""
    normalized = normalize_audible_topic_path(path)
    if normalized is None:
        return AudibleTopicResolution(node=None, failed=False)

    tree = get_audible_topic_tree()
    if tree is None:
        return AudibleTopicResolution(node=None, failed=True)
    return AudibleTopicResolution(
        node=find_audible_topic(tree.topics, normalized),
        failed=False,
        stale=tree.stale,
        region=tree.region,
        tld=tree.tld,
    )


def validate_audible_topic_path(value: object) -> tuple[list[str], str | None]:
    """Validate a settings value against the current storefront taxonomy."""
    if isinstance(value, (list, tuple)) and not value:
        return [], None

    normalized = normalize_audible_topic_path(value)
    if normalized is None:
        return [], "Select a valid Audible topic."

    resolution = resolve_audible_topic(normalized)
    if resolution.failed:
        return [], "Audible topics could not be verified right now. Please try again."
    if resolution.node is None:
        return [], "The selected Audible topic is no longer available. Choose another topic."
    return list(normalized), None


def audible_topic_path_digest(path: object) -> str:
    """Return a stable, segment-aware digest for a normalized topic path."""
    normalized = normalize_audible_topic_path(path)
    if normalized is None:
        msg = "Audible topic path must be a valid list or tuple"
        raise ValueError(msg)
    serialized = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
