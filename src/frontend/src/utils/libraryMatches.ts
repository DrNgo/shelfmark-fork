import type { Book, ButtonStateInfo } from '../types';

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

/**
 * Full tooltip text, one line per held edition.
 *
 * Names the edition but never the library holding it: which shelf a book sits
 * on is the operator's filing concern, not something a reader deciding whether
 * to grab a copy needs. The edition still matters, because "in library" is not
 * "same recording" — a 2021 rip and a 2024 re-recording are both the same book.
 */
export const libraryMatchTooltip = (match: LibraryMatch): string =>
  match.items
    .map((item, index) => {
      const asin = item.asin ? ` (ASIN ${item.asin})` : '';
      const prefix = index === 0 ? 'Already in your library: ' : '';
      return `${prefix}${item.title} — ${item.author}${asin}`;
    })
    .join('\n');

const IN_LIBRARY_BUTTON_STATE: ButtonStateInfo = { state: 'blocked', text: 'In library' };

/**
 * Turn the acquire action into a block when the book is already held.
 *
 * Reuses the existing `blocked` state rather than adding a parallel disabled
 * path, so both button variants inherit the lock icon, the greyed styling and
 * the click guard they already apply to a policy block.
 *
 * Only an offerable action is replaced. A download that is queued, running,
 * finished or failed keeps its own state: the index refreshes on a timer, so a
 * grab that just completed will match itself minutes later, and swapping
 * "Downloaded" for "In library" would erase the outcome the user was waiting on.
 */
export const applyInLibraryLock = (
  buttonState: ButtonStateInfo,
  isInLibrary: boolean,
): ButtonStateInfo =>
  isInLibrary && buttonState.state === 'download' ? IN_LIBRARY_BUTTON_STATE : buttonState;
