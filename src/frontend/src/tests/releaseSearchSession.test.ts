import { readFileSync } from 'node:fs';

import { afterEach, describe, expect, it } from 'vitest';

import {
  applyReleaseResponse,
  mergeExpandedReleases,
  queryContext,
  releaseResponseAction,
  usesBookReleaseCache,
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

afterEach(() => {
  invalidateCachedReleases(...KEY);
});

describe('query context and the book cache', () => {
  it('is the applied manual query, trimmed, or empty for the automatic search', () => {
    expect(queryContext(undefined)).toBe('');
    expect(queryContext('  ')).toBe('');
    expect(queryContext(' dxd volume 5 ')).toBe('dxd volume 5');
  });

  it('uses the book cache only for the automatic search', () => {
    expect(usesBookReleaseCache('')).toBe(true);
    expect(usesBookReleaseCache('dxd volume 5')).toBe(false);
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
    const store = (context: string, data: ReleasesResponse) => {
      if (usesBookReleaseCache(context)) setCachedReleases(...KEY, data);
    };

    // 1. Open the book: an annotated response is cached under the book.
    store('', response([annotated('a', matchPayload('match'))]));
    // 2. Manual search: every tab's entry is invalidated, and the unannotated manual
    //    response is not written back under the book.
    invalidateCachedReleases(...KEY);
    store('dxd volume 5', response([plain('a'), plain('m')]));
    // 3. Reopen: no manual results come back from the cache, so the modal searches again.
    expect(getCachedReleases(...KEY)).toBeNull();
    const reopened = response([
      annotated('a', matchPayload('match')),
      annotated('b', matchPayload('other', 6)),
    ]);
    store('', reopened);
    expect(getCachedReleases(...KEY)).toBe(reopened);
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
    for (const call of [
      'queryContext(',
      'usesBookReleaseCache(requestContext)',
      'releaseResponseAction(',
      'applyReleaseResponse(',
    ]) {
      expect(hook).toContain(call);
    }
    expect(hook).not.toContain('seenIds');
    expect(hook).not.toMatch(/new Set\(existing\.releases/);
  });

  it('writes the cache only behind the cache decision', () => {
    const writes = hook.match(/setCachedReleases\(/g) ?? [];
    expect(writes).toHaveLength(1);
    expect(hook).toMatch(
      /if \(!expandSearch && usesBookReleaseCache\(requestContext\)\) \{\s*setCachedReleases\(/,
    );
  });

  it('sends the applied manual query, never the draft text', () => {
    expect(hook).toContain('queryContext(manualQueryOverride ?? appliedManualQueryRef.current)');
    expect(hook).not.toMatch(/manualQueryOverride \?\? manualQuery\)/);
  });
});
