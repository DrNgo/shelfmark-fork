import type { Book } from '../types';

export interface DiscoverRowDef {
  key: string;
  label: string;
}

export interface DiscoverRowState {
  key: string;
  label: string;
  books: Book[] | null; // null = still loading
}

// Which discover rows each metadata provider supports. Mirrors
// ROWS_BY_PROVIDER in shelfmark/core/discover.py — keep in sync.
export const DISCOVER_ROWS_BY_PROVIDER: Record<string, DiscoverRowDef[]> = {
  hardcover: [
    { key: 'trending', label: 'Trending' },
    { key: 'new_releases', label: 'New Releases' },
  ],
  audible: [
    { key: 'best_sellers', label: 'Best Sellers' },
    { key: 'new_releases', label: 'New Releases' },
  ],
};

export const getDiscoverRowsForProvider = (provider: string | null): DiscoverRowDef[] =>
  provider ? (DISCOVER_ROWS_BY_PROVIDER[provider] ?? []) : [];

export const initialRowStates = (defs: DiscoverRowDef[]): DiscoverRowState[] =>
  defs.map((def) => ({ key: def.key, label: def.label, books: null }));

export const applyRowResponse = (
  rows: DiscoverRowState[],
  key: string,
  label: string | undefined,
  books: Book[],
): DiscoverRowState[] =>
  rows.map((row) => (row.key === key ? { ...row, label: label ?? row.label, books } : row));

export const applyRowError = (rows: DiscoverRowState[], key: string): DiscoverRowState[] =>
  rows.map((row) => (row.key === key ? { ...row, books: [] } : row));

/** Rows worth rendering: still loading (skeleton) or loaded with books. */
export const visibleRows = (rows: DiscoverRowState[]): DiscoverRowState[] =>
  rows.filter((row) => row.books === null || row.books.length > 0);
