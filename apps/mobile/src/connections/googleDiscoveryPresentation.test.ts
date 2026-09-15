import { googleDiscoveryPresentation } from "@/src/connections/googleDiscoveryPresentation";
import { disconnectedGoogle } from "@/src/connections/googleConnectionView";
import type { PrototypeConnection } from "@/src/prototype/types";

const connected = (): PrototypeConnection => ({
  ...disconnectedGoogle(),
  lastCheckedAt: "2026-08-31T00:22:00Z",
  lastSyncMode: "INITIAL_7_DAY",
  scanProgress: 100,
  discoveryRevision: 1,
  status: "CONNECTED",
  version: 35,
});

function present(
  overrides: Partial<Parameters<typeof googleDiscoveryPresentation>[0]> = {},
) {
  return googleDiscoveryPresentation({
    candidateCount: 0,
    connection: connected(),
    connectionChecking: false,
    connectionError: null,
    workspaceError: null,
    workspaceStatus: "ready",
    ...overrides,
  });
}

test("keeps connection authorization distinct from mailbox scanning", () => {
  expect(
    present({ connection: { ...connected(), status: "CONNECTING" } }).kind,
  ).toBe("LINKING");
  expect(
    present({
      connection: {
        ...connected(),
        lastCheckedAt: null,
        scanProgress: 35,
        status: "SCANNING",
      },
    }),
  ).toMatchObject({ badge: "35%", kind: "SCANNING" });
});

test("does not claim no work before the result request succeeds", () => {
  expect(present({ workspaceStatus: "booting" }).kind).toBe("LOADING_RESULTS");
  expect(
    present({
      workspaceError: "서버 응답 형식이 달라 안전하게 표시하지 않았어요.",
    }),
  ).toMatchObject({ kind: "ERROR", retryable: true });
});

test("requires a new action-ready scan before accepting a legacy empty result", () => {
  expect(
    present({
      connection: { ...connected(), discoveryRevision: 0 },
      workspaceError: "legacy candidate response",
    }),
  ).toMatchObject({ kind: "REANALYSIS_REQUIRED", retryable: true });
});

test("reports an incompatible deployed server as an error without a broken retry", () => {
  expect(
    present({
      connection: { ...connected(), discoveryRevision: null },
      workspaceError: "legacy candidate response",
    }),
  ).toMatchObject({
    badge: "Scan error",
    kind: "INCOMPATIBLE_SERVER",
    retryable: false,
    tone: "warning",
  });
});

test("reports work and verified no-work as separate completed outcomes", () => {
  expect(present({ candidateCount: 2 })).toMatchObject({
    badge: "2 items",
    kind: "HAS_WORK",
  });
  expect(present()).toMatchObject({
    badge: "Checked",
    kind: "NO_WORK",
  });
});

test("keeps a failed connection read out of the no-connection empty state", () => {
  expect(
    present({
      connection: disconnectedGoogle(),
      connectionChecking: true,
    }).kind,
  ).toBe("CHECKING_CONNECTION");
  expect(
    present({
      connection: disconnectedGoogle(),
      connectionError: "연결 상태를 불러오지 못했어요.",
    }).kind,
  ).toBe("ERROR");
});

test("keeps an incomplete model scan visible as a retryable error", () => {
  expect(
    present({
      connection: {
        ...connected(),
        discoveryRevision: 0,
        error: "DISCOVERY_INCOMPLETE",
        scanProgress: 99,
        status: "ERROR",
      },
    }),
  ).toMatchObject({
    badge: "Partially checked",
    kind: "ERROR",
    retryable: true,
  });
});

test("allows a fresh scan when connected state retains non-terminal progress", () => {
  expect(
    present({
      connection: {
        ...connected(),
        scanProgress: 63,
        status: "CONNECTED",
      },
    }),
  ).toMatchObject({
    badge: "Scan interrupted",
    kind: "REANALYSIS_REQUIRED",
    progress: 63,
    retryable: true,
    tone: "warning",
  });
});
