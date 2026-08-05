import { describe, expect, it } from 'vitest';

import type { AudibleTopicNode } from '../services/api';
import { findTopicByPath, flattenTopicDescendants, topicPathsEqual } from '../utils/audibleTopics';

const root: AudibleTopicNode = {
  name: 'Science Fiction & Fantasy',
  path: ['Science Fiction & Fantasy'],
  children: [
    {
      name: 'Fantasy',
      path: ['Science Fiction & Fantasy', 'Fantasy'],
      children: [
        {
          name: 'Epic',
          path: ['Science Fiction & Fantasy', 'Fantasy', 'Epic'],
          children: [],
        },
      ],
    },
  ],
};

const tree: AudibleTopicNode[] = [root];

describe('Audible topic helpers', () => {
  it('starts descendants with All and uses relative breadcrumbs', () => {
    expect(flattenTopicDescendants(root)).toEqual([
      { label: 'All Science Fiction & Fantasy', path: ['Science Fiction & Fantasy'] },
      { label: 'Fantasy', path: ['Science Fiction & Fantasy', 'Fantasy'] },
      {
        label: 'Fantasy → Epic',
        path: ['Science Fiction & Fantasy', 'Fantasy', 'Epic'],
      },
    ]);
  });

  it('restores an exact saved path and rejects a missing one', () => {
    expect(findTopicByPath(tree, ['Science Fiction & Fantasy', 'Fantasy'])?.name).toBe('Fantasy');
    expect(findTopicByPath(tree, ['Science Fiction & Fantasy', 'Missing'])).toBeUndefined();
  });

  it('compares topic paths structurally and in order', () => {
    expect(topicPathsEqual(['Fantasy', 'Epic'], ['Fantasy', 'Epic'])).toBe(true);
    expect(topicPathsEqual(['Fantasy', 'Epic'], ['Epic', 'Fantasy'])).toBe(false);
    expect(topicPathsEqual(['Fantasy'], ['Fantasy', 'Epic'])).toBe(false);
  });
});
