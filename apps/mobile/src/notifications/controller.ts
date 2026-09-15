import type * as Notifications from "expo-notifications";

import type { createProductApi } from "@/src/api/productApi";
import { logNotificationTap } from "./diagnostics";
import {
  type AttentionNotice,
  parseAttentionNotice,
  setupReady,
} from "./helpers";

type Api = Pick<
  ReturnType<typeof createProductApi>,
  "configured" | "pushDevice" | "registerPushToken" | "unregisterPushToken"
>;
type Native = Pick<
  typeof Notifications,
  | "getPermissionsAsync"
  | "requestPermissionsAsync"
  | "getExpoPushTokenAsync"
  | "unregisterForNotificationsAsync"
  | "dismissAllNotificationsAsync"
  | "clearLastNotificationResponseAsync"
> & {
  createChannel(): Promise<void>;
};
type Storage = {
  deviceId(): Promise<string>;
  enabled(owner: string): Promise<boolean>;
  setEnabled(owner: string, enabled: boolean): Promise<void>;
};
type Scope = { owner: string | null; live: boolean };
type Ticket = { scope: Scope; sequence: number; navigation?: boolean };
export type NotificationTapResult = "OPENED" | "IGNORED" | "RETRY";
export type NotificationState = {
  status:
    "checking" | "disabled" | "enabled" | "denied" | "unavailable" | "error";
  enabled: boolean;
  busy: boolean;
  message: string | null;
};
type Dependencies = {
  api: Api;
  native: Native;
  storage: Storage;
  platform: string;
  projectId: string | undefined;
  appVersion: string | undefined;
  refreshWorkspace(): Promise<void>;
  loadCase(
    caseId: string,
  ): Promise<{ caseId: string; dataSource: string } | null>;
  openCase(caseId: string): void;
};
let nativeTail: Promise<void> = Promise.resolve();
function serial<T>(work: () => Promise<T>): Promise<T> {
  const result = nativeTail.then(work, work);
  nativeTail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}
class StaleScope extends Error {}
const OFF: NotificationState = {
  status: "disabled",
  enabled: false,
  busy: false,
  message: null,
};
const FAILURE =
  "Could not check notifications. Check your connection and try again.";

export class NotificationController {
  private scope: Scope = { owner: null, live: false };
  private sequence = 0;
  private navigationSequence = 0;
  private state: NotificationState = OFF;
  private listeners = new Set<() => void>();
  private requests = new Set<AbortController>();
  private refreshing: Promise<void> | null = null;
  private events = new Set<string>();
  private pendingEvents = new Map<string, Promise<NotificationTapResult>>();
  private nativeBlocked = false;

  constructor(private readonly deps: Dependencies) {}
  snapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  matches(owner: string | null, live: boolean) {
    return this.scope.owner === owner && this.scope.live === live;
  }
  unavailable() {
    this.update({
      ...OFF,
      status: "unavailable",
      message: "Notifications are unavailable in this app configuration.",
    });
  }

  setScope(owner: string | null, live: boolean) {
    if (this.matches(owner, live)) return;
    const previous = this.scope.owner;
    this.scope = { owner, live };
    this.sequence += 1;
    this.navigationSequence += 1;
    this.abort();
    this.events.clear();
    this.pendingEvents.clear();
    this.refreshing = null;
    this.update({
      ...OFF,
      status: owner && live ? "checking" : "unavailable",
      message:
        owner && live
          ? null
          : "Sign in to the live workspace to manage notifications.",
    });
    if (previous && previous !== owner) {
      const scope = this.scope;
      void serial(async () => {
        if (this.scope !== scope) return;
        this.nativeBlocked = true;
        try {
          await this.deps.native.unregisterForNotificationsAsync();
          this.nativeBlocked = false;
          await this.deps.native.dismissAllNotificationsAsync();
          await this.deps.native.clearLastNotificationResponseAsync();
        } catch {
          /* Registration stays blocked until native revocation succeeds. */
        }
      });
    }
  }

  enable = () =>
    this.run(async (ticket) => {
      this.requireSetup(ticket);
      await this.safeNativeStart(ticket);
      await this.deps.native.createChannel();
      this.assert(ticket);
      let permission = await this.deps.native.getPermissionsAsync();
      this.assert(ticket);
      if (!permission.granted)
        permission = await this.deps.native.requestPermissionsAsync();
      this.assert(ticket);
      if (!permission.granted) {
        await this.revoke(ticket);
        this.update({
          ...OFF,
          status: "denied",
          message:
            "Notifications were not allowed. You can change this in device settings.",
        });
        return;
      }
      await this.register(ticket);
    });

  disable = () =>
    this.run(async (ticket) => {
      await this.revoke(ticket);
      this.update(OFF);
    });

  refresh = (): Promise<void> => {
    if (this.refreshing || this.state.busy)
      return this.refreshing ?? Promise.resolve();
    this.refreshing = this.run(
      async (ticket) => {
        this.assert(ticket);
        const owner = ticket.scope.owner!;
        await this.read(ticket, () => this.deps.refreshWorkspace());
        this.assert(ticket);
        const enabled = await this.deps.storage.enabled(owner);
        this.assert(ticket);
        if (
          !setupReady(
            this.deps.platform,
            this.deps.projectId,
            this.deps.appVersion,
          ) ||
          !this.deps.api.configured
        ) {
          this.update({
            ...OFF,
            status: "unavailable",
            message: "Notifications are unavailable in this app configuration.",
          });
          return;
        }
        const permission = await this.deps.native.getPermissionsAsync();
        this.assert(ticket);
        const deviceId = await this.deps.storage.deviceId();
        this.assert(ticket);
        const registered = await this.read(ticket, () =>
          this.deps.api.pushDevice(deviceId),
        );
        this.assert(ticket);
        if (!enabled || !permission.granted) {
          if (registered.registered || enabled) await this.revoke(ticket);
          this.update({
            ...OFF,
            status: enabled && !permission.granted ? "denied" : "disabled",
            message:
              enabled && !permission.granted
                ? "Notification permission is off in device settings."
                : null,
          });
          return;
        }
        await this.safeNativeStart(ticket);
        await this.register(ticket);
      },
      false,
      true,
      false,
    ).finally(() => {
      this.refreshing = null;
    });
    return this.refreshing;
  };

  async beforeSignOut(owner: string) {
    if (this.scope.owner !== owner)
      throw new Error("Your sign-in state changed.");
    await this.run(
      async (ticket) => {
        await this.revoke(ticket);
        this.update(OFF);
      },
      true,
      false,
    );
  }

  async handle(data: unknown, open: boolean): Promise<NotificationTapResult> {
    const notice = parseAttentionNotice(data);
    if (open)
      logNotificationTap("GUARD", data, {
        scope: !this.scope.owner
          ? "NO_OWNER"
          : this.scope.live
            ? "LIVE"
            : "SCENARIO",
        enabled: this.state.enabled,
        busy: this.state.busy,
        valid_notice: Boolean(notice),
      });
    if (!notice || !this.scope.live) return "IGNORED";
    if (!this.scope.owner) return "RETRY";
    if (open) {
      if (this.events.has(notice.event_id)) return "OPENED";
      const pending = this.pendingEvents.get(notice.event_id);
      if (pending) return pending;
      // Viewing an owned Case does not depend on re-registering push delivery.
      // Only explicit preference/auth/mode changes invalidate navigation.
      const ticket: Ticket = {
        scope: this.scope,
        sequence: this.navigationSequence,
        navigation: true,
      };
      const work = this.openNotice(ticket, notice).finally(() => {
        if (this.pendingEvents.get(notice.event_id) === work)
          this.pendingEvents.delete(notice.event_id);
      });
      this.pendingEvents.set(notice.event_id, work);
      return work;
    }
    if (!this.state.enabled) return "IGNORED";
    const ticket = { scope: this.scope, sequence: this.sequence };
    try {
      await this.read(ticket, () => this.deps.refreshWorkspace());
    } catch {
      /* Foreground refresh has no navigation or external action. */
    }
    return "IGNORED";
  }

  private async openNotice(
    ticket: Ticket,
    notice: AttentionNotice,
  ): Promise<NotificationTapResult> {
    logNotificationTap("CASE_LOOKUP", notice);
    try {
      const record = await this.read(ticket, () =>
        this.deps.loadCase(notice.case_id),
      );
      this.assert(ticket);
      const sameCase = record?.caseId === notice.case_id;
      const liveCase = record?.dataSource === "LIVE";
      logNotificationTap("CASE_RESULT", notice, {
        record_present: Boolean(record),
        same_case: sameCase,
        live_case: liveCase,
      });
      if (!record) return "RETRY";
      if (!sameCase || !liveCase) return "IGNORED";
      this.deps.openCase(notice.case_id);
      this.events.add(notice.event_id);
      if (this.events.size > 64)
        this.events.delete(this.events.values().next().value!);
      logNotificationTap("OPENED", notice, { outcome: "OPENED" });
      return "OPENED";
    } catch (error) {
      const outcome = error instanceof StaleScope ? "IGNORED" : "RETRY";
      logNotificationTap("RETRY", notice, { outcome });
      return outcome;
    }
  }

  private async register(ticket: Ticket) {
    const token = await this.deps.native.getExpoPushTokenAsync({
      projectId: this.deps.projectId,
    });
    this.assert(ticket);
    if (
      !/^(ExponentPushToken|ExpoPushToken)\[[A-Za-z0-9_-]{8,200}\]$/.test(
        token.data,
      )
    )
      throw new Error(FAILURE);
    const deviceId = await this.deps.storage.deviceId();
    this.assert(ticket);
    await this.bounded(ticket, (signal) =>
      this.deps.api.registerPushToken(
        {
          deviceId,
          expoPushToken: token.data,
          appVersion: this.deps.appVersion!,
        },
        signal,
      ),
    );
    const verified = await this.read(ticket, () =>
      this.deps.api.pushDevice(deviceId),
    );
    this.assert(ticket);
    if (!verified.registered) throw new Error(FAILURE);
    await this.deps.storage.setEnabled(ticket.scope.owner!, true);
    this.assert(ticket);
    this.update({
      status: "enabled",
      enabled: true,
      busy: false,
      message: "Only tasks needing a decision or review will alert you.",
    });
  }

  private async revoke(ticket: Ticket) {
    this.assert(ticket, false);
    const deviceId = await this.deps.storage.deviceId();
    this.assert(ticket, false);
    await this.deps.storage.setEnabled(ticket.scope.owner!, false);
    this.assert(ticket, false);
    let server = false;
    let native = false;
    try {
      await this.bounded(
        ticket,
        (signal) => this.deps.api.unregisterPushToken(deviceId, signal),
        false,
      );
      server = true;
    } catch (error) {
      if (error instanceof StaleScope) throw error;
    }
    this.assert(ticket, false);
    try {
      await this.deps.native.unregisterForNotificationsAsync();
      native = true;
    } catch {
      /* Server revocation can still make logout safe. */
    }
    this.assert(ticket, false);
    try {
      await this.deps.native.dismissAllNotificationsAsync();
      await this.deps.native.clearLastNotificationResponseAsync();
    } catch {
      /* Delivery history is best effort; no private payload is used. */
    }
    this.assert(ticket, false);
    if (!server && !native)
      throw new Error(
        "Could not disable notifications. Check your connection before signing out.",
      );
  }

  private async safeNativeStart(ticket: Ticket) {
    if (this.nativeBlocked) {
      await this.deps.native.unregisterForNotificationsAsync();
      this.assert(ticket);
      this.nativeBlocked = false;
    }
  }
  private requireSetup(ticket: Ticket) {
    this.assert(ticket);
    if (
      !this.deps.api.configured ||
      !setupReady(this.deps.platform, this.deps.projectId, this.deps.appVersion)
    ) {
      this.update({
        ...OFF,
        status: "unavailable",
        message: "Notifications are unavailable in this app configuration.",
      });
      throw new StaleScope();
    }
  }
  private assert(ticket: Ticket, live = true) {
    if (
      this.scope !== ticket.scope ||
      (ticket.navigation ? this.navigationSequence : this.sequence) !==
        ticket.sequence ||
      !ticket.scope.owner ||
      (live && !ticket.scope.live)
    )
      throw new StaleScope();
  }
  private abort() {
    for (const request of this.requests) request.abort();
  }
  private update(state: NotificationState) {
    this.state = state;
    this.listeners.forEach((listener) => listener());
  }
  private run(
    work: (ticket: Ticket) => Promise<void>,
    throwErrors = false,
    live = true,
    invalidateNavigation = true,
  ): Promise<void> {
    if (invalidateNavigation) this.navigationSequence += 1;
    const ticket = { scope: this.scope, sequence: ++this.sequence };
    this.abort();
    return serial(async () => {
      try {
        this.assert(ticket, live);
        this.update({ ...this.state, busy: true });
        await work(ticket);
      } catch (error) {
        if (
          !(error instanceof StaleScope) &&
          this.scope === ticket.scope &&
          this.sequence === ticket.sequence
        )
          this.update({
            ...this.state,
            status: "error",
            busy: false,
            message: FAILURE,
          });
        if (throwErrors)
          throw new Error(
            error instanceof StaleScope
              ? "Your sign-in state changed."
              : "Could not disable notifications. Check your connection and try again.",
          );
      } finally {
        if (
          this.scope === ticket.scope &&
          this.sequence === ticket.sequence &&
          this.state.busy
        )
          this.update({ ...this.state, busy: false });
      }
    });
  }
  private async bounded(
    ticket: Ticket,
    work: (signal: AbortSignal) => Promise<void>,
    live = true,
  ) {
    this.assert(ticket, live);
    const controller = new AbortController();
    this.requests.add(controller);
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      await Promise.race([
        work(controller.signal),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => {
            controller.abort();
            reject(new Error(FAILURE));
          }, 5000);
        }),
      ]);
      this.assert(ticket, live);
    } finally {
      clearTimeout(timer);
      this.requests.delete(controller);
    }
  }

  private async read<T>(ticket: Ticket, work: () => Promise<T>): Promise<T> {
    this.assert(ticket);
    let timer: ReturnType<typeof setTimeout> | undefined;
    try {
      const result = await Promise.race([
        work(),
        new Promise<never>((_, reject) => {
          timer = setTimeout(() => reject(new Error(FAILURE)), 5000);
        }),
      ]);
      this.assert(ticket);
      return result;
    } finally {
      clearTimeout(timer);
    }
  }
}
