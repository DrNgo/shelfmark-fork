import type { Book, ContentType } from '../types';
import type { CoverAspect } from './coverAspect';
import { MEDIA_TYPE_AUDIOBOOK, MEDIA_TYPE_EBOOK } from './mediaType';

export interface DiscoverRowDef {
  key: string;
  label: string;
  provider: string;
  coverAspect: CoverAspect;
}

export interface DiscoverRowState extends DiscoverRowDef {
  books: Book[] | null; // null = still loading
}

// Which discover rows each metadata provider supports. Mirrors
// ROWS_BY_PROVIDER in shelfmark/core/discover.py — keep in sync.
export const DISCOVER_ROWS_BY_PROVIDER: Record<string, DiscoverRowDef[]> = {
  hardcover: [
    {
      key: 'trending',
      label: 'Trending',
      provider: 'hardcover',
      coverAspect: 'portrait',
    },
    {
      key: 'new_releases',
      label: 'New Releases',
      provider: 'hardcover',
      coverAspect: 'portrait',
    },
  ],
  audible: [
    {
      key: 'best_sellers',
      label: 'Best Sellers',
      provider: 'audible',
      coverAspect: 'square',
    },
    {
      key: 'new_releases',
      label: 'New Releases',
      provider: 'audible',
      coverAspect: 'square',
    },
  ],
};

const AUDIBLE_TOPIC_ROWS: DiscoverRowDef[] = [
  { key: 'topic_fantasy', label: 'Fantasy', provider: 'audible', coverAspect: 'square' },
  { key: 'topic_romance', label: 'Romance', provider: 'audible', coverAspect: 'square' },
  {
    key: 'topic_mystery_thriller',
    label: 'Mystery, Thriller & Suspense',
    provider: 'audible',
    coverAspect: 'square',
  },
  {
    key: 'topic_science_fiction',
    label: 'Science Fiction',
    provider: 'audible',
    coverAspect: 'square',
  },
  {
    key: 'topic_historical_fiction',
    label: 'Historical Fiction',
    provider: 'audible',
    coverAspect: 'square',
  },
  { key: 'topic_horror', label: 'Horror', provider: 'audible', coverAspect: 'square' },
];

interface BuildDiscoverRowDefsContext {
  contentType: ContentType | 'combined';
  standardProvider: string | null;
  audiobookProvider: string | null;
  hasPreferredTopic: boolean;
  preferredCoreKey: string | null;
}

export const getDiscoverRowsForProvider = (provider: string | null): DiscoverRowDef[] =>
  provider ? (DISCOVER_ROWS_BY_PROVIDER[provider] ?? []) : [];

export const buildDiscoverRowDefs = ({
  contentType,
  standardProvider,
  audiobookProvider,
  hasPreferredTopic,
  preferredCoreKey,
}: BuildDiscoverRowDefsContext): DiscoverRowDef[] => {
  const standardRows = getDiscoverRowsForProvider(standardProvider);
  if (contentType === 'ebook' || audiobookProvider !== 'audible') {
    return standardRows;
  }

  const preferredCoreRow = hasPreferredTopic
    ? AUDIBLE_TOPIC_ROWS.find((row) => row.key === preferredCoreKey)
    : undefined;
  const preferredRows = hasPreferredTopic
    ? [
        preferredCoreRow ?? {
          key: 'preferred_topic',
          label: 'Preferred Topic',
          provider: 'audible',
          coverAspect: 'square' as const,
        },
      ]
    : [];
  const remainingTopicRows = preferredCoreRow
    ? AUDIBLE_TOPIC_ROWS.filter((row) => row.key !== preferredCoreRow.key)
    : AUDIBLE_TOPIC_ROWS;

  return [...preferredRows, ...standardRows, ...remainingTopicRows];
};

export const initialRowStates = (defs: DiscoverRowDef[]): DiscoverRowState[] =>
  defs.map((def) => ({ ...def, books: null }));

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

// Discover metadata never carries its own content_type — BookMetadata (the
// backend shape) has no such field, and transformMetadataToBook doesn't
// invent one — so DetailsModal falls back to the section-wide
// defaultContentType unless the caller tags the book itself. Mirror
// DiscoverSection's own gate (DiscoverSection.tsx:133-143): only combined
// mode mixes formats within a row, so only there does per-book tagging beat
// the section-wide type. In single-format mode (the whole section is one
// format), stay undefined so DetailsModal's
// `book?.content_type ?? defaultContentType` fallback keeps using the
// correct section-wide type instead of a provider-based guess.
export const contentTypeForDiscoverDetails = (
  book: Book,
  isCombinedMode: boolean,
): ContentType | undefined => (isCombinedMode ? contentTypeForDiscoverBook(book) : undefined);
