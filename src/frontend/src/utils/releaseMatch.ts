import type { ReleaseMatch } from '../types';
import { isRecord } from './objectHelpers';

// The version of `extra.release_match` this parser understands (backend RELEASE_MATCH_VERSION).
const RELEASE_MATCH_VERSION = 1;

const VOLUMES: ReadonlySet<unknown> = new Set(['match', 'other', 'unknown']);
const MEDIUMS: ReadonlySet<unknown> = new Set(['ebook', 'comic', 'audio', 'video', 'unknown']);

const isVolume = (value: unknown): value is ReleaseMatch['volume'] => VOLUMES.has(value);
const isMedium = (value: unknown): value is ReleaseMatch['medium'] => MEDIUMS.has(value);
const isPositiveInteger = (value: unknown): value is number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 1;

/**
 * The release's `extra.release_match`, or null when it is missing or malformed.
 *
 * The one parser for ranking and badges. A wrong version, an unknown volume or medium, or
 * non-boolean flags give null, which means today's behaviour. An invalid `other_volume`
 * (not a positive integer for another volume, or set on a release that is not another
 * volume) only makes the volume unknown: the medium and flags still stand.
 */
export function parseReleaseMatch(extra: unknown): ReleaseMatch | null {
  if (!isRecord(extra)) return null;
  const raw = extra.release_match;
  if (!isRecord(raw) || raw.v !== RELEASE_MATCH_VERSION) return null;

  const { volume, medium, compatible } = raw;
  const otherVolume = raw.other_volume;
  const fanMarker = raw.fan_marker;
  if (!isVolume(volume) || !isMedium(medium)) return null;
  if (typeof compatible !== 'boolean' || typeof fanMarker !== 'boolean') return null;

  if (volume === 'other') {
    if (isPositiveInteger(otherVolume)) {
      return { volume, other_volume: otherVolume, medium, compatible, fan_marker: fanMarker };
    }
  } else if (otherVolume == null) {
    return { volume, other_volume: null, medium, compatible, fan_marker: fanMarker };
  }
  return { volume: 'unknown', other_volume: null, medium, compatible, fan_marker: fanMarker };
}
