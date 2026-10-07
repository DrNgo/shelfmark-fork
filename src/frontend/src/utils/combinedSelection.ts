import type { Book, ContentType, Release, RequestPolicyMode } from '../types';
import type { ReleaseDownloadOptions } from './releasePayload';

/** The ebook + audiobook selection in combined mode (both formats in one go). */
export interface CombinedSelectionState {
  phase: ContentType;
  ebookMode: RequestPolicyMode;
  audiobookMode: RequestPolicyMode;
  stagedEbook?: { book: Book; release: Release };
  stagedAudiobook?: Release;
  // Each leg keeps its own library (fork-only): a Grimmory key for the ebook,
  // an Audiobookshelf key for the audiobook. A leg never carries the other's.
  ebookDestinationKey?: string;
  audiobookDestinationKey?: string;
}

type DestinationKeys = Pick<
  CombinedSelectionState,
  'ebookDestinationKey' | 'audiobookDestinationKey'
>;

const withPhaseDestinationKey = (
  state: CombinedSelectionState,
  phase: ContentType,
  destinationKey: string | undefined,
): CombinedSelectionState =>
  phase === 'ebook'
    ? { ...state, ebookDestinationKey: destinationKey }
    : { ...state, audiobookDestinationKey: destinationKey };

/** The library a phase's picker shows when the admin (re)enters that phase. */
export const stagedDestinationKeyForPhase = (
  state: DestinationKeys,
  phase: ContentType,
): string | undefined =>
  phase === 'ebook' ? state.ebookDestinationKey : state.audiobookDestinationKey;

/** Next: stage the ebook pick and the current phase's library, move on. */
export const advanceCombinedSelection = (
  state: CombinedSelectionState,
  nextPhase: ContentType,
  book: Book,
  release: Release | null,
  destinationKey: string | undefined,
): CombinedSelectionState =>
  withPhaseDestinationKey(
    { ...state, phase: nextPhase, stagedEbook: release ? { book, release } : undefined },
    state.phase,
    destinationKey,
  );

/** Back: stage the audiobook pick and the current phase's library, return to the ebook. */
export const retreatCombinedSelection = (
  state: CombinedSelectionState,
  audiobookRelease: Release | null,
  destinationKey: string | undefined,
): CombinedSelectionState =>
  withPhaseDestinationKey(
    { ...state, phase: 'ebook', stagedAudiobook: audiobookRelease ?? undefined },
    state.phase,
    destinationKey,
  );

/** Download: stage the final phase's pick and library. */
export const completeCombinedSelection = (
  state: CombinedSelectionState,
  book: Book,
  release: Release | null,
  destinationKey: string | undefined,
): CombinedSelectionState =>
  withPhaseDestinationKey(
    state.phase === 'ebook'
      ? { ...state, stagedEbook: release ? { book, release } : undefined }
      : { ...state, stagedAudiobook: release ?? undefined },
    state.phase,
    destinationKey,
  );

/** Download options for one leg: that leg's own library, and nothing else. */
export const combinedLegOptions = (
  state: DestinationKeys,
  leg: ContentType,
): ReleaseDownloadOptions => ({ destinationKey: stagedDestinationKeyForPhase(state, leg) });
