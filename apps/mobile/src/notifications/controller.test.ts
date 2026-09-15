import { NotificationController } from "./controller";
import { parseAttentionNotice, preferenceKey } from "./helpers";
jest.mock("./diagnostics", () => ({ logNotificationTap: jest.fn() }));

const TOKEN = "ExpoPushToken[synthetic_token_000001]";
const NOTICE = {
  case_id: "case-one",
  event_id: "a".repeat(64),
  kind: "DECISION_REQUIRED",
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}
async function tick() {
  for (let i = 0; i < 20; i += 1) await Promise.resolve();
}
function fixture(changes: Record<string, unknown> = {}) {
  const preferences = new Map<string, boolean>();
  let registered = false;
  const permission = {
    granted: false,
    canAskAgain: true,
    expires: "never",
    status: "undetermined",
  };
  const native = {
    createChannel: jest.fn().mockResolvedValue(undefined),
    getPermissionsAsync: jest.fn(async () => permission),
    requestPermissionsAsync: jest.fn(async () => {
      permission.granted = true;
      return permission;
    }),
    getExpoPushTokenAsync: jest
      .fn()
      .mockResolvedValue({ type: "expo", data: TOKEN }),
    unregisterForNotificationsAsync: jest.fn().mockResolvedValue(undefined),
    dismissAllNotificationsAsync: jest.fn().mockResolvedValue(undefined),
    clearLastNotificationResponseAsync: jest.fn().mockResolvedValue(undefined),
  };
  const api = {
    configured: true,
    pushDevice: jest.fn(async () => ({ device_id: "device-0001", registered })),
    registerPushToken: jest.fn(async (_input, _signal?) => {
      registered = true;
    }),
    unregisterPushToken: jest.fn(async (_device, _signal?) => {
      registered = false;
    }),
  };
  const storage = {
    deviceId: jest.fn().mockResolvedValue("device-0001"),
    enabled: jest.fn(async (owner: string) => preferences.get(owner) === true),
    setEnabled: jest.fn(async (owner: string, enabled: boolean) => {
      preferences.set(owner, enabled);
    }),
  };
  const deps = {
    api,
    native,
    storage,
    platform: "android",
    projectId: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    appVersion: "0.1.0",
    refreshWorkspace: jest.fn().mockResolvedValue(undefined),
    loadCase: jest
      .fn()
      .mockResolvedValue({ caseId: "case-one", dataSource: "LIVE" }),
    openCase: jest.fn(),
    ...changes,
  };
  const controller = new NotificationController(
    deps as unknown as ConstructorParameters<typeof NotificationController>[0],
  );
  controller.setScope("tenant|owner", true);
  return { controller, deps, native, api, storage, preferences, permission };
}

test("mount check never prompts or registers without an enabled preference", async () => {
  const f = fixture();
  await f.controller.refresh();
  expect(f.native.requestPermissionsAsync).not.toHaveBeenCalled();
  expect(f.native.getExpoPushTokenAsync).not.toHaveBeenCalled();
  expect(f.api.pushDevice).toHaveBeenCalledWith("device-0001");
  expect(f.deps.refreshWorkspace).toHaveBeenCalledTimes(1);
  expect(f.controller.snapshot().status).toBe("disabled");
});

test("explicit enable creates channel, requests permission, confirms backend and stores only owner preference", async () => {
  const f = fixture();
  await f.controller.enable();
  expect(f.native.createChannel).toHaveBeenCalledTimes(1);
  expect(f.native.requestPermissionsAsync).toHaveBeenCalledTimes(1);
  expect(f.native.getExpoPushTokenAsync).toHaveBeenCalledWith({
    projectId: f.deps.projectId,
  });
  expect(f.controller.snapshot().enabled).toBe(true);
  expect(f.storage.setEnabled).toHaveBeenLastCalledWith("tenant|owner", true);
  expect(JSON.stringify(f.controller.snapshot())).not.toContain(TOKEN);
  expect(JSON.stringify(f.storage.setEnabled.mock.calls)).not.toContain(TOKEN);
});

test.each([
  { projectId: undefined },
  { projectId: "bad-project" },
  { platform: "ios" },
  { appVersion: undefined },
])(
  "missing setup is unavailable without requesting permission: %p",
  async (changes) => {
    const f = fixture(changes);
    await f.controller.enable();
    expect(f.controller.snapshot().status).toBe("unavailable");
    expect(f.native.requestPermissionsAsync).not.toHaveBeenCalled();
    expect(f.api.registerPushToken).not.toHaveBeenCalled();
  },
);

test("denied permission remains disabled and does not request a push token", async () => {
  const f = fixture();
  f.native.requestPermissionsAsync.mockResolvedValue(f.permission);
  await f.controller.enable();
  expect(f.controller.snapshot().status).toBe("denied");
  expect(f.native.getExpoPushTokenAsync).not.toHaveBeenCalled();
  expect(f.preferences.get("tenant|owner")).toBe(false);
});

test("a register response without registered readback does not claim enabled", async () => {
  const f = fixture();
  f.api.pushDevice.mockResolvedValue({
    device_id: "device-0001",
    registered: false,
  });
  await f.controller.enable();
  expect(f.controller.snapshot()).toMatchObject({
    status: "error",
    enabled: false,
  });
});

test("resume checks revoked permission and removes backend/native registration without prompting", async () => {
  const f = fixture();
  await f.controller.enable();
  f.permission.granted = false;
  await f.controller.refresh();
  expect(f.native.requestPermissionsAsync).toHaveBeenCalledTimes(1);
  expect(f.api.unregisterPushToken).toHaveBeenCalledTimes(1);
  expect(f.native.unregisterForNotificationsAsync).toHaveBeenCalledTimes(1);
  expect(f.controller.snapshot()).toMatchObject({
    status: "denied",
    enabled: false,
  });
});

test("resume refreshes a rotated Expo token under the same exact owner", async () => {
  const f = fixture();
  await f.controller.enable();
  f.native.getExpoPushTokenAsync.mockResolvedValue({
    type: "expo",
    data: "ExpoPushToken[synthetic_rotated_token]",
  });
  await f.controller.refresh();
  expect(f.api.registerPushToken.mock.calls[1][0].expoPushToken).toBe(
    "ExpoPushToken[synthetic_rotated_token]",
  );
  expect(f.storage.setEnabled).toHaveBeenLastCalledWith("tenant|owner", true);
});

test.each(["server", "native"])(
  "logout can finish when %s revocation alone succeeds",
  async (success) => {
    const f = fixture();
    await f.controller.enable();
    if (success === "server")
      f.native.unregisterForNotificationsAsync.mockRejectedValue(
        new Error("private native error"),
      );
    else
      f.api.unregisterPushToken.mockRejectedValue(
        new Error("private provider error"),
      );
    await expect(
      f.controller.beforeSignOut("tenant|owner"),
    ).resolves.toBeUndefined();
    expect(f.controller.snapshot().enabled).toBe(false);
    expect(f.native.dismissAllNotificationsAsync).toHaveBeenCalled();
  },
);

test("both unregister failures reject logout and do not claim disabled", async () => {
  const f = fixture();
  await f.controller.enable();
  f.native.unregisterForNotificationsAsync.mockRejectedValue(new Error(TOKEN));
  f.api.unregisterPushToken.mockRejectedValue(new Error(TOKEN));
  await expect(f.controller.beforeSignOut("tenant|owner")).rejects.toThrow(
    "Could not disable notifications. Check your connection and try again.",
  );
  expect(f.controller.snapshot()).toMatchObject({
    status: "error",
    enabled: true,
  });
  expect(JSON.stringify(f.controller.snapshot())).not.toContain(TOKEN);
});

test("server unregister timeout aborts the request and native invalidation permits logout", async () => {
  jest.useFakeTimers();
  try {
    const f = fixture();
    await f.controller.enable();
    f.api.unregisterPushToken.mockImplementation(
      () => new Promise<void>(() => {}),
    );
    const pending = f.controller.beforeSignOut("tenant|owner");
    await tick();
    await jest.advanceTimersByTimeAsync(5001);
    await pending;
    expect(f.api.unregisterPushToken.mock.calls[0][1].aborted).toBe(true);
    expect(f.native.unregisterForNotificationsAsync).toHaveBeenCalled();
  } finally {
    jest.useRealTimers();
  }
});

test.each(["account", "mode"])(
  "a stale token response after %s change cannot register",
  async (change) => {
    const f = fixture();
    const token = deferred<{ type: string; data: string }>();
    f.native.getExpoPushTokenAsync.mockReturnValue(token.promise);
    const enabling = f.controller.enable();
    await tick();
    f.controller.setScope(
      change === "account" ? "new-owner" : "tenant|owner",
      change !== "mode",
    );
    token.resolve({ type: "expo", data: TOKEN });
    await enabling;
    expect(f.api.registerPushToken).not.toHaveBeenCalled();
    expect(f.controller.snapshot().enabled).toBe(false);
    await f.controller.refresh();
  },
);

test("old register is aborted and native cleanup finishes before a new owner's registration", async () => {
  const f = fixture();
  const pending = deferred<void>();
  f.api.registerPushToken.mockImplementationOnce(() => pending.promise);
  const first = f.controller.enable();
  await tick();
  f.controller.setScope("new-owner", true);
  const second = f.controller.enable();
  expect(f.api.registerPushToken.mock.calls[0][1].aborted).toBe(true);
  pending.resolve();
  await first;
  await second;
  expect(
    f.native.unregisterForNotificationsAsync.mock.invocationCallOrder[0],
  ).toBeLessThan(f.api.registerPushToken.mock.invocationCallOrder[1]);
  expect(f.preferences.get("tenant|owner")).not.toBe(true);
  expect(f.preferences.get("new-owner")).toBe(true);
});

test("a safe tap opens only a Case loaded for the current LIVE owner", async () => {
  const f = fixture();
  await f.controller.enable();
  await f.controller.handle(NOTICE, true);
  expect(f.deps.loadCase).toHaveBeenCalledWith("case-one");
  expect(f.deps.openCase).toHaveBeenCalledWith("case-one");
  await f.controller.handle(NOTICE, true);
  expect(f.deps.openCase).toHaveBeenCalledTimes(1);
});

test("tap ownership lookup does not wait for a slow whole-workspace refresh", async () => {
  const f = fixture();
  await f.controller.enable();
  const list = deferred<void>();
  f.deps.refreshWorkspace.mockReturnValue(list.promise);
  const handling = f.controller.handle(NOTICE, true);
  await tick();
  const loadedBeforeListFinished = f.deps.loadCase.mock.calls.length > 0;
  list.resolve();
  await handling;
  expect(loadedBeforeListFinished).toBe(true);
  expect(f.deps.openCase).toHaveBeenCalledWith("case-one");
});

test("routine registration refresh cannot invalidate an owned pending tap", async () => {
  const f = fixture();
  await f.controller.enable();
  const loaded = deferred<{ caseId: string; dataSource: string }>();
  f.deps.loadCase.mockReturnValue(loaded.promise);
  const handling = f.controller.handle(NOTICE, true);
  await tick();
  await f.controller.refresh();
  loaded.resolve({ caseId: "case-one", dataSource: "LIVE" });
  await handling;
  expect(f.deps.openCase).toHaveBeenCalledTimes(1);
});

test("listener and recovery await the same pending tap instead of dropping it", async () => {
  const f = fixture();
  await f.controller.enable();
  const loaded = deferred<{ caseId: string; dataSource: string }>();
  f.deps.loadCase.mockReturnValue(loaded.promise);
  const first = f.controller.handle(NOTICE, true);
  await tick();
  let secondFinished = false;
  const second = f.controller.handle(NOTICE, true).then(() => {
    secondFinished = true;
  });
  await tick();
  const finishedBeforeReadback = secondFinished;
  loaded.resolve({ caseId: "case-one", dataSource: "LIVE" });
  await Promise.all([first, second]);
  expect(finishedBeforeReadback).toBe(false);
  expect(f.deps.loadCase).toHaveBeenCalledTimes(1);
  expect(f.deps.openCase).toHaveBeenCalledTimes(1);
});

test("an authenticated cold-start tap does not wait for push registration state", async () => {
  const f = fixture();
  expect(f.controller.snapshot().enabled).toBe(false);
  expect(await f.controller.handle(NOTICE, true)).toBe("OPENED");
  expect(f.deps.loadCase).toHaveBeenCalledWith("case-one");
  expect(f.api.registerPushToken).not.toHaveBeenCalled();
  expect(f.native.requestPermissionsAsync).not.toHaveBeenCalled();
});

test("an explicit notification disable still cancels pending navigation", async () => {
  const f = fixture();
  await f.controller.enable();
  const loaded = deferred<{ caseId: string; dataSource: string }>();
  f.deps.loadCase.mockReturnValue(loaded.promise);
  const handling = f.controller.handle(NOTICE, true);
  await tick();
  await f.controller.disable();
  loaded.resolve({ caseId: "case-one", dataSource: "LIVE" });
  expect(await handling).toBe("IGNORED");
  expect(f.deps.openCase).not.toHaveBeenCalled();
});

test.each([
  null,
  { caseId: "case-other", dataSource: "LIVE" },
  { caseId: "case-one", dataSource: "SCENARIO" },
])("unowned or mismatched Case never opens: %p", async (record) => {
  const f = fixture();
  await f.controller.enable();
  f.deps.loadCase.mockResolvedValue(record);
  await f.controller.handle(NOTICE, true);
  expect(f.deps.openCase).not.toHaveBeenCalled();
});

test.each(["account", "mode"])(
  "tap readback from an old %s is ignored",
  async (change) => {
    const f = fixture();
    await f.controller.enable();
    const loaded = deferred<{ caseId: string; dataSource: string }>();
    f.deps.loadCase.mockReturnValue(loaded.promise);
    const handling = f.controller.handle(NOTICE, true);
    await tick();
    f.controller.setScope(
      change === "account" ? "new-owner" : "tenant|owner",
      change !== "mode",
    );
    loaded.resolve({ caseId: "case-one", dataSource: "LIVE" });
    await handling;
    expect(f.deps.openCase).not.toHaveBeenCalled();
    await f.controller.refresh();
  },
);

test.each([
  { ...NOTICE, kind: "COMPLETED" },
  { ...NOTICE, url: "https://other.test" },
  { ...NOTICE, case_id: "https://other.test" },
  { ...NOTICE, event_id: "bad" },
  { ...NOTICE, kind: {} },
  null,
])("unsafe or ordinary payload is ignored: %p", async (payload) => {
  const f = fixture();
  await f.controller.enable();
  const calls = f.deps.refreshWorkspace.mock.calls.length;
  await f.controller.handle(payload, true);
  expect(parseAttentionNotice(payload)).toBeNull();
  expect(f.deps.openCase).not.toHaveBeenCalled();
  expect(f.deps.refreshWorkspace).toHaveBeenCalledTimes(calls);
});

test("owner preference keys preserve whitespace, pipe and Unicode without collisions", () => {
  expect(preferenceKey("tenant|owner")).not.toBe(preferenceKey("tenant-owner"));
  expect(preferenceKey("owner ")).not.toBe(preferenceKey("owner"));
  expect(preferenceKey("사용자")).toMatch(/^[A-Za-z0-9._-]+$/);
});

test("a stuck registration read cannot indefinitely block before-signout cleanup", async () => {
  jest.useFakeTimers();
  try {
    const f = fixture();
    f.api.pushDevice.mockImplementation(() => new Promise(() => {}));
    const reading = f.controller.refresh();
    await tick();
    const cleanup = f.controller.beforeSignOut("tenant|owner");
    await jest.advanceTimersByTimeAsync(5001);
    await reading;
    await cleanup;
    expect(f.native.unregisterForNotificationsAsync).toHaveBeenCalledTimes(1);
  } finally {
    jest.useRealTimers();
  }
});
