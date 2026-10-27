"""HTTP routes and config keys for the Discover page (fork-only).

Kept out of main.py so upstream's file carries only the hooks: one registration
call and one spread into /api/config.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from flask import jsonify, request, session

from shelfmark.core.audible_topics import get_audible_topic_tree
from shelfmark.core.config import config as app_config
from shelfmark.core.discover import ALL_DISCOVER_ROW_KEYS, get_discover_row
from shelfmark.core.request_helpers import get_session_db_user_id
from shelfmark.core.utils import transform_cover_url
from shelfmark.metadata_providers.audible_taxonomy import (
    matching_core_topic_key,
    public_topic_nodes,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask import Flask, Response

_DISCOVER_CONTENT_TYPES = {"ebook", "audiobook", "combined"}


def discover_config(db_user_id: int | None) -> dict[str, Any]:
    """The /api/config keys the Discover page reads."""
    raw_topic = app_config.get("DEFAULT_DISCOVER_TOPIC", [], user_id=db_user_id)
    topic = (
        [segment.strip() for segment in raw_topic]
        if isinstance(raw_topic, list)
        and all(isinstance(segment, str) and bool(segment.strip()) for segment in raw_topic)
        else []
    )
    return {
        "default_discover_topic": topic,
        "default_discover_topic_core_key": matching_core_topic_key(
            str(app_config.get("AUDIBLE_REGION", "us") or "us"), topic
        ),
        "show_discover_rows": app_config.get("SHOW_DISCOVER_ROWS", True),
    }


def register_discover_routes(
    app: Flask,
    login_required: Callable[[Callable[..., Any]], Callable[..., Any]],
    attach_library_ownership: Callable[[Any, dict[str, Any]], None],
) -> None:
    """Register /api/discover and /api/metadata/audible/topics."""

    @app.route("/api/metadata/audible/topics", methods=["GET"])
    @login_required
    def api_audible_topics() -> Response | tuple[Response, int]:
        tree = get_audible_topic_tree()
        if tree is None:
            return jsonify({"error": "Audible topics are temporarily unavailable"}), 503
        return jsonify(
            {
                "region": tree.region,
                "stale": tree.stale,
                "topics": public_topic_nodes(tree.topics),
            }
        )

    @app.route("/api/discover", methods=["GET"])
    @login_required
    def api_discover() -> Response | tuple[Response, int]:
        """Return one discover row for the landing page."""
        if not app_config.get("SHOW_DISCOVER_ROWS", True):
            return jsonify({"error": "Discover rows are disabled"}), 404

        content_type = request.args.get("content_type", "ebook").strip().lower()
        row_key = request.args.get("row", "").strip().lower()
        if content_type not in _DISCOVER_CONTENT_TYPES:
            return jsonify({"error": f"Invalid content_type: {content_type}"}), 400
        if row_key not in ALL_DISCOVER_ROW_KEYS:
            return jsonify({"error": f"Invalid row: {row_key}"}), 400

        row = get_discover_row(content_type, row_key, user_id=get_session_db_user_id(session))
        if row is None:
            return jsonify({"row": row_key, "books": []})

        books_data = [asdict(book) for book in row.books]
        for book, book_dict in zip(row.books, books_data, strict=True):
            attach_library_ownership(book, book_dict)
            if book_dict.get("cover_url"):
                cache_id = f"{book_dict['provider']}_{book_dict['provider_id']}"
                book_dict["cover_url"] = transform_cover_url(book_dict["cover_url"], cache_id)

        return jsonify(
            {
                "row": row.key,
                "label": row.label,
                "provider": row.provider,
                "stale": row.stale,
                "books": books_data,
            }
        )
