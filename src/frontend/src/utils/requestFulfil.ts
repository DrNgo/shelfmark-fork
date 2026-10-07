import type { Book } from '../types';

const toOptionalText = (value: unknown): string | undefined => {
  if (typeof value === 'string' && value.trim()) {
    return value.trim();
  }
  if (typeof value === 'number' && Number.isFinite(value)) {
    return String(value);
  }
  return undefined;
};

const toOptionalNumber = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === 'string') {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return undefined;
};

const asRecord = (value: Record<string, unknown> | null | undefined): Record<string, unknown> => {
  if (value && typeof value === 'object') {
    return value;
  }
  return {};
};

export const bookFromRequestData = (bookData: Record<string, unknown> | null | undefined): Book => {
  const row = asRecord(bookData);
  const providerId = toOptionalText(row.provider_id);
  const title = toOptionalText(row.title) || 'Unknown title';

  return {
    id: providerId || title || 'request',
    title,
    author: toOptionalText(row.author) || 'Unknown author',
    source: toOptionalText(row.source),
    provider: toOptionalText(row.provider),
    provider_id: providerId,
    // Fork-only: the identity the release built from this book carries on.
    isbn_13: toOptionalText(row.isbn_13),
    isbn_10: toOptionalText(row.isbn_10),
    asin: toOptionalText(row.asin),
    preview: toOptionalText(row.preview),
    year: toOptionalText(row.year),
    series_name: toOptionalText(row.series_name),
    series_position: toOptionalNumber(row.series_position),
    subtitle: toOptionalText(row.subtitle),
    source_url: toOptionalText(row.source_url),
  };
};
