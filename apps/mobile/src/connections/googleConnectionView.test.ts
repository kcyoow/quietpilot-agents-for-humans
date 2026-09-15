import type { GoogleConnectionRecord } from "@/src/connections/googleApi";
import {
  disconnectedGoogle,
  syncModeLabel,
  toGoogleConnection,
  watchHealthLabel,
} from "@/src/connections/googleConnectionView";

const connected: GoogleConnectionRecord = {
  provider: "google",
  label: "Google",
  status: "CONNECTED",
  granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
  lookback_days: 7,
  scan_progress: 100,
  discovery_revision: 1,
  error_code: null,
  last_checked_at: "2026-08-30T00:05:00Z",
  last_sync_mode: "INCREMENTAL",
  next_renewal_due_at: "2026-08-31T00:00:00Z",
  watch_expires_at: "2026-09-06T00:00:00Z",
  watch_renewed_at: "2026-08-30T00:00:00Z",
  version: 9,
};

test("maps server watch health into the real Google connection card", () => {
  const view = toGoogleConnection(connected);

  expect(view.watchExpiresAt).toBe("2026-09-06T00:00:00Z");
  expect(view.nextRenewalDueAt).toBe("2026-08-31T00:00:00Z");
  expect(view.discoveryRevision).toBe(1);
  expect(view.accessible).toHaveLength(1);
  expect(watchHealthLabel(view, Date.parse("2026-08-30T12:00:00Z"))).toBe(
    "Healthy",
  );
  expect(syncModeLabel(view.lastSyncMode)).toBe("New changes");
});

test("reports overdue and expired watch states without inventing timestamps", () => {
  const view = toGoogleConnection(connected);
  expect(watchHealthLabel(view, Date.parse("2026-09-01T00:00:00Z"))).toBe(
    "Awaiting automatic renewal",
  );
  expect(watchHealthLabel(view, Date.parse("2026-09-07T00:00:00Z"))).toBe(
    "Renewal needs attention",
  );
  expect(watchHealthLabel(disconnectedGoogle())).toBe("Checking");
});
