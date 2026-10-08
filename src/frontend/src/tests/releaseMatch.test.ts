import { describe, expect, it } from 'vitest';

import { parseReleaseMatch } from '../utils/releaseMatch';

const valid = {
  v: 1,
  volume: 'other',
  other_volume: 25,
  medium: 'ebook',
  compatible: true,
  fan_marker: false,
};

describe('parseReleaseMatch', () => {
  it('parses a version-1 payload', () => {
    expect(parseReleaseMatch({ release_match: valid })).toEqual({
      volume: 'other',
      other_volume: 25,
      medium: 'ebook',
      compatible: true,
      fan_marker: false,
    });
    expect(
      parseReleaseMatch({
        author: 'Ichiei Ishibumi',
        release_match: { ...valid, volume: 'match', other_volume: null, fan_marker: true },
      }),
    ).toEqual({
      volume: 'match',
      other_volume: null,
      medium: 'ebook',
      compatible: true,
      fan_marker: true,
    });
  });

  it('returns null without a payload', () => {
    for (const extra of [undefined, null, 'x', 5, [], {}, { release_match: null }]) {
      expect(parseReleaseMatch(extra)).toBeNull();
    }
  });

  it('rejects a wrong or missing version', () => {
    for (const v of [2, 0, '1', null, undefined]) {
      expect(parseReleaseMatch({ release_match: { ...valid, v } })).toBeNull();
    }
  });

  it('rejects unknown enum values', () => {
    expect(parseReleaseMatch({ release_match: { ...valid, volume: 'maybe' } })).toBeNull();
    expect(parseReleaseMatch({ release_match: { ...valid, medium: 'tape' } })).toBeNull();
    expect(parseReleaseMatch({ release_match: { ...valid, medium: 'Ebook' } })).toBeNull();
  });

  it('rejects non-boolean flags', () => {
    expect(parseReleaseMatch({ release_match: { ...valid, compatible: 'true' } })).toBeNull();
    expect(parseReleaseMatch({ release_match: { ...valid, fan_marker: 1 } })).toBeNull();
  });

  it('downgrades an invalid other volume to unknown and keeps the rest', () => {
    for (const otherVolume of [0, -1, 2.5, '3', null, undefined, Number.NaN]) {
      expect(
        parseReleaseMatch({
          release_match: { ...valid, other_volume: otherVolume, medium: 'audio', fan_marker: true },
        }),
      ).toEqual({
        volume: 'unknown',
        other_volume: null,
        medium: 'audio',
        compatible: true,
        fan_marker: true,
      });
    }
  });

  it('downgrades an other volume set on a release that is not another volume', () => {
    expect(
      parseReleaseMatch({ release_match: { ...valid, volume: 'match', other_volume: 3 } }),
    ).toEqual({
      volume: 'unknown',
      other_volume: null,
      medium: 'ebook',
      compatible: true,
      fan_marker: false,
    });
  });
});
