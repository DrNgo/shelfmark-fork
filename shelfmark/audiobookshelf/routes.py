"""HTTP routes for the Audiobookshelf integration."""

from typing import TYPE_CHECKING

from flask import Flask, jsonify, session

from shelfmark.audiobookshelf.destinations import list_destination_options

if TYPE_CHECKING:
    from flask.typing import ResponseReturnValue


def register_audiobookshelf_routes(app: Flask) -> None:
    """Register the Audiobookshelf-backed endpoints on the Flask app."""

    @app.route("/api/audiobook-destinations", methods=["GET"])
    def api_audiobook_destinations() -> ResponseReturnValue:
        """List the audiobook libraries an admin can route an approval to.

        Served from stored config, never from a live Audiobookshelf call, so
        approving a request keeps working while Audiobookshelf is down.
        """
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403

        return jsonify({"destinations": list_destination_options()})
