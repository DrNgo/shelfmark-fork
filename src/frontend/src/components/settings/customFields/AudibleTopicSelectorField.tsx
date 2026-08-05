import type { TagListFieldConfig } from '../../../types/settings';
import { AudibleTopicSelector } from '../AudibleTopicSelector';
import type { CustomSettingsFieldRendererProps } from './types';

const isTagListField = (candidate: unknown): candidate is TagListFieldConfig =>
  Boolean(
    candidate &&
    typeof candidate === 'object' &&
    'type' in candidate &&
    candidate.type === 'TagListField',
  );

const readString = (value: unknown): string =>
  typeof value === 'string' ? value.trim().toLowerCase() : '';

const readPath = (value: unknown): string[] =>
  Array.isArray(value) && value.every((segment) => typeof segment === 'string') ? value : [];

export const AudibleTopicSelectorField = ({
  field,
  values,
  onChange,
  isDisabled,
}: CustomSettingsFieldRendererProps) => {
  const boundField = field.boundFields?.find(isTagListField);
  if (!boundField) {
    return <p className="text-xs opacity-60">Audible topic schema is unavailable.</p>;
  }

  const searchMode = readString(values.SEARCH_MODE);
  const audiobookProvider =
    readString(values.METADATA_PROVIDER_AUDIOBOOK) || readString(values.METADATA_PROVIDER);

  if (searchMode !== 'universal' || audiobookProvider !== 'audible') {
    return null;
  }

  return (
    <AudibleTopicSelector
      value={readPath(values[boundField.key])}
      onChange={(path) => onChange(boundField.key, path)}
      disabled={isDisabled || Boolean(boundField.fromEnv)}
    />
  );
};
