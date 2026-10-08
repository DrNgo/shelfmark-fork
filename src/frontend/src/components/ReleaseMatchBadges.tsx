import type { Release } from '../types';
import { parseReleaseMatch } from '../utils/releaseMatch';

export const FAN_TL_TOOLTIP = 'The release name says this is a fan translation';

interface MatchBadge {
  label: string;
  secondary: boolean;
  title?: string;
}

function getMatchBadges(release: Release): MatchBadge[] {
  const match = parseReleaseMatch(release.extra);
  if (!match) return [];

  const badges: MatchBadge[] = [];
  if (match.volume === 'other' && match.other_volume !== null) {
    badges.push({ label: `Vol ${match.other_volume}`, secondary: false });
  }
  if (match.medium === 'comic' && !match.compatible) {
    badges.push({ label: 'Manga/Comic', secondary: false });
  }
  if (match.medium === 'audio') {
    badges.push({ label: 'Audiobook', secondary: false });
  }
  if (match.medium === 'video') {
    badges.push({ label: 'Video', secondary: false });
  }
  if (match.fan_marker) {
    badges.push({ label: 'Fan TL?', secondary: true, title: FAN_TL_TOOLTIP });
  }
  return badges;
}

/**
 * Mismatch badges for a release row: another volume, a medium the book is not, and a
 * secondary "Fan TL?" marker. Rendered on their own line below the title, outside the
 * two-line title clamp; a matching release gets nothing. `compact` renders plain text for
 * the mobile layout, like other compact badges.
 */
export function ReleaseMatchBadges({
  release,
  compact = false,
}: {
  release: Release;
  compact?: boolean;
}) {
  const badges = getMatchBadges(release);
  if (badges.length === 0) return null;

  if (compact) {
    return (
      <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[10px]">
        {badges.map((badge, idx) => (
          <span key={badge.label} className="flex items-center gap-1.5">
            {idx > 0 && <span className="text-zinc-300 dark:text-zinc-600">·</span>}
            <span
              className={
                badge.secondary
                  ? 'text-zinc-500 dark:text-zinc-400'
                  : 'font-semibold text-amber-600 dark:text-amber-400'
              }
              title={badge.title}
            >
              {badge.label}
            </span>
          </span>
        ))}
      </p>
    );
  }

  return (
    <div className="mt-1 flex flex-wrap items-center gap-1">
      {badges.map((badge) => (
        <span
          key={badge.label}
          className={`rounded-lg px-1.5 py-0.5 text-[10px] font-semibold tracking-wide whitespace-nowrap sm:px-2 sm:text-[11px] ${
            badge.secondary
              ? 'bg-gray-500/20 text-gray-700 dark:text-gray-300'
              : 'bg-amber-500/20 text-amber-700 dark:text-amber-400'
          }`}
          title={badge.title}
        >
          {badge.label}
        </span>
      ))}
    </div>
  );
}
