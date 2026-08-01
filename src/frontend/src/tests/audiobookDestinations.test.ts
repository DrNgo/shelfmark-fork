import { describe, it, expect } from 'vitest';

import { buildFulfilAdminRequestBody } from '../services/requestApiHelpers';
import {
  resolveDefaultDestinationKey,
  shouldShowDestinationPicker,
  withDestinationKey,
} from '../utils/audiobookDestinations';

const DESTINATIONS = [
  { key: 'lib-fiction', name: 'Fiction' },
  { key: 'lib-kids', name: 'Kids' },
];

describe('fulfil payload with a destination key', () => {
  it('includes the chosen library', () => {
    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      destination_key: 'lib-kids',
    });

    expect(body.destination_key).toBe('lib-kids');
  });

  it('omits the key entirely when no library was chosen', () => {
    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
    });

    expect('destination_key' in body).toBe(false);
  });
});

describe('shouldShowDestinationPicker', () => {
  it('shows the picker for audiobooks with more than one destination', () => {
    expect(shouldShowDestinationPicker('audiobook', DESTINATIONS)).toBe(true);
  });

  it('hides the picker when only one destination is configured', () => {
    expect(shouldShowDestinationPicker('audiobook', [DESTINATIONS[0]])).toBe(false);
  });

  it('hides the picker when nothing is configured', () => {
    expect(shouldShowDestinationPicker('audiobook', [])).toBe(false);
  });

  it('hides the picker for ebooks', () => {
    expect(shouldShowDestinationPicker('ebook', DESTINATIONS)).toBe(false);
  });

  it('hides the picker for an unknown content type', () => {
    expect(shouldShowDestinationPicker(null, DESTINATIONS)).toBe(false);
  });
});

describe('resolveDefaultDestinationKey', () => {
  it('keeps the previously chosen library when it still exists', () => {
    expect(resolveDefaultDestinationKey('lib-kids', DESTINATIONS)).toBe('lib-kids');
  });

  it('falls back to no choice when the library is gone', () => {
    expect(resolveDefaultDestinationKey('lib-deleted', DESTINATIONS)).toBe('');
  });

  it('defaults to no choice, which routes to the default destination', () => {
    expect(resolveDefaultDestinationKey(null, DESTINATIONS)).toBe('');
  });
});

describe('withDestinationKey', () => {
  it('adds the chosen library to a direct-download payload', () => {
    expect(withDestinationKey({ source: 'prowlarr' }, 'lib-kids')).toEqual({
      source: 'prowlarr',
      destination_key: 'lib-kids',
    });
  });

  it('omits the key when no library was chosen', () => {
    expect('destination_key' in withDestinationKey({ source: 'prowlarr' }, undefined)).toBe(false);
  });

  it('omits the key for a blank choice rather than sending an empty one', () => {
    // '' is the picker's own value for "use the default destination", so
    // forwarding it would put a key on the wire that means nothing.
    expect('destination_key' in withDestinationKey({ source: 'prowlarr' }, '   ')).toBe(false);
  });

  it('leaves the payload it was given untouched', () => {
    const payload = { source: 'prowlarr' };

    withDestinationKey(payload, 'lib-kids');

    expect('destination_key' in payload).toBe(false);
  });
});
