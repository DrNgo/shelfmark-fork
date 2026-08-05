import { useMemo, useState } from 'react';

import { useMountEffect } from '../../hooks/useMountEffect';
import { getAudibleTopics, type AudibleTopicsResponse } from '../../services/api';
import type { SelectFieldConfig, SelectOption } from '../../types/settings';
import {
  createAudibleTopicRequestCoordinator,
  findTopicByPath,
  flattenTopicDescendants,
  topicPathsEqual,
  type AudibleTopicRequest,
} from '../../utils/audibleTopics';
import { SelectField } from './fields';

interface AudibleTopicSelectorProps {
  value: string[];
  onChange: (path: string[]) => void;
  disabled?: boolean;
}

const pathValue = (path: string[]): string => JSON.stringify(path);
const pathLabel = (path: string[]): string => path.join(' → ');

const broadField = (options: SelectOption[]): SelectFieldConfig => ({
  type: 'SelectField',
  key: 'audible_topic_broad',
  label: 'Broad topic',
  value: '',
  options,
});

const descendantField = (options: SelectOption[]): SelectFieldConfig => ({
  type: 'SelectField',
  key: 'audible_topic_descendant',
  label: 'Topic or subgenre',
  value: '',
  options,
});

export const AudibleTopicSelector = ({
  value,
  onChange,
  disabled = false,
}: AudibleTopicSelectorProps) => {
  const [response, setResponse] = useState<AudibleTopicsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [requestCoordinator] = useState(() =>
    createAudibleTopicRequestCoordinator<AudibleTopicsResponse>(),
  );

  const settleRequest = (request: AudibleTopicRequest<AudibleTopicsResponse>) => {
    void request.promise
      .then((nextResponse) => {
        if (requestCoordinator.isCurrent(request)) {
          setResponse(nextResponse);
          setError(null);
        }
      })
      .catch((reason: unknown) => {
        if (requestCoordinator.isCurrent(request)) {
          setError(reason instanceof Error ? reason.message : 'Unable to load Audible topics.');
        }
      })
      .finally(() => {
        if (requestCoordinator.isCurrent(request)) {
          setLoading(false);
        }
      });
  };

  useMountEffect(() => {
    requestCoordinator.activate();
    settleRequest(requestCoordinator.initial(getAudibleTopics));
    return () => {
      requestCoordinator.deactivate();
    };
  });

  const topics = useMemo(() => response?.topics ?? [], [response]);
  const broadSegment = value[0];
  const broadPath = useMemo(() => (broadSegment ? [broadSegment] : []), [broadSegment]);
  const broadTopic = broadPath.length > 0 ? findTopicByPath(topics, broadPath) : undefined;
  const selectedTopic = value.length > 0 ? findTopicByPath(topics, value) : undefined;
  const unavailable = value.length > 0 && Boolean(response) && !loading && !selectedTopic;

  const { broadOptions, broadPaths } = useMemo(() => {
    const paths = new Map<string, string[]>();
    const options = topics.map((topic) => {
      const encoded = pathValue(topic.path);
      paths.set(encoded, topic.path);
      return { value: encoded, label: topic.name };
    });
    if (broadPath.length > 0 && !topics.some((topic) => topicPathsEqual(topic.path, broadPath))) {
      options.push({
        value: pathValue(broadPath),
        label: `${pathLabel(value)}${unavailable ? ' (unavailable)' : ''}`,
      });
    }
    return { broadOptions: options, broadPaths: paths };
  }, [broadPath, topics, unavailable, value]);

  const { descendantOptions, descendantPaths } = useMemo(() => {
    const paths = new Map<string, string[]>();
    const options = broadTopic
      ? flattenTopicDescendants(broadTopic).map((option) => {
          const encoded = pathValue(option.path);
          paths.set(encoded, option.path);
          return { value: encoded, label: option.label };
        })
      : [];
    if (value.length > 0 && !options.some((option) => option.value === pathValue(value))) {
      options.push({
        value: pathValue(value),
        label: `${pathLabel(value)}${unavailable ? ' (unavailable)' : ''}`,
      });
    }
    return { descendantOptions: options, descendantPaths: paths };
  }, [broadTopic, unavailable, value]);

  const retry = () => {
    setLoading(true);
    setError(null);
    settleRequest(requestCoordinator.replace(getAudibleTopics));
  };

  return (
    <div className="space-y-3">
      <div className="space-y-1.5">
        <p className="text-xs font-medium">Broad topic</p>
        <SelectField
          field={broadField(broadOptions)}
          value={broadPath.length > 0 ? pathValue(broadPath) : ''}
          onChange={(encoded) => {
            const path = broadPaths.get(encoded);
            if (path) onChange(path);
          }}
          disabled={disabled || loading || !response}
        />
      </div>
      <div className="space-y-1.5">
        <p className="text-xs font-medium">Topic or subgenre</p>
        <SelectField
          field={descendantField(descendantOptions)}
          value={value.length > 0 ? pathValue(value) : ''}
          onChange={(encoded) => {
            const path = descendantPaths.get(encoded);
            if (path) onChange(path);
          }}
          disabled={disabled || loading || !broadTopic}
        />
      </div>
      <div className="space-y-1" aria-live="polite">
        {loading && <p className="text-xs opacity-60">Loading Audible topics…</p>}
        {response?.stale && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            This cached Audible topic list may be out of date. You can still make a selection.
          </p>
        )}
        {unavailable && (
          <p className="text-xs text-amber-600 dark:text-amber-400">
            This saved topic is no longer available. Reset the setting or choose an available topic.
          </p>
        )}
      </div>
      {error && (
        <div
          className="flex items-center gap-2 text-xs text-amber-600 dark:text-amber-400"
          role="alert"
        >
          <span>Audible topics could not be loaded: {error}</span>
          <button
            type="button"
            className="font-medium underline"
            onClick={retry}
            disabled={disabled}
          >
            Retry
          </button>
        </div>
      )}
    </div>
  );
};
