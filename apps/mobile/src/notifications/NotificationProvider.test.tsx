import { act, renderHook, waitFor } from "@testing-library/react-native";
import * as Notifications from "expo-notifications";
import { mapNotificationResponse } from "expo-notifications/build/utils/mapNotificationResponse";
import { router } from "expo-router";
import type { PropsWithChildren } from "react";
import { AppState, type AppStateStatus, Platform } from "react-native";

import { createProductApi } from "@/src/api/productApi";
import { useAuth } from "@/src/auth/AuthProvider";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";
import { NotificationProvider, useNotifications } from "./NotificationProvider";
import { notificationStorage } from "./storage";
jest.mock("./diagnostics", () => ({ logNotificationTap: jest.fn() }));

jest.mock("@/src/api/productApi", () => ({ createProductApi: jest.fn() }));
jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("@/src/workspace/WorkspaceProvider", () => ({
  useWorkspace: jest.fn(),
}));
jest.mock("./storage", () => ({
  notificationStorage: {
    deviceId: jest.fn(),
    enabled: jest.fn(),
    setEnabled: jest.fn(),
  },
}));
jest.mock("expo-constants", () => ({
  __esModule: true,
  default: {
    expoConfig: {
      version: "0.1.0",
      extra: { eas: { projectId: "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" } },
    },
  },
}));
jest.mock("expo-router", () => ({ router: { push: jest.fn() } }));
jest.mock("expo-notifications", () => ({
  DEFAULT_ACTION_IDENTIFIER: "expo.modules.notifications.actions.DEFAULT",
  AndroidImportance: { DEFAULT: 3 },
  getPermissionsAsync: jest.fn(),
  requestPermissionsAsync: jest.fn(),
  getExpoPushTokenAsync: jest.fn(),
  unregisterForNotificationsAsync: jest.fn(),
  dismissAllNotificationsAsync: jest.fn(),
  clearLastNotificationResponseAsync: jest.fn(),
  setNotificationChannelAsync: jest.fn(),
  setNotificationHandler: jest.fn(),
  getLastNotificationResponseAsync: jest.fn(),
  addNotificationReceivedListener: jest.fn(() => ({ remove: jest.fn() })),
  addNotificationResponseReceivedListener: jest.fn(() => ({
    remove: jest.fn(),
  })),
  addPushTokenListener: jest.fn(() => ({ remove: jest.fn() })),
}));
const native = jest.mocked(Notifications);
const storage = jest.mocked(notificationStorage);
const refresh = jest.fn().mockResolvedValue(undefined);
const loadCase = jest
  .fn()
  .mockResolvedValue({ caseId: "case-one", dataSource: "LIVE" });
let resume: (state: AppStateStatus) => void;
const wrapper = ({ children }: PropsWithChildren) => (
  <NotificationProvider>{children}</NotificationProvider>
);
function notification(kind: string): Notifications.Notification {
  return {
    date: Date.now(),
    request: {
      identifier: "synthetic-notification",
      trigger: { type: "push" },
      content: {
        title: "확인이 필요해요",
        subtitle: null,
        body: "앱에서 확인해 주세요.",
        categoryIdentifier: null,
        sound: null,
        data: { case_id: "case-one", event_id: "a".repeat(64), kind },
      },
    },
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  jest
    .spyOn(AppState, "addEventListener")
    .mockImplementation((_event, listener) => {
      resume = listener;
      return { remove: jest.fn() };
    });
  loadCase.mockResolvedValue({ caseId: "case-one", dataSource: "LIVE" });
  Object.defineProperty(Platform, "OS", {
    value: "android",
    configurable: true,
  });
  jest
    .mocked(useAuth)
    .mockReturnValue({ user: { userId: "owner" } } as ReturnType<
      typeof useAuth
    >);
  jest.mocked(useWorkspace).mockReturnValue({
    source: "LIVE",
    refresh,
    loadCase,
  } as unknown as ReturnType<typeof useWorkspace>);
  let registered = false;
  jest.mocked(createProductApi).mockReturnValue({
    configured: true,
    pushDevice: jest.fn(async () => ({ device_id: "device-0001", registered })),
    registerPushToken: jest.fn(async () => {
      registered = true;
    }),
    unregisterPushToken: jest.fn(async () => {
      registered = false;
    }),
  } as unknown as ReturnType<typeof createProductApi>);
  storage.deviceId.mockResolvedValue("device-0001");
  storage.enabled.mockResolvedValue(false);
  storage.setEnabled.mockResolvedValue(undefined);
  const permission = {
    granted: true,
    status: "granted",
    canAskAgain: true,
    expires: "never",
  } as Notifications.NotificationPermissionsStatus;
  native.getPermissionsAsync.mockResolvedValue(permission);
  native.requestPermissionsAsync.mockResolvedValue(permission);
  native.getExpoPushTokenAsync.mockResolvedValue({
    type: "expo",
    data: "ExpoPushToken[synthetic_token_000001]",
  });
  native.getLastNotificationResponseAsync.mockResolvedValue(null);
  native.unregisterForNotificationsAsync.mockResolvedValue(undefined);
  native.dismissAllNotificationsAsync.mockResolvedValue(undefined);
  native.clearLastNotificationResponseAsync.mockResolvedValue(undefined);
});

test("provider is quiet at mount and enables only after explicit interaction", async () => {
  const hook = await renderHook(useNotifications, { wrapper });
  await waitFor(() => expect(hook.result.current.status).toBe("disabled"));
  expect(native.requestPermissionsAsync).not.toHaveBeenCalled();
  expect(native.getExpoPushTokenAsync).not.toHaveBeenCalled();
  await act(async () => {
    await hook.result.current.enable();
  });
  expect(hook.result.current.enabled).toBe(true);
  expect(native.setNotificationChannelAsync).toHaveBeenCalledWith(
    "default",
    expect.objectContaining({ name: "Tasks needing attention" }),
  );
});

test("native tap callback only navigates after authenticated LIVE Case readback", async () => {
  const hook = await renderHook(useNotifications, { wrapper });
  await waitFor(() => expect(hook.result.current.status).toBe("disabled"));
  await act(async () => {
    await hook.result.current.enable();
  });
  const callback =
    native.addNotificationResponseReceivedListener.mock.calls[0][0];
  await act(async () => {
    callback({
      notification: notification("ACTION_FAILED"),
      actionIdentifier: Notifications.DEFAULT_ACTION_IDENTIFIER,
    });
  });
  await waitFor(() =>
    expect(router.push).toHaveBeenCalledWith({
      pathname: "/cases/[caseId]",
      params: { caseId: "case-one" },
    }),
  );
  expect(loadCase).toHaveBeenCalledWith("case-one");
});

test("foreground ordinary completion payload causes no workspace refresh or navigation", async () => {
  const hook = await renderHook(useNotifications, { wrapper });
  await waitFor(() => expect(hook.result.current.status).toBe("disabled"));
  await act(async () => {
    await hook.result.current.enable();
  });
  const count = refresh.mock.calls.length;
  const callback = native.addNotificationReceivedListener.mock.calls[0][0];
  await act(async () => {
    callback(notification("COMPLETED"));
  });
  expect(refresh).toHaveBeenCalledTimes(count);
  expect(router.push).not.toHaveBeenCalled();
});

test("unrecognized native action cannot open a Case", async () => {
  const hook = await renderHook(useNotifications, { wrapper });
  await waitFor(() => expect(hook.result.current.status).toBe("disabled"));
  await act(async () => {
    await hook.result.current.enable();
  });
  const callback =
    native.addNotificationResponseReceivedListener.mock.calls[0][0];
  await act(async () => {
    callback({
      notification: notification("ACTION_FAILED"),
      actionIdentifier: "arbitrary-action",
    });
  });
  expect(loadCase).not.toHaveBeenCalled();
  expect(router.push).not.toHaveBeenCalled();
});

test("actual SDK mapping turns Android dataString into the strict attention payload", async () => {
  const hook = await renderHook(useNotifications, { wrapper });
  await waitFor(() => expect(hook.result.current.status).toBe("disabled"));
  await act(async () => {
    await hook.result.current.enable();
  });
  const raw = notification("DECISION_REQUIRED");
  const payload = raw.request.content.data;
  delete raw.request.content.data;
  Object.assign(raw.request.content, { dataString: JSON.stringify(payload) });
  const mapped = mapNotificationResponse({
    notification: raw,
    actionIdentifier: Notifications.DEFAULT_ACTION_IDENTIFIER,
  });
  expect(mapped.notification.request.content.data).toEqual(payload);
  await act(async () => {
    native.addNotificationResponseReceivedListener.mock.calls[0][0](mapped);
  });
  await waitFor(() =>
    expect(router.push).toHaveBeenCalledWith({
      pathname: "/cases/[caseId]",
      params: { caseId: "case-one" },
    }),
  );
});

test("failed lookup retains native response and a later resume can open the owned Case", async () => {
  const hook = await renderHook(useNotifications, { wrapper });
  await waitFor(() => expect(hook.result.current.status).toBe("disabled"));
  await act(async () => {
    await hook.result.current.enable();
  });
  storage.enabled.mockResolvedValue(true);
  native.getLastNotificationResponseAsync.mockResolvedValue({
    notification: notification("ACTION_FAILED"),
    actionIdentifier: Notifications.DEFAULT_ACTION_IDENTIFIER,
  });
  loadCase.mockRejectedValueOnce(new Error("synthetic transient failure"));
  await act(async () => {
    resume("active");
  });
  await waitFor(() => expect(loadCase).toHaveBeenCalledTimes(1));
  expect(native.clearLastNotificationResponseAsync).not.toHaveBeenCalled();
  expect(router.push).not.toHaveBeenCalled();
  await act(async () => {
    resume("active");
  });
  await waitFor(() => expect(router.push).toHaveBeenCalledTimes(1));
  await waitFor(() =>
    expect(native.clearLastNotificationResponseAsync).toHaveBeenCalledTimes(1),
  );
});
