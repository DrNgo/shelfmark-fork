import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

const styles = readFileSync(new URL('../styles.css', import.meta.url), 'utf8');

describe('mobile homepage layout', () => {
  it('keeps the initial search section near the top without viewport-height spacing', () => {
    const mobileInitialState = styles.match(
      /@media \(max-width: 639px\) \{\s*\.search-initial-state \{(?<declarations>[^}]*)\}/,
    );

    expect(mobileInitialState).not.toBeNull();
    expect(mobileInitialState?.groups?.declarations).toContain('padding-top: 1rem;');
    expect(mobileInitialState?.groups?.declarations).not.toMatch(/padding-top:\s*\d+(?:\.\d+)?vh/);
  });
});
