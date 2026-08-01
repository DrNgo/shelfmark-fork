import { describe, expect, it } from 'vitest';

import type { Book, ButtonStateInfo } from '../types';
import {
  applyInLibraryLock,
  booksLookupSignature,
  buildLibraryLookupPayload,
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

describe('libraryMatchTooltip', () => {
  it('names the edition held, not just the fact of a match', () => {
    // "In library" is not "same recording" — a 2021 rip and a 2024 re-recording
    // are both The Locked Door, and the tooltip is what tells them apart.
    expect(libraryMatchTooltip(match())).toBe(
      'Already in your library: The Housemaid — Freida McFadden (ASIN B0BSHZ1234)',
    );
  });

  it('never names the library holding it', () => {
    // Which shelf a book sits on is not the reader's problem — the badge is
    // there to answer "do I already have this", and nothing more.
    expect(libraryMatchTooltip(match())).not.toContain('Audiobooks');
  });

  it('omits a missing ASIN', () => {
    const withoutAsin = match({
      items: [{ ...match().items[0], asin: '' }],
    });

    expect(libraryMatchTooltip(withoutAsin)).toBe(
      'Already in your library: The Housemaid — Freida McFadden',
    );
  });

  it('lists every edition on its own line, labelling only the first', () => {
    const twoCopies = match({
      libraries: ['Audiobooks', 'Kids'],
      items: [
        { ...match().items[0] },
        { ...match().items[0], item_id: 'li_2', library_name: 'Kids', asin: '' },
      ],
    });

    const lines = libraryMatchTooltip(twoCopies).split('\n');

    expect(lines).toHaveLength(2);
    expect(lines[1]).toBe('The Housemaid — Freida McFadden');
  });
});

describe('applyInLibraryLock', () => {
  const GET: ButtonStateInfo = { state: 'download', text: 'Get' };

  it('blocks the action when the book is already held', () => {
    expect(applyInLibraryLock(GET, true)).toEqual({ state: 'blocked', text: 'In library' });
  });

  it('leaves the action alone when the book is not held', () => {
    expect(applyInLibraryLock(GET, false)).toBe(GET);
  });

  it('does not overwrite a download that is already under way', () => {
    // The index catches up minutes after a grab finishes, so a completed
    // download would otherwise flip to "In library" and lose the result the
    // user was watching for.
    const complete: ButtonStateInfo = { state: 'complete', text: 'Downloaded' };

    expect(applyInLibraryLock(complete, true)).toBe(complete);
  });

  it('leaves an existing block untouched rather than relabelling it', () => {
    const blocked: ButtonStateInfo = { state: 'blocked', text: 'Unavailable' };

    expect(applyInLibraryLock(blocked, true)).toBe(blocked);
  });
});
