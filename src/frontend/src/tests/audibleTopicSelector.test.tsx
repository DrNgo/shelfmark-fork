import type { ReactElement, ReactNode } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';

import {
  AudibleTopicClearAction,
  AudibleTopicSelector,
} from '../components/settings/AudibleTopicSelector';

interface ClearActionElementProps {
  children: ReactNode;
  disabled: boolean;
  onClick: () => void;
}

const renderClearAction = (value: string[], onChange: (path: string[]) => void) =>
  AudibleTopicClearAction({ value, onChange }) as ReactElement<ClearActionElementProps> | null;

describe('AudibleTopicClearAction', () => {
  it('emits an explicit empty topic path', () => {
    const onChange = vi.fn();
    const action = renderClearAction(['Science Fiction & Fantasy', 'Fantasy'], onChange);

    action?.props.onClick();

    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('stays available for a saved value without taxonomy data', () => {
    const markup = renderToStaticMarkup(
      <AudibleTopicSelector
        value={['Science Fiction & Fantasy', 'Missing']}
        onChange={() => undefined}
      />,
    );
    const clearButton = markup.match(/<button[^>]*>Clear selection<\/button>/)?.[0];

    expect(clearButton).toBeDefined();
    expect(clearButton).not.toContain('disabled=""');
  });
});
