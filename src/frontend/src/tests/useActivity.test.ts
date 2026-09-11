import { describe, it, expect } from 'vitest';

import { mapHistoryRowToActivityItem } from '../hooks/useActivity';
import { getActivityErrorMessage } from '../hooks/useActivity.helpers';
import type { ActivityHistoryItem } from '../services/api';

describe('useActivity helpers', () => {
  it('returns the backend error message when present', () => {
    const error = new Error('User identity unavailable for activity workflow');

    expect(getActivityErrorMessage(error, 'Failed to clear item')).toBe(
      'User identity unavailable for activity workflow',
    );
  });

  it('falls back to the provided message for non-error values', () => {
    expect(getActivityErrorMessage(null, 'Failed to clear item')).toBe('Failed to clear item');
  });
});

const makeHistoryRow = (download: Record<string, unknown>): ActivityHistoryItem => ({
  id: 'download:task-1',
  user_id: 1,
  item_type: 'download',
  item_key: 'download:task-1',
  dismissed_at: '2026-09-11T10:00:00+00:00',
  snapshot: { kind: 'download', download },
  origin: 'direct',
  final_status: 'complete',
  terminal_at: '2026-09-11T09:00:00+00:00',
  request_id: null,
  source_id: 'task-1',
});

describe('mapHistoryRowToActivityItem', () => {
  it('carries the square aspect hint of audiobook artwork', () => {
    const item = mapHistoryRowToActivityItem(
      makeHistoryRow({ id: 'task-1', title: 'An Audiobook', author: 'A', cover_aspect: 'square' }),
      'user',
    );

    expect(item.coverAspect).toBe('square');
  });

  it('leaves the aspect unset when the snapshot carries no hint', () => {
    const item = mapHistoryRowToActivityItem(
      makeHistoryRow({ id: 'task-1', title: 'A Book', author: 'A' }),
      'user',
    );

    expect(item.coverAspect).toBeUndefined();
  });
});
