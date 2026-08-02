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
  if (match.items.length === 0 && match.other_formats.length === 0) return null;

  const held = match.items.length > 0;
  const label = libraryMatchTooltip(match);

  // A cross-format holding is not the same claim as owning this edition, and it
  // does not lock the button, so it must not wear the same badge. Muted and
  // hollow reads as "related" where solid reads as "you have this".
  const palette = held
    ? (variant === 'overlay'
        ? 'border-emerald-700 bg-emerald-600 text-white shadow-md'
        : 'border-emerald-600/40 bg-emerald-600/15 text-emerald-700 dark:text-emerald-300')
    : (variant === 'overlay'
        ? 'border-slate-400 bg-slate-700/80 text-slate-100 shadow-md'
        : 'border-slate-400/40 bg-slate-400/15 text-slate-600 dark:text-slate-300');

  const icon = held ? (
    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
  ) : (
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth={2.5}
      d="M12 6.253v13m0-13C10.832 5.477 9.246 5 7.5 5S4.168 5.477 3 6.253v13C4.168 18.477 5.754 18 7.5 18s3.332.477 4.5 1.253m0-13C13.168 5.477 14.754 5 16.5 5c1.747 0 3.332.477 4.5 1.253v13C19.832 18.477 18.247 18 16.5 18c-1.746 0-3.332.477-4.5 1.253"
    />
  );

  return (
    <span
      className={`inline-flex items-center justify-center rounded-full border p-1 ${palette} ${className}`}
      title={label}
      aria-label={label}
      role="img"
    >
      <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        {icon}
      </svg>
    </span>
  );
}
