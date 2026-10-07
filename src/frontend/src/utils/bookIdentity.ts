import type { Book } from '../types';

/**
 * The metadata identity a download carries (fork-only), so a post-upload hook
 * can tag the book it lands as. The server pairs provider/provider_id, and
 * canonicalizes the ISBN (an ISBN-10 becomes ISBN-13).
 */
export interface BookIdentityFields {
  provider?: string;
  provider_id?: string;
  isbn_13?: string;
  asin?: string;
}

/** Identity fields for a metadata book; a manual book has none. */
export const bookIdentityFields = (book: Book): BookIdentityFields => {
  if (book.provider === 'manual') {
    return {};
  }
  return {
    provider: book.provider,
    provider_id: book.provider_id,
    // `||`, not `??`: a blank ISBN-13 must not hide the ISBN-10.
    isbn_13: book.isbn_13 || book.isbn_10 || undefined,
    asin: book.asin,
  };
};
