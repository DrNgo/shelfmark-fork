import type { ContentType } from '../types';

/** One place an admin can route a download to (fork-only). */
export interface DownloadDestination {
  key: string;
  name: string;
}

/** `GET /api/download-destinations` for one content type. */
export interface DownloadDestinationList {
  destinations: DownloadDestination[];
  /** Name of the library a blank choice lands in, or '' when it cannot be known. */
  defaultName: string;
}

export const EMPTY_DESTINATION_LIST: DownloadDestinationList = {
  destinations: [],
  defaultName: '',
};

/**
 * Whether a library picker should be offered.
 *
 * Ebooks pick a Grimmory library, audiobooks an Audiobookshelf one. A single
 * destination is not a choice — the picker would be a control with one option —
 * unless an ebook already carries an explicit pick, which stays visible (and
 * clearable) whatever the list currently holds.
 */
export const shouldShowDestinationPicker = (
  contentType: string | null | undefined,
  destinations: DownloadDestination[],
  selectedKey?: string | null,
): boolean => {
  if (contentType !== 'ebook' && contentType !== 'audiobook') {
    return false;
  }
  if (destinations.length > 1) {
    return true;
  }
  return contentType === 'ebook' && Boolean((selectedKey ?? '').trim());
};

/**
 * Label for the picker's blank option. Ebooks always get the neutral label: a
 * fulfilled request runs as the requester, whose own settings decide the default.
 */
export const destinationDefaultLabel = (contentType: ContentType, defaultName: string): string => {
  const name = defaultName.trim();
  return name ? `Default (${name})` : `Default ${contentType} destination`;
};

/**
 * Pick the initially selected key, dropping one that no longer exists.
 *
 * An empty string means "no explicit choice", which routes to the default
 * destination rather than to a library that has since been removed.
 */
export const resolveDefaultDestinationKey = (
  currentKey: string | null | undefined,
  destinations: DownloadDestination[],
): string => {
  if (!currentKey) {
    return '';
  }
  return destinations.some((destination) => destination.key === currentKey) ? currentKey : '';
};

/**
 * The picker's current value.
 *
 * An explicit ebook pick is never blanked by the display list: the list may be
 * loading, empty while Grimmory is down, or stale, and the server verifies the
 * key against a fresh read and fails closed. Audiobooks keep the fallback of
 * `resolveDefaultDestinationKey`.
 */
export const resolveSelectedDestinationKey = (
  contentType: string | null | undefined,
  currentKey: string | null | undefined,
  destinations: DownloadDestination[],
): string => {
  if (contentType === 'ebook') {
    return (currentKey ?? '').trim();
  }
  return resolveDefaultDestinationKey(currentKey, destinations);
};

/**
 * The key a download or approval sends, or undefined for "use the default".
 *
 * Ebooks send any nonblank pick unchanged (see `resolveSelectedDestinationKey`);
 * audiobooks send only a listed key, and only while the picker is shown.
 */
export const destinationKeyToSend = (
  contentType: string | null | undefined,
  currentKey: string | null | undefined,
  destinations: DownloadDestination[],
): string | undefined => {
  if (contentType === 'ebook') {
    return resolveSelectedDestinationKey(contentType, currentKey, destinations) || undefined;
  }
  if (!shouldShowDestinationPicker(contentType, destinations)) {
    return undefined;
  }
  return resolveDefaultDestinationKey(currentKey, destinations) || undefined;
};

/** The picker's options: an ebook pick missing from the list is shown as its own option. */
export const pickerDestinations = (
  contentType: string | null | undefined,
  selectedKey: string,
  destinations: DownloadDestination[],
): DownloadDestination[] => {
  if (
    contentType !== 'ebook' ||
    !selectedKey ||
    destinations.some((destination) => destination.key === selectedKey)
  ) {
    return destinations;
  }
  return [...destinations, { key: selectedKey, name: `${selectedKey} (not in the current list)` }];
};

/**
 * Attach an admin's library choice to a direct-download payload.
 *
 * Copied rather than mutated, and omitted rather than blanked: '' is the
 * picker's own value for "use the default destination", so forwarding it would
 * put a key on the wire that means nothing. The server strips the key from a
 * non-admin's payload regardless, so this is presentation, not enforcement.
 */
export const withDestinationKey = <T extends object>(
  payload: T,
  destinationKey: string | null | undefined,
): T & { destination_key?: string } => {
  const key = (destinationKey ?? '').trim();
  return key ? { ...payload, destination_key: key } : { ...payload };
};

/**
 * Build a loader that fetches each content type's destinations once.
 *
 * Shared across every mounted picker: the lists change only when an admin
 * edits settings or Grimmory libraries. A failed lookup, and an empty list
 * (what the server answers while Grimmory is unreachable), are not cached —
 * the next caller retries. Both hide the picker instead of blocking the
 * download.
 */
export const createDestinationLoader = (
  fetchDestinations: (contentType: ContentType) => Promise<DownloadDestinationList>,
): ((contentType: ContentType) => Promise<DownloadDestinationList>) => {
  const cache = new Map<ContentType, Promise<DownloadDestinationList>>();
  return (contentType) => {
    let request = cache.get(contentType);
    if (!request) {
      request = fetchDestinations(contentType).then(
        (list) => {
          if (list.destinations.length === 0) {
            cache.delete(contentType);
          }
          return list;
        },
        () => {
          cache.delete(contentType);
          return EMPTY_DESTINATION_LIST;
        },
      );
      cache.set(contentType, request);
    }
    return request;
  };
};
