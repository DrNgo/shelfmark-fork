"""Strict title+author matching between Shelfmark books and library items.

Matching is exact after normalization — no scoring, no edit distance. The
asymmetry is deliberate: a missed match costs a badge, while a false "already
in library" quietly talks a user out of a request they were entitled to make.

ASIN would be the exact matcher, but no Shelfmark metadata provider surfaces
one, so normalized title+author is the only matcher available rather than a
fallback. Subtitles are kept for the same reason the matching is strict: four
distinct *Housemaid* titles in one real library differ only by suffix.
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


def build_match_keys(
    title: str | None,
    author: str | None,
    subtitle: str | None = None,
) -> set[str]:
    """Build the match keys for one book: every title variant × every author variant.

    Returns an empty set unless both a title and an author survive
    normalization — half a key would match every other half-key.
    """
    titles = title_match_keys(title, subtitle)
    authors = author_match_keys(author)
    if not titles or not authors:
        return set()

    return {f"{t}{KEY_SEPARATOR}{a}" for t in titles for a in authors}
