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


def fill_identity_from_book_data(
    release_data: Mapping[str, Any],
    book_data: object,
) -> dict[str, Any]:
    """Fill identity an approved release lacks from the request's stored book data.

    A release carrying its own complete provider pair keeps it, and takes the
    ISBN and ASIN from the book data only when both name the same book (same
    provider and id): an admin may have browsed to a release described by a
    different provider. A release that names a provider but lost its id, while
    the book data names a different provider, drops its half pair and imports
    nothing: adopting the request's pair would pin the release's ISBN to
    another book.
    Otherwise a release without a complete pair takes the book data's pair
    whole. Returns a new dict; the input is not modified.
    """
    filled = dict(release_data)
    if not isinstance(book_data, dict):
        return filled

    release = normalize_book_identity(release_data)
    book = normalize_book_identity(book_data)

    if release.provider is None:
        named_provider = _identity_text(release_data.get("provider"))
        if (
            named_provider is not None
            and book.provider is not None
            and named_provider.casefold() != book.provider.casefold()
        ):
            filled["provider"] = None
            filled["provider_id"] = None
            return filled
        if book.provider is not None:
            filled["provider"] = book.provider
            filled["provider_id"] = book.provider_id
    elif (
        book.provider is None
        or release.provider.casefold() != book.provider.casefold()
        or release.provider_id != book.provider_id
    ):
        return filled

    if release.isbn_13 is None and book.isbn_13 is not None:
        filled["isbn_13"] = book.isbn_13
    if release.asin is None and book.asin is not None:
        filled["asin"] = book.asin
    return filled
