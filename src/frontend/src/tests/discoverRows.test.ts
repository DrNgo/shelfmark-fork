import { describe, expect, it } from 'vitest';

import type { Book } from '../types';
import {
  applyRowError,
  applyRowResponse,
  contentTypeForDiscoverBook,
  contentTypeForDiscoverDetails,
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

// Reproduces handleShowDiscoverDetails's own call site verbatim
// (App.tsx:1002-1004): the real gate under test, contentTypeForDiscoverDetails,
// decides whether to tag; this just applies its answer the same way App.tsx does.
const buildTaggedBook = (candidate: Book, isCombinedMode: boolean): Book => {
  const discoverContentType = contentTypeForDiscoverDetails(candidate, isCombinedMode);
  return discoverContentType ? { ...candidate, content_type: discoverContentType } : candidate;
};

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

describe('contentTypeForDiscoverDetails', () => {
  // This is the real gate handleShowDiscoverDetails (App.tsx) calls before
  // opening a Discover tile's details modal — extracted so tests can exercise
  // the actual decision instead of hardcoding its outcome. App.tsx has no
  // component-render harness available (no RTL/jsdom, and `npm install` is
  // off-limits here), so `buildTaggedBook` below reproduces App.tsx's own
  // call site verbatim (App.tsx:1002-1005) and `resolveModalContentType`
  // reproduces the exact composition DetailsModal applies to whatever comes
  // out of it. Together they exercise the full path end to end.
  //
  // Two regressions have hit this exact gate in opposite directions:
  //   1. Untagged combined-mode books fell back to the section-wide type and
  //      produced a false "already own this" lock.
  //   2. Unconditional tagging misclassified single-format audiobook rows
  //      served by an ebook-only provider (Hardcover, in audiobook-only mode)
  //      as ebooks.
  // The four quadrants below are the full matrix that must hold for both bugs
  // to stay fixed.

  it('combined mode + Audible tile -> tags audiobook regardless of section default', () => {
    const audibleTile: Book = { id: 'ab-1', title: 'T', author: 'A', provider: 'audible' };
    const defaultContentType = 'ebook'; // section-wide default should be overridden by the tag

    const taggedBook = buildTaggedBook(audibleTile, true);

    expect(taggedBook.content_type).toBe('audiobook'); // the gate actually tagged it
    expect(resolveModalContentType(taggedBook, defaultContentType)).toBe('audiobook');
  });

  it('combined mode + Hardcover tile -> tags ebook regardless of section default', () => {
    const hardcoverTile: Book = { id: 'hc-1', title: 'T', author: 'A', provider: 'hardcover' };
    const defaultContentType = 'audiobook'; // section-wide default should be overridden by the tag

    const taggedBook = buildTaggedBook(hardcoverTile, true);

    expect(taggedBook.content_type).toBe('ebook'); // the gate actually tagged it
    expect(resolveModalContentType(taggedBook, defaultContentType)).toBe('ebook');
  });

  it('audiobook-only mode (non-combined) + Hardcover tile -> stays untagged, resolves via defaultContentType fallback', () => {
    // Audiobook-only Discover can be served by Hardcover (see module comment
    // above); the provider alone can't tell contentTypeForDiscoverBook this
    // is an audiobook row, so the gate must NOT tag here — the section-wide
    // default carries the correct answer instead.
    const hardcoverAudiobookTile: Book = {
      id: 'hc-2',
      title: 'T',
      author: 'A',
      provider: 'hardcover',
    };
    const defaultContentType = 'audiobook';

    const taggedBook = buildTaggedBook(hardcoverAudiobookTile, false);

    expect(taggedBook).toBe(hardcoverAudiobookTile); // untagged: same object, no content_type stamped
    expect(resolveModalContentType(taggedBook, defaultContentType)).toBe('audiobook');
  });

  it('ebook-only mode (non-combined) + Hardcover tile -> stays untagged, resolves via defaultContentType fallback', () => {
    const hardcoverEbookTile: Book = { id: 'hc-3', title: 'T', author: 'A', provider: 'hardcover' };
    const defaultContentType = 'ebook';

    const taggedBook = buildTaggedBook(hardcoverEbookTile, false);

    expect(taggedBook).toBe(hardcoverEbookTile); // untagged: same object, no content_type stamped
    expect(resolveModalContentType(taggedBook, defaultContentType)).toBe('ebook');
  });
});
