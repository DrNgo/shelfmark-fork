import type { AudibleTopicNode } from '../services/api';

export interface AudibleTopicOption {
  label: string;
  path: string[];
}

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
