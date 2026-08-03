import { describe, expect, it } from 'vitest';

import { coverAspectForContentType } from '../utils/mediaType';

describe('coverAspectForContentType', () => {
  it('treats audiobook art as square', () => {
    expect(coverAspectForContentType('audiobook')).toBe('square');
  });

  it('treats ebook art as portrait', () => {
    expect(coverAspectForContentType('ebook')).toBe('portrait');
  });
});
