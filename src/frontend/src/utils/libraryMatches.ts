import type { Book, ButtonStateInfo } from '../types';

interface LibraryMatchItem {
  source: string;
  media_type: string;
  item_id: string;
  library_id: string;
  library_name: string;
  title: string;
  author: string;
  asin: string;
  isbn13: string;
}

export interface LibraryMatch {
  libraries: string[];
  /** Holdings in the format being browsed. These drive the badge and the lock. */
  items: LibraryMatchItem[];
  /** Holdings in another format. Worth mentioning, never worth blocking on. */
  other_formats: LibraryMatchItem[];
}

export interface LibraryMatchesResponse {
  enabled: boolean;
  stale: boolean;
  last_sync_at: string | null;
  sources: Record<
    string,
    { enabled: boolean; stale: boolean; last_sync_at: string | null; item_count: number }
  >;
  matches: Record<string, LibraryMatch>;
}

export interface LibraryLookupBook {
  id: string;
  title?: string;
  author?: string;
  asin?: string;
  isbn_10?: string;
  isbn_13?: string;
  content_type?: string;
}

/**
 * Reduce books to the fields the matcher uses, dropping any that cannot match.
 *
 * A book needs a title and an author, or an ASIN, or an ISBN. Anything less has
 * no key, so asking about it would only cost a round trip. An ASIN or ISBN alone
 * is enough because each is a complete identity, which half a title+author key
 * is not.
 *
 * `defaultContentType` fills in for books that carry no content type of their
 * own, so a surface showing one format classifies its results correctly even
 * when the metadata provider omits the field.
 */
export const buildLibraryLookupPayload = (
  books: Book[],
  defaultContentType?: string,
): LibraryLookupBook[] => {
  const seen = new Set<string>();
  const payload: LibraryLookupBook[] = [];

  for (const book of books) {
    const id = (book.id ?? '').trim();
    const title = (book.title ?? '').trim();
    const author = (book.author ?? '').trim();
    const asin = (book.asin ?? '').trim();
    const isbn13 = (book.isbn_13 ?? '').trim();
    const isbn10 = (book.isbn_10 ?? '').trim();
    const contentType = (book.content_type ?? defaultContentType ?? '').trim();
    if (!id || seen.has(id)) continue;
    if (!asin && !isbn13 && !isbn10 && (!title || !author)) continue;

    seen.add(id);
    const entry: LibraryLookupBook = { id };
    if (title) entry.title = title;
    if (author) entry.author = author;
    if (asin) entry.asin = asin;
    if (isbn13) entry.isbn_13 = isbn13;
    if (isbn10) entry.isbn_10 = isbn10;
    if (contentType) entry.content_type = contentType;
    payload.push(entry);
  }

  return payload;
};

const NO_BOOKS: Book[] = [];

/**
 * Wrap a single book for the surfaces that ask about one — the details modal,
 * the request form and the approve panel.
 *
 * `contentType` is not optional in practice: the backend reads a missing content
 * type as "ebook", so an audiobook surface that omits it would match against the
 * ebook library and file its real audiobook holding under other_formats.
 *
 * The ISBN is stored as `isbn_13` regardless of which spelling it came in as —
 * the backend canonicalizes ISBN-10 to ISBN-13 anyway, so picking one field here
 * saves callers deciding which they hold.
 *
 * Returns a shared empty array when there is no usable key, so callers can pass
 * the result straight into the lookup hook without churning its dependency.
 */
export const singleBookLookup = (
  id: string,
  title: string | undefined,
  author: string | undefined,
  asin?: string,
  isbn?: string,
  contentType?: string,
): Book[] => {
  const trimmedTitle = (title ?? '').trim();
  const trimmedAuthor = (author ?? '').trim();
  const trimmedAsin = (asin ?? '').trim();
  const trimmedIsbn = (isbn ?? '').trim();
  if (!trimmedAsin && !trimmedIsbn && (!trimmedTitle || !trimmedAuthor)) return NO_BOOKS;

  return [
    {
      id,
      title: trimmedTitle,
      author: trimmedAuthor,
      asin: trimmedAsin || undefined,
      isbn_13: trimmedIsbn || undefined,
      content_type: contentType || undefined,
    },
  ];
};

/** A stable key for a book list, so scrolling a result set refetches only once. */
export const booksLookupSignature = (books: Book[], defaultContentType?: string): string =>
  buildLibraryLookupPayload(books, defaultContentType)
    .map((book) =>
      [book.id, book.asin ?? '', book.isbn_13 ?? book.isbn_10 ?? '', book.content_type ?? ''].join(
        '#',
      ),
    )
    .join(',');

const describeLibraryMatchItem = (item: LibraryMatchItem): string => {
  const asin = item.asin ? ` (ASIN ${item.asin})` : '';
  return `${item.title} — ${item.author}${asin}`;
};

/**
 * Full tooltip text, one line per held edition.
 *
 * Names the edition but never the library holding it: which shelf a book sits on
 * is the operator's filing concern. Cross-format holdings are labelled as such,
 * so "you have the audiobook" never reads as "you have this".
 */
export const libraryMatchTooltip = (match: LibraryMatch): string => {
  const lines = match.items.map(
    (item, index) =>
      `${index === 0 ? 'Already in your library: ' : ''}${describeLibraryMatchItem(item)}`,
  );

  lines.push(
    ...match.other_formats.map(
      (item, index) =>
        `${index === 0 ? `Also in your library as ${item.media_type === 'audiobook' ? 'an audiobook' : 'an ebook'}: ` : ''}${describeLibraryMatchItem(item)}`,
    ),
  );

  return lines.join('\n');
};

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

/** Whether the book is held in the very format being browsed. */
export const isHeldInFormat = (match: LibraryMatch | undefined): boolean =>
  (match?.items.length ?? 0) > 0;
