import { useState } from 'react';

import { getDownloadDestinations } from '../services/api';
import type { ContentType } from '../types';
import {
  createDestinationLoader,
  type DownloadDestinationList,
  EMPTY_DESTINATION_LIST,
} from '../utils/downloadDestinations';
import { useDependencyEffect } from './useMountEffect';

const loadDestinations = createDestinationLoader(getDownloadDestinations);

/**
 * The destinations an admin can route a download of `contentType` to.
 *
 * Pass `null` to skip the lookup (the viewer cannot choose). A list loaded for
 * the other format — combined mode just switched phase — is never returned.
 */
export const useDownloadDestinations = (
  contentType: ContentType | null,
): DownloadDestinationList => {
  const [loaded, setLoaded] = useState<{
    contentType: ContentType;
    list: DownloadDestinationList;
  } | null>(null);

  useDependencyEffect(() => {
    if (!contentType) {
      return undefined;
    }

    let cancelled = false;
    void loadDestinations(contentType).then((list) => {
      if (!cancelled) {
        setLoaded({ contentType, list });
      }
    });

    return () => {
      cancelled = true;
    };
  }, [contentType]);

  return loaded && loaded.contentType === contentType ? loaded.list : EMPTY_DESTINATION_LIST;
};
