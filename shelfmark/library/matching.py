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
    flipping would invent a person who does not exist. A comma-less two-token
    name is also emitted reversed, since surname-first order is common and
    carries no comma to announce it.
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
    elif "," not in raw:
        # Surname-first without a comma ("Maruyama Kugane") is common for Japanese
        # names. Only a two-token name is reversed: longer names have no single
        # obvious flip, and the reversed key still only ever pairs with the same
        # normalized title, so it cannot reach a different book by itself.
        parts = normalized.split()
        if len(parts) == 2:
            keys.add(f"{parts[1]} {parts[0]}")

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


# ISBNs are edition-specific — paperback, hardcover and ebook each get their
# own — so a hit is a strong yes while a miss proves nothing. Title+author
# keeps carrying the general case, exactly as it does around ASIN.
_ISBN_SEPARATORS = re.compile(r"[-\s]")
_ISBN10_SHAPE = re.compile(r"^[0-9]{9}[0-9X]$")
_ISBN13_SHAPE = re.compile(r"^[0-9]{13}$")
ISBN_KEY_PREFIX = "isbn:"
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


def isbn_match_key(value: object) -> str:
    """Build the namespaced key for an ISBN, or "" if it is unusable."""
    normalized = normalize_isbn(value)
    return f"{ISBN_KEY_PREFIX}{normalized}" if normalized else ""


HARDCOVER_KEY_PREFIX = "hardcover:"


def hardcover_match_key(value: object) -> str:
    """Build the namespaced key for a Hardcover book id, or "" if it is unusable.

    A Hardcover book id names a *work*: every edition shares it, and different
    adaptations (light novel vs manga) get different ids. Only a clean ASCII
    digit string is accepted — an exact match on junk is still an exact match.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    text = str(value).strip()
    if not text or not text.isascii() or not text.isdigit():
        return ""
    return f"{HARDCOVER_KEY_PREFIX}{text}"


# The bracketed segments `_BRACKETED_NOISE` deletes, recovered for ranking.
# Deleting them is right for keying — an edition marker must never break a
# match — but once two items key together the marker is the only thing left
# saying they are different recordings.
_EDITION_QUALIFIER = re.compile(r"[(\[{]([^)\]}]*)[)\]}]")

# Completeness and medium, not edition. "(Unabridged)" is near-universal shelf
# noise, and "(Light Novel)" / "(Novel)" / "(LN)" name the medium — one side
# carries the label and the other does not, for the very same release.
# "(Manga)" and "(Graphic Novel)" stay: those are different adaptations.
_NON_EDITION_QUALIFIERS = frozenset({"unabridged", "abridged", "light novel", "novel", "ln"})


def edition_qualifiers(title: str | None) -> frozenset[str]:
    """Return the normalized edition markers bracketed in a title.

    Only the title is read, never the subtitle. Library items carry a subtitle
    where a search result does not, so comparing subtitles would find a
    difference on every match and demote holdings the user really has.
    """
    if not isinstance(title, str):
        return frozenset()

    found = {_fold(segment) for segment in _EDITION_QUALIFIER.findall(title)}
    return frozenset(found - _NON_EDITION_QUALIFIERS - {""})


def build_match_keys(
    title: str | None,
    author: str | None,
    subtitle: str | None = None,
    asin: object = None,
    isbn: object = None,
    hardcover_id: object = None,
) -> set[str]:
    """Build the match keys for one book.

    Title keys are every title variant × every author variant; without both
    halves none are emitted, since half a key would match every other half-key.
    A valid ASIN, ISBN or Hardcover id adds one more key on top, and any of them
    is enough on its own — each is a complete identity where a bare title is not.
    """
    keys: set[str] = set()

    for key in (asin_match_key(asin), isbn_match_key(isbn), hardcover_match_key(hardcover_id)):
        if key:
            keys.add(key)

    titles = title_match_keys(title, subtitle)
    authors = author_match_keys(author)
    if titles and authors:
        keys.update(f"{t}{KEY_SEPARATOR}{a}" for t in titles for a in authors)

    return keys
