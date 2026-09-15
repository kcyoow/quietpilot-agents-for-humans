import * as WebBrowser from "expo-web-browser";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { useAuth } from "@/src/auth/AuthProvider";
import {
  completeGoogleOAuthOnce,
  createGoogleConnectionsApi,
  GoogleConnectionApiError,
} from "@/src/connections/googleApi";
import {
  disconnectedGoogle,
  toGoogleConnection,
} from "@/src/connections/googleConnectionView";
import type { PrototypeConnection } from "@/src/prototype/types";

const CALLBACK_URL = "quietpilot://oauth-return";
const POLL_INTERVAL_MS = 1_200;
const MAX_POLLS = 60;

type ConnectionOwner = {
  userId: string | null;
  readSequence: number;
  pendingOperations: number;
};
type ConnectionState = {
  owner: ConnectionOwner;
  connection: PrototypeConnection;
  error: string | null;
  checking: boolean;
  updating: boolean;
};

function emptyConnectionState(
  owner: ConnectionOwner,
  configured: boolean,
): ConnectionState {
  return {
    owner,
    connection: disconnectedGoogle(),
    error: null,
    checking: Boolean(owner.userId && configured),
    updating: false,
  };
}

function changedOwnerError() {
  return new Error("Your account changed. Check Connections.");
}

export function useGoogleConnection() {
  const { user } = useAuth();
  const userId = user?.userId ?? null;
  const api = useMemo(() => createGoogleConnectionsApi(), []);
  const owner = useMemo<ConnectionOwner>(
    () => ({ userId, readSequence: 0, pendingOperations: 0 }),
    [userId],
  );
  const [storedState, setStoredState] = useState(() =>
    emptyConnectionState(owner, api.configured),
  );
  const stateRef = useRef(storedState);
  const activeOwner = useRef<ConnectionOwner | null>(owner);

  useLayoutEffect(() => {
    activeOwner.current = owner;
    return () => {
      if (activeOwner.current === owner) activeOwner.current = null;
    };
  }, [owner]);

  const state =
    storedState.owner === owner
      ? storedState
      : emptyConnectionState(owner, api.configured);
  const isCurrentOwner = useCallback(
    () => activeOwner.current === owner,
    [owner],
  );
  const assertOwner = useCallback(() => {
    if (!isCurrentOwner() || !owner.userId) throw changedOwnerError();
  }, [isCurrentOwner, owner]);
  const updateState = useCallback(
    (update: (current: ConnectionState) => ConnectionState) => {
      if (!isCurrentOwner()) return;
      const current =
        stateRef.current.owner === owner
          ? stateRef.current
          : emptyConnectionState(owner, api.configured);
      const next = update(current);
      stateRef.current = next;
      setStoredState(next);
    },
    [api.configured, isCurrentOwner, owner],
  );
  const currentConnection = useCallback(
    () =>
      stateRef.current.owner === owner
        ? stateRef.current.connection
        : disconnectedGoogle(),
    [owner],
  );

  const refresh = useCallback(async () => {
    if (!isCurrentOwner()) throw changedOwnerError();
    if (!owner.userId || !api.configured) {
      const next = disconnectedGoogle();
      updateState((current) => ({
        ...current,
        connection: next,
        error: null,
        checking: false,
      }));
      return next;
    }
    const read = ++owner.readSequence;
    updateState((current) => ({ ...current, checking: true }));
    try {
      const record = await api.get();
      assertOwner();
      if (read !== owner.readSequence) return currentConnection();
      const next = record ? toGoogleConnection(record) : disconnectedGoogle();
      updateState((current) => ({ ...current, connection: next, error: null }));
      return next;
    } catch (caught) {
      if (!isCurrentOwner()) throw changedOwnerError();
      if (read !== owner.readSequence) return currentConnection();
      const authorizationLost =
        caught instanceof GoogleConnectionApiError &&
        (caught.status === 401 ||
          caught.status === 403 ||
          caught.code === "AUTH_REQUIRED" ||
          caught.code === "GOOGLE_AUTH_REQUIRED");
      updateState((current) => ({
        ...current,
        connection: authorizationLost
          ? disconnectedGoogle()
          : current.connection,
        error: errorMessage(caught),
      }));
      throw caught;
    } finally {
      if (read === owner.readSequence)
        updateState((current) => ({ ...current, checking: false }));
    }
  }, [api, assertOwner, currentConnection, isCurrentOwner, owner, updateState]);

  useEffect(() => {
    // refresh owns error reporting; an earlier account's rejection must stay ignored.
    void refresh().catch(() => undefined);
  }, [refresh]);

  const pendingPollCount = useRef(0);
  useEffect(() => {
    if (!["CONNECTING", "SCANNING"].includes(state.connection.status)) {
      pendingPollCount.current = 0;
      return;
    }
    if (
      state.checking ||
      state.updating ||
      state.error ||
      pendingPollCount.current >= MAX_POLLS
    )
      return;
    const timer = setTimeout(() => {
      pendingPollCount.current += 1;
      void refresh().catch(() => undefined);
    }, POLL_INTERVAL_MS);
    return () => clearTimeout(timer);
  }, [
    refresh,
    state.checking,
    state.connection.status,
    state.error,
    state.updating,
  ]);

  const publishConnection = useCallback(
    (connection: PrototypeConnection) => {
      assertOwner();
      // Reads started before this accepted operation response cannot roll it back.
      owner.readSequence += 1;
      updateState((current) => ({
        ...current,
        connection,
        error: null,
        checking: false,
      }));
    },
    [assertOwner, owner, updateState],
  );

  const waitForSettledConnection = useCallback(async () => {
    for (let index = 0; index < MAX_POLLS; index += 1) {
      assertOwner();
      await delay(POLL_INTERVAL_MS);
      assertOwner();
      const next = await refresh();
      assertOwner();
      if (!["SCANNING", "REVOKING"].includes(next.status)) return next;
    }
    throw new Error(
      "Connection setup is still in progress. Check again shortly.",
    );
  }, [assertOwner, refresh]);

  const runOperation = useCallback(
    async (operation: () => Promise<PrototypeConnection>) => {
      assertOwner();
      owner.pendingOperations += 1;
      owner.readSequence += 1;
      updateState((current) => ({
        ...current,
        error: null,
        checking: false,
        updating: true,
      }));
      try {
        const next = await operation();
        assertOwner();
        return next;
      } catch (caught) {
        if (!isCurrentOwner()) throw changedOwnerError();
        updateState((current) => ({ ...current, error: errorMessage(caught) }));
        throw caught;
      } finally {
        owner.pendingOperations -= 1;
        updateState((current) => ({
          ...current,
          updating: owner.pendingOperations > 0,
        }));
      }
    },
    [assertOwner, isCurrentOwner, owner, updateState],
  );

  const connect = useCallback(
    (capability?: "calendar") =>
      runOperation(async () => {
        const result = capability
          ? await api.authorize(capability)
          : await api.authorize();
        assertOwner();
        let connected = toGoogleConnection(result.connection);
        publishConnection(connected);
        if (result.authorization_url) {
          const browserResult = await WebBrowser.openAuthSessionAsync(
            result.authorization_url,
            CALLBACK_URL,
          );
          assertOwner();
          if (browserResult.type !== "success")
            throw new Error("Google connection cancelled.");
          const callback = parseCallback(browserResult.url);
          const completed = await completeGoogleOAuthOnce(
            api,
            callback.code,
            owner.userId!,
          );
          assertOwner();
          connected = toGoogleConnection(completed);
          publishConnection(connected);
        }
        // Authorization is complete; title recommendations continue in the
        // background while the user can enter interests on the main screen.
        return connected;
      }),
    [api, assertOwner, owner, publishConnection, runOperation],
  );

  const disconnect = useCallback(
    () =>
      runOperation(async () => {
        const pending = await api.disconnect();
        assertOwner();
        publishConnection(toGoogleConnection(pending));
        return waitForSettledConnection();
      }),
    [
      api,
      assertOwner,
      publishConnection,
      runOperation,
      waitForSettledConnection,
    ],
  );

  const rescan = useCallback(
    () =>
      runOperation(async () => {
        const pending = await api.scan(state.connection.lookbackDays);
        assertOwner();
        publishConnection(toGoogleConnection(pending));
        return waitForSettledConnection();
      }),
    [
      api,
      assertOwner,
      publishConnection,
      runOperation,
      state.connection.lookbackDays,
      waitForSettledConnection,
    ],
  );

  return {
    checking: state.checking,
    connect,
    connection: state.connection,
    disconnect,
    error: state.error,
    refresh,
    rescan,
    updating: state.updating,
  };
}

function parseCallback(url: string) {
  const parsed = new URL(url);
  const provider = parsed.searchParams.get("provider");
  const code = parsed.searchParams.get("code");
  if (provider !== "google" || !code) {
    throw new Error("Could not verify the Google response.");
  }
  return { code };
}

function delay(milliseconds: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, milliseconds));
}

function errorMessage(caught: unknown) {
  return caught instanceof Error
    ? caught.message
    : "Could not update Google connection.";
}
