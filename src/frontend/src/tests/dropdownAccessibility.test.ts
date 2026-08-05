import { describe, expect, it } from 'vitest';

import { getSelectFieldAccessibleName } from '../utils/dropdownAccessibility';

const options = [
  { value: '', label: 'Use main provider' },
  { value: 'audible', label: 'Audible' },
];

describe('select field accessible names', () => {
  it('includes the field label and placeholder when no option is available', () => {
    expect(getSelectFieldAccessibleName('Broad topic', '', [], 'Select...')).toBe(
      'Broad topic: Select...',
    );
  });

  it('includes the field label and selected option', () => {
    expect(getSelectFieldAccessibleName('Metadata Provider', 'audible', options, 'Select...')).toBe(
      'Metadata Provider: Audible',
    );
  });

  it('uses the empty-value option label when it is the rendered summary', () => {
    expect(
      getSelectFieldAccessibleName('Audiobook Metadata Provider', '', options, 'Select...'),
    ).toBe('Audiobook Metadata Provider: Use main provider');
  });

  it('preserves native trigger text naming when no field label is supplied', () => {
    expect(getSelectFieldAccessibleName('', 'audible', options, 'Select...')).toBeUndefined();
  });
});
