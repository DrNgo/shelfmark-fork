import { describe, it, expect } from 'vitest';

import {
  coverAspectRatio,
  coverObjectPosition,
  coverObjectPositionClass,
  isSquareCover,
  toCoverAspect,
} from '../utils/coverAspect';

describe('toCoverAspect', () => {
  it('passes through the two known aspects', () => {
    expect(toCoverAspect('square')).toBe('square');
    expect(toCoverAspect('portrait')).toBe('portrait');
  });

  it('rejects anything else rather than guessing', () => {
    expect(toCoverAspect(undefined)).toBeUndefined();
    expect(toCoverAspect(null)).toBeUndefined();
    expect(toCoverAspect('Square')).toBeUndefined();
    expect(toCoverAspect('')).toBeUndefined();
    expect(toCoverAspect(1)).toBeUndefined();
    expect(toCoverAspect({ cover_aspect: 'square' })).toBeUndefined();
  });
});

describe('isSquareCover', () => {
  it('is true only for square art', () => {
    expect(isSquareCover('square')).toBe(true);
  });

  it('treats portrait and anything unknown as not square', () => {
    expect(isSquareCover('portrait')).toBe(false);
    expect(isSquareCover(undefined)).toBe(false);
    expect(isSquareCover('Square')).toBe(false);
  });
});

describe('coverAspectRatio', () => {
  it('maps aspects to their CSS ratio', () => {
    expect(coverAspectRatio('square')).toBe('1/1');
    expect(coverAspectRatio('portrait')).toBe('2/3');
    expect(coverAspectRatio(undefined)).toBe('2/3');
  });
});

describe('coverObjectPosition', () => {
  it('centers square art and top-anchors portrait art', () => {
    expect(coverObjectPosition('square')).toBe('center');
    expect(coverObjectPosition('portrait')).toBe('top');
    expect(coverObjectPosition(undefined)).toBe('top');
  });

  it('keeps the Tailwind class form in step with the CSS value', () => {
    for (const aspect of ['square', 'portrait', undefined]) {
      expect(coverObjectPositionClass(aspect)).toBe(`object-${coverObjectPosition(aspect)}`);
    }
  });
});
