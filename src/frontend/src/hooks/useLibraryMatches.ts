import { useState } from 'react';

import { getLibraryMatches } from '../services/api';
import type { Book } from '../types';
import type { LibraryMatch } from '../utils/libraryMatches';
import { booksLookupSignature, buildLibraryLookupPayload } from '../utils/libraryMatches';
import { useDependencyEffect } from './useMountEffect';

/**
 * Ask once per set of books which of them are already in an Audiobookshelf
 * library.
 *
 * A failed lookup resolves to no matches rather than an error: the badge is
 * advisory, so losing it should be invisible, never a blocked search.
 */
export const useLibraryMatches = (
  books: Book[],
  defaultContentType?: string,
): Record<string, LibraryMatch> => {
  const [matches, setMatches] = useState<Record<string, LibraryMatch>>({});
  const signature = booksLookupSignature(books, defaultContentType);

  useDependencyEffect(() => {
    if (!signature) {
      setMatches({});
      return undefined;
    }

    let cancelled = false;
    void getLibraryMatches(buildLibraryLookupPayload(books, defaultContentType))
      .then((response) => {
        if (!cancelled) {
          setMatches(response.enabled ? response.matches : {});
        }
      })
      .catch(() => {
        if (!cancelled) {
          setMatches({});
        }
      });

    return () => {
      cancelled = true;
    };
    // Keyed by the signature only: re-running on the array identity would
    // refetch on every parent render.
  }, [signature]);

  return matches;
};
