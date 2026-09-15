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

import { useAuth } from "@/src/auth/AuthProvider";
import {
  createAsyncStoragePrototypeBackend,
  type PrototypeApprovalInput,
  type PrototypeCandidate,
  type PrototypeCase,
  type PrototypeConnection,
  type PrototypeMutationResult,
  type PrototypePolicy,
  type PrototypeProvider as ProviderName,
  type PrototypeScenario,
  type PrototypeState,
  type PrototypeSuppressionRule,
} from "@/src/prototype";

type PrototypeStatus = "booting" | "ready" | "updating";

type PrototypeContextValue = {
  approveCase(
    caseId: string,
    input: PrototypeApprovalInput,
  ): Promise<PrototypeCase>;
  connect(provider: ProviderName): Promise<PrototypeConnection>;
  convertCandidates(candidateIds: string[]): Promise<PrototypeCase>;
  createDirectCase(prompt: string): Promise<PrototypeCase>;
  deferCase(caseId: string): Promise<PrototypeCase>;
  disconnect(provider: ProviderName): Promise<PrototypeConnection>;
  error: string | null;
  hideCandidate(candidateId: string): Promise<PrototypeCandidate>;
  loadScenario(scenario: PrototypeScenario): Promise<PrototypeScenario>;
  postCaseMessage(
    caseId: string,
    text: string,
  ): Promise<PrototypeCase["messages"]>;
  reduceSimilar(candidateId: string): Promise<PrototypeSuppressionRule>;
  refresh(): Promise<void>;
  reset(): Promise<void>;
  retryCase(caseId: string): Promise<PrototypeCase>;
  revokePolicy(policyId: string): Promise<PrototypePolicy>;
  snapshot: PrototypeState | null;
  status: PrototypeStatus;
  stopCase(caseId: string): Promise<PrototypeCase>;
  undoSuppression(ruleId: string): Promise<string[]>;
};

const PrototypeContext = createContext<PrototypeContextValue | null>(null);

const delay = (milliseconds: number) =>
  new Promise<void>((resolve) => setTimeout(resolve, milliseconds));

export function PrototypeProvider({ children }: PropsWithChildren) {
  const { user } = useAuth();
  const storageKey = `quietpilot.prototype-state.v3:${user?.userId ?? "guest"}`;
  const backend = useMemo(
    () => createAsyncStoragePrototypeBackend({ storageKey }),
    [storageKey],
  );
  const [snapshot, setSnapshot] = useState<PrototypeState | null>(null);
  const [status, setStatus] = useState<PrototypeStatus>("booting");
  const [error, setError] = useState<string | null>(null);
  const executionLoops = useRef(new Set<string>());
  const activeBackend = useRef(backend);

  useEffect(() => {
    activeBackend.current = backend;
  }, [backend]);

  const refresh = useCallback(async () => {
    const expectedBackend = backend;
    setStatus("booting");
    try {
      const next = await backend.getSnapshot();
      if (activeBackend.current === expectedBackend) {
        setSnapshot(next);
        setError(null);
      }
    } catch (caught) {
      if (activeBackend.current === expectedBackend) {
        setError(errorMessage(caught));
      }
    } finally {
      if (activeBackend.current === expectedBackend) setStatus("ready");
    }
  }, [backend]);

  useEffect(() => {
    let active = true;
    executionLoops.current.clear();
    void backend
      .getSnapshot()
      .then((next) => {
        if (!active) return;
        setSnapshot(next);
        setError(null);
      })
      .catch((caught) => {
        if (active) setError(errorMessage(caught));
      })
      .finally(() => {
        if (active) setStatus("ready");
      });
    return () => {
      active = false;
    };
  }, [backend]);

  const commit = useCallback(
    async <T,>(operation: () => Promise<PrototypeMutationResult<T>>) => {
      const expectedBackend = backend;
      setStatus("updating");
      try {
        const result = await operation();
        if (activeBackend.current === expectedBackend) {
          setSnapshot(result.snapshot);
          setError(null);
        }
        return result.value;
      } catch (caught) {
        if (activeBackend.current === expectedBackend) {
          setError(errorMessage(caught));
        }
        throw caught;
      } finally {
        if (activeBackend.current === expectedBackend) setStatus("ready");
      }
    },
    [backend],
  );

  const reconcileExecution = useCallback(
    async (caseId: string) => {
      if (executionLoops.current.has(caseId)) return;
      executionLoops.current.add(caseId);
      try {
        await delay(850);
        const verifying = await commit(() =>
          backend.advanceCaseExecution(caseId),
        );
        if (verifying.status === "VERIFYING") {
          await delay(1_050);
          await commit(() => backend.advanceCaseExecution(caseId));
        }
      } catch {
        // The visible error and persisted Case state are the recovery surface.
      } finally {
        executionLoops.current.delete(caseId);
      }
    },
    [backend, commit],
  );

  useEffect(() => {
    const timer = setTimeout(() => {
      for (const item of snapshot?.cases ?? []) {
        if (item.status === "RUNNING" || item.status === "VERIFYING") {
          void reconcileExecution(item.caseId);
        }
      }
    }, 0);
    return () => clearTimeout(timer);
  }, [reconcileExecution, snapshot]);

  const value = useMemo<PrototypeContextValue>(
    () => ({
      async approveCase(caseId, input) {
        const item = await commit(() => backend.approveCase(caseId, input));
        void reconcileExecution(caseId);
        return item;
      },
      async connect(provider) {
        await commit(() => backend.startConnection(provider));
        await delay(320);
        await commit(() => backend.advanceConnection(provider));
        await delay(620);
        return commit(() => backend.advanceConnection(provider));
      },
      convertCandidates: (candidateIds) =>
        commit(() => backend.convertCandidates(candidateIds)),
      createDirectCase: (prompt) =>
        commit(() => backend.createDirectCase(prompt)),
      deferCase: (caseId) => commit(() => backend.deferCase(caseId)),
      disconnect: (provider) =>
        commit(() => backend.disconnectConnection(provider)),
      error,
      hideCandidate: (candidateId) =>
        commit(() => backend.hideCandidate(candidateId)),
      loadScenario: (scenario) => commit(() => backend.loadScenario(scenario)),
      postCaseMessage: (caseId, text) =>
        commit(() => backend.postCaseMessage(caseId, text)),
      reduceSimilar: (candidateId) =>
        commit(() => backend.reduceSimilar(candidateId)),
      refresh,
      async reset() {
        setStatus("updating");
        try {
          setSnapshot(await backend.reset());
          setError(null);
        } finally {
          setStatus("ready");
        }
      },
      async retryCase(caseId) {
        const item = await commit(() => backend.retryCase(caseId));
        void reconcileExecution(caseId);
        return item;
      },
      revokePolicy: (policyId) => commit(() => backend.revokePolicy(policyId)),
      snapshot,
      status,
      stopCase: (caseId) => commit(() => backend.stopCase(caseId)),
      undoSuppression: (ruleId) =>
        commit(() => backend.undoSuppression(ruleId)),
    }),
    [backend, commit, error, reconcileExecution, refresh, snapshot, status],
  );

  return (
    <PrototypeContext.Provider value={value}>
      {children}
    </PrototypeContext.Provider>
  );
}

export function usePrototype() {
  const value = useContext(PrototypeContext);
  if (!value) {
    throw new Error("usePrototype must be used inside PrototypeProvider");
  }
  return value;
}

function errorMessage(caught: unknown) {
  return caught instanceof Error
    ? caught.message
    : "Could not update the example.";
}
