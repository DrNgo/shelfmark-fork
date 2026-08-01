import type { LibraryMatch } from '../../utils/libraryMatches';
import { libraryMatchTooltip } from '../../utils/libraryMatches';

interface InLibraryBadgeProps {
  match?: LibraryMatch;
  className?: string;
}

/**
 * Marks a book that is already in an Audiobookshelf library.
 *
 * A bare check, with no text and no library name — in a dense result grid the
 * only thing worth spending a row of space on is "you have this". The tooltip
 * carries the edition for anyone who wants to know which copy is held.
 */
export function InLibraryBadge({ match, className = '' }: InLibraryBadgeProps) {
  if (!match) return null;

  const label = libraryMatchTooltip(match);

  return (
    <span
      className={`inline-flex items-center justify-center rounded-full border border-emerald-600/40 bg-emerald-600/15 p-1 text-emerald-700 dark:text-emerald-300 ${className}`}
      title={label}
      aria-label={label}
      role="img"
    >
      <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
      </svg>
    </span>
  );
}
