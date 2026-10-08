import { describe, expect, it } from 'vitest';

import type { Book, Release } from '../types';
import { sortReleasesForDisplay } from '../utils/releaseDisplaySort';

const book: Book = {
  id: 'dxd5',
  title: 'High School DxD Vol 5',
  author: 'Ichiei Ishibumi',
  provider: 'hardcover',
  provider_id: 'dxd5',
};

function release(id: string, volume: 'match' | 'other', sizeBytes: number, format = 'epub') {
  const payload = {
    v: 1,
    volume,
    other_volume: volume === 'other' ? 25 : null,
    medium: 'ebook',
    compatible: true,
    fan_marker: false,
  };
  const r: Release = {
    source: 'prowlarr',
    source_id: id,
    title: 'High School DxD Vol 5',
    format,
    size_bytes: sizeBytes,
    extra: { release_match: payload },
  };
  return r;
}

const releases = [
  release('small-match', 'match', 10, 'pdf'),
  release('big-other', 'other', 30),
  release('mid-match', 'match', 20),
];
const ids = (list: Release[]): string[] => list.map((r) => r.source_id);

describe('sortReleasesForDisplay', () => {
  it('uses the tiered best-match sort without a chosen sort', () => {
    expect(ids(sortReleasesForDisplay(releases, null, true, book, undefined))).toEqual([
      'small-match',
      'mid-match',
      'big-other',
    ]);
  });

  it('applies a saved column sort and ignores the tiers', () => {
    const saved = { key: 'size_bytes', direction: 'desc' as const };

    expect(ids(sortReleasesForDisplay(releases, saved, true, book, undefined))).toEqual([
      'big-other',
      'mid-match',
      'small-match',
    ]);
  });

  it('applies the format sort', () => {
    const formatSort = { key: '_format_priority', direction: 'asc' as const, value: 'pdf' };

    expect(ids(sortReleasesForDisplay(releases, formatSort, true, book, undefined))[0]).toBe(
      'small-match',
    );
  });

  it('falls back to best match when the source has no sortable columns', () => {
    const saved = { key: 'size_bytes', direction: 'desc' as const };

    expect(ids(sortReleasesForDisplay(releases, saved, false, book, undefined))).toEqual([
      'small-match',
      'mid-match',
      'big-other',
    ]);
  });
});
