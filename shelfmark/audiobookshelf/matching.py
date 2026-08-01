"""Strict title+author matching between Shelfmark books and library items.

Matching is exact after normalization — no scoring, no edit distance. The
asymmetry is deliberate: a missed match costs a badge, while a false "already
in library" quietly talks a user out of a request they were entitled to make.

An ASIN, where both sides happen to have one, is an *additional* exact key —
never a replacement. Regional editions, re-recordings and abridgements each get
their own ASIN, so an ASIN hit is a strong yes but an ASIN miss proves nothing;
title+author has to keep carrying the general case. Subtitles are kept for the
same reason the matching is strict: four distinct *Housemaid* titles in one real
library differ only by suffix.
"""

import re
import unicodedata

# Removed whole, not replaced with a space: "J.R.R." must normalize to "jrr",
# and "Housemaid's" to "housemaids".
_ELIDED_PUNCTUATION = re.compile(r"['‘’.]")
_BRACKETED_NOISE = re.compile(r"[(\[{][^)\]}]*[)\]}]")
_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]+")
_LEADING_ARTICLE = re.compile(r"^(the|a|an) ")

KEY_SEPARATOR = "|"

# ASINs are opaque, so shape is the only validation available. Anything that
# is not exactly ten alphanumerics — "N/A", a URL, a truncated field — must
# never become a key, because an exact match on junk is still an exact match.
_ASIN_SHAPE = re.compile(r"^[A-Z0-9]{10}$")
ASIN_KEY_PREFIX = "asin:"


def _fold(value: str | None) -> str:
    """Casefold, strip diacritics and punctuation, and collapse whitespace."""
    if not isinstance(value, str):
        return ""

    text = _BRACKETED_NOISE.sub(" ", value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = _ELIDED_PUNCTUATION.sub("", text).casefold()
    return _NON_ALPHANUMERIC.sub(" ", text).strip()


def normalize_title(title: str | None) -> str:
    """Normalize a title, dropping a leading article but never the subtitle."""
    return _LEADING_ARTICLE.sub("", _fold(title))


def normalize_author(author: str | None) -> str:
    """Normalize a single author name.

    Leading articles are not stripped here — "The Weeknd" is a name, not a
    title, and "an" or "a" can be a real name fragment.
    """
    return _fold(author)


def author_match_keys(author: str | None) -> set[str]:
    """Build every normalized spelling of one author's name.

    A single comma means an inverted name ("McFadden, Freida"), so both
    orderings are emitted. Two or more commas means a list of authors, where
    flipping would invent a person who does not exist.
    """
    normalized = normalize_author(author)
    if not normalized:
        return set()

    keys = {normalized}

    raw = author if isinstance(author, str) else ""
    if raw.count(",") == 1:
        last, _, first = raw.partition(",")
        flipped = normalize_author(f"{first.strip()} {last.strip()}")
        if flipped:
            keys.add(flipped)

    return keys


def title_match_keys(title: str | None, subtitle: str | None = None) -> set[str]:
    """Build every normalized spelling of one title.

    Audiobookshelf stores the subtitle in its own field while Shelfmark's
    metadata usually carries it joined onto the title, so both spellings are
    emitted and the two sides can still meet.
    """
    normalized = normalize_title(title)
    if not normalized:
        return set()

    keys = {normalized}

    if isinstance(subtitle, str) and subtitle.strip():
        joined = normalize_title(f"{title} {subtitle}")
        if joined:
            keys.add(joined)

    return keys


def normalize_asin(asin: object) -> str:
    """Normalize an ASIN, returning "" for anything of the wrong shape."""
    if not isinstance(asin, str):
        return ""

    candidate = asin.strip().upper()
    return candidate if _ASIN_SHAPE.match(candidate) else ""


def asin_match_key(asin: object) -> str:
    """Build the namespaced key for an ASIN, or "" if it is unusable.

    The prefix keeps ASINs clear of title keys, which always contain
    ``KEY_SEPARATOR`` — a namespace an ASIN can never enter.
    """
    normalized = normalize_asin(asin)
    return f"{ASIN_KEY_PREFIX}{normalized}" if normalized else ""


def build_match_keys(
    title: str | None,
    author: str | None,
    subtitle: str | None = None,
    asin: object = None,
) -> set[str]:
    """Build the match keys for one book.

    Title keys are every title variant × every author variant; without both
    halves none are emitted, since half a key would match every other half-key.
    A valid ASIN adds one more key on top, and is enough on its own — an ASIN
    is a complete identity where a bare title is not.
    """
    keys: set[str] = set()

    asin_key = asin_match_key(asin)
    if asin_key:
        keys.add(asin_key)

    titles = title_match_keys(title, subtitle)
    authors = author_match_keys(author)
    if titles and authors:
        keys.update(f"{t}{KEY_SEPARATOR}{a}" for t in titles for a in authors)

    return keys
