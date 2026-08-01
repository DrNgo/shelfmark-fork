import type { Book, RequestRecord } from '../types';

/**
 * Namespaced identity of a book as a request stores it.
 *
 * Provider ids are only unique within a provider — Hardcover and Audible can
 * both hand out the same string — so the provider is part of the key rather
 * than decoration.
 */
const requestKey = (provider: unknown, providerId: unknown): string => {
  const source = typeof provider === 'string' ? provider.trim().toLowerCase() : '';
  const id = typeof providerId === 'string' ? providerId.trim() : '';
  return source && id ? `${source}:${id}` : '';
};

/**
 * Every identity a search result could have been requested under.
 *
 * Deliberately exact rather than fuzzy, and narrower than the library matcher:
 * an open request is about the entry someone actually clicked, not about
 * owning the work in some edition. More than one key comes back because the
 * two request payload builders name the provider differently — the metadata
 * one falls back to the literal 'metadata', the direct one uses the browse
 * source — and a result does not carry which path created any prior request.
 *
 * The browse source is spelled out rather than taken from `getBrowseSource`,
 * which throws on a book with neither field. This runs for every row of every
 * result set, so a malformed one must degrade to "not requested", not blow up
 * the list.
 */
export const bookRequestKeys = (book: Book): string[] => {
  const id = (book.provider_id || book.id || '').trim();
  if (!id) return [];

  const providers = new Set([book.provider || 'metadata', book.source || book.provider || '']);
  const keys = new Set<string>();
  for (const provider of providers) {
    const key = requestKey(provider, id);
    if (key) keys.add(key);
  }
  return [...keys];
};

/**
 * Keys of every book with a request still awaiting a decision.
 *
 * Only `pending` counts. A fulfilled request has become a download the button
 * already reports on, and a rejected or cancelled one is exactly the case
 * where asking again is the right thing to do.
 */
export const buildOpenRequestKeys = (records: RequestRecord[]): Set<string> => {
  const keys = new Set<string>();

  for (const record of records) {
    if (record.status !== 'pending') continue;
    const bookData = record.book_data;
    if (!bookData) continue;

    const key = requestKey(bookData.provider, bookData.provider_id);
    if (key) keys.add(key);
  }

  return keys;
};

/** Whether this book already has a request waiting on someone's decision. */
export const isBookRequested = (book: Book, openRequestKeys: Set<string>): boolean => {
  if (openRequestKeys.size === 0) return false;
  return bookRequestKeys(book).some((key) => openRequestKeys.has(key));
};
