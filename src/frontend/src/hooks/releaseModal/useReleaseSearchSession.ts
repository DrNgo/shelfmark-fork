import { useCallback, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react';

import { useSocket } from '../../contexts/SocketContext';
import { getReleaseSources, getReleases } from '../../services/api';
import type {
  Book,
  ContentType,
  Language,
  ReleaseSource,
  ReleasesResponse,
  SearchStatusData,
} from '../../types';
import {
  LANGUAGE_OPTION_DEFAULT,
  getReleaseSearchLanguageParams,
} from '../../utils/languageFilters';
import {
  getCachedReleases,
  invalidateCachedReleases,
  setCachedReleases,
} from '../../utils/releaseCache';
import { useDependencyEffect, useMountEffect } from '../useMountEffect';
import {
  applyReleaseResponse,
  canSubmitManualSearch,
  isCurrentRequest,
  manualQueryAfterToggle,
  manualSearchSubmission,
  queryContext,
  releaseResponseAction,
  startRequest,
  supersedeRequests,
  tabNeedsFetch,
  usesBookReleaseCache,
} from './releaseSearchSession.helpers';

interface ReleaseModalTabInfo {
  name: string;
  displayName: string;
  enabled: boolean;
}

interface UseReleaseSearchSessionOptions {
  book: Book;
  contentType: ContentType;
  defaultReleaseSource?: string;
  defaultAudiobookReleaseSource?: string;
  defaultShowManualQuery?: boolean;
  bookLanguages: Language[];
  defaultLanguages: string[];
}

interface UseReleaseSearchSessionReturn {
  availableSources: ReleaseSource[];
  sourcesLoading: boolean;
  sourcesError: string | null;
  activeTab: string;
  setActiveTab: (tabName: string) => void;
  allTabs: ReleaseModalTabInfo[];
  releasesBySource: Record<string, ReleasesResponse | null>;
  loadingBySource: Record<string, boolean>;
  errorBySource: Record<string, string | null>;
  expandedBySource: Record<string, boolean>;
  searchStatus: SearchStatusData | null;
  formatFilter: string;
  setFormatFilter: Dispatch<SetStateAction<string>>;
  languageFilter: string[];
  setLanguageFilter: Dispatch<SetStateAction<string[]>>;
  indexerFilter: string[];
  setIndexerFilter: Dispatch<SetStateAction<string[]>>;
  manualQuery: string;
  setManualQuery: Dispatch<SetStateAction<string>>;
  showManualQuery: boolean;
  /** Whether the results come from a submitted manual query rather than the automatic search. */
  manualQueryApplied: boolean;
  /** Whether the manual form's Search button can be used. */
  canRunManualSearch: boolean;
  toggleManualQuery: () => void;
  applyCurrentFilters: () => void;
  runManualSearch: () => void;
  expandSearch: () => Promise<void>;
  isIndexerFilterInitialized: (tabName: string) => boolean;
}

const DEFAULT_EXPANDED_STATUS_DELAY_MS = 1500;

function getDefaultManualQuery(book: Book): string {
  const baseTitle = book.search_title || book.title || '';
  const baseAuthor = book.search_author || book.author || '';
  return `${baseTitle} ${baseAuthor}`.trim();
}

function buildReleaseTabs(
  availableSources: ReleaseSource[],
  providerName: string | undefined,
  contentType: ContentType,
  preferredDefaultReleaseSource: string | undefined,
): ReleaseModalTabInfo[] {
  type TabCandidate = { name: string; displayName: string; enabled: boolean };

  const enabledTabs: TabCandidate[] = [];
  const providerContextSourceName =
    availableSources.find(
      (source) => source.name === providerName && source.browse_results_are_releases,
    )?.name || null;

  availableSources.forEach((source) => {
    if (providerContextSourceName && source.name !== providerContextSourceName) {
      return;
    }

    const allowDisabledProviderContextTab = providerContextSourceName === source.name;
    if (!source.enabled && !allowDisabledProviderContextTab) {
      return;
    }

    const supportedTypes = source.supported_content_types || ['ebook', 'audiobook'];
    if (!supportedTypes.includes(contentType)) {
      return;
    }

    enabledTabs.push({ name: source.name, displayName: source.display_name, enabled: true });
  });

  if (preferredDefaultReleaseSource) {
    enabledTabs.sort((a, b) => {
      if (a.name === preferredDefaultReleaseSource) return -1;
      if (b.name === preferredDefaultReleaseSource) return 1;
      return 0;
    });
  }

  return enabledTabs;
}

export function useReleaseSearchSession(
  sessionOptions: UseReleaseSearchSessionOptions,
): UseReleaseSearchSessionReturn {
  const {
    book,
    contentType,
    defaultReleaseSource,
    defaultAudiobookReleaseSource,
    defaultShowManualQuery = false,
    bookLanguages,
    defaultLanguages,
  } = sessionOptions;
  const { socket } = useSocket();

  const preferredDefaultReleaseSource = useMemo(() => {
    if (contentType === 'audiobook') {
      return defaultAudiobookReleaseSource || defaultReleaseSource;
    }
    return defaultReleaseSource;
  }, [contentType, defaultAudiobookReleaseSource, defaultReleaseSource]);

  const defaultManualQuery = useMemo(() => getDefaultManualQuery(book), [book]);

  const [availableSources, setAvailableSources] = useState<ReleaseSource[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(true);
  const [sourcesError, setSourcesError] = useState<string | null>(null);

  const [activeTab, setActiveTabState] = useState(preferredDefaultReleaseSource || '');
  const [releasesBySource, setReleasesBySource] = useState<Record<string, ReleasesResponse | null>>(
    {},
  );
  const [loadingBySource, setLoadingBySource] = useState<Record<string, boolean>>({});
  const [errorBySource, setErrorBySource] = useState<Record<string, string | null>>({});
  const [expandedBySource, setExpandedBySource] = useState<Record<string, boolean>>({});
  const [searchStatus, setSearchStatus] = useState<SearchStatusData | null>(null);

  const [formatFilter, setFormatFilter] = useState('');
  const [languageFilter, setLanguageFilter] = useState([LANGUAGE_OPTION_DEFAULT]);
  const [indexerFilter, setIndexerFilter] = useState<string[]>([]);
  const [manualQuery, setManualQuery] = useState(defaultShowManualQuery ? defaultManualQuery : '');
  const [showManualQuery, setShowManualQuery] = useState(defaultShowManualQuery);
  // The manual query last submitted ('' for the automatic search), and per tab the query
  // context of the list on screen and the newest request. Refs, because a response is
  // checked against the values current when it arrives, not when it was requested.
  const initialAppliedManualQuery = defaultShowManualQuery ? defaultManualQuery : '';
  const appliedManualQueryRef = useRef(initialAppliedManualQuery);
  // The same value as state, for rendering.
  const [appliedManualQuery, setAppliedManualQuery] = useState(initialAppliedManualQuery);
  const displayedContextRef = useRef<Record<string, string>>({});
  const requestSeqRef = useRef<Record<string, number>>({});

  const indexerFilterInitializedRef = useRef(new Set<string>());
  const initialActiveTabRef = useRef(activeTab);
  const activeTabRef = useRef(activeTab);
  const lastStatusTimeRef = useRef(0);
  const pendingStatusRef = useRef<SearchStatusData | null>(null);
  const statusTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  const allTabs = useMemo(() => {
    return buildReleaseTabs(
      availableSources,
      book.provider,
      contentType,
      preferredDefaultReleaseSource,
    );
  }, [availableSources, book.provider, contentType, preferredDefaultReleaseSource]);

  const clearSearchStatusForTab = useCallback((tabName: string) => {
    setSearchStatus((current) => (current?.source === tabName ? null : current));
  }, []);

  const isIndexerFilterInitialized = useCallback(
    (tabName: string) => indexerFilterInitializedRef.current.has(tabName),
    [],
  );

  const initializeIndexerFilterForTab = useCallback(
    (tabName: string, response: ReleasesResponse | null | undefined) => {
      if (!response?.column_config) {
        return;
      }

      if (indexerFilterInitializedRef.current.has(tabName)) {
        return;
      }

      const defaultIndexers = response.column_config.default_indexers;
      if (defaultIndexers && defaultIndexers.length > 0) {
        setIndexerFilter(defaultIndexers);
      }
      indexerFilterInitializedRef.current.add(tabName);
    },
    [],
  );

  const fetchReleaseResults = useCallback(
    async (
      tabName: string,
      options: {
        expandSearch?: boolean;
        useFilters?: boolean;
        force?: boolean;
        supportsIndexerFilter?: boolean;
        manualQueryOverride?: string;
      } = {},
    ): Promise<void> => {
      const {
        expandSearch = false,
        useFilters = false,
        force = false,
        supportsIndexerFilter = false,
        manualQueryOverride,
      } = options;

      if (!book.provider || !book.provider_id || !tabName) {
        return;
      }

      if (!force && !tabNeedsFetch({ releasesBySource, loadingBySource, errorBySource }, tabName)) {
        return;
      }

      const provider = book.provider;
      const bookId = book.provider_id;
      const requestContext = queryContext(manualQueryOverride ?? appliedManualQueryRef.current);
      const currentManualQuery = requestContext || undefined;
      const languagesParam = useFilters
        ? getReleaseSearchLanguageParams(languageFilter, bookLanguages, defaultLanguages)
        : undefined;
      const indexersParam =
        useFilters && supportsIndexerFilter && indexerFilter.length > 0 ? indexerFilter : undefined;

      if (!expandSearch && usesBookReleaseCache(requestContext)) {
        const cached = getCachedReleases(provider, bookId, tabName, contentType);
        if (cached) {
          displayedContextRef.current[tabName] = requestContext;
          setReleasesBySource((prev) => ({ ...prev, [tabName]: cached }));
          initializeIndexerFilterForTab(tabName, cached);
          clearSearchStatusForTab(tabName);
          return;
        }
      }

      const requestSeq = startRequest(requestSeqRef.current, tabName);
      const isLatestRequest = () => isCurrentRequest(requestSeqRef.current, tabName, requestSeq);

      setLoadingBySource((prev) => ({ ...prev, [tabName]: true }));
      setErrorBySource((prev) => ({ ...prev, [tabName]: null }));

      try {
        const response = await getReleases(
          provider,
          bookId,
          tabName,
          book.title,
          book.author,
          expandSearch || undefined,
          languagesParam,
          contentType,
          currentManualQuery,
          indexersParam,
        );

        const action = releaseResponseAction({
          expandSearch,
          requestContext,
          currentContext: queryContext(appliedManualQueryRef.current),
          displayedContext: displayedContextRef.current[tabName],
          isLatestRequest: isLatestRequest(),
        });
        if (action === 'discard') {
          return;
        }
        if (!expandSearch && usesBookReleaseCache(requestContext)) {
          setCachedReleases(provider, bookId, tabName, contentType, response);
        }
        displayedContextRef.current[tabName] = requestContext;
        setReleasesBySource((prev) => ({
          ...prev,
          [tabName]: applyReleaseResponse(prev[tabName], response, action),
        }));

        initializeIndexerFilterForTab(tabName, response);
      } catch (err) {
        if (isLatestRequest()) {
          const message = err instanceof Error ? err.message : 'Failed to fetch releases';
          setErrorBySource((prev) => ({ ...prev, [tabName]: message }));
        }
      } finally {
        if (isLatestRequest()) {
          setLoadingBySource((prev) => ({ ...prev, [tabName]: false }));
          clearSearchStatusForTab(tabName);
        }
      }
    },
    [
      book.author,
      book.provider,
      book.provider_id,
      book.title,
      bookLanguages,
      clearSearchStatusForTab,
      contentType,
      defaultLanguages,
      errorBySource,
      indexerFilter,
      initializeIndexerFilterForTab,
      languageFilter,
      loadingBySource,
      releasesBySource,
    ],
  );
  useMountEffect(() => {
    let cancelled = false;

    const fetchSources = async () => {
      try {
        setSourcesLoading(true);
        setSourcesError(null);
        const sources = await getReleaseSources();
        if (cancelled) {
          return;
        }
        setAvailableSources(sources);
      } catch (err) {
        console.error('Failed to fetch release sources:', err);
        if (!cancelled) {
          setAvailableSources([]);
          setSourcesError(err instanceof Error ? err.message : 'Failed to load release sources');
        }
      } finally {
        if (!cancelled) {
          setSourcesLoading(false);
        }
      }
    };

    void fetchSources();
    return () => {
      cancelled = true;
    };
  });

  useDependencyEffect(() => {
    if (sourcesLoading) {
      return undefined;
    }

    indexerFilterInitializedRef.current = new Set<string>();
    // A new book or content type: back to the automatic search, and every response still
    // in flight is superseded.
    appliedManualQueryRef.current = defaultShowManualQuery ? defaultManualQuery : '';
    setAppliedManualQuery(appliedManualQueryRef.current);
    displayedContextRef.current = {};
    supersedeRequests(requestSeqRef.current);
    const nextInitialActiveTab = preferredDefaultReleaseSource || '';
    initialActiveTabRef.current = nextInitialActiveTab;
    pendingStatusRef.current = null;
    lastStatusTimeRef.current = 0;
    if (statusTimeoutRef.current) {
      clearTimeout(statusTimeoutRef.current);
      statusTimeoutRef.current = null;
    }

    const tabs = buildReleaseTabs(
      availableSources,
      book.provider,
      contentType,
      preferredDefaultReleaseSource,
    );
    const nextActiveTab = tabs.some((tab) => tab.name === nextInitialActiveTab)
      ? nextInitialActiveTab
      : (tabs[0]?.name ?? '');

    activeTabRef.current = nextActiveTab;
    setActiveTabState(nextActiveTab);
    setReleasesBySource({});
    setLoadingBySource({});
    setErrorBySource({});
    setExpandedBySource({});
    setSearchStatus(null);
    setFormatFilter('');
    setLanguageFilter([LANGUAGE_OPTION_DEFAULT]);
    setIndexerFilter([]);
    setManualQuery(defaultShowManualQuery ? defaultManualQuery : '');
    setShowManualQuery(defaultShowManualQuery);

    if (nextActiveTab) {
      void fetchReleaseResults(nextActiveTab, { force: true });
    }

    return undefined;
  }, [
    availableSources,
    book.id,
    book.provider,
    contentType,
    defaultManualQuery,
    defaultShowManualQuery,
    preferredDefaultReleaseSource,
    sourcesLoading,
  ]);

  useMountEffect(() => {
    if (!book || !socket) {
      return undefined;
    }

    const handleSearchStatus = (data: SearchStatusData) => {
      if (data.source !== activeTabRef.current) {
        return;
      }

      const now = Date.now();
      const elapsed = now - lastStatusTimeRef.current;

      if (elapsed >= DEFAULT_EXPANDED_STATUS_DELAY_MS) {
        setSearchStatus(data);
        lastStatusTimeRef.current = now;
        pendingStatusRef.current = null;
        return;
      }

      pendingStatusRef.current = data;

      if (statusTimeoutRef.current) {
        clearTimeout(statusTimeoutRef.current);
      }

      statusTimeoutRef.current = setTimeout(() => {
        if (pendingStatusRef.current) {
          setSearchStatus(pendingStatusRef.current);
          lastStatusTimeRef.current = Date.now();
          pendingStatusRef.current = null;
        }
      }, DEFAULT_EXPANDED_STATUS_DELAY_MS - elapsed);
    };

    socket.on('search_status', handleSearchStatus);

    return () => {
      socket.off('search_status', handleSearchStatus);
      if (statusTimeoutRef.current) {
        clearTimeout(statusTimeoutRef.current);
        statusTimeoutRef.current = null;
      }
      pendingStatusRef.current = null;
    };
  });

  const setActiveTab = useCallback(
    (tabName: string) => {
      activeTabRef.current = tabName;
      setActiveTabState(tabName);

      if (!tabName) {
        return;
      }

      if (tabNeedsFetch({ releasesBySource, loadingBySource, errorBySource }, tabName)) {
        void fetchReleaseResults(tabName, { force: false });
      }
    },
    [errorBySource, fetchReleaseResults, loadingBySource, releasesBySource],
  );

  // Make `nextQuery` the applied query ('' for the automatic search) and search the active
  // tab in it. Every tab's list, error and loading flag is cleared and every request still
  // in flight is superseded, so another tab refetches in the new context when activated.
  const switchQueryContext = useCallback(
    (nextQuery: string) => {
      if (!book.provider || !book.provider_id || !activeTab) {
        return;
      }

      for (const tab of allTabs) {
        invalidateCachedReleases(book.provider, book.provider_id, tab.name, contentType);
      }

      supersedeRequests(requestSeqRef.current);
      displayedContextRef.current = {};
      setExpandedBySource({});
      setErrorBySource({});
      setReleasesBySource({});
      setLoadingBySource({});

      // Submitting makes this the applied query: filters, tabs and expansion use it from now
      // on, whatever the text field holds later.
      appliedManualQueryRef.current = nextQuery;
      setAppliedManualQuery(nextQuery);
      void fetchReleaseResults(activeTab, {
        force: true,
        manualQueryOverride: nextQuery,
      });
    },
    [activeTab, allTabs, book.provider, book.provider_id, contentType, fetchReleaseResults],
  );

  const toggleManualQuery = useCallback(() => {
    const next = !showManualQuery;
    setShowManualQuery(next);
    if (next && !manualQuery.trim()) {
      setManualQuery(defaultManualQuery);
    }

    const nextQuery = manualQueryAfterToggle(next, appliedManualQueryRef.current);
    if (nextQuery !== null) {
      switchQueryContext(nextQuery);
    }
  }, [defaultManualQuery, manualQuery, showManualQuery, switchQueryContext]);

  const applyCurrentFilters = useCallback(() => {
    if (!book.provider || !book.provider_id || !activeTab) {
      return;
    }

    const supportsIndexerFilter =
      releasesBySource[activeTab]?.column_config?.supported_filters?.includes('indexer') ?? false;

    invalidateCachedReleases(book.provider, book.provider_id, activeTab, contentType);
    setExpandedBySource((prev) => {
      const next = { ...prev };
      delete next[activeTab];
      return next;
    });
    setErrorBySource((prev) => {
      const next = { ...prev };
      delete next[activeTab];
      return next;
    });
    setReleasesBySource((prev) => {
      const next = { ...prev };
      delete next[activeTab];
      return next;
    });

    void fetchReleaseResults(activeTab, {
      force: true,
      useFilters: true,
      supportsIndexerFilter,
    });
  }, [
    activeTab,
    book.provider,
    book.provider_id,
    contentType,
    fetchReleaseResults,
    releasesBySource,
  ]);

  const runManualSearch = useCallback(() => {
    const nextQuery = manualSearchSubmission(manualQuery, appliedManualQueryRef.current);
    if (nextQuery !== null) {
      switchQueryContext(nextQuery);
    }
  }, [manualQuery, switchQueryContext]);

  const expandSearch = useCallback(async (): Promise<void> => {
    if (!book.provider || !book.provider_id || !activeTab) {
      return;
    }

    const supportsIndexerFilter =
      releasesBySource[activeTab]?.column_config?.supported_filters?.includes('indexer') ?? false;

    setLoadingBySource((prev) => ({ ...prev, [activeTab]: true }));
    setExpandedBySource((prev) => ({ ...prev, [activeTab]: true }));

    await fetchReleaseResults(activeTab, {
      force: true,
      expandSearch: true,
      useFilters: true,
      supportsIndexerFilter,
    });
  }, [activeTab, book.provider, book.provider_id, fetchReleaseResults, releasesBySource]);

  return {
    availableSources,
    sourcesLoading,
    sourcesError,
    activeTab,
    setActiveTab,
    allTabs,
    releasesBySource,
    loadingBySource,
    errorBySource,
    expandedBySource,
    searchStatus,
    formatFilter,
    setFormatFilter,
    languageFilter,
    setLanguageFilter,
    indexerFilter,
    setIndexerFilter,
    manualQuery,
    setManualQuery,
    showManualQuery,
    manualQueryApplied: appliedManualQuery !== '',
    canRunManualSearch: canSubmitManualSearch(manualQuery, appliedManualQuery),
    toggleManualQuery,
    applyCurrentFilters,
    runManualSearch,
    expandSearch,
    isIndexerFilterInitialized,
  };
}
