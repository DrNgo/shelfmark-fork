import { useMemo, useState } from 'react';

import { useLibraryMatches } from '../hooks/useLibraryMatches';
import { useDependencyEffect } from '../hooks/useMountEffect';
import { getDiscoverRow } from '../services/api';
import type { Book, ButtonStateInfo, ContentType } from '../types';
import { transformMetadataToBook } from '../utils/bookTransformers';
import type { DiscoverRowState } from '../utils/discoverRows';
import {
  applyRowError,
  applyRowResponse,
  getDiscoverRowsForProvider,
  initialRowStates,
  visibleRows,
} from '../utils/discoverRows';
import type { LibraryMatch } from '../utils/libraryMatches';
import { isBookRequested } from '../utils/requestedBooks';
import { InLibraryBadge, RequestedBadge } from './shared';

interface DiscoverSectionProps {
  contentType: ContentType | 'combined';
  providerName: string | null;
  openRequestKeys: Set<string>;
  getButtonState: (bookId: string) => ButtonStateInfo;
  onDetails: (book: Book) => void;
}

const ACTIVE_STATES = new Set(['queued', 'resolving', 'locating', 'downloading', 'complete']);

interface DiscoverTileProps {
  book: Book;
  buttonState: ButtonStateInfo;
  requested: boolean;
  libraryMatch: LibraryMatch | undefined;
  onDetails: (book: Book) => void;
}

const DiscoverTile = ({
  book,
  buttonState,
  requested,
  libraryMatch,
  onDetails,
}: DiscoverTileProps) => {
  const [imageError, setImageError] = useState(false);

  return (
    <button
      type="button"
      onClick={() => onDetails(book)}
      className="w-32 flex-none snap-start text-left"
      title={book.title}
    >
      <div className="relative">
        {book.preview && !imageError ? (
          <img
            src={book.preview}
            alt={book.title}
            loading="lazy"
            onError={() => setImageError(true)}
            className="h-48 w-32 rounded-lg object-cover shadow-sm"
          />
        ) : (
          <div className="flex h-48 w-32 items-center justify-center rounded-lg bg-(--bg-soft) p-2 text-center text-xs opacity-70">
            {book.title}
          </div>
        )}
        <div className="absolute top-1 right-1 flex flex-col items-end gap-1">
          {libraryMatch && <InLibraryBadge match={libraryMatch} variant="overlay" />}
          {requested && <RequestedBadge variant="overlay" />}
          {ACTIVE_STATES.has(buttonState.state) && (
            <span className="rounded-sm bg-black/70 px-1.5 py-0.5 text-xs text-white">
              {buttonState.text}
            </span>
          )}
        </div>
      </div>
      <div className="mt-1 truncate text-sm">{book.title}</div>
      <div className="truncate text-xs opacity-70">{book.author || ''}</div>
    </button>
  );
};

export const DiscoverSection = ({
  contentType,
  providerName,
  openRequestKeys,
  getButtonState,
  onDetails,
}: DiscoverSectionProps) => {
  const [rows, setRows] = useState<DiscoverRowState[]>([]);

  useDependencyEffect(() => {
    const rowDefs = getDiscoverRowsForProvider(providerName);
    if (rowDefs.length === 0) {
      setRows([]);
      return undefined;
    }
    let cancelled = false;
    setRows(initialRowStates(rowDefs));

    rowDefs.forEach((def) => {
      void getDiscoverRow(contentType, def.key)
        .then((response) => {
          if (cancelled) return;
          const books = response.books.map(transformMetadataToBook);
          setRows((current) => applyRowResponse(current, def.key, response.label, books));
        })
        .catch(() => {
          if (cancelled) return;
          setRows((current) => applyRowError(current, def.key));
        });
    });

    return () => {
      cancelled = true;
    };
  }, [contentType, providerName]);

  const allBooks = useMemo(() => rows.flatMap((row) => row.books ?? []), [rows]);
  // Record keyed by book.id (buildLibraryLookupPayload uses book.id).
  const libraryMatches = useLibraryMatches(allBooks);

  const rendered = visibleRows(rows);
  if (rendered.length === 0) {
    return null;
  }

  return (
    <div className="mx-auto w-full max-w-6xl px-4">
      {rendered.map((row) => (
        <section key={row.key} className="mb-8" aria-label={row.label}>
          <h2 className="mb-3 text-lg font-semibold">{row.label}</h2>
          {row.books === null ? (
            <div className="flex gap-4 overflow-hidden">
              {Array.from({ length: 6 }, (_, i) => (
                <div
                  key={i}
                  className="h-48 w-32 flex-none animate-pulse rounded-lg bg-(--bg-soft)"
                />
              ))}
            </div>
          ) : (
            <div className="flex snap-x gap-4 overflow-x-auto pb-2">
              {row.books.map((book) => (
                <DiscoverTile
                  key={book.id}
                  book={book}
                  buttonState={getButtonState(book.id)}
                  requested={isBookRequested(book, openRequestKeys)}
                  libraryMatch={libraryMatches[book.id]}
                  onDetails={onDetails}
                />
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
};
