"""Fallback release-search queries and the identity check that decides when they stop.

Release search sends one title-shaped query per language variant, built from
``book.search_title or book.title``. For light-novel volumes that query often finds
nothing: Hardcover data puts "(Light Novel)" in titles no release carries, or reduces a
volume to a subtitle no release uses. The ladder below adds a few release-shaped
queries ("<Series> Vol. N", "<Series> vNN", ...) for Prowlarr and Newznab to try, in
order, only while nothing found so far is actually the requested book.

Both entry points are pure and total: junk input drops rungs or returns False, and
nothing here raises.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# Medium labels no release name carries. "(Manga)" is a different adaptation, so it stays.
_MEDIUM_LABEL_RE = re.compile(r"\s*\((?:light\s+novel|novel|ln)\)", re.IGNORECASE)

# "<Series>, Vol. N[: <Name>]" / "<Series> Volume N[: <Name>]" (medium labels removed first).
# The token is everything up to whitespace or a colon, so "1-3", "1.5" and "III" reach the
# digit check whole instead of being cut down to a number they do not mean.
_TITLE_PARSE_RE = re.compile(
    r"^(?P<series>.+?)(?:\s*,\s*|\s+)vol(?:ume)?s?\b\.?\s*(?P<token>[^\s:]+)"
    r"(?:\s*:\s*(?P<name>.*))?$",
    re.IGNORECASE,
)
_VOLUME_MARKER_RE = re.compile(r"\bvol(?:ume)?s?\b", re.IGNORECASE)

# One part of a split book. Only a title without a single parsed volume number is
# suppressed by it: in "Overlord, Vol. 5: The Men of the Kingdom Part I" the part is the
# book's name and Vol. 5 is still a distinct volume, while "Spice, Vol. 2 Part 1" never
# parses (its volume token is not alone) and "Spice Part II" carries no volume at all.
_POSITION_STRING_RE = re.compile(r"\+?[0-9]+(?:\.[0-9]+)?", re.ASCII)
_PART_RE = re.compile(r"\bpart\s+(?:[ivx]+|\d+|one|two|three|four|five)\b", re.IGNORECASE)

# Titles naming more than one volume: "Overlord Vol. 5" would be a different book (or
# several), so these get no single-volume series rungs.
_DISTINGUISHING_RE = re.compile(
    r"\b(?:omnibus|collected|box(?:ed)?\s*set"
    r"|vol(?:ume)?s?\.?\s*\d+\s*(?:-|–|—|~|to|and|&)\s*\d+)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ParsedTitle:
    series: str
    volume: int
    name: str


def clean_query(text: object) -> str:
    """Drop medium labels, turn ``:`` and ``,`` into spaces, collapse whitespace."""
    if not isinstance(text, str):
        return ""
    without_labels = _MEDIUM_LABEL_RE.sub(" ", text)
    return " ".join(re.sub(r"[:,]", " ", without_labels).split())


def query_key(text: object) -> str:
    """How two *ladder* queries are compared: cleaned, so punctuation does not count."""
    return clean_query(text).casefold()


def exact_query_key(text: object) -> str:
    """How a ladder query is compared with a query that is sent as-is.

    Today's query goes to the indexer uncleaned, so "High School DxD (Light Novel), Vol. 5:
    Hellcat..." and its cleaned form are different requests: only case and whitespace
    are ignored here.
    """
    return " ".join(text.split()).casefold() if isinstance(text, str) else ""


# No real series reaches this; anything larger is junk, and bounding it keeps int()
# away from pathological digit strings.
MAX_POSITION = 10_000
_MAX_POSITION_DIGITS = 6


def normalize_position(value: object) -> int | None:
    """A finite, non-negative, integral position up to ``MAX_POSITION``, or None.

    Integral floats and numeric strings are accepted without truncation ("3", 3.0,
    "3.0" parsed exactly); booleans, negatives, fractions, NaN, infinity, oversized
    numbers and digit strings longer than six digits are not.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = Decimal(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            return None
        number = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        digits = text.split(".", 1)[0].lstrip("+")
        if _POSITION_STRING_RE.fullmatch(text) is None:
            return None
        if len(text) > 2 * _MAX_POSITION_DIGITS or len(digits) > _MAX_POSITION_DIGITS:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:
            return None
    else:
        return None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        return None
    if number > MAX_POSITION:
        return None
    return int(number)


def _position_is_absent(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_title(title: str) -> _ParsedTitle | None:
    match = _TITLE_PARSE_RE.match(_MEDIUM_LABEL_RE.sub("", title).strip())
    if match is None:
        return None
    token = match.group("token")
    if not (token.isascii() and token.isdigit()) or len(token) > _MAX_POSITION_DIGITS:
        return None
    if int(token) > MAX_POSITION:
        return None
    return _ParsedTitle(
        series=match.group("series").strip(),
        volume=int(token),
        name=(match.group("name") or "").strip(),
    )


@dataclass(frozen=True)
class _Resolved:
    series: str
    position: int | None
    parsed: _ParsedTitle | None
    standalone: bool


def _resolve(title: str, series_name: object, series_position: object) -> _Resolved:
    """Work out the single-volume identity the series rungs may use, if any."""
    parsed = _parse_title(title)
    has_marker = bool(_VOLUME_MARKER_RE.search(title))
    cleaned_series_name = clean_query(series_name)

    position_absent = _position_is_absent(series_position)
    metadata_position = None if position_absent else normalize_position(series_position)

    standalone = not has_marker and not (cleaned_series_name and not position_absent)

    series = cleaned_series_name or (clean_query(parsed.series) if parsed else "")
    # Only an absent position is filled in from the title; an unusable one stays None.
    title_volume = parsed.volume if parsed else None
    position = title_volume if position_absent else metadata_position

    usable = (
        bool(series)
        and position is not None
        # A position that is present but unusable (1.5, -1, True) is not replaced by
        # the title's number: the metadata says this is not a plain single volume.
        and (position_absent or metadata_position is not None)
        # Metadata and title disagree: neither can be trusted.
        and not (parsed is not None and parsed.volume != position)
        # The title names a volume we could not parse ("Vol. III", "Vol. 1.5").
        and not (has_marker and parsed is None)
        # With a parsed single volume, only the text before the volume marker can make
        # this a collection: a book *name* like "The Collected Heroes" must not.
        and not _DISTINGUISHING_RE.search(parsed.series if parsed is not None else title)
        and not (parsed is None and _PART_RE.search(title))
    )
    if not usable:
        return _Resolved(series="", position=None, parsed=parsed, standalone=standalone)
    return _Resolved(series=series, position=position, parsed=parsed, standalone=standalone)


def _normalize_title(title: object) -> str:
    return " ".join(title.split()) if isinstance(title, str) else ""


def build_fallback_queries(
    *,
    title: object,
    current_query: object,
    series_name: object,
    series_position: object,
) -> list[str]:
    """Release-shaped queries to try after ``current_query`` finds nothing usable.

    Order: ``<Series> Vol. N``, ``<Series> vNN``, the book name after ``Vol. N:``,
    ``<Series> Volume NN``, the cleaned full title - each cleaned. A rung is dropped
    when it is the same request as ``current_query`` (case and whitespace aside; today's
    query is sent uncleaned) or the same cleaned query as an earlier rung.
    """
    title_text = _normalize_title(title)
    resolved = _resolve(title_text, series_name, series_position)
    if resolved.standalone:
        return []

    candidates: list[str] = []
    if resolved.position is not None:
        candidates += [
            f"{resolved.series} Vol. {resolved.position}",
            f"{resolved.series} v{resolved.position:02d}",
        ]
    if resolved.parsed is not None and resolved.parsed.name:
        candidates.append(clean_query(resolved.parsed.name))
    if resolved.position is not None:
        candidates.append(f"{resolved.series} Volume {resolved.position:02d}")
    candidates.append(clean_query(title_text))

    current = exact_query_key(current_query)
    seen: set[str] = set()
    queries: list[str] = []
    for candidate in candidates:
        key = query_key(candidate)
        if not key or key in seen or exact_query_key(candidate) == current:
            continue
        seen.add(key)
        queries.append(candidate)
    return queries
