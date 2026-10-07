import { describe, it, expect, vi, afterEach } from 'vitest';

import { downloadRelease } from '../services/api';
import type { Book, Release } from '../types';
import { combinedLegOptions, completeCombinedSelection } from '../utils/combinedSelection';
import { buildReleaseDownloadPayload } from '../utils/releasePayload';

const book: Book = {
  id: 'hc-1',
  title: 'Drive',
  author: 'James S. A. Corey',
  year: '2012',
  preview: 'https://img/drive.jpg',
  series_name: 'The Expanse',
  series_position: 2.6,
  subtitle: 'An Expanse Short Story',
  provider: 'hardcover',
  provider_id: 'hc-1',
  source: 'direct_download',
};

const release: Release = {
  source: 'audiobookbay',
  source_id: 'abb-1',
  title: 'James S. A. Corey - The Expanse Complete 2.0',
  format: 'm4b',
  language: 'en',
  download_url: 'https://audiobookbay.lu/abss/expanse/',
};

describe('buildReleaseDownloadPayload', () => {
  it('describes the searched book and the chosen release', () => {
    const payload = buildReleaseDownloadPayload(book, release, 'audiobook');
    expect(payload).toMatchObject({
      source: 'audiobookbay',
      source_id: 'abb-1',
      title: 'Drive',
      author: 'James S. A. Corey',
      series_name: 'The Expanse',
      series_position: 2.6,
      language: 'en',
      content_type: 'audiobook',
    });
    expect(payload.multi_book).toBeUndefined();
    expect(payload.book_plan).toBeUndefined();
  });

  it('uses the release title and author for manual books', () => {
    const manual: Book = { ...book, provider: 'manual', title: 'ignored' };
    const withAuthor = { ...release, extra: { author: 'Release Author' } };
    const payload = buildReleaseDownloadPayload(manual, withAuthor, 'audiobook');
    expect(payload.title).toBe(release.title);
    expect(payload.author).toBe('Release Author');
  });

  it('flags a manual multi-book pack', () => {
    const payload = buildReleaseDownloadPayload(book, release, 'audiobook', { multiBook: true });
    expect(payload.multi_book).toBe(true);
    expect(payload.book_plan).toBeUndefined();
  });

  it('attaches the approved book plan', () => {
    const plan = [{ title: 'Leviathan Wakes', series_position: 1, year: 2011, files: ['a.m4b'] }];
    const payload = buildReleaseDownloadPayload(book, release, 'audiobook', {
      multiBook: true,
      bookPlan: plan,
    });
    expect(payload.multi_book).toBe(true);
    expect(payload.book_plan).toEqual(plan);
  });

  // Fork-only: the activity view frames a finished audiobook from `cover_aspect`,
  // so it has to survive the trip through the download payload.
  it("carries the book's cover aspect", () => {
    const square: Book = { ...book, cover_aspect: 'square' };
    expect(buildReleaseDownloadPayload(square, release, 'audiobook').cover_aspect).toBe('square');
  });

  it('sends no cover aspect for a manual book, whose preview is the release thumbnail', () => {
    const manual: Book = { ...book, provider: 'manual', cover_aspect: 'square' };
    expect(buildReleaseDownloadPayload(manual, release, 'audiobook').cover_aspect).toBeUndefined();
  });

  // Fork-only: the Audiobookshelf library an admin picks in the release modal.
  it('attaches the chosen audiobook destination', () => {
    const payload = buildReleaseDownloadPayload(book, release, 'audiobook', {
      destinationKey: 'lib-kids',
    });
    expect(payload.destination_key).toBe('lib-kids');
  });

  it('omits the destination entirely when none was chosen', () => {
    expect('destination_key' in buildReleaseDownloadPayload(book, release, 'audiobook')).toBe(
      false,
    );
    expect(
      'destination_key' in
        buildReleaseDownloadPayload(book, release, 'audiobook', { destinationKey: '  ' }),
    ).toBe(false);
  });

  // A pack reviewed in the modal is still queued as one task, so it has to keep
  // the library the admin picked before the review panel opened.
  it('keeps the destination alongside an approved pack plan', () => {
    const payload = buildReleaseDownloadPayload(book, release, 'audiobook', {
      destinationKey: 'lib-kids',
      multiBook: true,
      bookPlan: [{ title: 'Leviathan Wakes', series_position: 1, year: 2011, files: ['a.m4b'] }],
    });
    expect(payload.destination_key).toBe('lib-kids');
    expect(payload.multi_book).toBe(true);
  });
});

// Fork-only: the book identity a post-upload hook tags the book with.
describe('buildReleaseDownloadPayload book identity', () => {
  const identified: Book = {
    ...book,
    provider: 'hardcover',
    provider_id: '886465',
    isbn_13: '9780316005142',
    isbn_10: '0316005142',
    asin: 'B0BSHZ1234',
  };
  const ebookRelease: Release = { source: 'prowlarr', source_id: 'ebook-1', title: 'Drive.epub' };

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('carries provider, provider id, ISBN and ASIN from the book', () => {
    expect(buildReleaseDownloadPayload(identified, ebookRelease, 'ebook')).toMatchObject({
      provider: 'hardcover',
      provider_id: '886465',
      isbn_13: '9780316005142',
      asin: 'B0BSHZ1234',
    });
  });

  it('sends the ISBN-10 when the book has no ISBN-13', () => {
    const isbn10Only: Book = { ...identified, isbn_13: undefined };

    expect(buildReleaseDownloadPayload(isbn10Only, ebookRelease, 'ebook').isbn_13).toBe(
      '0316005142',
    );
  });

  it('sends no identity for a manual book', () => {
    const manual: Book = { ...identified, provider: 'manual' };
    const payload = buildReleaseDownloadPayload(manual, ebookRelease, 'ebook');

    for (const field of ['provider', 'provider_id', 'isbn_13', 'asin'] as const) {
      expect(payload[field]).toBeUndefined();
    }
  });

  it('carries identity and the ebook library on a combined-mode leg', () => {
    const state = completeCombinedSelection(
      { phase: 'ebook', ebookMode: 'download', audiobookMode: 'request_book' },
      identified,
      ebookRelease,
      'grimmory:5:8',
    );

    const payload = buildReleaseDownloadPayload(
      identified,
      ebookRelease,
      'ebook',
      combinedLegOptions(state, 'ebook'),
    );

    expect(payload).toMatchObject({
      provider: 'hardcover',
      provider_id: '886465',
      destination_key: 'grimmory:5:8',
    });
  });

  it('keeps identity and the library on an on-behalf download', async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response('{"status":"queued"}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }),
    );
    vi.stubGlobal('fetch', fetchMock);

    await downloadRelease(
      buildReleaseDownloadPayload(identified, ebookRelease, 'ebook', {
        destinationKey: 'grimmory:5:8',
      }),
      42,
    );

    const sent = fetchMock.mock.calls[0][1]?.body;
    const body: unknown = typeof sent === 'string' ? JSON.parse(sent) : null;
    expect(body).toMatchObject({
      on_behalf_of_user_id: 42,
      provider: 'hardcover',
      provider_id: '886465',
      isbn_13: '9780316005142',
      asin: 'B0BSHZ1234',
      destination_key: 'grimmory:5:8',
    });
  });
});
