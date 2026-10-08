import type { Book, Release, ReleasesResponse } from '../types';
import {
  getBookAuthorCandidates,
  getBookTitleCandidates,
  sortReleasesByBookMatch,
} from './releaseScoring';
import type { SortState } from './releaseSort';
import { FORMAT_SORT_KEY, sortReleases, sortReleasesByFormat } from './releaseSort';

/**
 * The order the release modal shows: an explicit format sort, else an explicit (saved or
 * chosen) column sort when the source has sortable columns, else the default best-match
 * sort with its volume and medium tiers.
 */
export function sortReleasesForDisplay(
  releases: Release[],
  currentSort: SortState | null,
  hasSortOptions: boolean,
  uiBook: Book | null,
  responseBook: ReleasesResponse['book'] | undefined,
): Release[] {
  if (currentSort?.key === FORMAT_SORT_KEY && currentSort.value) {
    return sortReleasesByFormat(releases, currentSort.value, currentSort.direction);
  }
  if (currentSort && hasSortOptions) {
    return sortReleases(releases, currentSort.key, currentSort.direction);
  }
  return sortReleasesByBookMatch(
    releases,
    getBookTitleCandidates(uiBook, responseBook),
    getBookAuthorCandidates(uiBook, responseBook),
  );
}
