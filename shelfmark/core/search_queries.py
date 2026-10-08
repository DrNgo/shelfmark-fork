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

import html
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

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


def has_medium_label(text: object) -> bool:
    """Whether ``text`` carries a medium label such as "(Light Novel)"."""
    return isinstance(text, str) and _MEDIUM_LABEL_RE.search(text) is not None


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
    r"\b(?:omnibus|collected|collection|box(?:ed)?[\s._-]*set|bundle|complete[\s._-]+series"
    r"|trilogy|duology|quartet|books)\b|\s[/&+]\s"
    r"|(?<![\d.])\d{1,3}\s*(?:-|–|—|~|&|\+|\bto\b|\band\b)\s*\d{1,3}(?![\d.])",
    re.IGNORECASE,
)
# A release's file version, "(v2.0)" or "[v1.1]": not a volume number.
_VERSION_TAG_RE = re.compile(r"[(\[]\s*v\d+(?:\.\d+)+\s*[)\]]", re.IGNORECASE)
# Any standalone 1-3 digit number ("2", "#2", "Book 2"); four-digit years are not volumes.
_ANY_NUMBER_RE = re.compile(r"(?<![\d.])\d{1,3}(?![\d.])")


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
        # "Expanse 01 - Leviathan Wakes": the series, its number, then " - "; and the
        # bracketed "[Overlord 02] - The Dark Warrior".
        last = re.escape(series_tokens[-1])
        patterns.append(re.compile(rf"\b{last}[\s._]+(\d{{1,3}})\s+-\s"))
        patterns.append(re.compile(rf"\b{last}[\s._]+(\d{{1,3}})\s*\]"))
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
    # Indexers send "&amp;" for "&"; a file version tag "(v2.0)" is not a volume.
    text = _VERSION_TAG_RE.sub(" ", html.unescape(release_title)).casefold()
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
        # Named by title: only if the title has words of its own beyond the series name
        # ("The Hunger Games" 1 would match "The Hunger Games 2"), the release names no
        # other number (volume, "#2", "Book 2") and it is not a set.
        if volumes is None or not volumes <= {position} or _MULTI_VOLUME_RE.search(text):
            return False
        series_words = set(key_tokens)
        own = [t for t in title_tokens if isinstance(t, str) and t.casefold() not in series_words]
        if not own:
            return False
        if any(int(n) != position for n in _ANY_NUMBER_RE.findall(text)):
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


# --- Release ranking --------------------------------------------------------------------
#
# A classifier separate from ``is_identity_hit``: that predicate only decides when the
# fallback ladder may stop, and keeps its own, deliberately broader rules. This one decides
# how the release list is ordered, so it acts only on strong, explicit evidence: a release
# moves up only when it names this volume in explicit volume syntax, and down only when a
# declared format or category says it is another medium, or explicit volume syntax names
# another volume. Anything ambiguous is "unknown", which keeps today's order.

RELEASE_MATCH_VERSION = 1

type Volume = Literal["match", "other", "unknown"]
type Medium = Literal["ebook", "comic", "audio", "video", "unknown"]

_AUDIO_FORMATS = frozenset({"m4b", "mp3", "m4a", "flac", "aac"})
_COMIC_FORMATS = frozenset({"cbz", "cbr", "cb7"})
_EBOOK_FORMATS = frozenset({"epub", "mobi", "azw3", "pdf"})
# Prowlarr and Newznab report an ebook category as "book"; the other sources say "ebook".
_EBOOK_CONTENT_TYPES = frozenset({"ebook", "book"})
_ALL_FORMATS = _AUDIO_FORMATS | _COMIC_FORMATS | _EBOOK_FORMATS
_FORMAT_TOKENS = "|".join(sorted(_ALL_FORMATS))

# Technical video markers only: plain words such as "episode" say nothing about the medium
# ("Overlord Vol. 2: Episodes of the Kingdom" is a light novel).
_RANK_VIDEO_RE = re.compile(
    r"\b(?:2160p|1080p|720p|480p|x264|x265|h\.?264|h\.?265|hevc|mkv|mp4|avi"
    r"|bdrip|web-?dl|webrip|s\d{1,2}e\d{1,3})\b",
    re.IGNORECASE,
)
_RANK_AUDIO_WORD_RE = re.compile(r"\b(?:m4b|mp3|audiobook)\b", re.IGNORECASE)
_RANK_COMIC_WORD_RE = re.compile(
    r"\b(?:manga|comics?|graphic[\s.-]+novels?|cbz|cbr|cb7)\b", re.IGNORECASE
)
# Matched after "_" became a space, so "Fan_TL" and "Baka_Tsuki" count too.
_FAN_MARKER_RE = re.compile(
    r"\b(?:fan[\s.-]?tl|fan[\s.-]translations?|fan[\s.-]translated|baka[\s.-]?tsuki"
    r"|scanlations?)\b",
    re.IGNORECASE,
)

# Numbers that are never volumes, masked before any volume parsing: ISBNs, dates and file
# sizes ("978-1-9753-0...", "2016-05-24", "1-2 MB", "620.5 MB").
_RANK_NOISE_RES = (
    re.compile(r"\b97[89](?:[\s-]?\d){10}\b"),
    re.compile(r"\b(?:19|20)\d{2}-\d{1,2}-\d{1,2}\b"),
    re.compile(
        r"\b\d+(?:[.,]\d+)?(?:\s*(?:-|–|to)\s*\d+(?:[.,]\d+)?)?\s*(?:[kmgt]i?b|bytes?)\b",
        re.IGNORECASE,
    ),
)

# Explicit volume syntax: "Vol N", "Vol. N", "Volume N", "Vols N", "vNN", "#N", "Book N"
# (with ".", "_", " " or "-" as separators; "_" is a space by the time these run). N is
# one to three digits; bare "[N]" and "- N" are not volume syntax here.
_RANK_VOLUME_RES = (
    re.compile(r"\bvol(?:ume)?s?\b\.?[\s.-]*(\d{1,3})(?!\d)", re.IGNORECASE),
    re.compile(r"\bv(\d{1,3})(?!\d)", re.IGNORECASE),
    re.compile(r"#(\d{1,3})(?!\d)"),
    re.compile(r"\bbook[\s.-]+(\d{1,3})(?!\d)", re.IGNORECASE),
)
# A number that is not a whole volume: a decimal suffix of any length ("2.5", "2.125") or
# a letter suffix ("5a"). A four-digit year after a dot ("Vol.02.2016") is not a decimal.
# Ambiguous, so the release's volume is unknown.
_RANK_PARTIAL_VOLUME_RE = re.compile(r"\.(?!(?:19|20)\d{2}(?!\d))\d+|[^\W\d_]")
# A range or list separator between two volume numbers: "5-6", "5 & 6", "5 to 7", "2/3".
_RANK_RANGE_SEPARATOR = r"\s*(?:[-–—~&+,/]|\bto\b|\band\b|\bthrough\b)\s*"
# A second volume right after the first: "5-6", "5 & 6", "v05-v07", "1, 2", "2 / 3".
_RANK_VOLUME_LIST_RE = re.compile(
    _RANK_RANGE_SEPARATOR + r"(?:vol(?:ume)?s?\b\.?\s*|v|#|book\s+)?\d{1,3}(?![\d.]|[^\W\d_])",
    re.IGNORECASE,
)
# Collection evidence: several books in one release. Separators between names ("Corey &
# Abraham", "Author / Illustrator") are not; numbers count only in volume context.
_RANK_COLLECTION_RE = re.compile(
    r"\b(?:omnibus|box(?:ed)?[\s.-]*set|complete[\s.-]+series|collection|trilogy|duology"
    r"|quartet)\b"
    r"|\bbooks[\s.-]*\d{1,3}" + _RANK_RANGE_SEPARATOR + r"\d{1,3}(?!\d)",
    re.IGNORECASE,
)
# Where a run of title-like words ends: a bracket, a parenthesis or " - ".
_RANK_SEGMENT_END_RE = re.compile(r"[\[\](){}]|\s-\s")
# A revision tag in brackets, "[v2]" or "(v1.0)": not a volume. A bare "v2" is.
_RANK_REVISION_TAG_RE = re.compile(r"[(\[]\s*v\d+(?:\.\d+)*\s*[)\]]", re.IGNORECASE)
# Any bracketed or parenthesised annotation in an author field: "(Author)", "[Autor]".
_AUTHOR_ANNOTATION_RE = re.compile(r"\([^)]*\)|\[[^\]]*\]")
# A separator that can join two titles in one release name, after or before a title.
_RANK_TITLE_SEPARATOR = r"(?:\s*[,;|&+/]\s*|\s+-\s+|\s*\band\b\s*)"
_RANK_TITLE_SEPARATOR_RE = re.compile(_RANK_TITLE_SEPARATOR)
_RANK_TITLE_SEPARATOR_END_RE = re.compile(_RANK_TITLE_SEPARATOR + r"$")
_ARTICLES = frozenset({"the", "a", "an"})
_PAGE_WORDS = frozenset({"page", "pages"})
# Words that decorate a release name without naming another book: edition and language
# notes, number words, and publishers commonly prefixed to a title.
_NEUTRAL_WORDS = frozenset(
    {
        "retail", "ebook", "kindle", "edition", "editions", "anniversary", "illustrated",
        "unabridged", "abridged", "english", "eng", "en", "novel", "book", "books", "series",
        "saga", "digital", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth",
        "ninth", "tenth", "one", "two", "three", "four", "five", "six", "seven", "eight",
        "nine", "ten", "orbit", "tor", "del", "rey", "yen", "press", "on", "seas", "peace",
        "club", "penguin", "harpercollins", "scholastic", "bloomsbury",
    }
)  # fmt: skip
_ORDINAL_RE = re.compile(r"\d+(?:st|nd|rd|th)")
_RANK_BRACKET_RE = re.compile(r"[\[\](){}]")
# Words in an IRC author slot that name no person: volume markers ("Vol", "v02"), numbers,
# ordinals and "LN" / "Light Novel" ("!Bsk Overlord Vol 2 - The Dark Warrior.epub" puts the
# series and volume where the author goes).
_AUTHOR_NOISE_RE = re.compile(r"vols?|volumes?|v\d+|\d+|\d+(?:st|nd|rd|th)|ln|light|novels?")
# Author names that say nothing about who wrote the book.
_PLACEHOLDER_AUTHORS = frozenset({"unknown", "various", "anonymous", "n/a", "na", "none"})
# Separators between contributors in one author field ("Corey, James S A" is split too:
# each side is then compared on its own).
_AUTHOR_SPLIT_RE = re.compile(r"\s*(?:[,;&+/]|\band\b)\s*", re.IGNORECASE)


@dataclass(frozen=True)
class RankingIdentity:
    """The requested book, as the release ranking sees it (one per request)."""

    series_key: str = ""
    position: int | None = None
    title_tokens: tuple[str, ...] = ()
    title_names_volume: bool = True
    book_is_comic: bool = False
    authors: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseMatch:
    """How one release relates to the requested book (see ``classify_release``)."""

    volume: Volume
    other_volume: int | None  # set only when volume == "other"; never 0
    medium: Medium
    compatible: bool  # the medium suits the requested book
    fan_marker: bool  # the name explicitly says fan translation

    def to_payload(self) -> dict[str, object]:
        """The versioned ``extra["release_match"]`` value the frontend parses."""
        return {
            "v": RELEASE_MATCH_VERSION,
            "volume": self.volume,
            "other_volume": self.other_volume,
            "medium": self.medium,
            "compatible": self.compatible,
            "fan_marker": self.fan_marker,
        }


_UNKNOWN_MATCH = ReleaseMatch("unknown", None, "unknown", compatible=True, fan_marker=False)


def is_comic_book(title: object, series_name: object) -> bool:
    """Whether the requested book is itself a manga or comic (word-bounded, so not "Comical")."""
    title_text = title if isinstance(title, str) else ""
    series_text = series_name if isinstance(series_name, str) else ""
    return _RANK_COMIC_WORD_RE.search(f"{title_text} {series_text}") is not None


_APOSTROPHES = "'`\u00b4\u02bc\u2018\u2019"
_FOLD_TABLE = str.maketrans("", "", _APOSTROPHES)


def _fold(text: str) -> str:
    """Drop apostrophe variants so "Caliban's" and "Calibans" tokenise alike (ranking only)."""
    return text.translate(_FOLD_TABLE)


def _clean_strings(values: object) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(v for v in values if isinstance(v, str) and v.strip())


def build_ranking_identity(
    *,
    title: object,
    current_query: object,
    series_name: object,
    series_position: object,
    authors: object,
) -> RankingIdentity:
    """The ranking identity, resolved exactly as the ladder resolves series and position."""
    search_identity = build_search_identity(
        title=_fold(title) if isinstance(title, str) else title,
        current_query=_fold(current_query) if isinstance(current_query, str) else current_query,
        series_name=_fold(series_name) if isinstance(series_name, str) else series_name,
        series_position=series_position,
    )
    return RankingIdentity(
        series_key=search_identity.series_key,
        position=search_identity.position,
        title_tokens=search_identity.title_tokens,
        title_names_volume=search_identity.title_names_volume,
        book_is_comic=is_comic_book(title, series_name),
        authors=_clean_strings(authors),
    )


def _sanitize_identity(identity: object) -> RankingIdentity:
    """A well-typed copy of ``identity``: junk fields become their empty defaults."""
    if not isinstance(identity, RankingIdentity):
        return RankingIdentity()
    position = identity.position
    if isinstance(position, bool) or not isinstance(position, int) or position < 0:
        position = None
    series_key = _fold(identity.series_key) if isinstance(identity.series_key, str) else ""
    return RankingIdentity(
        series_key=series_key,
        position=position,
        title_tokens=tuple(_fold(t.casefold()) for t in _clean_strings(identity.title_tokens)),
        title_names_volume=identity.title_names_volume is not False,
        book_is_comic=identity.book_is_comic is True,
        authors=tuple(_fold(a) for a in _clean_strings(identity.authors)),
    )


def _declared_formats(formats: object) -> set[str]:
    if not isinstance(formats, (list, tuple)):
        return set()
    return {f.strip().casefold() for f in formats if isinstance(f, str) and f.strip()}


def _name_word_counts(pattern: re.Pattern[str], text: str, own_tokens: set[str]) -> bool:
    """Whether ``pattern`` finds a word in ``text`` that is not one of the book's own words."""
    for match in pattern.finditer(text):
        words = set(_tokens(match.group(0)))
        if not words <= own_tokens:
            return True
    return False


def _medium(text: str, formats: set[str], content_type: str, own_tokens: set[str]) -> Medium:
    if content_type == "audiobook" or formats & _AUDIO_FORMATS:
        return "audio"
    if formats & _COMIC_FORMATS:
        return "comic"
    if _RANK_VIDEO_RE.search(text):
        return "video"
    if _name_word_counts(_RANK_AUDIO_WORD_RE, text, own_tokens):
        return "audio"
    if _name_word_counts(_RANK_COMIC_WORD_RE, text, own_tokens):
        return "comic"
    if formats & _EBOOK_FORMATS or content_type in _EBOOK_CONTENT_TYPES:
        return "ebook"
    return "unknown"


def _series_volume_res(series_tokens: tuple[str, ...]) -> list[re.Pattern[str]]:
    if not series_tokens:
        return []
    last = re.escape(series_tokens[-1])
    return [
        # "[Overlord 02]" (and "[Overlord - Volume 02]", which "Volume" already covers).
        re.compile(rf"\b{last}(?:\s+|[.-])(\d{{1,3}})\s*\]"),
        # "Overlord 02" followed by " - ", "]", "(", a year, a format or the end. One
        # separator only: "High School DxD - 5" is a bare "- N", not volume syntax.
        re.compile(
            rf"\b{last}(?:\s+|[.-])(\d{{1,3}})(?=\s+-\s|\s*\]|\s*\(|[\s.-]+(?:19|20)\d{{2}}(?!\d)"
            rf"|[\s.-]+(?:{_FORMAT_TOKENS})\b|\s*$)"
        ),
    ]


def _explicit_volumes(text: str, series_tokens: tuple[str, ...]) -> set[int] | None:
    """Volume numbers ``text`` names in explicit syntax; None when one is not a whole volume."""
    numbers: set[int] = set()
    for pattern in [*_RANK_VOLUME_RES, *_series_volume_res(series_tokens)]:
        for match in pattern.finditer(text):
            if _RANK_PARTIAL_VOLUME_RE.match(text, match.end(1)):
                return None
            numbers.add(int(match.group(1)))
    return numbers


def _has_volume_list(text: str, series_tokens: tuple[str, ...]) -> bool:
    """A range or list of volume numbers right after a volume marker or the series name."""
    patterns = list(_RANK_VOLUME_RES)
    if series_tokens:
        last = re.escape(series_tokens[-1])
        patterns.append(re.compile(rf"\b{last}(?:\s+|[.-])(\d{{1,3}})(?!\d)"))
    for pattern in patterns:
        for match in pattern.finditer(text):
            if _RANK_VOLUME_LIST_RE.match(text, match.end(1)):
                return True
    return False


def _token_spans(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(0), m.start(), m.end()) for m in _TOKEN_RE.finditer(text)]


def _title_words(
    segment: str, own_words: frozenset[str], author_words: frozenset[str]
) -> list[str]:
    """Words of ``segment`` that could belong to another title.

    Not: formats, numbers, ordinals, initials, page counts, stopwords, series-key words,
    requested-author words and the neutral edition/language/publisher words.
    """
    skip = _ALL_FORMATS | _PAGE_WORDS | _STOPWORDS | _NEUTRAL_WORDS | own_words | author_words
    return [
        t
        for t in _tokens(segment)
        if t not in skip and len(t) > 1 and not t.isdigit() and not _ORDINAL_RE.fullmatch(t)
    ]


def _title_span(text: str, wanted: set[str]) -> tuple[int, int] | None:
    """Where the requested title sits in ``text``: first completion, walked back to its start.

    The span starts at the last occurrence of each title word before the first place all
    of them have been seen (so a series name repeated in a bundle anchors on the right
    copy) and takes the articles right before it ("The", "A", "An").
    """
    spans = _token_spans(text)
    seen: set[str] = set()
    last = None
    for index, (token, _start, _end) in enumerate(spans):
        if token in wanted:
            seen.add(token)
            if seen == wanted:
                last = index
                break
    if last is None:
        return None
    collected: set[str] = set()
    first = last
    for index in range(last, -1, -1):
        if spans[index][0] in wanted and spans[index][0] not in collected:
            collected.add(spans[index][0])
            first = index
            if collected == wanted:
                break
    while (
        first > 0
        and spans[first - 1][0] in _ARTICLES
        and not text[spans[first - 1][2] : spans[first][1]].strip()
    ):
        first -= 1
    return spans[first][1], spans[last][2]


def _title_joined_to_more(
    text: str, title_tokens: list[str], authors: tuple[str, ...], own_words: frozenset[str]
) -> bool:
    """Whether a separator joins the requested title to further title-like words.

    "Leviathan Wakes & Caliban's War" and "Caliban's War, Leviathan Wakes" name two books.
    The separators are ",", ";", "|", " - " and the conjunctions "&", "+", "/", "and". Not
    when the other words are only a requested author's, series words, formats, numbers,
    edition or language notes or a publisher ("Orbit - Leviathan Wakes"). The segment on
    either side is cut at a bracket or " - ", so an author prefix is never part of it. A
    before-segment that names a requested author is an author credit and is left alone.
    """
    span = _title_span(text, set(title_tokens))
    if span is None:
        return False
    start, end = span
    author_words = frozenset(t for author in authors for t in _tokens(author))

    separator = _RANK_TITLE_SEPARATOR_RE.match(text, end)
    if separator is not None:
        rest = text[separator.end() :]
        segment_end = _RANK_SEGMENT_END_RE.search(rest)
        segment = rest[: segment_end.start()] if segment_end is not None else rest
        if _title_words(segment, own_words, author_words):
            return True

    before = _RANK_TITLE_SEPARATOR_END_RE.search(text, 0, start)
    if before is not None:
        preceding = _RANK_SEGMENT_END_RE.split(text[: before.start()])[-1]
        if _title_words(preceding, own_words, author_words) and not (
            set(_tokens(preceding)) & author_words
        ):
            return True
    return False


def _names_whole_title(present: set[str], identity: RankingIdentity) -> bool:
    title_tokens = [t for t in identity.title_tokens if t]
    return bool(title_tokens) and all(token in present for token in title_tokens)


def _volume(text: str, identity: RankingIdentity) -> tuple[Volume, int | None]:
    if not identity.series_key or identity.position is None:
        return "unknown", None
    key_tokens = significant_tokens(identity.series_key)
    if _RANK_COLLECTION_RE.search(text) or _has_volume_list(text, key_tokens):
        return "unknown", None
    numbers = _explicit_volumes(text, key_tokens)
    if numbers is None or len(numbers) > 1:
        return "unknown", None

    present = set(_tokens(text))
    has_key = bool(key_tokens) and all(token in present for token in key_tokens)
    if numbers and has_key:
        (number,) = numbers
        if number == identity.position:
            return "match", None
        # A volume 0 is a prequel or an index page as often as a volume: not evidence.
        if number == 0:
            return "unknown", None
        # A natural-title book named in full: a series number that disagrees is reading
        # order against publication order, not another volume.
        if (
            not identity.title_names_volume
            and _names_whole_title(present, identity)
            and any(token not in key_tokens for token in identity.title_tokens)
        ):
            return "unknown", None
        return "other", number

    # A series book whose title names no volume ("Leviathan Wakes", The Expanse 1) is
    # also named by its own title words, as long as no explicit volume names another
    # number and no conjunction joins it to another title. Other numbers ("2nd edition",
    # "451", a year) do not veto it.
    if identity.title_names_volume or not numbers <= {identity.position}:
        return "unknown", None
    series_words = set(key_tokens)
    title_tokens = [t for t in identity.title_tokens if t]
    if (
        _names_whole_title(present, identity)
        and any(token not in series_words for token in title_tokens)
        and not _title_joined_to_more(text, title_tokens, identity.authors, frozenset(key_tokens))
    ):
        return "match", None
    return "unknown", None


def _surname_candidates(name: str) -> set[str]:
    """Words of one contributor that may be a surname: the last and the first non-initial.

    "Kugane Maruyama" and "Maruyama Kugane" both give {"kugane", "maruyama"}; initials
    ("S. A.") never count.
    """
    words = [t for t in _tokens(name) if len(t) > 1]
    return {words[0], words[-1]} if words else set()


def _author_conflicts(release_author: object, identity: RankingIdentity) -> bool:
    """True only when the release names a real author who is none of the requested ones.

    Contributors are compared one by one: a release author agrees with a requested author
    when one of its surname candidates is that author's surname (the last non-initial
    word). A shared given name alone ("James Patterson" vs "James S. A. Corey") is not
    agreement. An author field made only of the book's own words, volume markers, numbers,
    "LN" and neutral publisher or edition words (an IRC "Overlord Vol 2 - The Dark Warrior"
    line puts the series and volume where the author goes) is not an author.
    """
    if not isinstance(release_author, str):
        return False
    text = _AUTHOR_ANNOTATION_RE.sub(" ", _fold(html.unescape(release_author)))
    if " ".join(text.split()).casefold() in _PLACEHOLDER_AUTHORS:
        return False
    own_words = set(identity.title_tokens) | set(significant_tokens(identity.series_key))
    release_words = {
        t for t in _tokens(text) if len(t) > 1 and not _AUTHOR_NOISE_RE.fullmatch(t)
    } - _NEUTRAL_WORDS
    if not release_words or release_words <= own_words:
        return False
    surnames = set()
    for author in identity.authors:
        words = [t for t in _tokens(author) if len(t) > 1]
        if words:
            surnames.add(words[-1])
    if not surnames:
        return False
    candidates = set().union(*(_surname_candidates(p) for p in _AUTHOR_SPLIT_RE.split(text)))
    return not candidates & surnames


def _ranking_text(name: str) -> str:
    # Indexers send "&amp;" for "&"; a file version tag "(v2.0)" is not a volume; "_" is
    # a separator in scene names; ISBNs, dates and sizes are never volume numbers.
    text = _VERSION_TAG_RE.sub(" ", html.unescape(name))
    text = _RANK_REVISION_TAG_RE.sub(" ", text)
    text = _fold(text).replace("_", " ").casefold()
    for pattern in _RANK_NOISE_RES:
        text = pattern.sub(" ", text)
    return text


def classify_release(
    *,
    name: object,
    formats: Sequence[object],
    content_type: object,
    release_author: object,
    identity: RankingIdentity | None,
) -> ReleaseMatch:
    """Classify one release for the default "best match" sort. Pure and total.

    ``name`` is the release name as the indexer gave it; ``formats`` the formats the
    source declared (``release.format`` plus ``extra["formats"]``); ``content_type`` the
    release's content type. Declared format and content type beat words in the name.
    """
    if not isinstance(name, str) or not name.strip():
        return _UNKNOWN_MATCH
    safe_identity = _sanitize_identity(identity)
    text = _ranking_text(name)
    declared = _declared_formats(formats)
    kind = content_type.strip().casefold() if isinstance(content_type, str) else ""

    medium = _medium(text, declared, kind, set(safe_identity.title_tokens))
    compatible = medium in {"ebook", "unknown"} or (
        medium == "comic" and safe_identity.book_is_comic
    )
    volume, other_volume = _volume(text, safe_identity)
    if volume == "match" and _author_conflicts(release_author, safe_identity):
        volume = "unknown"
    return ReleaseMatch(
        volume=volume,
        other_volume=other_volume,
        medium=medium,
        compatible=compatible,
        fan_marker=_FAN_MARKER_RE.search(text) is not None,
    )
