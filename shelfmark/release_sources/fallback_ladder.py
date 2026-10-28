"""Fork: the fallback query ladder for Prowlarr and Newznab.

Each source runs its own queries exactly as upstream does, then hands over to one
function here, which tries the release-shaped queries from
``shelfmark.core.search_queries`` ("<Series> Vol. N", "<Series> vNN", ...) only while
nothing found so far is the requested book. Kept out of the sources so upstream syncs
touch as little fork code as possible; this module imports nothing from either source
(they import it), so everything source-specific comes in as a callable.
"""

from __future__ import annotations

import time
import traceback
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from shelfmark.core import search_deadline
from shelfmark.core.logger import setup_logger
from shelfmark.core.search_plan import _pick_search_title
from shelfmark.core.search_queries import (
    SearchIdentity,
    any_identity_hit,
    build_fallback_queries,
    build_search_identity,
    exact_query_key,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Container

    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

# Each Prowlarr indexer / Newznab connection gets at most this many fallback requests
# per search, auto-expanded retries included.
FALLBACK_REQUESTS_PER_INDEXER = 4
FALLBACK_REQUESTS_PER_CONNECTION = 4

# The Newznab client timeout assumed when a client reports no usable one.
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class Fallbacks:
    """The ladder planned for one search; ``queries`` is empty when there is none."""

    queries: list[str]
    identity: SearchIdentity | None

    def hit(self, rows: list[dict], content_type: str) -> bool:
        """Whether any of ``rows`` is the requested book."""
        return any_identity_hit(
            (r.get("title") for r in rows), self.identity, content_type=content_type
        )


def plan_fallbacks(
    book: BookMetadata, plan: ReleaseSearchPlan, content_type: str | None
) -> Fallbacks:
    """The ladder for an ebook metadata search, after (and never repeating) its titles.

    Audiobook searches keep today's queries; a manual query or a manual-provider book is
    whatever the user typed, a "shelfmark" book is a retry re-finding one exact release
    (prowlarr/handler.py), and a book with no title searches by ISBN only. A ladder
    query that is the same request as a planned title (case and whitespace aside -
    planned titles are sent uncleaned) is dropped.
    """
    none = Fallbacks(queries=[], identity=None)
    if not isinstance(content_type, str) or content_type.strip().lower() != "ebook":
        return none
    if book.provider in {"manual", "shelfmark"} or plan.manual_query:
        return none
    base_title = _pick_search_title(book)
    if not base_title:
        return none

    seen = {exact_query_key(v.title) for v in plan.title_variants if v.title}
    queries: list[str] = []
    for query in build_fallback_queries(
        title=book.title,
        current_query=base_title,
        series_name=book.series_name,
        series_position=book.series_position,
    ):
        key = exact_query_key(query)
        if key not in seen:
            seen.add(key)
            queries.append(query)
    if not queries:
        return none
    identity = build_search_identity(
        title=book.title,
        current_query=base_title,
        series_name=book.series_name,
        series_position=book.series_position,
    )
    return Fallbacks(queries=queries, identity=identity)


def remaining_seconds(source_deadline: float) -> float:
    """Seconds left before ``source_deadline`` (a ``time.monotonic()`` value) or the
    endpoint's budget, whichever comes first.

    A request that cannot finish inside the sooner of the two is not worth starting.
    """
    remaining = source_deadline - time.monotonic()
    deadline = search_deadline.current()
    if deadline is not None:
        remaining = min(remaining, 0.0 if deadline.expired else deadline.remaining)
    return remaining


def _log_summary(stop: str, message: str, *args: object) -> None:
    # Routine outcomes at INFO; no ladder at all is DEBUG noise, and a ladder skipped
    # for lack of time is worth a warning.
    if stop == "not planned":
        logger.debug(message, *args)
    elif stop == "deadline":
        logger.warning(message, *args)
    else:
        logger.info(message, *args)


@dataclass
class ProwlarrLadderResult:
    # Set when the ladder stopped for lack of time: an empty search was not complete.
    incomplete_message: str | None = None
    # A fallback was retried without categories.
    expanded: bool = False


class _ProwlarrSend(Protocol):
    def __call__(
        self, indexer_id: int, query: str, cats: list[int] | None, *, rung: str, expanded: bool
    ) -> list[dict] | Exception: ...


def run_prowlarr_fallbacks(
    fallbacks: Fallbacks,
    *,
    content_type: str,
    found: list[dict],
    timed_out: bool,
    categories: list[int] | None,
    auto_expand: object,
    candidates: Callable[[list[int] | None], list[int]],
    excluded: Callable[[], Container[int]],
    names: dict[int, str],
    send: _ProwlarrSend,
    add: Callable[[list[dict]], list[dict]],
    deadline: float,
    request_seconds: float,
    search_budget: float,
) -> ProwlarrLadderResult:
    """Run the ladder over Prowlarr's indexers after its mandatory queries.

    Each rung goes to every indexer still eligible: one that failed during this search
    (``excluded``) or has used its cap sits out. A request is only started while the
    budget left covers a whole one (``request_seconds``); past that the ladder stops
    without raising, so what was already found is kept. Failed fallback requests are
    logged, never counted as a failed search. ``send`` returns the exception for a
    failed request; ``add`` keeps new rows and returns those it had not seen.
    """
    result = ProwlarrLadderResult()
    requests: dict[int, int] = {}
    failures: list[str] = []
    budget_left: float | None = None
    rungs_run = 0

    def targets(cats: list[int] | None) -> list[int]:
        skip = excluded()
        return [
            indexer_id
            for indexer_id in candidates(cats)
            if indexer_id not in skip
            and requests.get(indexer_id, 0) < FALLBACK_REQUESTS_PER_INDEXER
        ]

    def send_pass(
        query: str, cats: list[int] | None, ids: list[int], rung: str, *, expanded: bool
    ) -> tuple[list[dict], int, bool]:
        """Send to ``ids``; return (rows, requests sent, stopped for lack of time)."""
        nonlocal budget_left
        rows: list[dict] = []
        sent = 0
        for indexer_id in ids:
            remaining = remaining_seconds(deadline)
            if remaining < request_seconds:
                budget_left = max(remaining, 0.0)
                return rows, sent, True
            requests[indexer_id] = requests.get(indexer_id, 0) + 1
            sent += 1
            raw = send(indexer_id, query, cats, rung=rung, expanded=expanded)
            if isinstance(raw, Exception):
                failure = "rate-limited" if getattr(raw, "rate_limited", False) else "failed"
                failures.append(f"{names.get(indexer_id, str(indexer_id))}: {failure}")
                continue
            rows.extend(raw)
        return rows, sent, False

    if not fallbacks.queries:
        stop = "not planned"
    elif timed_out:
        stop = "deadline"
    elif fallbacks.hit(found, content_type):
        stop = "hit"
    else:
        stop = "exhausted"
        for idx, query in enumerate(fallbacks.queries, start=1):
            rung = f"fallback {idx}"
            cats = categories
            ids = targets(cats)
            if not ids and cats:
                # No category-compatible indexer can take this rung, but others may:
                # send it to them unrestricted rather than stopping the ladder.
                cats = None
                ids = targets(None)
            if not ids:
                # Every indexer has failed during this search or used its cap.
                stop = "cap"
                break
            # Not an auto-expand retry: the rung's only send to these indexers.
            rows, sent, out_of_time = send_pass(query, cats, ids, rung, expanded=False)
            if not sent:
                stop = "deadline"
                break
            rungs_run += 1

            # Expansion goes only to indexers still eligible, so a failed one is never
            # asked twice; a healthy one that answered empty still is.
            if not rows and not out_of_time and cats and auto_expand:
                expand_ids = targets(None)
                if expand_ids:
                    rows, sent, out_of_time = send_pass(
                        query, None, expand_ids, rung, expanded=True
                    )
                    if sent:
                        result.expanded = True

            if fallbacks.hit(add(rows), content_type):
                stop = "hit"
                break
            if out_of_time:
                stop = "deadline"
                break

    _log_summary(
        stop,
        "Prowlarr fallbacks: ran=%s stop=%s rungs=%s/%s requests=%s",
        "yes" if rungs_run else "no",
        stop,
        rungs_run,
        len(fallbacks.queries),
        sum(requests.values()),
    )
    if failures:
        logger.warning(
            "Prowlarr: %s fallback request(s) failed, not counted as a failed search (%s)",
            len(failures),
            "; ".join(failures),
        )
    if stop == "deadline":
        if budget_left is not None:
            result.incomplete_message = (
                "search incomplete: not enough time left to try fallback queries "
                f"(needs up to {int(request_seconds)}s, {int(budget_left)}s left)"
            )
        else:
            result.incomplete_message = (
                "search incomplete: ran out of time before every fallback query ran "
                f"({int(search_budget)}s budget)"
            )
    return result


def _newznab_request_timeout(client: object) -> float:
    """The longest one request on ``client`` may take: connect plus read.

    ``NewznabClient`` passes its single ``timeout`` to requests as a scalar, which
    requests applies to the connect and to the read separately - so a request can take
    up to twice that value.
    """
    timeout = getattr(client, "timeout", None)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        timeout = _DEFAULT_REQUEST_TIMEOUT_SECONDS
    return 2 * float(timeout)


@dataclass
class _LadderProgress:
    """What one connection's ladder has sent so far (survives an exception)."""

    rungs: int = 0
    requests: int = 0


class _NewznabSend(Protocol):
    def __call__(
        self,
        connection: Any,
        query: str,
        categories: list[int] | None,
        *,
        rung: str,
        errors: list[str],
        expanded: bool = False,
    ) -> list[dict] | None: ...


def run_newznab_fallbacks(
    fallbacks: Fallbacks,
    *,
    content_type: str,
    answered: list[tuple[Any, list[dict], bool]],
    categories: list[int] | None,
    auto_expand: object,
    send: _NewznabSend,
    add: Callable[[Any, list[dict]], list[dict]],
    redact: Callable[[Any, object], str],
    deadline: float,
) -> str | None:
    """Run the ladder per connection after every connection's mandatory queries.

    ``answered`` holds (connection, rows it showed, whether it failed); a failed
    connection gets no fallbacks. ``send`` is one logged request (None when it failed),
    ``add`` keeps a response and returns the rows it newly showed, ``redact`` takes API
    keys out of text. Returns why the search is incomplete when a ladder could not
    start a request for lack of time, else None.
    """
    budget_skip: tuple[float, float] | None = None
    errors: list[str] = []

    def run(connection: Any, found: list[dict], progress: _LadderProgress) -> str:
        nonlocal budget_skip
        if fallbacks.hit(found, content_type):
            return "hit"
        timeout = _newznab_request_timeout(connection.client)

        def request(query: str, cats: list[int] | None, idx: int) -> list[dict] | str:
            nonlocal budget_skip
            if progress.requests >= FALLBACK_REQUESTS_PER_CONNECTION:
                return "cap"
            remaining = remaining_seconds(deadline)
            if remaining < timeout:
                budget_skip = (timeout, max(remaining, 0.0))
                return "deadline"
            progress.requests += 1
            progress.rungs = max(progress.rungs, idx)
            raw = send(
                connection,
                query,
                cats,
                rung=f"fallback {idx}",
                errors=errors,
                expanded=cats is None and bool(categories),
            )
            return "failed" if raw is None else raw

        for idx, query in enumerate(fallbacks.queries, start=1):
            raw = request(query, categories, idx)
            if isinstance(raw, str):
                return raw
            if not raw and categories and auto_expand:
                raw = request(query, None, idx)
                if isinstance(raw, str):
                    return raw
            if fallbacks.hit(add(connection, raw), content_type):
                return "hit"
        return "exhausted"

    for connection, found, failed in answered:
        progress = _LadderProgress()
        if not fallbacks.queries:
            stop = "not planned"
        elif failed:
            stop = "failed"
        else:
            try:
                stop = run(connection, found, progress)
            except Exception as e:  # noqa: BLE001 - a fallback must not end the search
                # Extra to a search that already completed: ends this ladder only.
                # Not logger.exception: the raw traceback text may carry the API key.
                logger.error(  # noqa: TRY400
                    "Newznab fallback search failed for %s:\n%s",
                    connection.name,
                    redact(connection, "".join(traceback.format_exception(e)).rstrip()),
                )
                errors.append(f"{connection.name}: {redact(connection, e)}")
                stop = "failed"
        _log_summary(
            stop,
            "Newznab [%s] fallbacks: ran=%s stop=%s rungs=%s/%s requests=%s",
            connection.name,
            "yes" if progress.rungs else "no",
            stop,
            progress.rungs,
            len(fallbacks.queries),
            progress.requests,
        )

    if errors:
        logger.warning(
            "Newznab: %d fallback request(s) failed, not counted as a failed search (%s)",
            len(errors),
            "; ".join(errors[-3:]),
        )
    if budget_skip is None:
        return None
    needed, left = budget_skip
    return (
        "Newznab search incomplete: not enough time left to try fallback "
        f"queries (needs up to {int(needed)}s, {int(left)}s left)"
    )
