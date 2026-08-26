import type { ContentType } from '../types';
import type { CoverAspect } from './coverAspect';

/** The ebook/audiobook vocabulary shared across the library-match and request-payload code. */
export const MEDIA_TYPE_EBOOK: ContentType = 'ebook';
export const MEDIA_TYPE_AUDIOBOOK: ContentType = 'audiobook';

/** Normalize a free-form string into a valid `ContentType`, defaulting to ebook. */
export const toContentType = (value: string): ContentType => {
  return value.trim().toLowerCase() === MEDIA_TYPE_AUDIOBOOK
    ? MEDIA_TYPE_AUDIOBOOK
    : MEDIA_TYPE_EBOOK;
};

/**
 * Cover-art shape implied by a media type: audiobook art is square, book covers
 * are portrait. Release rows need this because release sources send no
 * per-release aspect hint, and `Book.cover_aspect` describes the metadata
 * record rather than the release art — in combined mode the book can be an
 * ebook record while the audiobook tab lists square audiobook covers.
 */
export const coverAspectForContentType = (contentType: ContentType): CoverAspect =>
  contentType === MEDIA_TYPE_AUDIOBOOK ? 'square' : 'portrait';

/** Human-readable label for a media type, for use inside a sentence (e.g. "Also in your library as an audiobook"). */
export const mediaTypeLabel = (mediaType: string): string =>
  mediaType === MEDIA_TYPE_AUDIOBOOK ? 'an audiobook' : 'an ebook';
