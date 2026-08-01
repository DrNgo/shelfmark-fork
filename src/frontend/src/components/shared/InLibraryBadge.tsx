import type { LibraryMatch } from '../../utils/libraryMatches';
import { libraryMatchTooltip } from '../../utils/libraryMatches';

interface InLibraryBadgeProps {
  match?: LibraryMatch;
  className?: string;
  /** 'overlay' renders solid, for a fixed spot on cover artwork. */
  variant?: 'inline' | 'overlay';
}

/**
 * Marks a book that is already in an Audiobookshelf library.
 *
 * A bare check, with no text and no library name — in a dense result grid the
 * only thing worth spending a row of space on is "you have this". The tooltip
 * carries the edition for anyone who wants to know which copy is held.
 *
 * The overlay variant sits on cover artwork (the card views give badges a
 * fixed corner slot so their async arrival never reflows the card text), so it
 * needs a solid fill and a shadow to stay legible on any art.
 */
export function InLibraryBadge({ match, className = '', variant = 'inline' }: InLibraryBadgeProps) {
  if (!match) return null;

  const label = libraryMatchTooltip(match);
  const palette =
    variant === 'overlay'
      ? 'border-emerald-700 bg-emerald-600 text-white shadow-md'
      : 'border-emerald-600/40 bg-emerald-600/15 text-emerald-700 dark:text-emerald-300';

  return (
    <span
      className={`inline-flex items-center justify-center rounded-full border p-1 ${palette} ${className}`}
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
