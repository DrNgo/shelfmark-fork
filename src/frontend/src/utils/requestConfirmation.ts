import type { Book, CreateRequestPayload } from '../types';
import { toStringValue } from './objectHelpers';

export const MAX_REQUEST_NOTE_LENGTH = 1000;

const toText = (value: unknown, fallback: string): string => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  return fallback;
};

const formatSourceLabel = (value: unknown): string => {
  const source = (toStringValue(value) ?? '').trim();
  if (!source) {
    return 'Unknown source';
  }
  return source
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
};

const buildSeriesLine = (name: string, position: number | null, count: number | null): string => {
  if (!name) return '';
  if (position != null) {
    return `#${position}${count ? ` of ${count}` : ''} in ${name}`;
  }
  return name;
};

export interface RequestConfirmationPreview {
  title: string;
  author: string;
  asin: string;
  isbn_10: string;
  isbn_13: string;
  content_type: string;
  year: string;
  seriesLine: string;
  preview: string;
  coverAspect: 'portrait' | 'square';
  releaseLine: string;
}

export const buildRequestConfirmationPreview = (
  payload: CreateRequestPayload,
): RequestConfirmationPreview => {
  const bookData = payload.book_data || {};
  const releaseData = payload.release_data || {};
  const requestLevel = payload.context?.request_level;

  const seriesLine = buildSeriesLine(
    toText(bookData.series_name, ''),
    typeof bookData.series_position === 'number' ? bookData.series_position : null,
    typeof bookData.series_count === 'number' ? bookData.series_count : null,
  );
  let preview = '';
  if (typeof bookData.preview === 'string') {
    preview = bookData.preview;
  } else if (typeof releaseData.preview === 'string') {
    preview = releaseData.preview;
  }
  const coverAspect =
    bookData.cover_aspect === 'square' || releaseData.cover_aspect === 'square'
      ? ('square' as const)
      : ('portrait' as const);

  return {
    title: toText(bookData.title ?? releaseData.title, 'Untitled'),
    author: toText(bookData.author ?? releaseData.author, 'Unknown author'),
    asin: toText(bookData.asin, ''),
    isbn_10: toText(bookData.isbn_10 ?? releaseData.isbn_10, ''),
    isbn_13: toText(bookData.isbn_13 ?? releaseData.isbn_13, ''),
    content_type: toText(payload.context?.content_type, ''),
    year: toText(bookData.year ?? releaseData.year, ''),
    seriesLine,
    preview,
    coverAspect,
    releaseLine:
      requestLevel === 'release'
        ? [
            typeof releaseData.format === 'string' && releaseData.format
              ? releaseData.format.toUpperCase()
              : null,
            typeof releaseData.size === 'string' && releaseData.size ? releaseData.size : null,
            formatSourceLabel(releaseData.source || payload.context?.source),
          ]
            .filter(Boolean)
            .join(' | ')
        : '',
  };
};

export const truncateRequestNote = (
  value: string,
  maxLength: number = MAX_REQUEST_NOTE_LENGTH,
): string => value.slice(0, maxLength);

export const enrichPreviewFromBook = (
  base: RequestConfirmationPreview,
  book: Book,
): RequestConfirmationPreview => {
  const seriesLine = buildSeriesLine(
    book.series_name ?? '',
    book.series_position ?? null,
    book.series_count ?? null,
  );
  if (!seriesLine && !book.year) return base;

  return {
    ...base,
    seriesLine: seriesLine || base.seriesLine,
    year: book.year && !base.year ? book.year : base.year,
  };
};

export const applyRequestNoteToPayload = (
  payload: CreateRequestPayload,
  note: string,
  allowNotes: boolean,
): CreateRequestPayload => {
  const trimmedNote = note.trim();
  const nextPayload: CreateRequestPayload = {
    ...payload,
  };

  if (allowNotes && trimmedNote) {
    nextPayload.note = trimmedNote;
  } else {
    delete nextPayload.note;
  }

  return nextPayload;
};
