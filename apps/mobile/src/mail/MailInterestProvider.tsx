import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { AppState } from "react-native";

import { useAuth } from "@/src/auth/AuthProvider";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";
import {
  createMailApi,
  MailApiError,
  isMailConfigured,
  type MailInterestState,
  type MailProfileInput,
  type MailResults,
} from "./mailApi";

type MailContext = {
  state: MailInterestState | null;
  results: MailResults | null;
  loading: boolean;
  loadingMore: boolean;
  saving: boolean;
  requesting: boolean;
  error: string | null;
  enabled: boolean;
  refresh(): Promise<void>;
  save(input: MailProfileInput): Promise<void>;
  recommend(): Promise<void>;
  scan(): Promise<void>;
  loadMore(): Promise<void>;
};

const MailInterestContext = createContext<MailContext | null>(null);

export function MailInterestProvider({ children }: PropsWithChildren) {
  const { user } = useAuth();
  const { source, refresh } = useWorkspace();
  return (
    <MailInterestSession
      key={`${user?.userId ?? "guest"}:${source}`}
      ownerId={user?.userId ?? null}
      enabled={Boolean(user) && source === "LIVE"}
      onSaved={refresh}
    >
      {children}
    </MailInterestSession>
  );
}

function MailInterestSession({
  children,
  ownerId,
  enabled,
  onSaved,
}: PropsWithChildren<{
  ownerId: string | null;
  enabled: boolean;
  onSaved(): Promise<void>;
}>) {
  const api = useMemo(
    () => createMailApi({ ownerId: ownerId ?? undefined }),
    [ownerId],
  );
  const [state, setState] = useState<MailInterestState | null>(null);
  const [results, setResults] = useState<MailResults | null>(null);
  const [loading, setLoading] = useState(enabled);
  const [reading, setReading] = useState(enabled);
  const [readCompletion, setReadCompletion] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [saving, setSaving] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(false);
  const revision = useRef(0);
  const readInFlight = useRef<number | null>(null);
  const refreshQueued = useRef(false);
  const forceResultsQueued = useRef(false);
  const resultReadNeeded = useRef(false);
  const readFailures = useRef(0);
  const saveInFlight = useRef(false);
  const requestInFlight = useRef(false);
  const moreInFlight = useRef(false);
  const resultsRef = useRef<MailResults | null>(null);
  const stateRef = useRef<MailInterestState | null>(null);
  const loadedCursors = useRef(new Set<string>());
  const workspaceRefresh = useRef(onSaved);
  const lastReadyNotification = useRef<string | null>(null);

  useEffect(() => {
    workspaceRefresh.current = onSaved;
  }, [onSaved]);

  const publishResults = useCallback((next: MailResults | null) => {
    resultsRef.current = next;
    setResults(next);
  }, []);

  const publishState = useCallback(
    (next: MailInterestState) => {
      stateRef.current = next;
      setState(next);
      const current = resultsRef.current;
      if (
        !current ||
        current.scan_id !== next.scan.scan_id ||
        current.profile_version !== next.profile.version ||
        !canReadResults(next)
      ) {
        loadedCursors.current.clear();
        publishResults(null);
      }
    },
    [publishResults],
  );

  const readState = useCallback(
    function readState(forceResults = false): Promise<void> {
      if (!enabled || !active.current) return Promise.resolve();
      if (
        readInFlight.current !== null ||
        saveInFlight.current ||
        requestInFlight.current
      ) {
        // A screen return or reconnection must get a fresh read after the current work.
        refreshQueued.current = true;
        forceResultsQueued.current ||= forceResults;
        return Promise.resolve();
      }
      const forcePage = forceResults || forceResultsQueued.current;
      forceResultsQueued.current = false;
      refreshQueued.current = false;
      const requestRevision = ++revision.current;
      readInFlight.current = requestRevision;
      setReading(true);
      let pendingPageRead = false;
      return api
        .getState()
        .then(async (next) => {
          if (!active.current || revision.current !== requestRevision) return;
          setError(null);
          const previous = stateRef.current;
          const previousResults = resultsRef.current;
          const finishingPartial = Boolean(
            previous &&
            (previous?.scan.status === "PENDING" ||
              previousResults?.status === "PENDING") &&
            previous.scan.scan_id === next.scan.scan_id &&
            previous.profile.version === next.profile.version,
          );
          const eligible = canReadResults(next);
          const displayState =
            eligible && finishingPartial && next.scan.status === "READY"
              ? { ...next, scan: { ...next.scan, status: "PENDING" as const } }
              : next;
          publishState(displayState);
          const needsPage =
            eligible &&
            (forcePage ||
              resultReadNeeded.current ||
              !previousResults ||
              previousResults.scan_id !== next.scan.scan_id ||
              previousResults.profile_version !== next.profile.version ||
              next.scan.status === "PENDING" ||
              previousResults.status === "PENDING" ||
              finishingPartial ||
              JSON.stringify(previous?.scan) !== JSON.stringify(next.scan));
          if (needsPage) {
            pendingPageRead = displayState.scan.status === "PENDING";
            resultReadNeeded.current = true;
            const page = await api.getResults();
            if (!active.current || revision.current !== requestRevision) return;
            if (
              page.scan_id !== next.scan.scan_id ||
              page.profile_version !== next.profile.version ||
              (page.status !== "READY" &&
                page.status !== "PENDING" &&
                page.status !== "ERROR")
            ) {
              publishResults(null);
              throw new Error("Your interests changed. Refresh the results.");
            }
            // A failed scan may still have validated rows. Its failure wins over
            // a partial page returned during the state/results transition.
            const status =
              next.scan.status === "ERROR" || page.status === "ERROR"
                ? "ERROR"
                : next.scan.status === "PENDING" || page.status === "PENDING"
                  ? "PENDING"
                  : "READY";
            publishState({ ...next, scan: { ...next.scan, status } });
            loadedCursors.current.clear();
            publishResults(status === "ERROR" ? { ...page, status } : page);
            resultReadNeeded.current = false;
            if (status === "READY") {
              const readyStamp = `${next.profile.version}:${next.scan.scan_id}:${next.scan.completed_at}:${next.scan.matched_count}`;
              if (lastReadyNotification.current !== readyStamp) {
                lastReadyNotification.current = readyStamp;
                void workspaceRefresh.current().catch(() => undefined);
              }
            }
          } else if (!eligible) {
            resultReadNeeded.current = false;
          }
          readFailures.current = 0;
        })
        .catch((caught: unknown) => {
          if (active.current && revision.current === requestRevision) {
            if (
              pendingPageRead &&
              caught instanceof MailApiError &&
              caught.status === 409 &&
              caught.code === "MAIL_VERSION_CONFLICT"
            ) {
              // A batch changed while results were read; keep validated rows and poll again.
              readFailures.current = 0;
              setError(null);
              return;
            }
            if (isAuthorizationFailure(caught)) publishResults(null);
            readFailures.current =
              caught instanceof MailApiError &&
              caught.status < 500 &&
              caught.status !== 429 &&
              !(
                caught.status === 0 &&
                ["NETWORK_ERROR", "REQUEST_TIMEOUT"].includes(caught.code)
              )
                ? 3
                : readFailures.current + 1;
            setError(message(caught));
          }
        })
        .finally(() => {
          if (readInFlight.current === requestRevision) {
            readInFlight.current = null;
            if (active.current) {
              if (
                refreshQueued.current &&
                !saveInFlight.current &&
                !requestInFlight.current
              )
                return readState();
              setReading(false);
              setReadCompletion((completed) => completed + 1);
              if (!refreshQueued.current) setLoading(false);
            }
          }
        });
    },
    [api, enabled, publishResults, publishState],
  );

  const refresh = useCallback(async () => {
    if (!enabled || !active.current) return;
    readFailures.current = 0;
    setLoading(true);
    setError(null);
    await readState(true);
  }, [enabled, readState]);

  useEffect(() => {
    active.current = true;
    void readState();
    return () => {
      active.current = false;
      revision.current += 1;
      readInFlight.current = null;
      refreshQueued.current = false;
      forceResultsQueued.current = false;
    };
  }, [readState]);

  useEffect(() => {
    const listener = AppState.addEventListener("change", (status) => {
      if (status === "active") void refresh();
    });
    return () => listener.remove();
  }, [refresh]);

  useEffect(() => {
    if (
      !enabled ||
      (error && readFailures.current >= 3) ||
      reading ||
      loadingMore ||
      saving ||
      requesting ||
      !state ||
      state.scan.error_code === "GOOGLE_AUTH_REQUIRED" ||
      state.recommendations.error_code === "GOOGLE_AUTH_REQUIRED" ||
      (state.recommendations.status !== "PENDING" &&
        state.scan.status !== "PENDING")
    )
      return;
    const timer = setTimeout(
      () => void readState(),
      error ? 3000 * readFailures.current : 1800,
    );
    return () => clearTimeout(timer);
  }, [
    enabled,
    error,
    loadingMore,
    readCompletion,
    reading,
    readState,
    requesting,
    saving,
    state,
  ]);

  async function save(input: MailProfileInput) {
    if (!enabled || !active.current)
      throw new Error("Sign in to save interests.");
    if (saveInFlight.current) throw new Error("Saving interests. Please wait.");
    saveInFlight.current = true;
    const requestRevision = ++revision.current;
    setSaving(true);
    setError(null);
    try {
      const next = await api.save(input);
      if (!active.current) throw new Error("Your account or screen changed.");
      if (revision.current === requestRevision) publishState(next);
      void onSaved().catch(() => undefined);
    } catch (caught) {
      if (active.current && revision.current === requestRevision) {
        if (isAuthorizationFailure(caught)) {
          readFailures.current = 3;
          publishResults(null);
        }
        if (
          caught instanceof MailApiError &&
          caught.status === 409 &&
          caught.code !== "OWNER_CHANGED"
        ) {
          try {
            const latest = await api.getState();
            if (active.current && revision.current === requestRevision)
              publishState(latest);
          } catch (latestError) {
            if (active.current && revision.current === requestRevision) {
              if (isAuthorizationFailure(latestError)) {
                readFailures.current = 3;
                publishResults(null);
              }
              setError("Could not load your latest interests. Try again.");
            }
          }
        } else {
          setError(message(caught));
        }
      }
      throw caught;
    } finally {
      saveInFlight.current = false;
      if (active.current) {
        setSaving(false);
        if (refreshQueued.current) void readState();
      }
    }
  }

  async function requestWork(kind: "recommend" | "scan") {
    if (!enabled || !active.current)
      throw new Error("Connect mail, then try again.");
    if (requestInFlight.current || saveInFlight.current) return;
    requestInFlight.current = true;
    const requestRevision = ++revision.current;
    setRequesting(true);
    setError(null);
    try {
      const next = await api[kind]();
      if (active.current && revision.current === requestRevision)
        publishState(next);
    } catch (caught) {
      if (active.current && revision.current === requestRevision) {
        if (isAuthorizationFailure(caught)) {
          readFailures.current = 3;
          publishResults(null);
        }
        setError(message(caught));
      }
      throw caught;
    } finally {
      requestInFlight.current = false;
      if (active.current) {
        setRequesting(false);
        if (refreshQueued.current) void readState();
      }
    }
  }

  async function loadMore() {
    const current = resultsRef.current;
    const cursor = current?.next_cursor;
    if (
      !enabled ||
      !active.current ||
      !current ||
      !cursor ||
      (current.status !== "READY" && current.status !== "ERROR") ||
      (stateRef.current?.scan.status !== "READY" &&
        stateRef.current?.scan.status !== "ERROR") ||
      moreInFlight.current ||
      saving
    )
      return;
    if (loadedCursors.current.has(cursor)) {
      setError("Could not load more mail. Refresh the results.");
      return;
    }
    moreInFlight.current = true;
    setLoadingMore(true);
    const requestRevision = revision.current;
    try {
      const page = await api.getResults(cursor);
      if (
        !active.current ||
        revision.current !== requestRevision ||
        resultsRef.current !== current
      )
        return;
      if (
        page.status === "PENDING" &&
        page.scan_id === current.scan_id &&
        page.profile_version === current.profile_version
      ) {
        publishResults({
          ...current,
          status:
            current.status === "ERROR" ||
            stateRef.current?.scan.status === "ERROR"
              ? "ERROR"
              : "PENDING",
          next_cursor: null,
        });
        await refresh();
        return;
      }
      if (
        page.scan_id !== current.scan_id ||
        page.profile_version !== current.profile_version ||
        (page.status !== "READY" && page.status !== "ERROR") ||
        page.next_cursor === cursor
      ) {
        throw new Error("Mail results changed. Please refresh.");
      }
      loadedCursors.current.add(cursor);
      const items = new Map(
        current.items.map((item) => [item.evidence_ref, item]),
      );
      for (const item of page.items) items.set(item.evidence_ref, item);
      const status =
        current.status === "ERROR" || page.status === "ERROR"
          ? "ERROR"
          : "READY";
      const latest = stateRef.current;
      if (status === "ERROR" && latest)
        publishState({ ...latest, scan: { ...latest.scan, status } });
      publishResults({ ...page, status, items: [...items.values()] });
    } catch (caught) {
      if (
        active.current &&
        revision.current === requestRevision &&
        resultsRef.current === current
      ) {
        if (isAuthorizationFailure(caught)) {
          readFailures.current = 3;
          publishResults(null);
          setError(message(caught));
          return;
        }
        if (
          caught instanceof MailApiError &&
          caught.status === 409 &&
          caught.code === "MAIL_VERSION_CONFLICT"
        ) {
          loadedCursors.current.clear();
          publishResults({ ...current, next_cursor: null });
          await refresh();
          return;
        }
        setError(message(caught));
      }
    } finally {
      moreInFlight.current = false;
      if (active.current) setLoadingMore(false);
    }
  }

  return (
    <MailInterestContext.Provider
      value={{
        state,
        results,
        loading,
        loadingMore,
        saving,
        requesting,
        error,
        enabled,
        refresh,
        save,
        recommend: () => requestWork("recommend"),
        scan: () => requestWork("scan"),
        loadMore,
      }}
    >
      {children}
    </MailInterestContext.Provider>
  );
}

export function useMailInterestState() {
  const context = useContext(MailInterestContext);
  if (!context) throw new Error("MailInterestProvider is missing");
  return context;
}

function message(caught: unknown) {
  return caught instanceof Error
    ? caught.message
    : "Could not check relevant mail.";
}

function canReadResults(next: MailInterestState) {
  return (
    isMailConfigured(next.profile) &&
    next.scan.scan_id !== null &&
    next.scan.profile_version === next.profile.version &&
    (next.scan.status === "READY" ||
      next.scan.status === "PENDING" ||
      next.scan.status === "ERROR") &&
    next.scan.error_code !== "GOOGLE_AUTH_REQUIRED" &&
    next.recommendations.error_code !== "GOOGLE_AUTH_REQUIRED"
  );
}

function isAuthorizationFailure(caught: unknown) {
  return (
    caught instanceof MailApiError &&
    (caught.status === 401 ||
      caught.status === 403 ||
      caught.code === "OWNER_CHANGED" ||
      caught.code === "GOOGLE_AUTH_REQUIRED")
  );
}
