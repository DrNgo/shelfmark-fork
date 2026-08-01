import { describe, expect, it } from 'vitest';

import type { Book } from '../types';
import {
  booksLookupSignature,
  buildLibraryLookupPayload,
  describeLibraryMatch,
  libraryMatchTooltip,
  singleBookLookup,
} from '../utils/libraryMatches';
import type { LibraryMatch } from '../utils/libraryMatches';

const book = (overrides: Partial<Book> = {}): Book => ({
  id: 'bk1',
  title: 'The Housemaid',
  author: 'Freida McFadden',
  ...overrides,
});

const match = (overrides: Partial<LibraryMatch> = {}): LibraryMatch => ({
  libraries: ['Audiobooks'],
  items: [
    {
      item_id: 'li_1',
      library_id: 'lib_books',
      library_name: 'Audiobooks',
      title: 'The Housemaid',
      author: 'Freida McFadden',
      asin: 'B0BSHZ1234',
    },
  ],
  ...overrides,
});

describe('buildLibraryLookupPayload', () => {
  it('sends only what the matcher needs', () => {
    expect(buildLibraryLookupPayload([book()])).toEqual([
      { id: 'bk1', title: 'The Housemaid', author: 'Freida McFadden' },
    ]);
  });

  it('skips books that cannot be matched on title and author', () => {
    const payload = buildLibraryLookupPayload([
      book({ id: 'bk1', author: '' }),
      book({ id: 'bk2', title: '' }),
      book({ id: '' }),
    ]);

    expect(payload).toEqual([]);
  });

  it('drops duplicate ids so one card is asked about once', () => {
    expect(buildLibraryLookupPayload([book(), book()])).toHaveLength(1);
  });

  it('forwards an ASIN so the match can be exact', () => {
    expect(buildLibraryLookupPayload([book({ asin: 'B0BSHZ1234' })])).toEqual([
      { id: 'bk1', title: 'The Housemaid', author: 'Freida McFadden', asin: 'B0BSHZ1234' },
    ]);
  });

  it('keeps a book that has an ASIN but no usable author', () => {
    // An ASIN is a complete identity where half a title+author key is not.
    expect(buildLibraryLookupPayload([book({ author: '', asin: 'B0BSHZ1234' })])).toEqual([
      { id: 'bk1', title: 'The Housemaid', author: '', asin: 'B0BSHZ1234' },
    ]);
  });

  it('omits the key entirely when there is no ASIN', () => {
    expect(buildLibraryLookupPayload([book()])[0]).not.toHaveProperty('asin');
  });
});

describe('singleBookLookup', () => {
  it('wraps one title and author for the one-book surfaces', () => {
    expect(singleBookLookup('review', 'The Housemaid', 'Freida McFadden')).toEqual([
      { id: 'review', title: 'The Housemaid', author: 'Freida McFadden' },
    ]);
  });

  it('reads title and author out of a stored request payload', () => {
    const record = { title: 'The Housemaid', author: 'Freida McFadden', year: '2022' };

    expect(singleBookLookup('review', record.title, record.author)).toHaveLength(1);
  });

  it('yields nothing when either half of the key is missing', () => {
    expect(singleBookLookup('review', 'The Housemaid', undefined)).toEqual([]);
    expect(singleBookLookup('review', '', 'Freida McFadden')).toEqual([]);
  });

  it('carries an ASIN through', () => {
    expect(singleBookLookup('review', 'The Housemaid', 'Freida McFadden', 'B0BSHZ1234')).toEqual([
      { id: 'review', title: 'The Housemaid', author: 'Freida McFadden', asin: 'B0BSHZ1234' },
    ]);
  });

  it('asks about a book that has only an ASIN', () => {
    expect(singleBookLookup('review', '', undefined, 'B0BSHZ1234')).toEqual([
      { id: 'review', title: '', author: '', asin: 'B0BSHZ1234' },
    ]);
  });
});

describe('booksLookupSignature', () => {
  it('is stable for the same books', () => {
    expect(booksLookupSignature([book()])).toBe(booksLookupSignature([book()]));
  });

  it('changes when a book is added', () => {
    expect(booksLookupSignature([book()])).not.toBe(
      booksLookupSignature([book(), book({ id: 'bk2', title: 'The Coworker' })]),
    );
  });

  it('is empty when nothing is worth asking about', () => {
    expect(booksLookupSignature([book({ author: '' })])).toBe('');
  });

  it('changes when an ASIN appears, so the answer is refetched', () => {
    expect(booksLookupSignature([book()])).not.toBe(
      booksLookupSignature([book({ asin: 'B0BSHZ1234' })]),
    );
  });
});

describe('describeLibraryMatch', () => {
  it('names the library holding the book', () => {
    expect(describeLibraryMatch(match())).toBe('In Audiobooks');
  });

  it('counts the extras when a book is in several libraries', () => {
    expect(describeLibraryMatch(match({ libraries: ['Audiobooks', 'Kids'] }))).toBe(
      'In Audiobooks +1',
    );
  });

  it('falls back to a generic label when no library is named', () => {
    expect(describeLibraryMatch(match({ libraries: [] }))).toBe('In library');
  });
});

describe('libraryMatchTooltip', () => {
  it('names the edition held, not just the fact of a match', () => {
    // "In library" is not "same recording" — a 2021 rip and a 2024 re-recording
    // are both The Locked Door, and the tooltip is what tells them apart.
    expect(libraryMatchTooltip(match())).toBe(
      'Audiobooks: The Housemaid — Freida McFadden (ASIN B0BSHZ1234)',
    );
  });

  it('omits a missing ASIN', () => {
    const withoutAsin = match({
      items: [{ ...match().items[0], asin: '' }],
    });

    expect(libraryMatchTooltip(withoutAsin)).toBe('Audiobooks: The Housemaid — Freida McFadden');
  });

  it('lists every edition on its own line', () => {
    const twoCopies = match({
      libraries: ['Audiobooks', 'Kids'],
      items: [
        { ...match().items[0] },
        { ...match().items[0], item_id: 'li_2', library_name: 'Kids', asin: '' },
      ],
    });

    expect(libraryMatchTooltip(twoCopies).split('\n')).toHaveLength(2);
  });
});
