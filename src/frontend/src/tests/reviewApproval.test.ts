import { describe, it, expect } from 'vitest';

import { reviewApproveOptions } from '../components/activity/reviewApproval';
import { buildFulfilAdminRequestBody } from '../services/requestApiHelpers';
import { destinationKeyToSend } from '../utils/downloadDestinations';

describe('reviewApproveOptions', () => {
  it('approving keeps the library pick', () => {
    expect(reviewApproveOptions('approve', 'grimmory:5:8')).toEqual({
      destinationKey: 'grimmory:5:8',
    });
  });

  it('browsing (before approve or for alternatives) keeps the library pick', () => {
    // The browse modal hides its own picker, so this is the only carrier.
    expect(reviewApproveOptions('browse', 'grimmory:5:8')).toEqual({
      browseOnly: true,
      destinationKey: 'grimmory:5:8',
    });
  });

  it('manual approval downloads nothing, so it carries no library', () => {
    expect(reviewApproveOptions('manual', 'grimmory:5:8')).toEqual({ manualApproval: true });
  });

  it('select → browse alternatives → fulfil sends the chosen library', () => {
    // Picked while the ebook list was still loading: the pick still travels.
    const options = reviewApproveOptions(
      'browse',
      destinationKeyToSend('ebook', 'grimmory:5:8', []),
    );

    const body = buildFulfilAdminRequestBody({
      release_data: { source: 'prowlarr', source_id: 'rel-42' },
      destination_key: options.destinationKey,
    });

    expect(body.destination_key).toBe('grimmory:5:8');
  });
});
