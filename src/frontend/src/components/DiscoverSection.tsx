import { useState } from 'react';

import { useDependencyEffect } from '../hooks/useMountEffect';
import { getDiscoverRow, getMetadataBookInfo } from '../services/api';
import type { Book, ButtonStateInfo, ContentType } from '../types';
import { isMetadataBook } from '../types';
import { transformMetadataToBook } from '../utils/bookTransformers';
import { coverAspectRatio } from '../utils/coverAspect';
import type { DiscoverRowState } from '../utils/discoverRows';
import {
  applyRowError,
  applyRowResponse,
  buildDiscoverRowDefs,
  contentTypeForDiscoverDetails,
  initialRowStates,
  visibleRows,
} from '../utils/discoverRows';
import { isBookRequested } from '../utils/requestedBooks';
import { LibraryBadge, RequestedBadge } from './shared';

interface DiscoverSectionProps {
  contentType: ContentType | 'combined';
  /** The configured metadata providers, as App resolves them; rows pick their own. */
  metadataProvider: string | null;
  audiobookMetadataProvider: string | null;
  combinedMetadataProvider: string | null;
  preferredTopicPath: string[];
  preferredCoreTopicKey: string | null;
  openRequestKeys: Set<string>;
  getButtonState: (bookId: string) => ButtonStateInfo;
  /** Receives the tile's book with full details loaded. */
  onDetails: (book: Book) => void;
  onShowToast: (message: string, type: 'success' | 'error' | 'info') => void;
}

// Discover tiles hold Books that are not in search-results state, so details are
// loaded for the Book itself instead of an id looked up in the results.
async function loadDetails(book: Book, isCombined: boolean): Promise<Book | null> {
  if (!isMetadataBook(book)) return null;
  // See contentTypeForDiscoverDetails (discoverRows.ts) for why this is mode-dependent.
  const discoverContentType = contentTypeForDiscoverDetails(book, isCombined);
  const tagged = discoverContentType ? { ...book, content_type: discoverContentType } : book;
  try {
    const fullBook = await getMetadataBookInfo(tagged.provider, tagged.provider_id);
    return {
      ...tagged,
      description: fullBook.description || tagged.description,
      series_id: fullBook.series_id || tagged.series_id,
      series_name: fullBook.series_name,
      series_position: fullBook.series_position,
      series_count: fullBook.series_count,
    };
  } catch (error) {
    console.error('Failed to load book description, using discover data:', error);
    return tagged;
  }
}

const ACTIVE_STATES = new Set(['queued', 'resolving', 'locating', 'downloading', 'complete']);

interface DiscoverTileProps {
  book: Book;
  buttonState: ButtonStateInfo;
  requested: boolean;
  onDetails: (book: Book) => void;
}

const DiscoverTile = ({ book, buttonState, requested, onDetails }: DiscoverTileProps) => {
  const [imageError, setImageError] = useState(false);

  return (
    <button
      type="button"
      onClick={() => onDetails(book)}
      className="w-32 flex-none snap-start text-left"
      title={book.title}
    >
      {/* Audiobook art is square; portrait would letterbox it (same rule as CardView). */}
      <div className="relative w-32" style={{ aspectRatio: coverAspectRatio(book.cover_aspect) }}>
        {book.preview && !imageError ? (
          <img
            src={book.preview}
            alt={book.title}
            loading="lazy"
            onError={() => setImageError(true)}
            className="h-full w-full rounded-lg object-cover shadow-sm"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center rounded-lg bg-(--bg-soft) p-2 text-center text-xs opacity-70">
            {book.title}
          </div>
        )}
        <div className="absolute top-1 right-1 flex flex-col items-end gap-1">
          <LibraryBadge library={book.library} overlay />
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
  metadataProvider,
  audiobookMetadataProvider,
  combinedMetadataProvider,
  preferredTopicPath,
  preferredCoreTopicKey,
  openRequestKeys,
  getButtonState,
  onDetails,
  onShowToast,
}: DiscoverSectionProps) => {
  const [rows, setRows] = useState<DiscoverRowState[]>([]);
  const isCombined = contentType === 'combined';
  const audiobookProviderName = audiobookMetadataProvider ?? metadataProvider;
  let standardProviderName = metadataProvider;
  if (isCombined) standardProviderName = combinedMetadataProvider ?? metadataProvider;
  else if (contentType === 'audiobook') standardProviderName = audiobookProviderName;

  const showDetails = async (book: Book) => {
    const details = await loadDetails(book, isCombined);
    if (details) onDetails(details);
    else onShowToast('Failed to load book details', 'error');
  };

  useDependencyEffect(() => {
    const rowDefs = buildDiscoverRowDefs({
      contentType,
      standardProvider: standardProviderName,
      audiobookProvider: audiobookProviderName,
      hasPreferredTopic: preferredTopicPath.length > 0,
      preferredCoreKey: preferredCoreTopicKey,
    });
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
  }, [
    contentType,
    standardProviderName,
    audiobookProviderName,
    preferredTopicPath,
    preferredCoreTopicKey,
  ]);

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
                  className="w-32 flex-none animate-pulse rounded-lg bg-(--bg-soft)"
                  // Audible covers are square; match the skeleton so rows don't jump.
                  style={{ aspectRatio: coverAspectRatio(row.coverAspect) }}
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
                  onDetails={(tileBook) => void showDetails(tileBook)}
                />
              ))}
            </div>
          )}
        </section>
      ))}
    </div>
  );
};
