import type { Book } from '../types';

interface LibraryMatchItem {
  item_id: string;
  library_id: string;
  library_name: string;
  title: string;
  author: string;
  asin: string;
}

export interface LibraryMatch {
  libraries: string[];
  items: LibraryMatchItem[];
}

export interface LibraryMatchesResponse {
  enabled: boolean;
  stale: boolean;
  last_sync_at: string | null;
  matches: Record<string, LibraryMatch>;
}

export interface LibraryLookupBook {
  id: string;
  title: string;
  author: string;
  asin?: string;
}

/**
 * Reduce books to the fields the matcher uses, dropping any that cannot match.
 *
 * A book needs either both a title and an author, or an ASIN — anything less
 * has no key, so asking about it would only cost a round trip. An ASIN alone
 * is enough because it is a complete identity, which half a title+author key
 * is not.
 */
export const buildLibraryLookupPayload = (books: Book[]): LibraryLookupBook[] => {
  const seen = new Set<string>();
  const payload: LibraryLookupBook[] = [];

  for (const book of books) {
    const id = (book.id ?? '').trim();
    const title = (book.title ?? '').trim();
    const author = (book.author ?? '').trim();
    const asin = (book.asin ?? '').trim();
    if (!id || seen.has(id)) continue;
    if (!asin && (!title || !author)) continue;

    seen.add(id);
    payload.push(asin ? { id, title, author, asin } : { id, title, author });
  }

  return payload;
};

const NO_BOOKS: Book[] = [];

/**
 * Wrap a single title and author for the surfaces that ask about one book —
 * the request form and the approve panel.
 *
 * Returns a shared empty array when either half is missing, so callers can pass
 * the result straight into the lookup hook without churning its dependency.
 */
export const singleBookLookup = (
  id: string,
  title: string | undefined,
  author: string | undefined,
  asin?: string,
): Book[] => {
  const trimmedTitle = (title ?? '').trim();
  const trimmedAuthor = (author ?? '').trim();
  const trimmedAsin = (asin ?? '').trim();
  if (!trimmedAsin && (!trimmedTitle || !trimmedAuthor)) return NO_BOOKS;

  return [{ id, title: trimmedTitle, author: trimmedAuthor, asin: trimmedAsin || undefined }];
};

/** A stable key for a book list, so scrolling a result set refetches only once. */
export const booksLookupSignature = (books: Book[]): string =>
  buildLibraryLookupPayload(books)
    .map((book) => (book.asin ? `${book.id}#${book.asin}` : book.id))
    .join(',');

/** Short badge text naming where the book is held. */
export const describeLibraryMatch = (match: LibraryMatch): string => {
  const [first, ...rest] = match.libraries;
  if (!first) return 'In library';
  return rest.length > 0 ? `In ${first} +${rest.length}` : `In ${first}`;
};

/**
 * Full tooltip text, one line per held edition.
 *
 * "In library" is not "same recording": a 2021 rip and a 2024 re-recording are
 * both the same book, so the tooltip names the edition rather than asserting
 * the user already has the thing they were about to download.
 */
export const libraryMatchTooltip = (match: LibraryMatch): string =>
  match.items
    .map((item) => {
      const asin = item.asin ? ` (ASIN ${item.asin})` : '';
      return `${item.library_name}: ${item.title} — ${item.author}${asin}`;
    })
    .join('\n');
