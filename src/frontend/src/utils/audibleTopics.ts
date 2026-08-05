import type { AudibleTopicNode } from '../services/api';

export interface AudibleTopicOption {
  label: string;
  path: string[];
}

export interface AudibleTopicRequest<T> {
  lifecycleGeneration: number;
  requestGeneration: number;
  promise: Promise<T>;
}

export interface AudibleTopicRequestCoordinator<T> {
  activate: () => void;
  deactivate: () => void;
  initial: (factory: () => Promise<T>) => AudibleTopicRequest<T>;
  replace: (factory: () => Promise<T>) => AudibleTopicRequest<T>;
  isCurrent: (request: AudibleTopicRequest<T>) => boolean;
}

export const createAudibleTopicRequestCoordinator = <T>(): AudibleTopicRequestCoordinator<T> => {
  let lifecycleGeneration = 0;
  let requestGeneration = 0;
  let initialRequest: Pick<AudibleTopicRequest<T>, 'requestGeneration' | 'promise'> | undefined;

  const start = (factory: () => Promise<T>) => {
    requestGeneration += 1;
    return { requestGeneration, promise: factory() };
  };

  const subscribe = (
    request: Pick<AudibleTopicRequest<T>, 'requestGeneration' | 'promise'>,
  ): AudibleTopicRequest<T> => ({ ...request, lifecycleGeneration });

  return {
    activate: () => {
      lifecycleGeneration += 1;
    },
    deactivate: () => {
      lifecycleGeneration += 1;
    },
    initial: (factory) => {
      initialRequest ??= start(factory);
      return subscribe(initialRequest);
    },
    replace: (factory) => subscribe(start(factory)),
    isCurrent: (request) =>
      request.lifecycleGeneration === lifecycleGeneration &&
      request.requestGeneration === requestGeneration,
  };
};

export const topicPathsEqual = (left: string[], right: string[]): boolean =>
  left.length === right.length && left.every((segment, index) => segment === right[index]);

export const findTopicByPath = (
  topics: AudibleTopicNode[],
  path: string[],
): AudibleTopicNode | undefined => {
  let level = topics;
  let match: AudibleTopicNode | undefined;

  for (const segment of path) {
    match = level.find((topic) => topic.name === segment);
    if (!match) {
      return undefined;
    }
    level = match.children;
  }

  return match;
};

export const flattenTopicDescendants = (topic: AudibleTopicNode): AudibleTopicOption[] => {
  const options: AudibleTopicOption[] = [{ label: `All ${topic.name}`, path: topic.path }];

  const visit = (node: AudibleTopicNode) => {
    options.push({
      label: node.path.slice(topic.path.length).join(' → '),
      path: node.path,
    });
    node.children.forEach(visit);
  };

  topic.children.forEach(visit);
  return options;
};
