import { useState } from 'react';

import { getAudiobookDestinations } from '../services/api';
import type { AudiobookDestination } from '../utils/audiobookDestinations';
import { useDependencyEffect } from './useMountEffect';

/**
 * Shared across every mounted approve panel: the destination map changes only
 * when an admin edits settings, and one request per activity card would be a
 * burst of identical calls every time the sidebar renders.
 */
let cachedRequest: Promise<AudiobookDestination[]> | null = null;

const loadDestinations = (): Promise<AudiobookDestination[]> => {
  cachedRequest ??= getAudiobookDestinations().catch(() => {
    // A failed lookup must not wedge the cache — the next panel retries, and
    // an empty list simply hides the picker instead of blocking the approval.
    cachedRequest = null;
    return [];
  });
  return cachedRequest;
};

export const useAudiobookDestinations = (enabled: boolean): AudiobookDestination[] => {
  const [destinations, setDestinations] = useState<AudiobookDestination[]>([]);

  useDependencyEffect(() => {
    if (!enabled) {
      return undefined;
    }

    let cancelled = false;
    void loadDestinations().then((loaded) => {
      if (!cancelled) {
        setDestinations(loaded);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [enabled]);

  return destinations;
};
