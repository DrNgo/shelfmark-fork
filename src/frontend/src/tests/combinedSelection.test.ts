import { describe, it, expect } from 'vitest';

import type { Book, Release } from '../types';
import {
  advanceCombinedSelection,
  combinedLegOptions,
  type CombinedSelectionState,
  completeCombinedSelection,
  retreatCombinedSelection,
  stagedDestinationKeyForPhase,
} from '../utils/combinedSelection';
import { buildReleaseDownloadPayload } from '../utils/releasePayload';

const book: Book = {
  id: 'hc-1',
  title: 'Overlord',
  author: 'Kugane Maruyama',
  provider: 'hardcover',
  provider_id: '886465',
};

const ebookRelease: Release = { source: 'prowlarr', source_id: 'ebook-1', title: 'Overlord.epub' };
const audiobookRelease: Release = {
  source: 'audiobookbay',
  source_id: 'audio-1',
  title: 'Overlord (Unabridged)',
};

const start: CombinedSelectionState = {
  phase: 'ebook',
  ebookMode: 'download',
  audiobookMode: 'download',
};

describe('combined selection keeps one library per leg', () => {
  it('survives Next → Back → Next → Download', () => {
    // Ebook step: pick a release and Light Novels, then Next.
    let state = advanceCombinedSelection(start, 'audiobook', book, ebookRelease, 'grimmory:5:8');
    expect(state.phase).toBe('audiobook');
    expect(stagedDestinationKeyForPhase(state, 'audiobook')).toBeUndefined();

    // Audiobook step: pick Kids, then Back — the ebook picker shows Light Novels again.
    state = retreatCombinedSelection(state, audiobookRelease, 'lib-kids');
    expect(state.phase).toBe('ebook');
    expect(stagedDestinationKeyForPhase(state, 'ebook')).toBe('grimmory:5:8');

    // Change the ebook library, Next — the audiobook picker shows Kids again.
    state = advanceCombinedSelection(state, 'audiobook', book, ebookRelease, 'grimmory:3:3');
    expect(stagedDestinationKeyForPhase(state, 'audiobook')).toBe('lib-kids');

    // Download from the audiobook step.
    state = completeCombinedSelection(state, book, audiobookRelease, 'lib-kids');
    expect(state.stagedEbook?.release).toBe(ebookRelease);
    expect(state.stagedAudiobook).toBe(audiobookRelease);
    expect(combinedLegOptions(state, 'ebook')).toEqual({ destinationKey: 'grimmory:3:3' });
    expect(combinedLegOptions(state, 'audiobook')).toEqual({ destinationKey: 'lib-kids' });
  });

  it('never sends one leg the other leg key', () => {
    const state = completeCombinedSelection(
      advanceCombinedSelection(start, 'audiobook', book, ebookRelease, 'grimmory:5:8'),
      book,
      audiobookRelease,
      'lib-kids',
    );

    const ebookPayload = buildReleaseDownloadPayload(
      book,
      ebookRelease,
      'ebook',
      combinedLegOptions(state, 'ebook'),
    );
    const audiobookPayload = buildReleaseDownloadPayload(
      book,
      audiobookRelease,
      'audiobook',
      combinedLegOptions(state, 'audiobook'),
    );

    expect(ebookPayload.destination_key).toBe('grimmory:5:8');
    expect(audiobookPayload.destination_key).toBe('lib-kids');
  });

  it('a skipped ebook step leaves the ebook leg without a key', () => {
    const state = completeCombinedSelection(
      advanceCombinedSelection(start, 'audiobook', book, null, undefined),
      book,
      audiobookRelease,
      'lib-kids',
    );

    expect(state.stagedEbook).toBeUndefined();
    expect(combinedLegOptions(state, 'ebook')).toEqual({ destinationKey: undefined });
    expect(combinedLegOptions(state, 'audiobook')).toEqual({ destinationKey: 'lib-kids' });
  });

  it('an audiobook-only flow never gives the ebook leg a key', () => {
    // The ebook leg is a request, so the selection starts on the audiobook step.
    const audiobookOnly: CombinedSelectionState = {
      ...start,
      phase: 'audiobook',
      ebookMode: 'request_book',
    };

    const state = completeCombinedSelection(audiobookOnly, book, audiobookRelease, 'lib-kids');

    expect(combinedLegOptions(state, 'ebook').destinationKey).toBeUndefined();
    expect(combinedLegOptions(state, 'audiobook').destinationKey).toBe('lib-kids');
  });

  it('an ebook-only flow stores the key on the ebook leg', () => {
    const ebookOnly: CombinedSelectionState = { ...start, audiobookMode: 'request_book' };

    const state = completeCombinedSelection(ebookOnly, book, ebookRelease, 'grimmory:5:8');

    expect(state.stagedEbook?.release).toBe(ebookRelease);
    expect(combinedLegOptions(state, 'ebook').destinationKey).toBe('grimmory:5:8');
    expect(combinedLegOptions(state, 'audiobook').destinationKey).toBeUndefined();
  });

  it('clearing a pick on Back keeps the stored library', () => {
    const forward = advanceCombinedSelection(
      start,
      'audiobook',
      book,
      ebookRelease,
      'grimmory:5:8',
    );

    const back = retreatCombinedSelection(forward, null, undefined);

    expect(back.stagedAudiobook).toBeUndefined();
    expect(stagedDestinationKeyForPhase(back, 'ebook')).toBe('grimmory:5:8');
    expect(stagedDestinationKeyForPhase(back, 'audiobook')).toBeUndefined();
  });

  it('does not modify the state it was given', () => {
    advanceCombinedSelection(start, 'audiobook', book, ebookRelease, 'grimmory:5:8');

    expect(start).toEqual({ phase: 'ebook', ebookMode: 'download', audiobookMode: 'download' });
  });
});
