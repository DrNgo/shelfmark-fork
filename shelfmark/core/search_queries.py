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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

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


_STOPWORDS = frozenset(
    {"a", "an", "and", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
)
# Words that say "a volume" without saying which book; not identity on their own.
_TITLE_NOISE = frozenset({"vol", "volume"})
# Title-token identity needs this many significant tokens, or it would stop on almost
# anything; with fewer, nothing stops the ladder (which costs requests, never results).
_MIN_TITLE_TOKENS = 2
_TOKEN_RE = re.compile(r"[^\W_]+")
# A number joined to another by a dot or range mark: a fractional or multi-volume title.
_NUMBER_JOINER_RE = re.compile(r"\d\s*[.\-–—~&+]\s*\d")

# Release names that are not an ebook of the book: video encodes and episode markers.
_VIDEO_RE = re.compile(
    r"\b(?:2160p|1080p|720p|480p|x264|x265|h\.?264|h\.?265|hevc|bd|bdrip|blu-?ray|web-?dl"
    r"|webrip|mkv|mp4|avi|dual[ ._-]?audio|s\d{1,2}e\d{1,3}|episodes?|ep\.?\s?\d+)\b",
    re.IGNORECASE,
)
# A manga or comic edition is a different book from the novel it adapts. Applied only
# when the requested book itself is not a manga or comic.
_COMIC_RELEASE_RE = re.compile(r"\b(?:manga|comics?|graphic[\s._-]+novels?)\b", re.IGNORECASE)
_COMIC_BOOK_RE = re.compile(r"manga|comic", re.IGNORECASE)
# An ebook search is not satisfied by a recording of the book.
_AUDIO_RE = re.compile(r"\b(?:mp3|m4b|m4a|flac|aac|audiobook|unabridged)\b", re.IGNORECASE)

# Volume numbers a release name can carry: "Vol. 5", "Volume 05", "v05", "[5]", "- 5".
# The number is captured whole (at most six digits, so int() stays cheap); what follows
# it decides whether it is a complete volume token.
_RELEASE_VOLUME_RES = (
    re.compile(r"\bvol(?:ume)?s?\b\.?\s*(\d{1,6})(?!\d)", re.IGNORECASE),
    re.compile(r"\bv(\d{1,6})(?!\d)", re.IGNORECASE),
    re.compile(r"\[\s*(\d{1,3})\s*\](?!\d)"),
    re.compile(r"(?:^|\s)-\s*(\d{1,3})(?![\d.])"),
)
# After a volume number: a fraction ("5.5" - but not "05.2022", a year), a letter
# ("5a"), or a second volume ("5 & 6", "5 to 7", "5-7", "5—7", "v05-07") make it not a
# single complete volume.
_INCOMPLETE_VOLUME_RE = re.compile(
    r"\.\d(?!\d)|[^\W\d_]|\s*(?:[-–—~&+]|\bto\b|\band\b)\s*v?\d"
    r"|\s*,\s*(?:vol(?:ume)?s?\b\.?\s*|v)?\d{1,3}(?!\d|[^\W\d_])",
    re.IGNORECASE,
)

# A release that is several books at once: a collection word, or two numbers joined as a
# range or list ("The Expanse 1-3", "Books 1 & 2"). Bounded to three digits so a year
# range is not read as volumes.
_MULTI_VOLUME_RE = re.compile(
    r"\b(?:omnibus|collected|box(?:ed)?[\s._-]*set|bundle|complete[\s._-]+series)\b"
    r"|(?<![\d.])\d{1,3}\s*(?:-|–|—|~|&|\+|\bto\b|\band\b)\s*\d{1,3}(?![\d.])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SearchIdentity:
    """What a release name has to show to count as the requested book.

    ``series_key`` and ``position`` are set together, only when the book has a
    complete, consistent single-volume identity; otherwise ``title_tokens`` decide.
    """

    series_key: str = ""
    position: int | None = None
    title_tokens: tuple[str, ...] = ()
    # The requested book is itself a manga or comic, so such releases may be it.
    book_is_comic: bool = False
    # The book's title names its volume ("..., Vol. 5"). When it does not (Leviathan
    # Wakes is The Expanse 1), a release may name the book by title instead of by series
    # and number, so the title-token rule also applies.
    title_names_volume: bool = True


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.casefold())


def _title_tokens(title: str) -> tuple[str, ...]:
    """Significant tokens of the full cleaned title, without bare volume words."""
    return tuple(t for t in significant_tokens(title) if t not in _TITLE_NOISE)


def significant_tokens(text: object) -> tuple[str, ...]:
    """Word tokens of ``text`` without stopwords (all tokens if only stopwords remain)."""
    tokens = _tokens(clean_query(text))
    significant = [t for t in tokens if t not in _STOPWORDS]
    return tuple(significant or tokens)


def build_search_identity(
    *,
    title: object,
    current_query: object,
    series_name: object,
    series_position: object,
) -> SearchIdentity:
    """The identity ``is_identity_hit`` checks results against for this book."""
    title_text = _normalize_title(title)
    resolved = _resolve(title_text, series_name, series_position)
    # The full title, not today's (often shortened) query: "Spice, Vol. 5: Wolf" must not
    # be stopped by any release that merely says "Wolf".
    title_tokens = _title_tokens(title_text) or _title_tokens(clean_query(current_query))
    if _NUMBER_JOINER_RE.search(title_text):
        # "Vol. 5.5", "Vol. 1-3": the tokens cannot tell this book from its neighbours,
        # so nothing may stop the ladder (costs requests, never results).
        title_tokens = ()
    series_text = series_name if isinstance(series_name, str) else ""
    return SearchIdentity(
        series_key=resolved.series,
        position=resolved.position,
        title_tokens=title_tokens,
        book_is_comic=bool(_COMIC_BOOK_RE.search(f"{title_text} {series_text}")),
        title_names_volume=bool(_VOLUME_MARKER_RE.search(title_text)),
    )


def _release_volumes(text: str, series_tokens: tuple[str, ...]) -> set[int] | None:
    """The volume numbers ``text`` names, or None if any of them is not a whole volume."""
    patterns = list(_RELEASE_VOLUME_RES)
    if series_tokens:
        # "Expanse 01 - Leviathan Wakes": the series, its number, then " - ".
        last = re.escape(series_tokens[-1])
        patterns.append(re.compile(rf"\b{last}[\s._]+(\d{{1,3}})\s+-\s"))
    numbers: set[int] = set()
    for pattern in patterns:
        for match in pattern.finditer(text):
            if _INCOMPLETE_VOLUME_RE.match(text, match.end(1)):
                return None
            numbers.add(int(match.group(1)))
    return numbers


def is_identity_hit(
    release_title: object,
    *,
    series_key: str,
    position: int | None,
    title_tokens: tuple[str, ...] | list[str],
    content_type: str,
    book_is_comic: bool = False,
    title_names_volume: bool = True,
) -> bool:
    """Whether a release name is the requested book, for deciding when fallbacks stop.

    A series volume must name this volume (and no other), carry the series key tokens
    and not be video. Any other book must carry its significant title tokens and not be
    video. Unless ``book_is_comic``, a manga, comic or graphic-novel release is not the
    book either. Never used to filter or reorder results.

    A series volume whose title does not name its volume (``title_names_volume`` False:
    "Leviathan Wakes", The Expanse 1) is also the book by its title tokens, as long as
    the release names no other volume and is not a multi-volume set.
    """
    if not isinstance(release_title, str) or not release_title.strip():
        return False
    if not isinstance(title_tokens, (tuple, list)):
        return False
    if not isinstance(series_key, str) or isinstance(position, bool):
        series_key, position = "", None
    if position is not None and not isinstance(position, int):
        position = None
    text = release_title.casefold()
    if _VIDEO_RE.search(text):
        return False
    if str(content_type).strip().lower() == "ebook" and _AUDIO_RE.search(text):
        return False
    if not book_is_comic and _COMIC_RELEASE_RE.search(text):
        return False

    present = set(_tokens(text))
    if series_key and position is not None:
        key_tokens = significant_tokens(series_key)
        has_key = bool(key_tokens) and all(token in present for token in key_tokens)
        volumes = _release_volumes(text, key_tokens)
        if has_key and volumes == {position}:
            return True
        if title_names_volume is not False:
            return False
        # Named by title: only if it names no other volume and is not a set.
        if volumes is None or not volumes <= {position} or _MULTI_VOLUME_RE.search(text):
            return False

    wanted = [token.casefold() for token in title_tokens if isinstance(token, str) and token]
    return len(wanted) >= _MIN_TITLE_TOKENS and all(token in present for token in wanted)


def any_identity_hit(
    release_titles: Iterable[object],
    identity: SearchIdentity | None,
    *,
    content_type: str,
) -> bool:
    """Whether any of ``release_titles`` is the book ``identity`` describes."""
    if identity is None:
        return False
    try:
        return any(
            is_identity_hit(
                title,
                series_key=identity.series_key,
                position=identity.position,
                title_tokens=identity.title_tokens,
                content_type=content_type,
                book_is_comic=identity.book_is_comic,
                title_names_volume=identity.title_names_volume,
            )
            for title in release_titles
        )
    except TypeError:
        return False
