import type { Book, ContentType } from '../types';
import { MEDIA_TYPE_AUDIOBOOK, MEDIA_TYPE_EBOOK } from './mediaType';

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

// Discover metadata never carries its own content_type — BookMetadata (the
// backend shape) has no such field, and transformMetadataToBook doesn't
// invent one — so a combined row has no per-tile format to hand the library
// lookup. Without this, an owned audiobook surfaced through a combined row
// would silently default to "ebook" and fail to badge/lock. Audible is the
// only registered metadata provider that catalogues audiobooks exclusively
// (shelfmark/metadata_providers/audible.py; Hardcover, Open Library and
// Google Books are ebook catalogs with no ASIN support), so provider
// identity is what stands in for a real content type here.
const AUDIOBOOK_ONLY_PROVIDERS = new Set(['audible']);

export const contentTypeForDiscoverBook = (book: Book): ContentType =>
  book.provider && AUDIOBOOK_ONLY_PROVIDERS.has(book.provider)
    ? MEDIA_TYPE_AUDIOBOOK
    : MEDIA_TYPE_EBOOK;
