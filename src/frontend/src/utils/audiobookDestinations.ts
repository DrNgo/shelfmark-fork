export interface AudiobookDestination {
  key: string;
  name: string;
}

/**
 * Whether a library picker should be offered.
 *
 * Routing is audiobooks-only, and a single destination is not a choice — with
 * one configured library the picker would just be a control with one option.
 */
export const shouldShowDestinationPicker = (
  contentType: string | null | undefined,
  destinations: AudiobookDestination[],
): boolean => {
  return contentType === 'audiobook' && destinations.length > 1;
};

/**
 * Pick the initially selected key, dropping one that no longer exists.
 *
 * An empty string means "no explicit choice", which routes to the default
 * audiobook destination rather than to a library that has since been removed.
 */
export const resolveDefaultDestinationKey = (
  currentKey: string | null | undefined,
  destinations: AudiobookDestination[],
): string => {
  if (!currentKey) {
    return '';
  }
  return destinations.some((destination) => destination.key === currentKey) ? currentKey : '';
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
