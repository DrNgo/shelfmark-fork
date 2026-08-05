interface AccessibleSelectOption {
  value: string;
  label: string;
}

export const getSelectFieldAccessibleName = (
  fieldLabel: string | undefined,
  value: string,
  options: ReadonlyArray<AccessibleSelectOption>,
  placeholder: string,
): string | undefined => {
  const normalizedLabel = fieldLabel?.trim();
  if (!normalizedLabel) {
    return undefined;
  }

  const selectedOption = options.find((option) => option.value === value);
  const emptyOption = options.find((option) => option.value === '');
  const summary = selectedOption?.label ?? emptyOption?.label ?? placeholder;
  return `${normalizedLabel}: ${summary}`;
};
