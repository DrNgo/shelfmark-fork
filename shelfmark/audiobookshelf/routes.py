"""HTTP routes for the Audiobookshelf integration."""

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, session

from shelfmark.audiobookshelf.destinations import list_destination_options
from shelfmark.audiobookshelf.library_lookup import lookup_books

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask.typing import ResponseReturnValue


def register_audiobookshelf_routes(
    app: Flask,
    *,
    resolve_auth_mode: Callable[[], str] | None = None,
) -> None:
    """Register the Audiobookshelf-backed endpoints on the Flask app.

    `resolve_auth_mode` is resolved per request rather than captured, so a
    runtime auth-mode change takes effect without re-registering routes.
    """

    def require_login() -> ResponseReturnValue | None:
        if resolve_auth_mode is not None and resolve_auth_mode() == "none":
            return None
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return None

    @app.route("/api/audiobook-destinations", methods=["GET"])
    def api_audiobook_destinations() -> ResponseReturnValue:
        """List the audiobook libraries an admin can route an approval to.

        Served from stored config, never from a live Audiobookshelf call, so
        approving a request keeps working while Audiobookshelf is down.
        """
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403

        return jsonify({"destinations": list_destination_options()})

    @app.route("/api/library-matches", methods=["POST"])
    def api_library_matches() -> ResponseReturnValue:
        """Report which of the posted books are already in an ABS library.

        Open to every signed-in user, not just admins: the whole point is that
        a requester sees "you already have this" before asking for it.
        """
        unauthorized = require_login()
        if unauthorized is not None:
            return unauthorized

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "Expected a JSON object"}), 400

        books = payload.get("books", [])
        return jsonify(lookup_books(books if isinstance(books, list) else []))
