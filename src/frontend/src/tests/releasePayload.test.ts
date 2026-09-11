import { describe, it, expect } from 'vitest';

import type { Book, Release } from '../types';
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
