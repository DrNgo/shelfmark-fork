"""The ebook/audiobook vocabulary shared across the library index.

Both source providers and the lookup path need to say "ebook" or "audiobook"
in exactly the same words, so the two constants and the one conversion that
matters across format boundaries — "what format is this incoming book
request?" — live here rather than being redefined per module.
"""

from __future__ import annotations

from shelfmark.core.utils import is_audiobook

MEDIA_TYPE_EBOOK = "ebook"
MEDIA_TYPE_AUDIOBOOK = "audiobook"


def media_type_for_content_type(content_type: str | None) -> str:
    """Classify a book payload's `content_type` as an ebook or audiobook.

    A missing content type reads as an ebook. This is a documented API
    contract, not an oversight: today's frontend does not send `content_type`
    on every surface yet, so treating "no content type" as "audiobook" would
    badge and lock acquisition for books nobody has confirmed are held in
    that format. Reading it as ebook is the conservative default until every
    caller is wired up to send the field.
    """
    return MEDIA_TYPE_AUDIOBOOK if is_audiobook(content_type) else MEDIA_TYPE_EBOOK
