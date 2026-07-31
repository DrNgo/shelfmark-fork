import type { LibraryMatch } from '../../utils/libraryMatches';
import { describeLibraryMatch, libraryMatchTooltip } from '../../utils/libraryMatches';

interface InLibraryBadgeProps {
  match?: LibraryMatch;
  className?: string;
}

/**
 * Marks a book that is already in an Audiobookshelf library.
 *
 * Advisory only — nothing about it disables downloading. Re-acquiring a better
 * edition is legitimate, and the tooltip names the edition already held so the
 * choice is an informed one.
 */
export function InLibraryBadge({ match, className = '' }: InLibraryBadgeProps) {
  if (!match) return null;

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border border-emerald-600/40 bg-emerald-600/15 px-1.5 py-0.5 text-xs font-medium whitespace-nowrap text-emerald-700 dark:text-emerald-300 ${className}`}
      title={libraryMatchTooltip(match)}
    >
      <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
      </svg>
      {describeLibraryMatch(match)}
    </span>
  );
}
