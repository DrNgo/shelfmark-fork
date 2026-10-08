import { describe, expect, it } from 'vitest';

import type { Release } from '../types';
import { sortReleasesByBookMatch } from '../utils/releaseScoring';

type Volume = 'match' | 'other' | 'unknown';

function matchPayload(
  volume: Volume,
  overrides: Record<string, unknown> = {},
): Record<string, unknown> {
  return {
    v: 1,
    volume,
    other_volume: volume === 'other' ? 25 : null,
    medium: 'ebook',
    compatible: true,
    fan_marker: false,
    ...overrides,
  };
}

function release(
  id: string,
  title: string,
  releaseMatch?: Record<string, unknown>,
  extra: Record<string, unknown> = {},
): Release {
  return {
    source: 'prowlarr',
    source_id: id,
    title,
    extra: releaseMatch ? { ...extra, release_match: releaseMatch } : extra,
  };
}

const ids = (releases: Release[]): string[] => releases.map((r) => r.source_id);

const CANDIDATES = ['high school dxd vol 5'];

describe('sortReleasesByBookMatch tiers', () => {
  it('lets the tier beat the title score', () => {
    const releases = [
      release('exact-other', 'High School DxD Vol 5', matchPayload('other')),
      release('comic', 'High School DxD Vol 5 Manga', matchPayload('match', { compatible: false })),
      release('exact-unknown', 'High School DxD Vol 5', matchPayload('unknown')),
      release('no-data', 'High School DxD Vol 5 epub'),
      release('weak-match', 'DxD v05 Hellcat', matchPayload('match')),
    ];

    expect(ids(sortReleasesByBookMatch(releases, CANDIDATES, []))).toEqual([
      'weak-match',
      'exact-unknown',
      'no-data',
      'exact-other',
      'comic',
    ]);
  });

  it('puts an incompatible unknown volume in the bottom tier', () => {
    const releases = [
      release(
        'audio',
        'High School DxD Vol 5',
        matchPayload('unknown', { medium: 'audio', compatible: false }),
      ),
      release('plain', 'Unrelated'),
    ];

    expect(ids(sortReleasesByBookMatch(releases, CANDIDATES, []))).toEqual(['plain', 'audio']);
  });

  it("keeps today's order inside a tier", () => {
    const plain = [
      release('prefix', 'High School DxD Vol 5 Hellcat'),
      release('unrelated', 'Something Else Entirely'),
      release('exact', 'High School DxD Vol 5'),
      release('author', 'High School DxD Vol 5 Hellcat', undefined, { author: 'Ichiei Ishibumi' }),
    ];
    const tiered = plain.map((r) => ({
      ...r,
      extra: { ...r.extra, release_match: matchPayload('unknown') },
    }));

    const expected = ['exact', 'author', 'prefix', 'unrelated'];
    expect(ids(sortReleasesByBookMatch(plain, CANDIDATES, ['ichiei ishibumi']))).toEqual(expected);
    expect(ids(sortReleasesByBookMatch(tiered, CANDIDATES, ['ichiei ishibumi']))).toEqual(expected);
  });

  it("keeps today's order when no release has match data", () => {
    const releases = [
      release('b', 'Other Book'),
      release('a', 'High School DxD Vol 5'),
      release('c', 'Other Book'),
    ];

    expect(ids(sortReleasesByBookMatch(releases, CANDIDATES, []))).toEqual(['a', 'b', 'c']);
  });

  it('ignores a malformed payload', () => {
    const releases = [
      release('bad', 'Unrelated', { ...matchPayload('match'), v: 2 }),
      release('good', 'High School DxD Vol 5'),
    ];

    expect(ids(sortReleasesByBookMatch(releases, CANDIDATES, []))).toEqual(['good', 'bad']);
  });

  it('still orders by tier without title candidates', () => {
    const releases = [
      release('unknown', 'A', matchPayload('unknown')),
      release('other', 'B', matchPayload('other')),
      release('match', 'C', matchPayload('match')),
      release('none', 'D'),
      release('match-2', 'E', matchPayload('match')),
    ];

    expect(ids(sortReleasesByBookMatch(releases, [], []))).toEqual([
      'match',
      'match-2',
      'unknown',
      'none',
      'other',
    ]);
  });

  it('keeps input order without title candidates or match data', () => {
    const releases = [release('z', 'Z'), release('a', 'High School DxD Vol 5'), release('m', 'M')];

    expect(ids(sortReleasesByBookMatch(releases, [], []))).toEqual(['z', 'a', 'm']);
  });

  it('scores 2000 releases once each, outside the comparator', () => {
    const reads = { match: 0, title: 0, author: 0 };
    const volumes: Volume[] = ['match', 'other', 'unknown'];
    const releases: Release[] = Array.from({ length: 2000 }, (_, index) => {
      const payload = matchPayload(volumes[index % 3]);
      const extra: Record<string, unknown> = {};
      Object.defineProperty(extra, 'release_match', {
        enumerable: true,
        get: () => {
          reads.match += 1;
          return payload;
        },
      });
      Object.defineProperty(extra, 'author', {
        enumerable: true,
        get: () => {
          reads.author += 1;
          return 'Ichiei Ishibumi';
        },
      });
      const item: Release = { source: 'prowlarr', source_id: `r-${index}`, title: '', extra };
      Object.defineProperty(item, 'title', {
        enumerable: true,
        get: () => {
          reads.title += 1;
          return `High School DxD Vol ${index % 30}`;
        },
      });
      return item;
    });

    // One title candidate and one author candidate: each release's title and author are
    // read exactly once by the scoring, however many comparisons the sort makes.
    const sorted = sortReleasesByBookMatch(releases, CANDIDATES, ['ichiei ishibumi']);

    expect(reads).toEqual({ match: 2000, title: 2000, author: 2000 });
    expect(sorted).toHaveLength(2000);
    expect(sorted.slice(0, 667).every((r) => Number(r.source_id.slice(2)) % 3 === 0)).toBe(true);
    expect(sorted.slice(-667).every((r) => Number(r.source_id.slice(2)) % 3 === 1)).toBe(true);
  });
});
