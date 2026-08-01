interface RequestedBadgeProps {
  className?: string;
  /** 'overlay' renders solid, for a fixed spot on cover artwork. */
  variant?: 'inline' | 'overlay';
}

const LABEL = 'Already requested — waiting on a decision';

/**
 * Marks a book that someone has an open request for.
 *
 * Amber rather than the in-library green, because the two say different
 * things: one is settled, the other is still waiting on an admin. Only
 * undecided requests reach here — a rejected one is precisely when asking
 * again makes sense.
 */
export function RequestedBadge({ className = '', variant = 'inline' }: RequestedBadgeProps) {
  const palette =
    variant === 'overlay'
      ? 'border-amber-700 bg-amber-600 text-white shadow-md'
      : 'border-amber-600/40 bg-amber-600/15 text-amber-700 dark:text-amber-300';

  return (
    <span
      className={`inline-flex items-center justify-center rounded-full border p-1 ${palette} ${className}`}
      title={LABEL}
      aria-label={LABEL}
      role="img"
    >
      <svg
        className="h-3 w-3"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
        strokeWidth={2}
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
        />
      </svg>
    </span>
  );
}
