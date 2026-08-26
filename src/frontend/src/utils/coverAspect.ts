/**
 * The shape of a piece of cover artwork.
 *
 * This describes the *image*, not the media type of whatever it illustrates:
 * Audible supplies square art, every other metadata provider supplies 2:3
 * portrait covers. A download's `content_type` cannot stand in for it, because
 * one book record can back both an ebook and an audiobook download while
 * carrying a single cover image. See `coverAspectForContentType` in
 * `mediaType.ts` for the separate, release-level derivation.
 */
export type CoverAspect = 'portrait' | 'square';

/** Narrow an untrusted value (API payload, request snapshot) to a `CoverAspect`. */
export const toCoverAspect = (value: unknown): CoverAspect | undefined =>
  value === 'square' || value === 'portrait' ? value : undefined;

/** True for square artwork. Anything unknown reads as portrait, the default cover shape. */
export const isSquareCover = (value: unknown): boolean => value === 'square';

/** CSS `aspect-ratio` for a frame holding this artwork. */
export const coverAspectRatio = (value: unknown): string => (isSquareCover(value) ? '1/1' : '2/3');

/**
 * CSS `object-position` for artwork cropped by `object-cover`. Square art is
 * centered; portrait art is top-anchored so the title survives the crop.
 */
export const coverObjectPosition = (value: unknown): 'center' | 'top' =>
  isSquareCover(value) ? 'center' : 'top';

/**
 * Tailwind form of `coverObjectPosition`. The class names are spelled out
 * rather than built from it, because Tailwind only emits classes it can find
 * as literals in the source.
 */
export const coverObjectPositionClass = (value: unknown): string =>
  isSquareCover(value) ? 'object-center' : 'object-top';
