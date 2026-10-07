"""Book identity carried from the metadata provider onto a download task (fork-only).

A post-upload hook tags the book it was given, and the strongest evidence it
can start from is what the admin was looking at when they picked the release:
the metadata provider's id, the ISBN and the ASIN. `provider` and
`provider_id` only mean something together, so they travel as a pair: one
without the other drops both rather than mixing ids from different books.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from shelfmark.core.request_helpers import normalize_optional_text
from shelfmark.library.matching import normalize_isbn

if TYPE_CHECKING:
    from collections.abc import Mapping

# Manual books have no metadata record: their "provider id" names nothing.
_PROVIDERS_WITHOUT_IDENTITY = frozenset({"manual"})


@dataclass(frozen=True)
class BookIdentity:
    """The four identity fields a download task carries (all optional)."""

    provider: str | None = None
    provider_id: str | None = None
    isbn_13: str | None = None
    asin: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        """Return the fields as a plain dict, for payloads and retry rows."""
        return asdict(self)


def _identity_text(value: object) -> str | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return normalize_optional_text(value)


def normalize_book_identity(data: Mapping[str, Any]) -> BookIdentity:
    """Read the identity fields from a release payload, request or retry row.

    Strings are trimmed (blank becomes None); the ISBN is canonicalized to
    ISBN-13 from `isbn_13`, else `isbn_10`, and dropped when invalid.
    """
    provider = _identity_text(data.get("provider"))
    provider_id = _identity_text(data.get("provider_id"))
    if (
        provider is None
        or provider_id is None
        or provider.casefold() in _PROVIDERS_WITHOUT_IDENTITY
    ):
        provider = provider_id = None

    isbn_13 = normalize_isbn(data.get("isbn_13")) or normalize_isbn(data.get("isbn_10"))

    return BookIdentity(
        provider=provider,
        provider_id=provider_id,
        isbn_13=isbn_13 or None,
        asin=normalize_optional_text(data.get("asin")),
    )
