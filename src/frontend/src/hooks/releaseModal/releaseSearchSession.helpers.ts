import type { Release, ReleasesResponse } from '../../types';
import { isRecord } from '../../utils/objectHelpers';

/**
 * The query context of a search: `''` for the automatic search, otherwise the applied
 * manual query (the one last submitted, never the draft in the text field).
 */
export function queryContext(appliedManualQuery: string | undefined): string {
  return appliedManualQuery?.trim() ?? '';
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

/** Start a request for a tab: its new sequence number, which supersedes older ones. */
export function startRequest(requestSeq: Record<string, number>, tabName: string): number {
  const next = (requestSeq[tabName] ?? 0) + 1;
  requestSeq[tabName] = next;
  return next;
}

/** Whether `seq` is still the newest request for the tab. */
export function isCurrentRequest(
  requestSeq: Record<string, number>,
  tabName: string,
  seq: number,
): boolean {
  return requestSeq[tabName] === seq;
}

/**
 * Supersede every tab's in-flight request (a new book, content type or query context):
 * its response is discarded and it no longer owns the tab's loading flag.
 */
export function supersedeRequests(requestSeq: Record<string, number>): void {
  for (const tab of Object.keys(requestSeq)) {
    requestSeq[tab] += 1;
  }
}

/** Whether activating a tab must start a search: it has no list, request or error. */
export function tabNeedsFetch(
  state: {
    releasesBySource: Record<string, ReleasesResponse | null>;
    loadingBySource: Record<string, boolean>;
    errorBySource: Record<string, string | null>;
  },
  tabName: string,
): boolean {
  return (
    state.releasesBySource[tabName] === undefined &&
    !state.loadingBySource[tabName] &&
    !state.errorBySource[tabName]
  );
}

/**
 * The query to apply when the manual form is submitted: the trimmed draft, `''` (back to
 * the automatic search) for an empty draft while a manual query is applied, or `null`
 * when there is nothing to do.
 */
export function manualSearchSubmission(draft: string, appliedManualQuery: string): string | null {
  const query = draft.trim();
  if (query) return query;
  return appliedManualQuery ? '' : null;
}

/** Whether the manual form's Search button can be used. */
export function canSubmitManualSearch(draft: string, appliedManualQuery: string): boolean {
  return manualSearchSubmission(draft, appliedManualQuery) !== null;
}

/**
 * The query to apply after the manual panel is toggled: closing it over an applied manual
 * query returns to the automatic search (`''`); anything else changes nothing (`null`).
 */
export function manualQueryAfterToggle(
  nextShown: boolean,
  appliedManualQuery: string,
): string | null {
  return !nextShown && appliedManualQuery ? '' : null;
}
