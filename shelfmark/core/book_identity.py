"""Book identity carried from the metadata provider onto a download task (fork-only).

A post-upload hook tags the book it was given, and the strongest evidence it
can start from is what the admin was looking at when they picked the release:
the metadata provider's id, the ISBN and the ASIN. `provider` and
`provider_id` only mean something together, so they travel as a pair: one
without the other drops both rather than mixing ids from different books.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from shelfmark.core.request_helpers import normalize_optional_text

if TYPE_CHECKING:
    from collections.abc import Mapping

_ISBN_SEPARATORS = re.compile(r"[-\s]")
_ISBN10_SHAPE = re.compile(r"^[0-9]{9}[0-9X]$")
_ISBN13_SHAPE = re.compile(r"^[0-9]{13}$")
# Only the "Bookland" EAN prefixes are ISBNs; any other 13-digit EAN can still
# pass the ISBN-13 check digit.
_ISBN13_PREFIXES = ("978", "979")


def _isbn13_check_digit(body: str) -> str:
    """Return the check digit for the first twelve digits of an ISBN-13."""
    total = sum(int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(body[:12]))
    return str((10 - total % 10) % 10)


def _isbn13_is_valid(candidate: str) -> bool:
    return _isbn13_check_digit(candidate) == candidate[12]


def _isbn10_is_valid(candidate: str) -> bool:
    total = sum(
        (10 if char == "X" else int(char)) * (10 - index) for index, char in enumerate(candidate)
    )
    return total % 11 == 0


def normalize_isbn(value: object) -> str:
    """Normalize an ISBN to its ISBN-13 form, returning "" for anything unusable.

    ISBN-10 is converted rather than stored alongside: the mapping is lossless
    and deterministic, so canonicalizing means an ISBN-10 on one side and an
    ISBN-13 on the other still meet. Check digits are verified because metadata
    fields routinely carry "N/A" or zero-filled placeholders, and an exact match
    on junk is still an exact match.
    """
    if not isinstance(value, str):
        return ""

    candidate = _ISBN_SEPARATORS.sub("", value).upper()

    # Zero-filled placeholders satisfy their own check-digit arithmetic, so they
    # have to be turned away by hand.
    if not candidate or set(candidate) == {"0"}:
        return ""

    if _ISBN13_SHAPE.match(candidate):
        if not candidate.startswith(_ISBN13_PREFIXES):
            return ""
        return candidate if _isbn13_is_valid(candidate) else ""

    if _ISBN10_SHAPE.match(candidate):
        if not _isbn10_is_valid(candidate):
            return ""
        body = f"978{candidate[:9]}"
        return f"{body}{_isbn13_check_digit(body)}"

    return ""


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


def _differ(left: str | None, right: str | None) -> bool:
    return left is not None and right is not None and left.casefold() != right.casefold()


def _identifiers_conflict(
    release_data: Mapping[str, Any],
    release: BookIdentity,
    book: BookIdentity,
) -> bool:
    """True when an identifier present on both sides names a different book.

    The release's provider id is read raw: a lone id is dropped by the pair
    rule, yet it still says which book the release is.
    """
    return (
        _differ(_identity_text(release_data.get("provider_id")), book.provider_id)
        or _differ(release.isbn_13, book.isbn_13)
        or _differ(release.asin, book.asin)
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
    another book. Likewise, a release without a complete pair whose provider
    id, ISBN or ASIN disagrees with the book data's drops its half pair and
    imports nothing.
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
        ) or _identifiers_conflict(release_data, release, book):
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
