import type { CSSProperties } from 'react';

import { useSearchMode } from '../contexts/SearchModeContext';
import type { Book, ButtonStateInfo } from '../types';
import { applyInLibraryLock } from '../utils/libraryMatches';
import { BookDownloadButton } from './BookDownloadButton';
import { BookGetButton } from './BookGetButton';

type ButtonSize = 'sm' | 'md';
type ButtonVariant = 'default' | 'icon';

interface BookActionButtonProps {
  book: Book;
  buttonState: ButtonStateInfo;
  onDownload: (book: Book) => Promise<void>;
  onGetReleases: (book: Book) => void;
  isLoadingReleases?: boolean;
  /** Already in an Audiobookshelf library, so there is nothing to acquire. */
  isInLibrary?: boolean;
  size?: ButtonSize;
  variant?: ButtonVariant;
  fullWidth?: boolean;
  className?: string;
  style?: CSSProperties;
}

export function BookActionButton({
  book,
  buttonState,
  onDownload,
  onGetReleases,
  isLoadingReleases,
  isInLibrary = false,
  size,
  variant = 'default',
  fullWidth,
  className,
  style,
}: BookActionButtonProps) {
  const { searchMode } = useSearchMode();
  // Applied here rather than in either button so both search modes block the
  // same way — a book you already own is no more worth downloading directly
  // than it is worth browsing releases for.
  const effectiveButtonState = applyInLibraryLock(buttonState, isInLibrary);

  if (searchMode === 'universal') {
    return (
      <BookGetButton
        book={book}
        onGetReleases={onGetReleases}
        buttonState={effectiveButtonState}
        isLoading={isLoadingReleases}
        size={size}
        variant={variant}
        fullWidth={fullWidth}
        className={className}
        style={style}
      />
    );
  }

  return (
    <BookDownloadButton
      buttonState={effectiveButtonState}
      onDownload={() => onDownload(book)}
      size={size}
      variant={variant === 'default' ? 'primary' : 'icon'}
      fullWidth={fullWidth}
      className={className}
      style={style}
      ariaLabel={effectiveButtonState.text}
    />
  );
}
