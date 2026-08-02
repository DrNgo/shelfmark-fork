"""HTTP routes for library lookups from multiple sources."""

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, session

from shelfmark.library.lookup import lookup_books

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask.typing import ResponseReturnValue


def register_library_routes(
    app: Flask,
    *,
    resolve_auth_mode: Callable[[], str] | None = None,
) -> None:
    """Register the library-backed endpoints on the Flask app.

    `resolve_auth_mode` is resolved per request rather than captured, so a
    runtime auth-mode change takes effect without re-registering routes.
    """

    def no_auth_configured() -> bool:
        return resolve_auth_mode is not None and resolve_auth_mode() == "none"

    def require_login() -> ResponseReturnValue | None:
        if no_auth_configured():
            return None
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return None

    @app.route("/api/library-matches", methods=["POST"])
    def api_library_matches() -> ResponseReturnValue:
        """Report which of the posted books are already in a library.

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
