import { describe, it, expect } from 'vitest';

import { buildFulfilAdminRequestBody } from '../services/requestApiHelpers';
import {
  createDestinationLoader,
  destinationDefaultLabel,
  destinationKeyToSend,
  type DownloadDestinationList,
  pickerDestinations,
  resolveDefaultDestinationKey,
  resolveSelectedDestinationKey,
  shouldShowDestinationPicker,
  withDestinationKey,
} from '../utils/downloadDestinations';

const DESTINATIONS = [
  { key: 'lib-fiction', name: 'Fiction' },
  { key: 'lib-kids', name: 'Kids' },
];

const EBOOK_DESTINATIONS = [
  { key: 'grimmory:3:3', name: 'Fiction' },
  { key: 'grimmory:5:8', name: 'Light Novels' },
];

describe('fulfil payload with a destination key', () => {
  it('includes the chosen library', () => {
    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      destination_key: 'lib-kids',
    });

    expect(body.destination_key).toBe('lib-kids');
  });

  it('carries an ebook library key the same way', () => {
    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      destination_key: 'grimmory:5:8',
    });

    expect(body.destination_key).toBe('grimmory:5:8');
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

  it('shows the picker for ebooks with more than one destination', () => {
    expect(shouldShowDestinationPicker('ebook', EBOOK_DESTINATIONS)).toBe(true);
  });

  it('hides the picker when only one destination is configured', () => {
    expect(shouldShowDestinationPicker('audiobook', [DESTINATIONS[0]])).toBe(false);
    expect(shouldShowDestinationPicker('ebook', [EBOOK_DESTINATIONS[0]])).toBe(false);
  });

  it('hides the picker when nothing is configured', () => {
    expect(shouldShowDestinationPicker('ebook', [])).toBe(false);
  });

  it('hides the picker for an unknown content type', () => {
    expect(shouldShowDestinationPicker(null, DESTINATIONS)).toBe(false);
    expect(shouldShowDestinationPicker('magazine', DESTINATIONS)).toBe(false);
  });
});

describe('destinationDefaultLabel', () => {
  it('names the default when the server knows it', () => {
    expect(destinationDefaultLabel('audiobook', 'Fiction')).toBe('Default (Fiction)');
  });

  it('keeps the existing audiobook label when the name is unknown', () => {
    expect(destinationDefaultLabel('audiobook', '')).toBe('Default audiobook destination');
  });

  it('labels the ebook default neutrally', () => {
    // The library a blank choice lands in depends on the target user's overrides.
    expect(destinationDefaultLabel('ebook', '')).toBe('Default ebook destination');
    expect(destinationDefaultLabel('ebook', '   ')).toBe('Default ebook destination');
  });
});

describe('resolveDefaultDestinationKey', () => {
  it('keeps the previously chosen library when it still exists', () => {
    expect(resolveDefaultDestinationKey('lib-kids', DESTINATIONS)).toBe('lib-kids');
  });

  it('falls back to no choice when the library is gone', () => {
    expect(resolveDefaultDestinationKey('lib-deleted', DESTINATIONS)).toBe('');
  });

  it('drops a key from the other format', () => {
    expect(resolveDefaultDestinationKey('lib-kids', EBOOK_DESTINATIONS)).toBe('');
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

const list = (name: string): DownloadDestinationList => ({
  destinations: [{ key: name, name }],
  defaultName: '',
});

describe('createDestinationLoader', () => {
  it('fetches each content type once and caches it separately', async () => {
    const calls: string[] = [];
    const load = createDestinationLoader(async (contentType) => {
      calls.push(contentType);
      return list(contentType);
    });

    expect(await load('ebook')).toEqual(list('ebook'));
    expect(await load('audiobook')).toEqual(list('audiobook'));
    expect(await load('ebook')).toEqual(list('ebook'));
    expect(calls).toEqual(['ebook', 'audiobook']);
  });

  it('never caches an empty list, so a Grimmory outage clears up on its own', async () => {
    // The server answers 200 [] while Grimmory is unreachable.
    const responses = [{ destinations: [], defaultName: '' }, list('ebook')];
    const load = createDestinationLoader(async () => responses.shift() ?? list('late'));

    expect(await load('ebook')).toEqual({ destinations: [], defaultName: '' });
    expect(await load('ebook')).toEqual(list('ebook'));
    expect(await load('ebook')).toEqual(list('ebook'));
    expect(responses).toEqual([]);
  });

  it('yields an empty list on error and retries next time', async () => {
    let attempts = 0;
    const load = createDestinationLoader(async (contentType) => {
      attempts += 1;
      if (attempts === 1) {
        throw new Error('offline');
      }
      return list(contentType);
    });

    expect(await load('ebook')).toEqual({ destinations: [], defaultName: '' });
    expect(await load('ebook')).toEqual(list('ebook'));
  });
});

// An explicit ebook pick is never erased in the browser: the list may still be
// loading, empty because Grimmory is down, or stale. The server verifies the key
// against a fresh read and fails closed, so the browser sends it unchanged.
describe('an explicit ebook pick survives the display list', () => {
  const loading: typeof EBOOK_DESTINATIONS = [];

  it('is kept while the list is still loading', () => {
    expect(resolveSelectedDestinationKey('ebook', 'grimmory:5:8', loading)).toBe('grimmory:5:8');
    expect(destinationKeyToSend('ebook', 'grimmory:5:8', loading)).toBe('grimmory:5:8');
  });

  it('is kept when the loaded list no longer has it', () => {
    expect(destinationKeyToSend('ebook', 'grimmory:9:9', EBOOK_DESTINATIONS)).toBe('grimmory:9:9');
  });

  it('keeps the picker visible so the admin can see and clear it', () => {
    expect(shouldShowDestinationPicker('ebook', loading, 'grimmory:5:8')).toBe(true);
    expect(shouldShowDestinationPicker('ebook', loading, '')).toBe(false);
  });

  it('lists an unlisted pick as its own option', () => {
    expect(pickerDestinations('ebook', 'grimmory:9:9', EBOOK_DESTINATIONS)).toEqual([
      ...EBOOK_DESTINATIONS,
      { key: 'grimmory:9:9', name: 'grimmory:9:9 (not in the current list)' },
    ]);
    expect(pickerDestinations('ebook', 'grimmory:5:8', EBOOK_DESTINATIONS)).toEqual(
      EBOOK_DESTINATIONS,
    );
    expect(pickerDestinations('ebook', '', EBOOK_DESTINATIONS)).toEqual(EBOOK_DESTINATIONS);
  });

  it('a blank ebook choice sends nothing', () => {
    expect(destinationKeyToSend('ebook', '  ', EBOOK_DESTINATIONS)).toBeUndefined();
    expect(destinationKeyToSend('ebook', undefined, EBOOK_DESTINATIONS)).toBeUndefined();
  });
});

describe('audiobook picks keep the existing fallback', () => {
  it('drops a key the list no longer has', () => {
    expect(resolveSelectedDestinationKey('audiobook', 'lib-gone', DESTINATIONS)).toBe('');
    expect(destinationKeyToSend('audiobook', 'lib-gone', DESTINATIONS)).toBeUndefined();
  });

  it('sends nothing while the picker is hidden', () => {
    expect(destinationKeyToSend('audiobook', 'lib-kids', [])).toBeUndefined();
    expect(destinationKeyToSend('audiobook', 'lib-kids', [DESTINATIONS[1]])).toBeUndefined();
    expect(shouldShowDestinationPicker('audiobook', [], 'lib-kids')).toBe(false);
  });

  it('sends a listed key', () => {
    expect(destinationKeyToSend('audiobook', 'lib-kids', DESTINATIONS)).toBe('lib-kids');
  });

  it('never lists an unknown audiobook key', () => {
    expect(pickerDestinations('audiobook', 'lib-gone', DESTINATIONS)).toEqual(DESTINATIONS);
  });
});
