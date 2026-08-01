import { describe, expect, it } from 'vitest';

import type { Book, RequestRecord } from '../types';
import { bookRequestKeys, buildOpenRequestKeys, isBookRequested } from '../utils/requestedBooks';

const book = (overrides: Partial<Book> = {}): Book => ({
  id: 'bk1',
  title: 'The Housemaid',
  author: 'Freida McFadden',
  provider: 'audible',
  provider_id: 'B0BSHZ1234',
  ...overrides,
});

const request = (overrides: Partial<RequestRecord> = {}): RequestRecord => ({
  id: 1,
  user_id: 7,
  status: 'pending',
  source_hint: null,
  content_type: 'audiobook',
  request_level: 'book',
  policy_mode: 'request_book',
  book_data: { provider: 'audible', provider_id: 'B0BSHZ1234' },
  release_data: null,
  note: null,
  admin_note: null,
  reviewed_by: null,
  reviewed_at: null,
  created_at: '2026-07-31T00:00:00Z',
  updated_at: '2026-07-31T00:00:00Z',
  ...overrides,
});

describe('bookRequestKeys', () => {
  it('keys a book by provider and provider id', () => {
    expect(bookRequestKeys(book())).toContain('audible:B0BSHZ1234');
  });

  it('falls back to the book id when there is no provider id', () => {
    expect(bookRequestKeys(book({ provider_id: undefined }))).toContain('audible:bk1');
  });

  it('offers the metadata fallback a request would have stored', () => {
    // buildMetadataBookRequestData writes `provider: book.provider || 'metadata'`,
    // so a providerless book must still be findable under that name.
    expect(bookRequestKeys(book({ provider: undefined }))).toContain('metadata:B0BSHZ1234');
  });

  it('is case-insensitive about the provider', () => {
    expect(bookRequestKeys(book({ provider: 'Audible' }))).toContain('audible:B0BSHZ1234');
  });

  it('yields nothing for a book with no identity at all', () => {
    expect(bookRequestKeys(book({ id: '', provider_id: '' }))).toEqual([]);
  });
});

describe('buildOpenRequestKeys', () => {
  it('includes a request still awaiting a decision', () => {
    expect(buildOpenRequestKeys([request()]).has('audible:B0BSHZ1234')).toBe(true);
  });

  it.each(['fulfilled', 'rejected', 'cancelled'] as const)('excludes a %s request', (status) => {
    // Only an undecided request is worth flagging. A fulfilled one becomes a
    // download the button already reports, and a rejected or cancelled one is
    // precisely the case where asking again is the right move.
    expect(buildOpenRequestKeys([request({ status })]).size).toBe(0);
  });

  it('ignores a request whose book data is missing', () => {
    expect(buildOpenRequestKeys([request({ book_data: null })]).size).toBe(0);
  });

  it('ignores a request whose book data has no provider id', () => {
    expect(buildOpenRequestKeys([request({ book_data: { provider: 'audible' } })]).size).toBe(0);
  });
});

describe('isBookRequested', () => {
  it('matches a book against an open request', () => {
    expect(isBookRequested(book(), buildOpenRequestKeys([request()]))).toBe(true);
  });

  it('does not match a different book by the same provider', () => {
    const other = book({ id: 'bk2', provider_id: 'B0OTHER999' });

    expect(isBookRequested(other, buildOpenRequestKeys([request()]))).toBe(false);
  });

  it('does not match the same id under a different provider', () => {
    // Provider ids are only unique within a provider, so the namespace matters.
    const keys = buildOpenRequestKeys([
      request({ book_data: { provider: 'hardcover', provider_id: 'B0BSHZ1234' } }),
    ]);

    expect(isBookRequested(book(), keys)).toBe(false);
  });
});
