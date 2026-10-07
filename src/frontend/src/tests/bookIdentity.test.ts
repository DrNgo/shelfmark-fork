import { describe, it, expect } from 'vitest';

import type { Book } from '../types/index';
import { bookIdentityFields } from '../utils/bookIdentity';

const book: Book = {
  id: 'book-1',
  title: 'Example Title',
  author: 'Example Author',
  provider: 'hardcover',
  provider_id: '886465',
};

describe('bookIdentityFields', () => {
  it('falls back to the ISBN-10 when the ISBN-13 is blank', () => {
    expect(bookIdentityFields({ ...book, isbn_13: '', isbn_10: '0316005142' }).isbn_13).toBe(
      '0316005142',
    );
  });

  it('prefers the ISBN-13', () => {
    expect(
      bookIdentityFields({ ...book, isbn_13: '9780316005142', isbn_10: '0316005142' }).isbn_13,
    ).toBe('9780316005142');
  });

  it('leaves the ISBN undefined when both are blank', () => {
    expect(bookIdentityFields({ ...book, isbn_13: '', isbn_10: '' }).isbn_13).toBeUndefined();
  });
});
