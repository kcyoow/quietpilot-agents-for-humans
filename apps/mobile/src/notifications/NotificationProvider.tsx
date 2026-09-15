import Constants from "expo-constants";
import * as Notifications from "expo-notifications";
import { router } from "expo-router";
import {
  createContext,
  type PropsWithChildren,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
} from "react";
import { AppState, Platform } from "react-native";

import { createProductApi } from "@/src/api/productApi";
import { useAuth } from "@/src/auth/AuthProvider";
import { registerBeforeSignOutCleanup } from "@/src/auth/signOutCleanup";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";
import { NotificationController, type NotificationState } from "./controller";
import { logNotificationTap } from "./diagnostics";
import { parseAttentionNotice } from "./helpers";
import { notificationStorage } from "./storage";

type Context = NotificationState & {
  enable(): Promise<void>;
  disable(): Promise<void>;
  refresh(): Promise<void>;
};
const NotificationContext = createContext<Context | null>(null);

export function NotificationProvider({ children }: PropsWithChildren) {
  const { user } = useAuth();
  const workspace = useWorkspace();
  const workspaceRef = useRef(workspace);
  useLayoutEffect(() => {
    workspaceRef.current = workspace;
  }, [workspace]);
  const owner = user?.userId ?? null;
  const live = workspace.source === "LIVE";
  const controller = useMemo(
    () =>
      new NotificationController({
        api: createProductApi(),
        native: {
          ...Notifications,
          async createChannel() {
            await Notifications.setNotificationChannelAsync("default", {
              name: "Tasks needing attention",
              importance: Notifications.AndroidImportance.DEFAULT,
            });
          },
        },
        storage: notificationStorage,
        platform: Platform.OS,
        projectId: Constants.expoConfig?.extra?.eas?.projectId,
        appVersion:
          Constants.expoConfig?.version ??
          Constants.nativeAppVersion ??
          undefined,
        refreshWorkspace: () => workspaceRef.current.refresh(),
        loadCase: (caseId) => workspaceRef.current.loadCase(caseId),
        openCase: (caseId) =>
          router.push({ pathname: "/cases/[caseId]", params: { caseId } }),
      }),
    [],
  );
  const snapshot = useSyncExternalStore(
    controller.subscribe,
    controller.snapshot,
    controller.snapshot,
  );

  useLayoutEffect(() => {
    controller.setScope(owner, live);
  }, [controller, owner, live]);
  useEffect(() => {
    if (!owner) return;
    return registerBeforeSignOutCleanup(owner, () =>
      controller.beforeSignOut(owner),
    );
  }, [controller, owner]);
  useEffect(() => {
    let active = true;
    const consume = async (
      response: Notifications.NotificationResponse,
      origin: "LISTENER" | "RECOVERY",
    ) => {
      const data = response.notification.request.content.data;
      const defaultAction =
        response.actionIdentifier === Notifications.DEFAULT_ACTION_IDENTIFIER;
      logNotificationTap("RESPONSE", data, {
        origin,
        action: defaultAction ? "DEFAULT" : "OTHER",
      });
      if (!active || !defaultAction || !controller.matches(owner, live)) return;
      const result = await controller.handle(data, true);
      if (result === "RETRY" || !active || !controller.matches(owner, live))
        return;
      // Never clear an unhandled tap or a newer response that arrived during lookup.
      const current = await Notifications.getLastNotificationResponseAsync();
      if (
        active &&
        controller.matches(owner, live) &&
        current &&
        current.actionIdentifier === response.actionIdentifier &&
        current.notification.request.identifier ===
          response.notification.request.identifier &&
        parseAttentionNotice(current.notification.request.content.data)
          ?.event_id === parseAttentionNotice(data)?.event_id
      )
        await Notifications.clearLastNotificationResponseAsync();
    };
    const recover = async () => {
      await controller.refresh();
      if (!active || !owner || !live || !controller.matches(owner, live))
        return;
      try {
        const response = await Notifications.getLastNotificationResponseAsync();
        if (
          active &&
          controller.matches(owner, live) &&
          response &&
          response.actionIdentifier === Notifications.DEFAULT_ACTION_IDENTIFIER
        ) {
          await consume(response, "RECOVERY");
        }
      } catch {
        /* Missing native setup is surfaced by the state check. */
      }
    };
    void recover();
    const resume = AppState.addEventListener("change", (state) => {
      if (state === "active") void recover();
    });
    const listeners: { remove(): void }[] = [];
    try {
      Notifications.setNotificationHandler({
        handleNotification: async () => ({
          shouldShowBanner: false,
          shouldShowList: false,
          shouldPlaySound: false,
          shouldSetBadge: false,
        }),
      });
      listeners.push(
        Notifications.addNotificationReceivedListener((notification) => {
          if (active)
            void controller.handle(notification.request.content.data, false);
        }),
      );
      listeners.push(
        Notifications.addNotificationResponseReceivedListener((response) => {
          if (
            active &&
            response.actionIdentifier ===
              Notifications.DEFAULT_ACTION_IDENTIFIER
          )
            void consume(response, "LISTENER").catch(() => {});
        }),
      );
      listeners.push(
        Notifications.addPushTokenListener(() => {
          if (active) void controller.refresh();
        }),
      );
    } catch {
      controller.unavailable();
    }
    return () => {
      active = false;
      resume.remove();
      listeners.forEach((listener) => listener.remove());
      try {
        Notifications.setNotificationHandler(null);
      } catch {
        /* Native module unavailable. */
      }
    };
  }, [controller, owner, live]);
  const value: Context = {
    ...(controller.matches(owner, live)
      ? snapshot
      : {
          status: "checking" as const,
          enabled: false,
          busy: true,
          message: null,
        }),
    enable: controller.enable,
    disable: controller.disable,
    refresh: controller.refresh,
  };
  return (
    <NotificationContext.Provider value={value}>
      {children}
    </NotificationContext.Provider>
  );
}

export function useNotifications() {
  const value = useContext(NotificationContext);
  if (!value)
    throw new Error(
      "useNotifications must be used inside NotificationProvider",
    );
  return value;
}
