import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { createProductApi, type LiveCaseSummary } from "@/src/api/productApi";
import { useAuth } from "@/src/auth/AuthProvider";
import { usePrototype } from "@/src/prototype/PrototypeProvider";
import type {
  PrototypeApprovalInput,
  PrototypeCandidate,
  PrototypeCase,
  PrototypeSuppressionRule,
} from "@/src/prototype/types";
import {
  canRetryLiveCalendar,
  isOnceCalendarPlan,
  liveCandidateToWorkspace,
  liveCaseDetailToWorkspace,
  liveCaseSummaryToWorkspace,
  liveGroupToWorkspace,
} from "@/src/workspace/adapters";
import type {
  WorkspaceCandidate,
  WorkspaceCase,
  WorkspaceSnapshot,
  WorkspaceStatus,
} from "@/src/workspace/types";

type WorkspaceContextValue = {
  approveCase(
    caseId: string,
    input: PrototypeApprovalInput,
  ): Promise<WorkspaceCase>;
  convertCandidates(candidateIds: string[]): Promise<WorkspaceCase>;
  createDirectCase(prompt: string): Promise<WorkspaceCase>;
  deferCase(caseId: string): Promise<WorkspaceCase>;
  error: string | null;
  hideCandidate(candidateId: string): Promise<PrototypeCandidate>;
  loadCase(caseId: string): Promise<WorkspaceCase | null>;
  postCaseMessage(
    caseId: string,
    text: string,
  ): Promise<PrototypeCase["messages"]>;
  reduceSimilar(candidateId: string): Promise<PrototypeSuppressionRule>;
  refresh(): Promise<void>;
  retryCase(caseId: string): Promise<WorkspaceCase>;
  snapshot: WorkspaceSnapshot | null;
  source: "LIVE" | "SCENARIO";
  status: WorkspaceStatus;
  stopCase(caseId: string): Promise<WorkspaceCase>;
  undoSuppression(ruleId: string): Promise<string[]>;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

type LiveScope = {
  userId: string | null;
  scenarioActive: boolean;
  nextRequest: number;
  latestRefresh: number;
  latestFeedback: number;
  refreshing: boolean;
  pendingMutations: number;
  detailRequests: Map<string, number>;
  detailWrites: Map<string, number>;
};

type LiveState = {
  scope: LiveScope;
  candidates: WorkspaceCandidate[];
  groups: WorkspaceSnapshot["candidateGroups"];
  cases: WorkspaceCase[];
  error: string | null;
  status: WorkspaceStatus;
};

function emptyLiveState(scope: LiveScope, configured: boolean): LiveState {
  return {
    scope,
    candidates: [],
    groups: [],
    cases: [],
    error: configured ? null : "QuietPilot needs a server connection.",
    status:
      scope.userId && configured && !scope.scenarioActive ? "booting" : "ready",
  };
}

function changedWorkspaceError() {
  return new Error("Your account or workspace changed. Please refresh.");
}

export function WorkspaceProvider({ children }: PropsWithChildren) {
  const { user } = useAuth();
  const prototype = usePrototype();
  const api = useMemo(() => createProductApi(), []);
  const scenarioActive =
    __DEV__ &&
    prototype.snapshot !== null &&
    prototype.snapshot.loadedScenario !== "LIVE";
  const userId = user?.userId ?? null;
  const scope = useMemo<LiveScope>(
    () => ({
      userId,
      scenarioActive,
      nextRequest: 0,
      latestRefresh: 0,
      latestFeedback: 0,
      refreshing: false,
      pendingMutations: 0,
      detailRequests: new Map(),
      detailWrites: new Map(),
    }),
    [userId, scenarioActive],
  );
  const [storedLiveState, setStoredLiveState] = useState(() =>
    emptyLiveState(scope, api.configured),
  );
  const liveStateRef = useRef(storedLiveState);
  const activeScope = useRef<LiveScope | null>(scope);

  useLayoutEffect(() => {
    activeScope.current = scope;
    return () => {
      if (activeScope.current === scope) activeScope.current = null;
    };
  }, [scope]);

  // Mask an old account/source immediately, before the next refresh starts.
  const currentLiveState =
    storedLiveState.scope === scope
      ? storedLiveState
      : emptyLiveState(scope, api.configured);
  const {
    candidates: liveCandidates,
    groups: liveGroups,
    cases: liveCases,
    error: liveError,
    status: liveStatus,
  } = currentLiveState;

  const isCurrentScope = useCallback(
    () => activeScope.current === scope,
    [scope],
  );
  const updateLive = useCallback(
    (update: (state: LiveState) => LiveState) => {
      if (!isCurrentScope()) return;
      const previous =
        liveStateRef.current.scope === scope
          ? liveStateRef.current
          : emptyLiveState(scope, api.configured);
      const next = update(previous);
      if (next === previous && liveStateRef.current.scope === scope) return;
      liveStateRef.current = next;
      setStoredLiveState(next);
    },
    [api.configured, isCurrentScope, scope],
  );

  const publishStatus = useCallback(() => {
    const status: WorkspaceStatus =
      scope.pendingMutations > 0
        ? "updating"
        : scope.refreshing
          ? "booting"
          : "ready";
    updateLive((current) =>
      current.status === status ? current : { ...current, status },
    );
  }, [scope, updateLive]);

  const refreshLive = useCallback(async () => {
    if (!isCurrentScope() || scope.scenarioActive) return;
    if (!scope.userId || !api.configured) {
      updateLive(() => emptyLiveState(scope, api.configured));
      return;
    }
    const request = ++scope.nextRequest;
    scope.latestRefresh = request;
    scope.latestFeedback = request;
    scope.refreshing = true;
    publishStatus();
    try {
      const [candidateRecords, groupRecords, activeCases, historyCases] =
        await Promise.all([
          api.listSuggestions(),
          api.listSuggestionGroups(),
          api.listCases("active"),
          api.listCases("history"),
        ]);
      if (!isCurrentScope() || request !== scope.latestRefresh) return;
      const candidates = candidateRecords.map(liveCandidateToWorkspace);
      const groups = groupRecords.map((record) =>
        liveGroupToWorkspace(record, candidates),
      );
      const summaries = [...activeCases, ...historyCases].map(
        liveCaseSummaryToWorkspace,
      );
      updateLive((current) => {
        const known = new Map(current.cases.map((item) => [item.caseId, item]));
        const cases = summaries.map((summary) => {
          const existing = known.get(summary.caseId);
          return existing && existing.version >= summary.version
            ? existing
            : summary;
        });
        const included = new Set(cases.map((item) => item.caseId));
        // An older list response cannot remove a detail requested after it began.
        for (const item of current.cases) {
          if (
            !included.has(item.caseId) &&
            (scope.detailWrites.get(item.caseId) ?? 0) > request
          )
            cases.push(item);
        }
        return {
          ...current,
          candidates,
          groups,
          cases,
          error: scope.latestFeedback === request ? null : current.error,
        };
      });
    } catch (caught) {
      if (request === scope.latestRefresh && request === scope.latestFeedback) {
        updateLive((current) => ({ ...current, error: errorMessage(caught) }));
      }
    } finally {
      if (request === scope.latestRefresh) {
        scope.refreshing = false;
        publishStatus();
      }
    }
  }, [api, isCurrentScope, publishStatus, scope, updateLive]);

  useEffect(() => {
    if (scenarioActive) return;
    const timer = setTimeout(() => {
      void refreshLive();
    }, 0);
    return () => clearTimeout(timer);
  }, [refreshLive, scenarioActive]);

  const loadLiveCase = useCallback(
    async (caseId: string) => {
      if (
        !isCurrentScope() ||
        !scope.userId ||
        scope.scenarioActive ||
        !api.configured
      )
        return null;
      const request = ++scope.nextRequest;
      scope.detailRequests.set(caseId, request);
      scope.latestFeedback = request;
      try {
        const record = await api.getCase(caseId);
        if (!isCurrentScope() || scope.detailRequests.get(caseId) !== request)
          return null;
        const item = liveCaseDetailToWorkspace(record);
        const existing =
          liveStateRef.current.scope === scope
            ? liveStateRef.current.cases.find(
                (candidate) => candidate.caseId === caseId,
              )
            : undefined;
        if (existing && existing.version > item.version) return existing;
        scope.detailWrites.set(caseId, request);
        updateLive((current) => ({
          ...current,
          cases: [
            ...current.cases.filter((candidate) => candidate.caseId !== caseId),
            item,
          ],
          error: scope.latestFeedback === request ? null : current.error,
        }));
        return item;
      } catch (caught) {
        if (!isCurrentScope() || scope.detailRequests.get(caseId) !== request)
          return null;
        if (scope.latestFeedback === request)
          updateLive((current) => ({
            ...current,
            error: errorMessage(caught),
          }));
        throw caught;
      }
    },
    [api, isCurrentScope, scope, updateLive],
  );

  const requireLiveCase = useCallback(
    async (caseId: string) => {
      const item = await loadLiveCase(caseId);
      if (item) return item;
      // A foreground poll can supersede this read after a mutation was accepted.
      const current =
        isCurrentScope() && liveStateRef.current.scope === scope
          ? liveStateRef.current.cases.find(
              (candidate) => candidate.caseId === caseId,
            )
          : null;
      if (!current) throw new Error("Could not load the task.");
      return current;
    },
    [isCurrentScope, loadLiveCase, scope],
  );

  const acceptCaseSummary = useCallback(
    (record: LiveCaseSummary) => {
      const summary = liveCaseSummaryToWorkspace(record);
      scope.detailWrites.set(summary.caseId, ++scope.nextRequest);
      updateLive((current) => {
        const existing = current.cases.find(
          (item) => item.caseId === summary.caseId,
        );
        if (existing && existing.version > summary.version) return current;
        const item = existing
          ? {
              ...existing,
              ...summary,
              currentPlan: existing.currentPlan,
              evidence: existing.evidence,
              messages: existing.messages,
              timeline: existing.timeline,
            }
          : summary;
        return {
          ...current,
          cases: [
            ...current.cases.filter(
              (candidate) => candidate.caseId !== item.caseId,
            ),
            item,
          ],
        };
      });
    },
    [scope, updateLive],
  );

  const runLiveMutation = useCallback(
    async <T,>(operation: () => Promise<T>) => {
      if (!isCurrentScope() || !scope.userId || scope.scenarioActive)
        throw changedWorkspaceError();
      const request = ++scope.nextRequest;
      scope.latestFeedback = request;
      scope.pendingMutations += 1;
      publishStatus();
      try {
        const value = await operation();
        if (!isCurrentScope()) throw changedWorkspaceError();
        if (scope.latestFeedback === request)
          updateLive((current) => ({ ...current, error: null }));
        return value;
      } catch (caught) {
        if (!isCurrentScope()) throw changedWorkspaceError();
        if (scope.latestFeedback === request)
          updateLive((current) => ({
            ...current,
            error: errorMessage(caught),
          }));
        throw caught;
      } finally {
        scope.pendingMutations -= 1;
        publishStatus();
      }
    },
    [isCurrentScope, publishStatus, scope, updateLive],
  );

  const scenarioSnapshot = useMemo<WorkspaceSnapshot | null>(() => {
    if (!prototype.snapshot) return null;
    return {
      candidateGroups: prototype.snapshot.candidateGroups.map((item) => ({
        ...item,
        dataSource: "SCENARIO" as const,
      })),
      candidates: prototype.snapshot.candidates.map((item) => ({
        ...item,
        dataSource: "SCENARIO" as const,
      })),
      cases: prototype.snapshot.cases.map((item) => ({
        ...item,
        dataSource: "SCENARIO" as const,
      })),
      dataSource: "SCENARIO",
      loadedScenario: prototype.snapshot.loadedScenario,
    };
  }, [prototype.snapshot]);

  const liveSnapshot = useMemo<WorkspaceSnapshot>(
    () => ({
      candidateGroups: liveGroups,
      candidates: liveCandidates,
      cases: liveCases,
      dataSource: "LIVE",
      loadedScenario: null,
    }),
    [liveCandidates, liveCases, liveGroups],
  );
  const source = scenarioActive ? "SCENARIO" : "LIVE";
  const snapshot = source === "SCENARIO" ? scenarioSnapshot : liveSnapshot;

  const findCase = useCallback(
    (caseId: string) => snapshot?.cases.find((item) => item.caseId === caseId),
    [snapshot?.cases],
  );

  const value = useMemo<WorkspaceContextValue>(
    () => ({
      async approveCase(caseId, input) {
        if (source === "SCENARIO") {
          return {
            ...(await prototype.approveCase(caseId, input)),
            dataSource: "SCENARIO",
          };
        }
        const item = findCase(caseId);
        if (!item?.currentPlan) throw new Error("No current plan to approve.");
        if (!isOnceCalendarPlan(item.currentPlan) || input.grantMode !== "ONCE")
          throw new Error("Approve one personal Calendar event at a time.");
        return runLiveMutation(async () => {
          const accepted = await api.decideCase(caseId, {
            decision: "APPROVE",
            expectedVersion: item.version,
            grantMode: input.grantMode,
            planHash: input.planHash,
            planVersion: input.planVersion,
          });
          acceptCaseSummary(accepted);
          return requireLiveCase(caseId);
        });
      },
      async convertCandidates(candidateIds) {
        if (source === "SCENARIO") {
          return {
            ...(await prototype.convertCandidates(candidateIds)),
            dataSource: "SCENARIO",
          };
        }
        const candidates = candidateIds.map((candidateId) => {
          const candidate = liveCandidates.find(
            (item) => item.candidateId === candidateId,
          );
          if (!candidate)
            throw new Error("The selected suggestion is no longer available.");
          return { candidateId, version: candidate.version };
        });
        const reference = await runLiveMutation(() =>
          api.convertCandidates(candidates),
        );
        await refreshLive();
        return requireLiveCase(reference.case_id);
      },
      async createDirectCase(prompt) {
        if (source === "SCENARIO") {
          return {
            ...(await prototype.createDirectCase(prompt)),
            dataSource: "SCENARIO",
          };
        }
        const reference = await runLiveMutation(() =>
          api.createDirectCase(prompt),
        );
        await refreshLive();
        return requireLiveCase(reference.case_id);
      },
      async deferCase(caseId) {
        if (source === "SCENARIO") {
          return {
            ...(await prototype.deferCase(caseId)),
            dataSource: "SCENARIO",
          };
        }
        const item = findCase(caseId);
        if (!item?.currentPlan) throw new Error("No current plan to defer.");
        await runLiveMutation(() =>
          api.decideCase(caseId, {
            decision: "DEFER",
            expectedVersion: item.version,
            grantMode: "ONCE",
            planHash: item.currentPlan!.hash,
            planVersion: item.currentPlan!.version,
          }),
        );
        await refreshLive();
        return requireLiveCase(caseId);
      },
      error: source === "SCENARIO" ? prototype.error : liveError,
      async hideCandidate(candidateId) {
        if (source === "SCENARIO") {
          return prototype.hideCandidate(candidateId);
        }
        const candidate = liveCandidates.find(
          (item) => item.candidateId === candidateId,
        );
        if (!candidate)
          throw new Error("This suggestion is no longer available.");
        await runLiveMutation(() =>
          api.submitSuggestionFeedback(
            candidateId,
            "HIDE_ONCE",
            candidate.version,
          ),
        );
        await refreshLive();
        return {
          ...candidate,
          status: "HIDDEN",
          version: candidate.version + 1,
        };
      },
      loadCase:
        source === "SCENARIO"
          ? (caseId) => Promise.resolve(findCase(caseId) ?? null)
          : loadLiveCase,
      async postCaseMessage(caseId, text) {
        if (source === "SCENARIO")
          return prototype.postCaseMessage(caseId, text);
        const item = findCase(caseId);
        if (!item) throw new Error("Task not found.");
        await runLiveMutation(() =>
          api.postCaseMessage(caseId, text, item.version),
        );
        return (await loadLiveCase(caseId))?.messages ?? [];
      },
      async reduceSimilar(candidateId) {
        if (source === "SCENARIO") {
          return prototype.reduceSimilar(candidateId);
        }
        const candidate = liveCandidates.find(
          (item) => item.candidateId === candidateId,
        );
        if (!candidate)
          throw new Error("This suggestion is no longer available.");
        const result = await runLiveMutation(() =>
          api.submitSuggestionFeedback(
            candidateId,
            "REDUCE_SIMILAR",
            candidate.version,
          ),
        );
        if (!result.rule_id) {
          throw new Error("Could not confirm the suggestion filter.");
        }
        await refreshLive();
        return {
          affectedCandidateIds: result.affected_candidate_ids,
          createdAt: new Date().toISOString(),
          explanation:
            "Similar suggestions in this group will appear less often.",
          groupId: candidate.primaryGroupId,
          provider: candidate.provider,
          ruleId: result.rule_id,
          sourceCandidateId: candidateId,
        };
      },
      refresh: source === "SCENARIO" ? prototype.refresh : refreshLive,
      async retryCase(caseId) {
        if (source === "SCENARIO") {
          return {
            ...(await prototype.retryCase(caseId)),
            dataSource: "SCENARIO",
          };
        }
        const item = findCase(caseId);
        const canPrepareAgain =
          item?.status === "DECISION_REQUIRED" &&
          item.currentPlan?.actions.length === 0 &&
          !item.partialFailure;
        if (!item || (!canPrepareAgain && !canRetryLiveCalendar(item)))
          throw new Error(
            "This task cannot be retried now. Check its status and connection.",
          );
        return runLiveMutation(async () => {
          const accepted = await api.retryCase(caseId, item.version);
          acceptCaseSummary(accepted);
          return requireLiveCase(caseId);
        });
      },
      snapshot,
      source,
      status: source === "SCENARIO" ? prototype.status : liveStatus,
      async stopCase(caseId) {
        if (source === "SCENARIO") {
          return {
            ...(await prototype.stopCase(caseId)),
            dataSource: "SCENARIO",
          };
        }
        const item = findCase(caseId);
        if (!item) throw new Error("Task not found.");
        await runLiveMutation(() => api.stopCase(caseId, item.version));
        await refreshLive();
        return requireLiveCase(caseId);
      },
      async undoSuppression(ruleId) {
        if (source === "SCENARIO") {
          return prototype.undoSuppression(ruleId);
        }
        const result = await runLiveMutation(() =>
          api.undoSuggestionSuppression(ruleId),
        );
        await refreshLive();
        return result.restored_candidate_ids;
      },
    }),
    [
      acceptCaseSummary,
      api,
      findCase,
      liveCandidates,
      liveError,
      liveStatus,
      loadLiveCase,
      prototype,
      refreshLive,
      requireLiveCase,
      runLiveMutation,
      snapshot,
      source,
    ],
  );

  return (
    <WorkspaceContext.Provider value={value}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace() {
  const value = useContext(WorkspaceContext);
  if (!value) {
    throw new Error("useWorkspace must be used inside WorkspaceProvider");
  }
  return value;
}

function errorMessage(caught: unknown) {
  return caught instanceof Error
    ? caught.message
    : "Could not update QuietPilot.";
}
