export interface AudiobookDestination {
  key: string;
  name: string;
}

/**
 * Whether the approve panel should offer a library picker.
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
