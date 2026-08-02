import type { ContentType } from '../types';

/** The ebook/audiobook vocabulary shared across the library-match and request-payload code. */
export const MEDIA_TYPE_EBOOK: ContentType = 'ebook';
export const MEDIA_TYPE_AUDIOBOOK: ContentType = 'audiobook';

/** Normalize a free-form string into a valid `ContentType`, defaulting to ebook. */
export const toContentType = (value: string): ContentType => {
  return value.trim().toLowerCase() === MEDIA_TYPE_AUDIOBOOK
    ? MEDIA_TYPE_AUDIOBOOK
    : MEDIA_TYPE_EBOOK;
};

/** Human-readable label for a media type, for use inside a sentence (e.g. "Also in your library as an audiobook"). */
export const mediaTypeLabel = (mediaType: string): string =>
  mediaType === MEDIA_TYPE_AUDIOBOOK ? 'an audiobook' : 'an ebook';
