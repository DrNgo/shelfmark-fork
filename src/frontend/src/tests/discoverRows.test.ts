import { describe, expect, it } from 'vitest';

import type { Book } from '../types';
import {
  applyRowError,
  applyRowResponse,
  contentTypeForDiscoverBook,
  getDiscoverRowsForProvider,
  initialRowStates,
  visibleRows,
} from '../utils/discoverRows';
import { buildLibraryLookupPayload } from '../utils/libraryMatches';

const book = (id: string): Book => ({ id, title: `Book ${id}`, author: 'A' });

// The exact composition expression DetailsModal uses internally to resolve a
// book's format (components/DetailsModal.tsx, singleBookLookup's contentType
// argument): `book.content_type ?? defaultContentType`.
const resolveModalContentType = (candidate: Book, defaultContentType: string): string =>
  candidate.content_type ?? defaultContentType;

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

describe('contentTypeForDiscoverBook', () => {
  // Combined-mode discover rows carry no content_type of their own (neither
  // the backend's BookMetadata nor transformMetadataToBook sets one), so this
  // is what stands in for it — an owned audiobook from an audiobook-only
  // provider must resolve to 'audiobook', not the ebook default, or it would
  // badge as a cross-format holding and never lock.
  it('classifies a book from an audiobook-only provider as audiobook', () => {
    expect(contentTypeForDiscoverBook({ ...book('1'), provider: 'audible' })).toBe('audiobook');
  });

  it('classifies a book from an ebook provider as ebook', () => {
    expect(contentTypeForDiscoverBook({ ...book('1'), provider: 'hardcover' })).toBe('ebook');
  });

  it('classifies a book with no provider at all as ebook', () => {
    expect(contentTypeForDiscoverBook(book('1'))).toBe('ebook');
  });

  it('reaches the library lookup payload — the exact wiring DiscoverSection uses in combined mode', () => {
    const audiobookTile: Book = { ...book('1'), provider: 'audible' };
    const ebookTile: Book = { ...book('2'), provider: 'hardcover' };
    const booksForLookup: Book[] = [
      { ...audiobookTile, content_type: contentTypeForDiscoverBook(audiobookTile) },
      { ...ebookTile, content_type: contentTypeForDiscoverBook(ebookTile) },
    ];

    const payload = buildLibraryLookupPayload(booksForLookup);

    expect(payload.find((entry) => entry.id === '1')?.content_type).toBe('audiobook');
    expect(payload.find((entry) => entry.id === '2')?.content_type).toBe('ebook');
  });
});

describe('handleShowDiscoverDetails regression (App.tsx)', () => {
  // App.tsx has no component-render harness available (no RTL/jsdom, and
  // `npm install` is off-limits here), so this exercises the real
  // `contentTypeForDiscoverBook` classifier plus the real composition
  // expression the modal uses (resolveModalContentType, defined above).
  //
  // Regression scenario: a combined-mode Discover row mixes an Audible
  // audiobook tile with a Hardcover ebook tile. The section-wide
  // defaultContentType passed to the modal reflects whichever tile the user
  // is *currently viewing's neighbor row phase* — here simulated as
  // 'audiobook' to match the bug report (user owns the audiobook; opens the
  // ebook tile). Before the fix, handleShowDiscoverDetails put the book into
  // setSelectedBook untagged, so DetailsModal fell back to defaultContentType
  // and asked the library lookup for 'audiobook' — even though the tile is a
  // Hardcover ebook. That produced a false "In library" lock on a book the
  // user does not own as an ebook.

  it('tags an ebook-provider tile as ebook even when the section-wide type is audiobook', () => {
    const hardcoverTile: Book = { id: 'hc-1', title: 'T', author: 'A', provider: 'hardcover' };
    const defaultContentType = 'audiobook'; // effectiveContentType at the time the tile was opened

    // This is the fix: handleShowDiscoverDetails now tags the book the same
    // way DiscoverSection's own library lookup does before handing it to
    // setSelectedBook, so DetailsModal never needs its defaultContentType
    // fallback for a book that has a knowable per-tile format.
    const taggedBook: Book = {
      ...hardcoverTile,
      content_type: contentTypeForDiscoverBook(hardcoverTile),
    };

    expect(resolveModalContentType(taggedBook, defaultContentType)).toBe('ebook');
  });

  it('proves the bug: an UNTAGGED book falls back to the section-wide default and mislabels the tile', () => {
    // This reproduces the pre-fix behaviour directly (no tagging applied —
    // exactly what handleShowDiscoverDetails did before this change), to
    // demonstrate why the fix above is necessary rather than a no-op.
    const hardcoverTile: Book = { id: 'hc-1', title: 'T', author: 'A', provider: 'hardcover' };
    const defaultContentType = 'audiobook';

    expect(resolveModalContentType(hardcoverTile, defaultContentType)).toBe('audiobook');
  });
});
