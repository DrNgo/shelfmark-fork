import { readFileSync } from 'node:fs';

import { afterEach, describe, expect, it } from 'vitest';

import {
  applyReleaseResponse,
  canSubmitManualSearch,
  isCurrentRequest,
  manualQueryAfterToggle,
  manualSearchSubmission,
  mergeExpandedReleases,
  queryContext,
  releaseResponseAction,
  startRequest,
  supersedeRequests,
  tabNeedsFetch,
} from '../hooks/releaseModal/releaseSearchSession.helpers';
import type { Release, ReleasesResponse } from '../types';
import {
  getCachedReleases,
  invalidateCachedReleases,
  setCachedReleases,
} from '../utils/releaseCache';

function matchPayload(volume: 'match' | 'other' | 'unknown', otherVolume: number | null = null) {
  return {
    v: 1,
    volume,
    other_volume: otherVolume,
    medium: 'ebook',
    compatible: true,
    fan_marker: false,
  };
}

function annotated(id: string, payload: Record<string, unknown>, title = id): Release {
  return {
    source: 'prowlarr',
    source_id: id,
    title,
    extra: { author: 'A', release_match: payload },
  };
}

function plain(id: string): Release {
  return { source: 'prowlarr', source_id: id, title: id, extra: { author: 'A' } };
}

function response(releases: Release[], title = 'High School DxD, Vol. 5'): ReleasesResponse {
  return {
    releases,
    book: { provider: 'hardcover', provider_id: 'dxd5', title },
    sources_searched: ['prowlarr'],
    column_config: null,
  };
}

// A release whose `extra` is not an object, as an older or broken backend could send.
function malformed(id: string, extra: unknown): Release {
  const release: Release = { source: 'prowlarr', source_id: id, title: id };
  Reflect.set(release, 'extra', extra);
  return release;
}

const KEY = ['hardcover', 'dxd5', 'prowlarr', 'ebook'] as const;
const MANUAL = 'dxd volume 5';

afterEach(() => {
  invalidateCachedReleases(...KEY, '');
  invalidateCachedReleases(...KEY, MANUAL);
});

describe('query context and the book cache', () => {
  it('is the applied manual query, trimmed, or empty for the automatic search', () => {
    expect(queryContext(undefined)).toBe('');
    expect(queryContext('  ')).toBe('');
    expect(queryContext(' dxd volume 5 ')).toBe('dxd volume 5');
  });

  it('keeps a separate cache entry for every query context', () => {
    const automatic = response([annotated('a', matchPayload('match'))]);
    const manual = response([plain('m')]);
    setCachedReleases(...KEY, '', automatic);
    setCachedReleases(...KEY, MANUAL, manual);

    expect(getCachedReleases(...KEY, '')).toBe(automatic);
    expect(getCachedReleases(...KEY, MANUAL)).toBe(manual);

    invalidateCachedReleases(...KEY, MANUAL);
    expect(getCachedReleases(...KEY, MANUAL)).toBeNull();
    expect(getCachedReleases(...KEY, '')).toBe(automatic);
  });
});

describe('releaseResponseAction', () => {
  const base = {
    expandSearch: false,
    requestContext: '',
    currentContext: '',
    displayedContext: '',
    isLatestRequest: true,
  };

  it('replaces the list with a normal search response', () => {
    expect(releaseResponseAction(base)).toBe('replace');
  });

  it('merges an expansion only into a list from the same context', () => {
    expect(releaseResponseAction({ ...base, expandSearch: true })).toBe('merge');
    expect(
      releaseResponseAction({
        ...base,
        expandSearch: true,
        requestContext: 'dxd 5',
        currentContext: 'dxd 5',
        displayedContext: 'dxd 5',
      }),
    ).toBe('merge');
  });

  it('replaces instead of mixing automatic and manual rows', () => {
    // Automatic list on screen, expansion of the applied manual query.
    expect(
      releaseResponseAction({
        ...base,
        expandSearch: true,
        requestContext: 'dxd 5',
        currentContext: 'dxd 5',
        displayedContext: '',
      }),
    ).toBe('replace');
    // Manual list on screen, expansion of the automatic search (after a reopen).
    expect(releaseResponseAction({ ...base, expandSearch: true, displayedContext: 'dxd 5' })).toBe(
      'replace',
    );
    // Nothing on screen yet.
    expect(
      releaseResponseAction({ ...base, expandSearch: true, displayedContext: undefined }),
    ).toBe('replace');
  });

  it('discards a response superseded by a newer request or another context', () => {
    expect(releaseResponseAction({ ...base, isLatestRequest: false })).toBe('discard');
    expect(releaseResponseAction({ ...base, currentContext: 'dxd 5' })).toBe('discard');
  });
});

describe('out-of-order completion', () => {
  it('shows only the newest request, whatever order the responses arrive in', () => {
    // The hook's bookkeeping for one tab: a sequence number per request, the applied
    // context, and what is on screen.
    let seq = 0;
    let applied = '';
    let shown: ReleasesResponse | undefined;
    let displayed: string | undefined;
    const start = (context: string) => ({ id: ++seq, context });
    const arrive = (request: { id: number; context: string }, data: ReleasesResponse) => {
      const action = releaseResponseAction({
        expandSearch: false,
        requestContext: request.context,
        currentContext: applied,
        displayedContext: displayed,
        isLatestRequest: request.id === seq,
      });
      if (action === 'discard') return;
      displayed = request.context;
      shown = applyReleaseResponse(shown, data, action);
    };

    const automatic = start('');
    applied = 'dxd 5';
    const manual = start('dxd 5');
    arrive(manual, response([plain('m')]));
    arrive(automatic, response([annotated('a', matchPayload('match'))]));

    expect(shown?.releases.map((r) => r.source_id)).toEqual(['m']);
    expect(displayed).toBe('dxd 5');
  });
});

describe('applyReleaseResponse', () => {
  it('replaces, merges, and merges into nothing as a replace', () => {
    const existing = response([plain('a')]);
    const incoming = response([plain('b')]);

    expect(applyReleaseResponse(existing, incoming, 'replace')).toBe(incoming);
    expect(
      applyReleaseResponse(existing, incoming, 'merge').releases.map((r) => r.source_id),
    ).toEqual(['a', 'b']);
    expect(applyReleaseResponse(undefined, incoming, 'merge')).toBe(incoming);
  });
});

describe('mergeExpandedReleases', () => {
  it('keeps existing rows in place and appends new ones', () => {
    const merged = mergeExpandedReleases(
      response([plain('a'), plain('b')]),
      response([plain('c'), plain('a')], 'ignored'),
    );

    expect(merged.releases.map((r) => r.source_id)).toEqual(['a', 'b', 'c']);
    expect(merged.book.title).toBe('High School DxD, Vol. 5');
  });

  it("refreshes a duplicate row's release match from the incoming response", () => {
    const merged = mergeExpandedReleases(
      response([annotated('a', matchPayload('unknown'), 'kept title')]),
      response([annotated('a', matchPayload('other', 25), 'new title')]),
    );

    expect(merged.releases[0].title).toBe('kept title');
    expect(merged.releases[0].extra).toEqual({
      author: 'A',
      release_match: matchPayload('other', 25),
    });
  });

  it('drops a stale release match the incoming row no longer carries', () => {
    const merged = mergeExpandedReleases(
      response([annotated('a', matchPayload('match'))]),
      response([plain('a')]),
    );

    expect(merged.releases[0].extra).toEqual({ author: 'A' });
  });

  it('leaves rows the response does not return untouched', () => {
    const kept = annotated('a', matchPayload('match'));
    const merged = mergeExpandedReleases(response([kept]), response([plain('b')]));

    expect(merged.releases[0]).toBe(kept);
  });

  it('survives malformed extras on either side', () => {
    const merged = mergeExpandedReleases(
      response([malformed('a', ['x']), malformed('b', null), malformed('c', undefined)]),
      response([
        annotated('a', matchPayload('match')),
        malformed('b', ['release_match']),
        malformed('c', 'release_match'),
      ]),
    );

    expect(merged.releases.map((r) => r.extra)).toEqual([
      { release_match: matchPayload('match') },
      {},
      {},
    ]);
  });
});

describe('manual search, reopen, expand', () => {
  it('shows no stale or unannotated mix', () => {
    // 1. Open the book: an annotated response is cached under the automatic context.
    const reopened = response([
      annotated('a', matchPayload('match')),
      annotated('b', matchPayload('other', 6)),
    ]);
    setCachedReleases(...KEY, '', reopened);
    // 2. Manual search: only the manual context's entry is refreshed, and the unannotated
    //    manual response is cached under its own context, never under the automatic one.
    invalidateCachedReleases(...KEY, MANUAL);
    const manual = response([plain('a'), plain('m')]);
    setCachedReleases(...KEY, MANUAL, manual);
    // 3. Reopen: the automatic search comes back annotated from the cache, and a modal
    //    that opens on the manual query (defaultShowManualQuery) hits that query's entry.
    expect(getCachedReleases(...KEY, '')).toBe(reopened);
    expect(getCachedReleases(...KEY, MANUAL)).toBe(manual);
    // 4. Expand: duplicates take the incoming match data; new rows arrive annotated.
    const action = releaseResponseAction({
      expandSearch: true,
      requestContext: '',
      currentContext: '',
      displayedContext: '',
      isLatestRequest: true,
    });
    const expanded = applyReleaseResponse(
      reopened,
      response([annotated('b', matchPayload('other', 7)), annotated('c', matchPayload('unknown'))]),
      action === 'discard' ? 'replace' : action,
    );

    expect(action).toBe('merge');
    expect(expanded.releases.map((r) => [r.source_id, r.extra?.release_match])).toEqual([
      ['a', matchPayload('match')],
      ['b', matchPayload('other', 7)],
      ['c', matchPayload('unknown')],
    ]);
  });
});

describe('useReleaseSearchSession uses the helpers', () => {
  const hook = readFileSync(
    new URL('../hooks/releaseModal/useReleaseSearchSession.ts', import.meta.url),
    'utf8',
  );

  it('calls the extracted decisions instead of reimplementing them', () => {
    for (const call of ['queryContext(', 'releaseResponseAction(', 'applyReleaseResponse(']) {
      expect(hook).toContain(call);
    }
    expect(hook).not.toContain('seenIds');
    expect(hook).not.toMatch(/new Set\(existing\.releases/);
  });

  it('writes the cache under the request context, never for an expansion', () => {
    const writes = hook.match(/setCachedReleases\(/g) ?? [];
    expect(writes).toHaveLength(1);
    expect(hook).toMatch(
      /if \(!expandSearch\) \{\s*setCachedReleases\(provider, bookId, tabName, contentType, requestContext, response\)/,
    );
  });

  it('sends the applied manual query, never the draft text', () => {
    expect(hook).toContain('queryContext(manualQueryOverride ?? appliedManualQueryRef.current)');
    expect(hook).not.toMatch(/manualQueryOverride \?\? manualQuery\)/);
  });

  it('reads the cache under the request context', () => {
    expect(hook).toMatch(
      /if \(!expandSearch\) \{\s*const cached = getCachedReleases\(provider, bookId, tabName, contentType, requestContext\)/,
    );
  });

  it('refreshes only the next context on a switch, and the current one on a filter change', () => {
    expect(hook).toContain(
      'invalidateCachedReleases(book.provider, book.provider_id, tab.name, contentType, nextQuery)',
    );
    expect(hook).toMatch(
      /invalidateCachedReleases\(\s*book\.provider,\s*book\.provider_id,\s*activeTab,\s*contentType,\s*queryContext\(appliedManualQueryRef\.current\),?\s*\)/,
    );
    expect(hook.match(/invalidateCachedReleases\(/g) ?? []).toHaveLength(2);
  });

  it('clears loading and errors only for the newest request', () => {
    expect(hook).toMatch(/\} catch \(err\) \{\s*if \(isLatestRequest\(\)\) \{/);
    expect(hook).toMatch(/\} finally \{\s*if \(isLatestRequest\(\)\) \{/);
  });

  it('switches the query context through the extracted bookkeeping', () => {
    for (const call of [
      'startRequest(requestSeqRef.current, tabName)',
      'isCurrentRequest(requestSeqRef.current, tabName, requestSeq)',
      'manualSearchSubmission(manualQuery, appliedManualQueryRef.current)',
      'manualQueryAfterToggle(next, appliedManualQueryRef.current)',
      'canSubmitManualSearch(manualQuery, appliedManualQuery)',
    ]) {
      expect(hook).toContain(call);
    }
    // The book reset and every context switch supersede all tabs' requests and clear
    // every tab's loading flag.
    expect(hook.match(/supersedeRequests\(requestSeqRef\.current\)/g) ?? []).toHaveLength(2);
    expect(hook.match(/setLoadingBySource\(\{\}\)/g) ?? []).toHaveLength(2);
    expect(hook.match(/tabNeedsFetch\(/g) ?? []).toHaveLength(2);
  });
});

describe('ReleaseModal reads the applied manual query', () => {
  const modal = readFileSync(new URL('../components/ReleaseModal.tsx', import.meta.url), 'utf8');

  it('highlights the toggle and enables Search from the hook, not the draft text', () => {
    expect(modal).toMatch(/manualQueryApplied \? 'text-emerald-600/);
    expect(modal).not.toMatch(/manualQuery\.trim\(\) \? 'text-emerald/);
    expect(modal).toContain('disabled={currentTabLoading || !canRunManualSearch}');
    expect(modal).not.toContain('!manualQuery.trim()');
  });
});

describe('request bookkeeping', () => {
  it('starts, checks and supersedes per-tab requests', () => {
    const seqs: Record<string, number> = {};
    const a1 = startRequest(seqs, 'a');
    const b1 = startRequest(seqs, 'b');
    expect(isCurrentRequest(seqs, 'a', a1)).toBe(true);
    const a2 = startRequest(seqs, 'a');
    expect(isCurrentRequest(seqs, 'a', a1)).toBe(false);
    expect(isCurrentRequest(seqs, 'a', a2)).toBe(true);
    supersedeRequests(seqs);
    expect(isCurrentRequest(seqs, 'a', a2)).toBe(false);
    expect(isCurrentRequest(seqs, 'b', b1)).toBe(false);
  });

  it('fetches a tab only when it has no list, request or error', () => {
    const state = { releasesBySource: {}, loadingBySource: {}, errorBySource: {} };
    expect(tabNeedsFetch(state, 'b')).toBe(true);
    expect(tabNeedsFetch({ ...state, loadingBySource: { b: true } }, 'b')).toBe(false);
    expect(tabNeedsFetch({ ...state, errorBySource: { b: 'x' } }, 'b')).toBe(false);
    expect(tabNeedsFetch({ ...state, releasesBySource: { b: null } }, 'b')).toBe(false);
  });

  it('never leaves a tab loading with no request after a context switch', () => {
    // The hook's bookkeeping for two tabs, driven through the same helpers it uses.
    const seqs: Record<string, number> = {};
    let applied = '';
    let loading: Record<string, boolean> = {};
    const releases: Record<string, ReleasesResponse | null> = {};
    const displayed: Record<string, string> = {};
    const fetchTab = (tab: string) => {
      const request = { tab, id: startRequest(seqs, tab), context: queryContext(applied) };
      loading = { ...loading, [tab]: true };
      return request;
    };
    const arrive = (
      request: { tab: string; id: number; context: string },
      data: ReleasesResponse,
    ) => {
      const latest = isCurrentRequest(seqs, request.tab, request.id);
      const action = releaseResponseAction({
        expandSearch: false,
        requestContext: request.context,
        currentContext: queryContext(applied),
        displayedContext: displayed[request.tab],
        isLatestRequest: latest,
      });
      if (action !== 'discard') {
        displayed[request.tab] = request.context;
        releases[request.tab] = applyReleaseResponse(releases[request.tab], data, action);
      }
      if (latest) loading = { ...loading, [request.tab]: false };
    };
    const switchContext = (next: string, activeTab: string) => {
      applied = next;
      supersedeRequests(seqs);
      loading = {};
      for (const tab of Object.keys(releases)) delete releases[tab];
      return fetchTab(activeTab);
    };
    const activate = (tab: string) =>
      tabNeedsFetch(
        { releasesBySource: releases, loadingBySource: loading, errorBySource: {} },
        tab,
      )
        ? fetchTab(tab)
        : null;

    // Tab B's automatic request is in flight; the user submits a manual query on A.
    const staleB = fetchTab('b');
    const manualA = switchContext('dxd 5', 'a');
    arrive(manualA, response([plain('m')]));
    // Clicking B starts a request in the manual context instead of waiting on the stale one.
    const freshB = activate('b');
    if (!freshB) throw new Error('activating B started no request');
    expect(freshB.context).toBe('dxd 5');
    // B's stale automatic response arrives: dropped, and B stays loading for its new request.
    arrive(staleB, response([annotated('a', matchPayload('match'))]));
    expect(releases.b).toBeUndefined();
    expect(loading.b).toBe(true);
    arrive(freshB, response([plain('n')]));
    expect(releases.b?.releases.map((r) => r.source_id)).toEqual(['n']);
    expect(loading.b).toBe(false);
  });
});

describe('getting back to the automatic search', () => {
  it('submits a trimmed manual query, or the automatic search from an empty field', () => {
    expect(manualSearchSubmission(' dxd 5 ', '')).toBe('dxd 5');
    expect(manualSearchSubmission('dxd 6', 'dxd 5')).toBe('dxd 6');
    expect(manualSearchSubmission('  ', 'dxd 5')).toBe('');
    expect(manualSearchSubmission('', '')).toBeNull();
  });

  it('enables Search for a query, or for an empty field while a manual query is applied', () => {
    expect(canSubmitManualSearch('dxd', '')).toBe(true);
    expect(canSubmitManualSearch(' ', 'dxd 5')).toBe(true);
    expect(canSubmitManualSearch(' ', '')).toBe(false);
  });

  it('returns to the automatic search when the panel closes over an applied query', () => {
    expect(manualQueryAfterToggle(false, 'dxd 5')).toBe('');
    expect(manualQueryAfterToggle(false, '')).toBeNull();
    expect(manualQueryAfterToggle(true, 'dxd 5')).toBeNull();
    expect(manualQueryAfterToggle(true, '')).toBeNull();
  });
});
