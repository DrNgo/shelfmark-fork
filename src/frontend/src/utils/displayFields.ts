/**
 * Collapse a comma-separated name list to "First Name +N more".
 *
 * Full-cast audiobook productions list a dozen-plus readers; rendered raw they
 * dominate (or clip out of) any card or row they appear in. Values with at most
 * two names — and non-list values like "4.8 (98,388)" — pass through unchanged.
 */
export const summarizeNameList = (value: string): string => {
  const names = value
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean);
  if (names.length <= 2) return value;
  return `${names[0]} +${names.length - 1} more`;
};
