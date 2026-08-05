import { describe, expect, it } from 'vitest';

import { buildUserSettingsPayload } from '../components/settings/users/settingsPayload';
import type { DeliveryPreferencesResponse } from '../services/api';

const searchPreferences: DeliveryPreferencesResponse = {
  tab: 'search_mode',
  keys: ['DEFAULT_DISCOVER_TOPIC'],
  fields: [],
  globalValues: {
    DEFAULT_DISCOVER_TOPIC: ['Science Fiction & Fantasy', 'Fantasy'],
  },
  userOverrides: {},
  effective: {},
};

describe('Audible topic preference payload', () => {
  it('preserves an explicit empty override and emits null after Reset', () => {
    expect(
      buildUserSettingsPayload({ DEFAULT_DISCOVER_TOPIC: [] }, new Set(), [searchPreferences]),
    ).toEqual({ DEFAULT_DISCOVER_TOPIC: [] });

    expect(buildUserSettingsPayload({}, new Set(), [searchPreferences])).toEqual({
      DEFAULT_DISCOVER_TOPIC: null,
    });
  });
});
