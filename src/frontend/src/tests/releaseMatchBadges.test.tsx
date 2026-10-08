import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';

import { ReleaseMatchBadges, FAN_TL_TOOLTIP } from '../components/ReleaseMatchBadges';
import { ReleaseRow } from '../components/ReleaseModal';
import type { Release } from '../types';

function release(releaseMatch?: Record<string, unknown>): Release {
  return {
    source: 'prowlarr',
    source_id: 'r-1',
    title: 'High School DxD - Volume 25',
    extra: releaseMatch ? { release_match: releaseMatch } : {},
  };
}

function payload(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    v: 1,
    volume: 'unknown',
    other_volume: null,
    medium: 'ebook',
    compatible: true,
    fan_marker: false,
    ...overrides,
  };
}

const render = (r: Release, compact = false): string =>
  renderToStaticMarkup(<ReleaseMatchBadges release={r} compact={compact} />);

describe('ReleaseMatchBadges', () => {
  it('names another volume', () => {
    expect(render(release(payload({ volume: 'other', other_volume: 25 })))).toContain('Vol 25');
  });

  it('flags an incompatible comic, an audiobook and a video', () => {
    expect(render(release(payload({ medium: 'comic', compatible: false })))).toContain(
      'Manga/Comic',
    );
    expect(render(release(payload({ medium: 'audio', compatible: false })))).toContain('Audiobook');
    expect(render(release(payload({ medium: 'video', compatible: false })))).toContain('Video');
  });

  it('renders nothing for a match, a compatible comic, an unknown or no data', () => {
    expect(render(release(payload({ volume: 'match' })))).toBe('');
    expect(render(release(payload({ medium: 'comic', compatible: true })))).toBe('');
    expect(render(release(payload()))).toBe('');
    expect(render(release())).toBe('');
    expect(render(release({ ...payload({ volume: 'other', other_volume: 25 }), v: 2 }))).toBe('');
  });

  it('shows the fan marker as a secondary badge with its tooltip', () => {
    const markup = render(release(payload({ volume: 'match', fan_marker: true })));

    expect(FAN_TL_TOOLTIP).toBe('The release name says this is a fan translation');
    expect(markup).toContain('Fan TL?');
    expect(markup).toContain(`title="${FAN_TL_TOOLTIP}"`);
  });

  it('renders plain text in the compact layout', () => {
    const markup = render(
      release(payload({ volume: 'other', other_volume: 25, fan_marker: true })),
      true,
    );

    expect(markup).toContain('Vol 25');
    expect(markup).toContain(`title="${FAN_TL_TOOLTIP}"`);
    expect(markup).not.toContain('rounded-lg');
  });

  it('separates several compact badges without orphan separators', () => {
    const markup = render(
      release(
        payload({
          volume: 'other',
          other_volume: 25,
          medium: 'comic',
          compatible: false,
          fan_marker: true,
        }),
      ),
      true,
    );
    const text = markup.replaceAll(/<[^>]+>/g, '');

    expect(text).toBe('Vol 25·Manga/Comic·Fan TL?');
  });
});

const renderRow = (r: Release): string =>
  renderToStaticMarkup(
    <ReleaseRow
      release={r}
      index={0}
      onDownload={async () => undefined}
      buttonState={{ text: 'Download', state: 'download' }}
      columns={[]}
      gridTemplate="minmax(0,2fr)"
      leadingCell={{ type: 'none' }}
      showReleaseSourceLinks={false}
    />,
  );

const clampedTitles = (markup: string): string[] =>
  [...markup.matchAll(/<p class="line-clamp-2[^"]*"[^>]*>.*?<\/p>/g)].map((m) => m[0]);

describe('ReleaseRow mismatch badges', () => {
  it('renders the badges outside the two-line title clamp in both layouts', () => {
    const markup = renderRow(release(payload({ volume: 'other', other_volume: 25 })));
    const titles = clampedTitles(markup);

    expect(titles).toHaveLength(2);
    expect(titles.every((title) => !title.includes('Vol 25'))).toBe(true);
    expect(markup.match(/Vol 25/g)).toHaveLength(2);
  });

  it('renders no badge line for a matching release', () => {
    const markup = renderRow(release(payload({ volume: 'match' })));

    expect(markup).not.toContain('Vol ');
    expect(markup).not.toContain('Fan TL?');
  });
});
