import type { Release, ReleasesResponse } from '../../types';
import { isRecord } from '../../utils/objectHelpers';

/**
 * The query context of a search: `''` for the automatic search, otherwise the applied
 * manual query (the one last submitted, never the draft in the text field).
 */
export function queryContext(appliedManualQuery: string | undefined): string {
  return appliedManualQuery?.trim() ?? '';
}

/**
 * Whether a search in this context may read and write the book's normal cache entry.
 *
 * A manual query's results are the user's own words and carry no `release_match`, so
 * they are never stored under (or served from) the book's entry: reopening the book
 * always shows a normal, annotated search.
 */
export function usesBookReleaseCache(context: string): boolean {
  return context === '';
}

type ReleaseResponseAction = 'discard' | 'merge' | 'replace';

/**
 * What to do with a search response when it arrives.
 *
 * - `discard`: a newer request for the tab was started, or the query context changed
 *   while this one was in flight.
 * - `merge`: an expanded search whose context is the one the list is showing.
 * - `replace`: anything else, including an expanded search over a list from another
 *   context (it must not mix manual and automatic rows).
 */
export function releaseResponseAction(options: {
  expandSearch: boolean;
  requestContext: string;
  currentContext: string;
  displayedContext: string | undefined;
  isLatestRequest: boolean;
}): ReleaseResponseAction {
  const { expandSearch, requestContext, currentContext, displayedContext, isLatestRequest } =
    options;
  if (!isLatestRequest || requestContext !== currentContext) return 'discard';
  if (expandSearch && displayedContext === requestContext) return 'merge';
  return 'replace';
}

/** The list to show after a `merge` or `replace` action. */
export function applyReleaseResponse(
  existing: ReleasesResponse | null | undefined,
  response: ReleasesResponse,
  action: 'merge' | 'replace',
): ReleasesResponse {
  return action === 'merge' && existing ? mergeExpandedReleases(existing, response) : response;
}

function withIncomingReleaseMatch(existing: Release, incoming: Release): Release {
  const extra: Record<string, unknown> = isRecord(existing.extra) ? { ...existing.extra } : {};
  if (isRecord(incoming.extra) && 'release_match' in incoming.extra) {
    extra.release_match = incoming.extra.release_match;
  } else {
    delete extra.release_match;
  }
  return { ...existing, extra };
}

/**
 * Merge an expanded search into the rows already shown.
 *
 * Existing rows keep their place and data, except that a row the response returns again
 * takes the response's `extra.release_match` (or loses a stale one the response no longer
 * carries). Rows the existing list does not have are appended in response order.
 */
export function mergeExpandedReleases(
  existing: ReleasesResponse,
  incoming: ReleasesResponse,
): ReleasesResponse {
  const incomingById = new Map(incoming.releases.map((release) => [release.source_id, release]));
  const existingIds = new Set(existing.releases.map((release) => release.source_id));

  const refreshed = existing.releases.map((release) => {
    const fresh = incomingById.get(release.source_id);
    return fresh ? withIncomingReleaseMatch(release, fresh) : release;
  });
  const added = incoming.releases.filter((release) => !existingIds.has(release.source_id));

  return { ...existing, releases: [...refreshed, ...added] };
}
