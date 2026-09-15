import type { GoogleConnectionRecord } from "@/src/connections/googleApi";
import type { PrototypeConnection } from "@/src/prototype/types";

export function toGoogleConnection(
  record: GoogleConnectionRecord,
): PrototypeConnection {
  const connected = record.status === "CONNECTED";
  const calendarConnected =
    connected &&
    record.calendar?.status === "CONNECTED" &&
    record.calendar.granted_scopes.includes(
      "https://www.googleapis.com/auth/calendar.events.owned",
    );
  return {
    provider: "google",
    label: "Google",
    status: record.status,
    grantedScopes: record.granted_scopes,
    lookbackDays: record.lookback_days,
    scanProgress: record.scan_progress,
    discoveryRevision: record.discovery_revision ?? null,
    accessible: connected
      ? [
          {
            itemId: "gmail-recent-live",
            label: "Gmail from the last 7 days",
            detail: "Authorized read-only Google access",
            capability: "gmail.readonly",
          },
          ...(calendarConnected
            ? [
                {
                  itemId: "calendar-live",
                  label: "Google Calendar",
                  detail: "Create and verify the personal event you approve.",
                  capability: "calendar.events.owned",
                },
              ]
            : []),
        ]
      : [],
    unavailable: connected
      ? [
          {
            itemId: record.calendar
              ? "calendar-connect-live"
              : "google-write-live",
            label: calendarConnected
              ? "Send or edit mail"
              : "Create Calendar event",
            detail: calendarConnected
              ? "Mail access is read-only."
              : "Connect Calendar to create approved events.",
            capability: "unavailable",
          },
        ]
      : [],
    error: record.error_code,
    lastCheckedAt: record.last_checked_at,
    lastSyncMode: record.last_sync_mode,
    nextRenewalDueAt: record.next_renewal_due_at,
    watchExpiresAt: record.watch_expires_at,
    watchRenewedAt: record.watch_renewed_at,
    version: record.version,
  };
}

export function disconnectedGoogle(): PrototypeConnection {
  return {
    provider: "google",
    label: "Google",
    status: "DISCONNECTED",
    grantedScopes: [],
    lookbackDays: 7,
    scanProgress: 0,
    discoveryRevision: 0,
    accessible: [],
    unavailable: [],
    error: null,
    lastCheckedAt: null,
    lastSyncMode: null,
    nextRenewalDueAt: null,
    watchExpiresAt: null,
    watchRenewedAt: null,
    version: 0,
  };
}

export function watchHealthLabel(
  connection: PrototypeConnection,
  now = Date.now(),
) {
  const expiration = parseTimestamp(connection.watchExpiresAt);
  if (expiration === null) return "Checking";
  if (expiration <= now) return "Renewal needs attention";
  const renewalDue = parseTimestamp(connection.nextRenewalDueAt);
  return renewalDue !== null && renewalDue <= now
    ? "Awaiting automatic renewal"
    : "Healthy";
}

export function syncModeLabel(mode: PrototypeConnection["lastSyncMode"]) {
  if (mode === null) return null;
  return {
    INITIAL_7_DAY: "Last 7 days",
    INCREMENTAL: "New changes",
    BOUNDED_FULL_SYNC: "Recovery sync",
  }[mode];
}

export function formatConnectionTime(value: string | null) {
  const timestamp = parseTimestamp(value);
  if (timestamp === null) return "Checking";
  return new Intl.DateTimeFormat("en-US", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(timestamp);
}

function parseTimestamp(value: string | null) {
  if (!value) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}
