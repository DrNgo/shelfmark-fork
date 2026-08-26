import { describe, expect, it } from 'vitest';

import type { Book, ButtonStateInfo } from '../types';
import {
  applyInLibraryLock,
  booksLookupSignature,
  buildLibraryLookupPayload,
  isHeldInFormat,
  libraryMatchOwnershipMessage,
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
      source: 'audiobookshelf',
      media_type: 'audiobook',
      item_id: 'li_1',
      library_id: 'lib_books',
      library_name: 'Audiobooks',
      title: 'The Housemaid',
      author: 'Freida McFadden',
      asin: 'B0BSHZ1234',
      isbn13: '',
    },
  ],
  other_editions: [],
  other_formats: [],
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
    // An empty author is not a value, so it is omitted from the payload
    // rather than sent as an empty string.
    expect(buildLibraryLookupPayload([book({ author: '', asin: 'B0BSHZ1234' })])).toEqual([
      { id: 'bk1', title: 'The Housemaid', asin: 'B0BSHZ1234' },
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

  it('carries the content type through so an audiobook surface matches its own format', () => {
    expect(singleBookLookup('x', 'T', 'A', undefined, undefined, 'audiobook')[0]).toMatchObject({
      content_type: 'audiobook',
    });
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

describe('buildLibraryLookupPayload with ISBNs', () => {
  it('forwards both ISBN spellings and the content type', () => {
    const payload = buildLibraryLookupPayload([
      {
        id: 'b1',
        title: 'The Housemaid',
        author: 'Freida McFadden',
        isbn_13: '9780593135204',
        content_type: 'ebook',
      },
    ]);

    expect(payload[0]).toEqual({
      id: 'b1',
      title: 'The Housemaid',
      author: 'Freida McFadden',
      isbn_13: '9780593135204',
      content_type: 'ebook',
    });
  });

  it('keeps a book that has only an ISBN', () => {
    const payload = buildLibraryLookupPayload([
      { id: 'b1', title: '', author: '', isbn_10: '0306406152' },
    ]);

    expect(payload).toHaveLength(1);
  });

  it('still drops a book with no title, author, ASIN or ISBN', () => {
    expect(buildLibraryLookupPayload([{ id: 'b1', title: 'Half a key', author: '' }])).toEqual([]);
  });

  it('falls back to the surface content type when a book carries none', () => {
    const payload = buildLibraryLookupPayload(
      [{ id: 'b1', title: '', author: '', isbn_10: '0306406152' }],
      'audiobook',
    );

    expect(payload[0].content_type).toBe('audiobook');
  });
});

describe('booksLookupSignature', () => {
  it('changes when the content type changes, so a format switch refetches', () => {
    const signatureBook = { id: 'b1', title: 'T', author: 'A' };

    expect(booksLookupSignature([signatureBook], 'ebook')).not.toBe(
      booksLookupSignature([signatureBook], 'audiobook'),
    );
  });

  it('changes when an ISBN is added', () => {
    const base = { id: 'b1', title: 'T', author: 'A' };

    expect(booksLookupSignature([base])).not.toBe(
      booksLookupSignature([{ ...base, isbn_13: '9780593135204' }]),
    );
  });
});

describe('isHeldInFormat', () => {
  const held: LibraryMatch = {
    libraries: ['Ebooks'],
    items: [
      {
        source: 'grimmory',
        media_type: 'ebook',
        item_id: '1',
        library_id: '7',
        library_name: 'Ebooks',
        title: 'The Housemaid',
        author: 'Freida McFadden',
        asin: '',
        isbn13: '9780593135204',
      },
    ],
    other_editions: [],
    other_formats: [],
  };
  const otherOnly: LibraryMatch = { ...held, items: [], other_formats: held.items };

  it('is true when the same format is held', () => {
    expect(isHeldInFormat(held)).toBe(true);
  });

  it('is false when only another format is held', () => {
    expect(isHeldInFormat(otherOnly)).toBe(false);
  });

  it('is false when there is no match at all', () => {
    expect(isHeldInFormat(undefined)).toBe(false);
  });
});

describe('applyInLibraryLock', () => {
  it('does not lock acquisition when only another format is held', () => {
    const otherOnly: LibraryMatch = {
      libraries: [],
      items: [],
      other_editions: [],
      other_formats: [
        {
          source: 'audiobookshelf',
          media_type: 'audiobook',
          item_id: 'abs_1',
          library_id: 'lib',
          library_name: 'Audiobooks',
          title: 'The Housemaid',
          author: 'Freida McFadden',
          asin: '',
          isbn13: '',
        },
      ],
    };

    expect(
      applyInLibraryLock({ state: 'download', text: 'Download' }, isHeldInFormat(otherOnly)),
    ).toEqual({ state: 'download', text: 'Download' });
  });
});

describe('libraryMatchTooltip', () => {
  it('names the other format without implying ownership of this one', () => {
    const otherOnly: LibraryMatch = {
      libraries: [],
      items: [],
      other_editions: [],
      other_formats: [
        {
          source: 'audiobookshelf',
          media_type: 'audiobook',
          item_id: 'abs_1',
          library_id: 'lib',
          library_name: 'Audiobooks',
          title: 'The Housemaid',
          author: 'Freida McFadden',
          asin: 'B09XYZ1234',
          isbn13: '',
        },
      ],
    };

    expect(libraryMatchTooltip(otherOnly)).toContain('as an audiobook');
    expect(libraryMatchTooltip(otherOnly)).not.toContain('Already in your library: ');
  });
});

// Every acquire button (DetailsModal, and the isInLibrary prop into
// BookActionButton from CardView/CompactView/ListView) locks by composing
// these two functions in this exact order. A cross-format-only match must
// survive the trip through both without locking, or the badge changes colour
// while the button stays blocked.
describe('acquire-button wiring: isHeldInFormat feeding applyInLibraryLock', () => {
  const GET: ButtonStateInfo = { state: 'download', text: 'Get' };

  it('locks when the match holds this format', () => {
    const heldHere: LibraryMatch = {
      libraries: ['Ebooks'],
      items: [
        {
          source: 'grimmory',
          media_type: 'ebook',
          item_id: 'gm_1',
          library_id: 'lib',
          library_name: 'Ebooks',
          title: 'The Housemaid',
          author: 'Freida McFadden',
          asin: '',
          isbn13: '9780593135204',
        },
      ],
      other_editions: [],
      other_formats: [],
    };

    expect(applyInLibraryLock(GET, isHeldInFormat(heldHere))).toEqual({
      state: 'blocked',
      text: 'In library',
    });
  });

  it('does NOT lock when the match only holds another format', () => {
    const audiobookOnly: LibraryMatch = {
      libraries: ['Audiobooks'],
      items: [],
      other_editions: [],
      other_formats: [
        {
          source: 'audiobookshelf',
          media_type: 'audiobook',
          item_id: 'abs_1',
          library_id: 'lib',
          library_name: 'Audiobooks',
          title: 'The Housemaid',
          author: 'Freida McFadden',
          asin: 'B0BSHZ1234',
          isbn13: '',
        },
      ],
    };

    expect(applyInLibraryLock(GET, isHeldInFormat(audiobookOnly))).toBe(GET);
  });

  it('does not lock when there is no match at all', () => {
    expect(applyInLibraryLock(GET, isHeldInFormat(undefined))).toBe(GET);
  });
});

// DetailsModal, RequestConfirmationModal and ActivityCard each build a
// one-book lookup locally and must pass their book's real content type and
// ISBN, or the backend defaults the request to "ebook" — filing a real
// audiobook holding under other_formats instead of items, so the modal never
// locks even though the reader already owns the audiobook.
describe('single-book surfaces thread format and ISBN through the lookup', () => {
  it('DetailsModal-shaped audiobook lookup carries content_type and ISBN', () => {
    // No Book on the search path ever sets content_type (transformMetadataToBook
    // never assigns it), so DetailsModal cannot get "audiobook" from book.content_type
    // itself — it falls back to the defaultContentType prop passed in from App.tsx,
    // via `book?.content_type ?? defaultContentType`. Modeling anything else here
    // (e.g. passing 'audiobook' as if it came straight from the book) would exercise
    // a shape DetailsModal never actually produces.
    const detailsModalBook: Partial<Book> = { content_type: undefined };
    const defaultContentType = 'audiobook';
    const effectiveContentType = detailsModalBook.content_type ?? defaultContentType;

    const payload = singleBookLookup(
      'details-bk1',
      'The Housemaid',
      'Freida McFadden',
      undefined,
      '9780593135204',
      effectiveContentType,
    );

    expect(payload).toEqual([
      {
        id: 'details-bk1',
        title: 'The Housemaid',
        author: 'Freida McFadden',
        isbn_13: '9780593135204',
        content_type: 'audiobook',
      },
    ]);

    // Close the loop: this is exactly the payload that reaches the backend,
    // which (per lookup.py's format split) files an audiobook holding under
    // `items` when the request says "audiobook". Confirm that response locks
    // the modal's footer button instead of leaving it offering "Get".
    const audiobookHeldHere: LibraryMatch = {
      libraries: ['Audiobooks'],
      items: [
        {
          source: 'audiobookshelf',
          media_type: 'audiobook',
          item_id: 'abs_1',
          library_id: 'lib',
          library_name: 'Audiobooks',
          title: 'The Housemaid',
          author: 'Freida McFadden',
          asin: '',
          isbn13: '9780593135204',
        },
      ],
      other_editions: [],
      other_formats: [],
    };
    const GET: ButtonStateInfo = { state: 'download', text: 'Get' };

    expect(isHeldInFormat(audiobookHeldHere)).toBe(true);
    expect(applyInLibraryLock(GET, isHeldInFormat(audiobookHeldHere))).toEqual({
      state: 'blocked',
      text: 'In library',
    });
  });

  it('regresses to a false "Get" without defaultContentType (the pre-fix DetailsModal)', () => {
    // Before DetailsModal accepted a defaultContentType prop, this is what it
    // actually sent: book.content_type alone, which is always undefined for
    // every Book on the search path. This test pins that failure mode so a
    // future regression that drops the defaultContentType fallback is caught
    // here, not in production.
    const detailsModalBookNoFallback: Partial<Book> = { content_type: undefined };
    const effectiveContentTypeWithoutFallback = detailsModalBookNoFallback.content_type;

    const payload = singleBookLookup(
      'details-bk1',
      'The Housemaid',
      'Freida McFadden',
      undefined,
      '9780593135204',
      effectiveContentTypeWithoutFallback,
    );

    // No content_type on the wire means the backend classifies the request as
    // an ebook (media_type_for_content_type's documented default), so the
    // very same Audiobookshelf copy is filed under other_formats, not items.
    expect(payload[0]?.content_type).toBeUndefined();

    const audiobookFiledAsOtherFormat: LibraryMatch = {
      libraries: ['Audiobooks'],
      items: [],
      other_editions: [],
      other_formats: [
        {
          source: 'audiobookshelf',
          media_type: 'audiobook',
          item_id: 'abs_1',
          library_id: 'lib',
          library_name: 'Audiobooks',
          title: 'The Housemaid',
          author: 'Freida McFadden',
          asin: '',
          isbn13: '9780593135204',
        },
      ],
    };
    const GET: ButtonStateInfo = { state: 'download', text: 'Get' };

    expect(isHeldInFormat(audiobookFiledAsOtherFormat)).toBe(false);
    expect(applyInLibraryLock(GET, isHeldInFormat(audiobookFiledAsOtherFormat))).toBe(GET);
  });

  it('an audiobook holding lands in items (held) when content_type is passed, not other_formats', () => {
    // What the backend returns once the lookup honestly says "audiobook":
    // the Audiobookshelf copy is the requested format, so it is an item.
    const correctlyClassified: LibraryMatch = {
      libraries: ['Audiobooks'],
      items: [
        {
          source: 'audiobookshelf',
          media_type: 'audiobook',
          item_id: 'abs_1',
          library_id: 'lib',
          library_name: 'Audiobooks',
          title: 'The Housemaid',
          author: 'Freida McFadden',
          asin: '',
          isbn13: '9780593135204',
        },
      ],
      other_editions: [],
      other_formats: [],
    };

    expect(isHeldInFormat(correctlyClassified)).toBe(true);
  });

  it('the same holding would have landed in other_formats (not held) under the old ebook-default bug', () => {
    // What the backend returns when no content_type is sent: it defaults to
    // "ebook", so the very same Audiobookshelf copy is filed as a cross-format
    // holding instead of an item — this is the bug threading content_type fixes.
    const defaultedToEbook: LibraryMatch = {
      libraries: ['Audiobooks'],
      items: [],
      other_editions: [],
      other_formats: [
        {
          source: 'audiobookshelf',
          media_type: 'audiobook',
          item_id: 'abs_1',
          library_id: 'lib',
          library_name: 'Audiobooks',
          title: 'The Housemaid',
          author: 'Freida McFadden',
          asin: '',
          isbn13: '9780593135204',
        },
      ],
    };

    expect(isHeldInFormat(defaultedToEbook)).toBe(false);
  });
});

describe('libraryMatchOwnershipMessage', () => {
  const audiobookHeldHere: LibraryMatch = {
    libraries: ['Audiobooks'],
    items: [
      {
        source: 'audiobookshelf',
        media_type: 'audiobook',
        item_id: 'abs_1',
        library_id: 'lib',
        library_name: 'Audiobooks',
        title: 'The Housemaid',
        author: 'Freida McFadden',
        asin: '',
        isbn13: '',
      },
    ],
    other_editions: [],
    other_formats: [],
  };

  const audiobookOnly: LibraryMatch = {
    libraries: ['Audiobooks'],
    items: [],
    other_editions: [],
    other_formats: [
      {
        source: 'audiobookshelf',
        media_type: 'audiobook',
        item_id: 'abs_1',
        library_id: 'lib',
        library_name: 'Audiobooks',
        title: 'The Housemaid',
        author: 'Freida McFadden',
        asin: '',
        isbn13: '',
      },
    ],
  };

  it('claims ownership for a same-format holding', () => {
    expect(libraryMatchOwnershipMessage(audiobookHeldHere)).toContain('You already have this');
  });

  it('does NOT claim ownership for a cross-format-only holding', () => {
    // This is the exact failure mode the whole feature exists to prevent:
    // telling an ebook requester they already own it because they hold the
    // audiobook would talk them out of a request they are entitled to make.
    const message = libraryMatchOwnershipMessage(audiobookOnly);

    expect(message).not.toContain('You already have this');
    expect(message).not.toMatch(/already have this/i);
  });

  it('names the format actually held for a cross-format-only holding', () => {
    expect(libraryMatchOwnershipMessage(audiobookOnly)).toContain('an audiobook');
  });

  it('does not read as a reason to abandon the request', () => {
    const message = libraryMatchOwnershipMessage(audiobookOnly);

    expect(message.toLowerCase()).not.toContain("don't request");
    expect(message.toLowerCase()).not.toContain('cannot request');
  });
});

// A full-cast release keys to the same title+author as the single-narrator
// original, so the backend hands it back as a match. It arrives in
// `other_editions`, and every surface has to read that as "you own a different
// recording" rather than "you own this".
describe('other editions: a different recording of the same book', () => {
  const GET: ButtonStateInfo = { state: 'download', text: 'Get' };

  const graphicAudio: LibraryMatch = {
    libraries: [],
    items: [],
    other_editions: [
      {
        source: 'audiobookshelf',
        media_type: 'audiobook',
        item_id: 'li_1',
        library_id: 'lib_books',
        library_name: 'Audiobooks',
        title: 'Dungeon Crawler Carl (Audio Immersion Tunnel)',
        author: 'Matt Dinniman',
        asin: '',
        isbn13: '',
      },
    ],
    other_formats: [],
  };

  it('is not held in format, so the acquire button stays offerable', () => {
    // The bug in one line: this used to come back in `items`, which locked the
    // button on a release the user did not own.
    expect(isHeldInFormat(graphicAudio)).toBe(false);
    expect(applyInLibraryLock(GET, isHeldInFormat(graphicAudio))).toBe(GET);
  });

  it('names the edition held in the tooltip', () => {
    const tooltip = libraryMatchTooltip(graphicAudio);

    expect(tooltip).toContain('Dungeon Crawler Carl (Audio Immersion Tunnel)');
    expect(tooltip).toMatch(/different edition/i);
  });

  it('does not claim you already have this', () => {
    expect(libraryMatchTooltip(graphicAudio)).not.toContain('Already in your library');
    expect(libraryMatchOwnershipMessage(graphicAudio)).not.toMatch(/already have this/i);
  });

  it('still encourages the request rather than discouraging it', () => {
    const message = libraryMatchOwnershipMessage(graphicAudio).toLowerCase();

    expect(message).toContain('request');
    expect(message).not.toContain("don't request");
    expect(message).not.toContain('cannot request');
  });

  it('reports a real holding normally when one sits alongside the other edition', () => {
    const both: LibraryMatch = {
      ...graphicAudio,
      libraries: ['Audiobooks'],
      items: [
        {
          source: 'audiobookshelf',
          media_type: 'audiobook',
          item_id: 'li_2',
          library_id: 'lib_books',
          library_name: 'Audiobooks',
          title: 'Dungeon Crawler Carl',
          author: 'Matt Dinniman',
          asin: 'B08V8B2CGV',
          isbn13: '',
        },
      ],
    };

    expect(isHeldInFormat(both)).toBe(true);
    expect(libraryMatchTooltip(both)).toContain('Already in your library');
    expect(libraryMatchOwnershipMessage(both)).toContain('You already have this');
  });
});
