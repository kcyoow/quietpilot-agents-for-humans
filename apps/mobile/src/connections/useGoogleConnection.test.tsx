import { act, cleanup, renderHook } from "@testing-library/react-native";
import * as WebBrowser from "expo-web-browser";
import { useAuth } from "@/src/auth/AuthProvider";
import {
  completeGoogleOAuthOnce,
  createGoogleConnectionsApi,
  type GoogleConnectionRecord,
} from "@/src/connections/googleApi";
import { useGoogleConnection } from "@/src/connections/useGoogleConnection";

jest.mock("expo-web-browser", () => ({
  openAuthSessionAsync: jest.fn(),
  WebBrowserResultType: { CANCEL: "cancel" },
}));
jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("@/src/connections/googleApi", () => {
  const actual = jest.requireActual("@/src/connections/googleApi");
  return {
    ...actual,
    createGoogleConnectionsApi: jest.fn(),
    completeGoogleOAuthOnce: jest.fn(actual.completeGoogleOAuthOnce),
  };
});
const mockedFactory = jest.mocked(createGoogleConnectionsApi);
const mockedComplete = jest.mocked(completeGoogleOAuthOnce);
const mockedAuth = jest.mocked(useAuth);
const mockedBrowser = jest.mocked(WebBrowser.openAuthSessionAsync);
let sequence = 0;
let callbackCode = "";

function record(
  status: GoogleConnectionRecord["status"] = "CONNECTED",
  version = 1,
): GoogleConnectionRecord {
  return {
    discovery_revision: 1,
    error_code: null,
    granted_scopes: [],
    label: "Google",
    last_checked_at: null,
    last_sync_mode: null,
    lookback_days: 7,
    next_renewal_due_at: null,
    provider: "google",
    scan_progress: status === "SCANNING" ? 25 : 100,
    status,
    version,
    watch_expires_at: null,
    watch_renewed_at: null,
  };
}
function callback() {
  return `quietpilot://oauth-return?provider=google&code=${callbackCode}`;
}
function api() {
  return {
    configured: true,
    get: jest.fn().mockResolvedValue(record()),
    authorize: jest.fn().mockResolvedValue({
      connection: record("CONNECTING", 2),
      authorization_url: "https://accounts.example.invalid/authorize",
    }),
    complete: jest.fn().mockResolvedValue(record("SCANNING", 3)),
    disconnect: jest.fn().mockResolvedValue(record("REVOKING", 2)),
    scan: jest.fn().mockResolvedValue(record("SCANNING", 2)),
  };
}
function auth(userId: string | null) {
  mockedAuth.mockReturnValue({
    status: "ready",
    user: userId
      ? {
          userId,
          mode: "cognito",
          email: "audit@example.invalid",
          displayName: "사용자",
        }
      : null,
  } as never);
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}
async function mount(client = api()) {
  mockedFactory.mockReturnValue(client as never);
  const hook = await renderHook(() => useGoogleConnection());
  await act(async () => {
    await jest.advanceTimersByTimeAsync(0);
  });
  return hook;
}
async function finishPoll(
  pending: Promise<unknown>,
  client: ReturnType<typeof api>,
  status: GoogleConnectionRecord["status"] = "CONNECTED",
) {
  client.get.mockResolvedValue(record(status, 40));
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1_200);
    await pending;
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  callbackCode = String(++sequence).padStart(43, "c");
  mockedFactory.mockReset();
  mockedBrowser
    .mockReset()
    .mockResolvedValue({ type: "success", url: callback() } as never);
  mockedComplete
    .mockReset()
    .mockImplementation(
      jest.requireActual("@/src/connections/googleApi").completeGoogleOAuthOnce,
    );
  auth("user-a");
});
afterEach(async () => {
  await cleanup();
  jest.useRealTimers();
});

test("sign-out immediately hides cached connection data and its error", async () => {
  const client = api();
  const hook = await mount(client);
  client.get.mockRejectedValueOnce(new Error("first account error"));
  await act(async () => {
    await hook.result.current.refresh().catch(() => undefined);
  });
  expect(hook.result.current.error).toBe("first account error");
  auth(null);
  await hook.rerender(undefined);
  expect(hook.result.current.connection.status).toBe("DISCONNECTED");
  expect(hook.result.current.error).toBeNull();
  expect(hook.result.current.checking).toBe(false);
  expect(hook.result.current.updating).toBe(false);
});

test.each(["success", "failure"] as const)(
  "ignores the former account's late refresh %s",
  async (outcome) => {
    const client = api();
    const hook = await mount(client);
    const response = deferred<GoogleConnectionRecord>();
    client.get.mockReturnValueOnce(response.promise);
    let pending!: Promise<unknown>;
    await act(async () => {
      pending = hook.result.current.refresh().catch((error) => error);
    });
    client.get.mockResolvedValue(record("CONNECTED", 22));
    auth("user-b");
    await hook.rerender(undefined);
    await act(async () => {
      if (outcome === "success") response.resolve(record("CONNECTED", 11));
      else response.reject(new Error("old account error"));
      expect(await pending).toBeInstanceOf(Error);
    });
    expect(hook.result.current.connection.version).toBe(22);
    expect(hook.result.current.error).toBeNull();
    expect(hook.result.current.checking).toBe(false);
  },
);

test.each(["refresh", "connect", "disconnect", "rescan"] as const)(
  "a saved %s callback cannot start requests after its account changed",
  async (action) => {
    const client = api();
    const hook = await mount(client);
    const saved = hook.result.current[action];
    auth("user-b");
    await hook.rerender(undefined);
    const reads = client.get.mock.calls.length;
    let pending!: Promise<unknown>;
    await act(async () => {
      pending = saved().catch((error) => error);
    });
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1_200);
      expect(await pending).toBeInstanceOf(Error);
    });
    expect(client.get).toHaveBeenCalledTimes(reads);
    expect(client.authorize).not.toHaveBeenCalled();
    expect(client.disconnect).not.toHaveBeenCalled();
    expect(client.scan).not.toHaveBeenCalled();
  },
);

test("a late authorize response does not open a browser for the next account", async () => {
  const client = api();
  const hook = await mount(client);
  const response = deferred<Awaited<ReturnType<typeof client.authorize>>>();
  client.authorize.mockReturnValueOnce(response.promise);
  let pending!: Promise<unknown>;
  await act(async () => {
    pending = hook.result.current.connect().catch((error) => error);
  });
  auth("user-b");
  await hook.rerender(undefined);
  await act(async () => {
    response.resolve({
      connection: record("CONNECTING", 11),
      authorization_url: "https://accounts.example.invalid/old",
    });
    await jest.advanceTimersByTimeAsync(1_200);
    expect(await pending).toBeInstanceOf(Error);
  });
  expect(mockedBrowser).not.toHaveBeenCalled();
  expect(mockedComplete).not.toHaveBeenCalled();
  expect(hook.result.current.updating).toBe(false);
});

test("a late browser return cannot complete a code for a different account", async () => {
  const client = api();
  const hook = await mount(client);
  const browser = deferred<never>();
  mockedBrowser.mockReturnValueOnce(browser.promise);
  let pending!: Promise<unknown>;
  await act(async () => {
    pending = hook.result.current.connect().catch((error) => error);
  });
  expect(mockedBrowser).toHaveBeenCalledTimes(1);
  auth("user-b");
  await hook.rerender(undefined);
  await act(async () => {
    browser.resolve({ type: "success", url: callback() } as never);
    await jest.advanceTimersByTimeAsync(1_200);
    expect(await pending).toBeInstanceOf(Error);
  });
  expect(mockedComplete).not.toHaveBeenCalled();
});

test("a late completion cannot update another account or start its poll", async () => {
  const client = api();
  const hook = await mount(client);
  const completed = deferred<GoogleConnectionRecord>();
  client.complete.mockReturnValueOnce(completed.promise);
  let pending!: Promise<unknown>;
  await act(async () => {
    pending = hook.result.current.connect().catch((error) => error);
  });
  expect(mockedComplete).toHaveBeenCalledWith(client, callbackCode, "user-a");
  auth("user-b");
  client.get.mockResolvedValue(record("CONNECTED", 22));
  await hook.rerender(undefined);
  const reads = client.get.mock.calls.length;
  await act(async () => {
    completed.resolve(record("CONNECTED", 11));
    await jest.advanceTimersByTimeAsync(1_200);
    expect(await pending).toBeInstanceOf(Error);
  });
  expect(hook.result.current.connection.version).toBe(22);
  expect(client.get).toHaveBeenCalledTimes(reads);
});

test("polling stops before another request when the account signs out during its delay", async () => {
  const client = api();
  const hook = await mount(client);
  let pending!: Promise<unknown>;
  await act(async () => {
    pending = hook.result.current.rescan().catch((error) => error);
  });
  expect(hook.result.current.connection.status).toBe("SCANNING");
  auth(null);
  await hook.rerender(undefined);
  const reads = client.get.mock.calls.length;
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1_200);
    expect(await pending).toBeInstanceOf(Error);
  });
  expect(client.get).toHaveBeenCalledTimes(reads);
  expect(hook.result.current.connection.status).toBe("DISCONNECTED");
  expect(hook.result.current.updating).toBe(false);
});

test.each([
  {
    action: "rescan" as const,
    pendingStatus: "SCANNING" as const,
    finalStatus: "CONNECTED" as const,
  },
  {
    action: "disconnect" as const,
    pendingStatus: "REVOKING" as const,
    finalStatus: "DISCONNECTED" as const,
  },
])(
  "an old refresh cannot roll back an accepted $action response",
  async ({ action, pendingStatus, finalStatus }) => {
    const client = api();
    const hook = await mount(client);
    const oldRead = deferred<GoogleConnectionRecord>();
    client.get.mockReturnValueOnce(oldRead.promise);
    let read!: Promise<unknown>;
    let operation!: Promise<unknown>;
    await act(async () => {
      read = hook.result.current.refresh();
    });
    await act(async () => {
      operation = hook.result.current[action]();
    });
    expect(hook.result.current.connection.status).toBe(pendingStatus);
    await act(async () => {
      oldRead.resolve(record("CONNECTED", 1));
      await read;
    });
    expect(hook.result.current.connection.status).toBe(pendingStatus);
    expect(hook.result.current.updating).toBe(true);
    await finishPoll(operation, client, finalStatus);
    expect(hook.result.current.connection.status).toBe(finalStatus);
    expect(hook.result.current.updating).toBe(false);
  },
);

test("an earlier refresh failure does not clear the newer refresh's checking state", async () => {
  const client = api();
  const hook = await mount(client);
  const old = deferred<GoogleConnectionRecord>();
  const next = deferred<GoogleConnectionRecord>();
  client.get.mockReturnValueOnce(old.promise).mockReturnValueOnce(next.promise);
  let oldRun!: Promise<unknown>;
  let nextRun!: Promise<unknown>;
  await act(async () => {
    oldRun = hook.result.current.refresh().catch((error) => error);
    nextRun = hook.result.current.refresh();
  });
  await act(async () => {
    old.reject(new Error("older refresh error"));
    await oldRun;
  });
  expect(hook.result.current.error).toBeNull();
  expect(hook.result.current.checking).toBe(true);
  await act(async () => {
    next.resolve(record("CONNECTED", 4));
    await nextRun;
  });
  expect(hook.result.current.connection.version).toBe(4);
  expect(hook.result.current.checking).toBe(false);
});

test("connect returns after authorization and queued setup without waiting for a mail scan", async () => {
  const client = api();
  const hook = await mount(client);
  const reads = client.get.mock.calls.length;
  let pending!: ReturnType<typeof hook.result.current.connect>;
  await act(async () => {
    pending = hook.result.current.connect();
    await pending;
  });
  expect(mockedComplete).toHaveBeenCalledWith(client, callbackCode, "user-a");
  expect(client.complete).toHaveBeenCalledWith(callbackCode);
  await expect(pending).resolves.toMatchObject({
    status: "SCANNING",
    version: 3,
  });
  expect(client.authorize).toHaveBeenCalledTimes(1);
  expect(client.get).toHaveBeenCalledTimes(reads);
  expect(hook.result.current.connection.status).toBe("SCANNING");
  expect(hook.result.current.updating).toBe(false);
});

test("token-available CONNECTING returns immediately and settles through background refresh", async () => {
  const client = api();
  const scope = "https://www.googleapis.com/auth/gmail.readonly";
  const queued = { ...record("CONNECTING", 3), granted_scopes: [scope] };
  client.get.mockResolvedValue(record("DISCONNECTED"));
  client.authorize.mockResolvedValueOnce({ connection: queued });
  const hook = await mount(client);
  const reads = client.get.mock.calls.length;
  let pending!: ReturnType<typeof hook.result.current.connect>;
  await act(async () => {
    pending = hook.result.current.connect();
    await pending;
  });

  await expect(pending).resolves.toMatchObject({
    status: "CONNECTING",
    grantedScopes: [scope],
    version: 3,
  });
  expect(hook.result.current.updating).toBe(false);
  expect(client.get).toHaveBeenCalledTimes(reads);
  expect(mockedBrowser).not.toHaveBeenCalled();
  expect(mockedComplete).not.toHaveBeenCalled();
  client.get.mockResolvedValue({
    ...record("CONNECTED", 4),
    granted_scopes: [scope],
  });
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1_200);
  });
  expect(hook.result.current.connection.status).toBe("CONNECTED");
  expect(hook.result.current.connection.version).toBe(4);
  expect(client.get).toHaveBeenCalledTimes(reads + 1);
  await act(async () => {
    await jest.advanceTimersByTimeAsync(5_000);
  });
  expect(client.get).toHaveBeenCalledTimes(reads + 1);
});

test("a cancelled authorization does not complete a code or claim a connected result", async () => {
  const client = api();
  client.get.mockResolvedValue(record("DISCONNECTED"));
  mockedBrowser.mockResolvedValueOnce({
    type: WebBrowser.WebBrowserResultType.CANCEL,
  });
  const hook = await mount(client);
  const reads = client.get.mock.calls.length;
  await act(async () => {
    await expect(hook.result.current.connect()).rejects.toThrow("cancelled");
  });

  expect(mockedComplete).not.toHaveBeenCalled();
  expect(client.complete).not.toHaveBeenCalled();
  expect(client.get).toHaveBeenCalledTimes(reads);
  expect(hook.result.current.connection.status).toBe("CONNECTING");
  expect(hook.result.current.error).toContain("cancelled");
  expect(hook.result.current.updating).toBe(false);
});

test("an old operation's finally does not stop the next account's updating flag", async () => {
  const client = api();
  const hook = await mount(client);
  const oldAuthorize = deferred<Awaited<ReturnType<typeof client.authorize>>>();
  client.authorize.mockReturnValueOnce(oldAuthorize.promise);
  let oldRun!: Promise<unknown>;
  await act(async () => {
    oldRun = hook.result.current.connect().catch((error) => error);
  });
  auth("user-b");
  await hook.rerender(undefined);
  const scan = deferred<GoogleConnectionRecord>();
  client.scan.mockReturnValueOnce(scan.promise);
  let newRun!: Promise<unknown>;
  await act(async () => {
    newRun = hook.result.current.rescan();
  });
  await act(async () => {
    oldAuthorize.resolve({
      connection: record("CONNECTING", 11),
      authorization_url: "https://accounts.example.invalid/old",
    });
    await jest.advanceTimersByTimeAsync(1_200);
    await oldRun;
  });
  expect(hook.result.current.updating).toBe(true);
  await act(async () => {
    scan.resolve(record("SCANNING", 33));
  });
  await finishPoll(newRun, client);
  expect(hook.result.current.updating).toBe(false);
});
