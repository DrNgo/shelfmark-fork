"""HTTP route for the download destination picker (fork-only).

One endpoint serves both formats: audiobooks route to an Audiobookshelf
library mapped in settings, ebooks to a Grimmory library path.
"""

from typing import TYPE_CHECKING

from flask import Flask, jsonify, request, session

from shelfmark.audiobookshelf.destinations import list_destination_options
from shelfmark.config.booklore_settings import get_booklore_destination_options

if TYPE_CHECKING:
    from collections.abc import Callable

    from flask.typing import ResponseReturnValue

_CONTENT_TYPES = frozenset({"ebook", "audiobook"})


def register_destination_routes(
    app: Flask,
    *,
    resolve_auth_mode: Callable[[], str] | None = None,
) -> None:
    """Register `GET /api/download-destinations` on the Flask app.

    `resolve_auth_mode` is resolved per request rather than captured, so a
    runtime auth-mode change takes effect without re-registering routes.
    """

    def no_auth_configured() -> bool:
        return resolve_auth_mode is not None and resolve_auth_mode() == "none"

    def require_admin() -> ResponseReturnValue | None:
        # Auth mode "none" means there are no accounts at all and every caller
        # is a full admin — that is what `/api/auth/check` reports, and the UI
        # renders admin controls on that basis. Gating on a session flag nobody
        # can hold would silently hide the picker on that setup.
        if no_auth_configured():
            return None
        if not session.get("is_admin", False):
            return jsonify({"error": "Admin access required"}), 403
        return None

    @app.route("/api/download-destinations", methods=["GET"])
    def api_download_destinations() -> ResponseReturnValue:
        """List where an admin can route a download of the given content type.

        Audiobook destinations come from stored config, so approving keeps
        working while Audiobookshelf is down. Ebook destinations come from the
        cached Grimmory library list; they are display only, and an upload
        verifies its target again. `default_name` is always "": the default a
        blank choice lands in depends on the target user's own settings.
        """
        forbidden = require_admin()
        if forbidden is not None:
            return forbidden

        content_type = request.args.get("content_type", "")
        if content_type not in _CONTENT_TYPES:
            return jsonify({"error": "content_type must be 'ebook' or 'audiobook'"}), 400

        destinations = (
            list_destination_options()
            if content_type == "audiobook"
            else get_booklore_destination_options()
        )
        return jsonify({"destinations": destinations, "default_name": ""})
