import { describe, expect, it } from 'vitest';

import type { Book } from '../types';
import {
  applyRowError,
  applyRowResponse,
  getDiscoverRowsForProvider,
  initialRowStates,
  visibleRows,
} from '../utils/discoverRows';

const book = (id: string): Book => ({ id, title: `Book ${id}`, author: 'A' });

describe('getDiscoverRowsForProvider', () => {
  it('returns trending + new releases for hardcover', () => {
    expect(getDiscoverRowsForProvider('hardcover').map((r) => r.key)).toEqual([
      'trending',
      'new_releases',
    ]);
  });

  it('returns best sellers + new releases for audible', () => {
    expect(getDiscoverRowsForProvider('audible').map((r) => r.key)).toEqual([
      'best_sellers',
      'new_releases',
    ]);
  });

  it('returns empty for other providers and null', () => {
    expect(getDiscoverRowsForProvider('openlibrary')).toEqual([]);
    expect(getDiscoverRowsForProvider('googlebooks')).toEqual([]);
    expect(getDiscoverRowsForProvider(null)).toEqual([]);
  });
});

describe('row state transitions', () => {
  const defs = getDiscoverRowsForProvider('hardcover');

  it('starts all rows loading (books null)', () => {
    expect(initialRowStates(defs).every((r) => r.books === null)).toBe(true);
  });

  it('applies a response to only the matching row, keeping label fallback', () => {
    const rows = applyRowResponse(initialRowStates(defs), 'trending', undefined, [book('1')]);
    expect(rows[0].books).toHaveLength(1);
    expect(rows[0].label).toBe('Trending'); // fallback kept when response has no label
    expect(rows[1].books).toBeNull(); // other row untouched — rows load independently
  });

  it('marks an errored row as empty', () => {
    const rows = applyRowError(initialRowStates(defs), 'trending');
    expect(rows[0].books).toEqual([]);
    expect(rows[1].books).toBeNull();
  });

  it('hides loaded-empty rows, keeps loading and non-empty rows', () => {
    let rows = initialRowStates(defs);
    rows = applyRowResponse(rows, 'trending', 'Trending', []);
    expect(visibleRows(rows).map((r) => r.key)).toEqual(['new_releases']); // still loading
    rows = applyRowResponse(rows, 'new_releases', 'New Releases', [book('1')]);
    expect(visibleRows(rows).map((r) => r.key)).toEqual(['new_releases']);
  });
});
