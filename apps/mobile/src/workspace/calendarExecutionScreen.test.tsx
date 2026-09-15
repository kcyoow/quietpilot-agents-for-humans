import { act, cleanup, fireEvent, render } from "@testing-library/react-native";
import { AppState, Linking, type AppStateStatus } from "react-native";

import CaseDetailScreen from "@/app/cases/[caseId]";
import { createProductApi, type LiveCaseDetail } from "@/src/api/productApi";
import { useAuth } from "@/src/auth/AuthProvider";
import { usePrototype } from "@/src/prototype/PrototypeProvider";
import { WorkspaceProvider } from "@/src/workspace/WorkspaceProvider";

jest.mock("@/src/api/productApi", () => ({
  ...jest.requireActual("@/src/api/productApi"),
  createProductApi: jest.fn(),
}));
jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("@/src/prototype/PrototypeProvider", () => ({
  usePrototype: jest.fn(),
}));
jest.mock("@/src/theme/useAppTheme", () => ({
  useAppTheme: () => ({
    colors: {
      accent: "blue",
      text: "black",
      textMuted: "gray",
      danger: "red",
      success: "green",
    },
  }),
}));
jest.mock("expo-router", () => ({
  router: { back: jest.fn(), push: jest.fn() },
  useFocusEffect: (callback: () => void) => {
    const { useEffect } = jest.requireActual("react");
    useEffect(callback, [callback]);
  },
  useLocalSearchParams: () => ({ caseId: "calendar-case" }),
}));

function detail(
  status: LiveCaseDetail["status"] = "DECISION_REQUIRED",
  version = 3,
): LiveCaseDetail {
  const action: LiveCaseDetail["actions"][number] = {
    action_id: "calendar-action",
    connector: "google",
    label: "개인 일정 등록",
    parameters: {
      summary: "합성 검토 일정",
      start: "2026-10-07T00:30:00Z",
      end: "2026-10-07T01:30:00Z",
      description: "안건 검토",
      source_ref: "gmail:synthetic-private-ref",
    },
    required_scopes: ["calendar.events.owned"],
    result_summary: null,
    result_ref: null,
    html_url: null,
    verified: false,
    error_code: null,
    reversible: true,
    risk: "MEDIUM",
    status: "PROPOSED",
    target: "calendar:primary",
    verb: "calendar_event_create",
  };
  return {
    case_id: "calendar-case",
    case_type: "CONNECTED_SIGNAL",
    goal: "검토 일정 준비",
    status,
    version,
    next_action: "일정 내용을 확인해 주세요.",
    priority: 50,
    providers: ["google"],
    risk: "MEDIUM",
    summary: "합성 메일에서 준비한 일정이에요.",
    updated_at: "2026-09-14T00:00:00Z",
    why_now: "검토 일정을 놓치지 않도록 준비했어요.",
    current_plan_hash: "b".repeat(64),
    current_plan_version: 2,
    plan: {
      actions: [action],
      available_grant_modes: ["ONCE"],
      expected_outcome: "개인 일정 하나 등록",
      hash: "b".repeat(64),
      reason: "요청한 시간을 확인했어요.",
      required_scopes: ["calendar.events.owned"],
      reversibility: "등록한 일정은 Google Calendar에서 삭제할 수 있어요.",
      risk: "MEDIUM",
      version: 2,
    },
    actions: [{ ...action }],
    evidence: [],
    messages: [],
    timeline: [],
  };
}

let current: LiveCaseDetail;
let requests: jest.Mock;
let listeners: Set<(state: AppStateStatus) => void>;
let detailFailure = false;
let approvalExpired = false;

function tree() {
  return (
    <WorkspaceProvider>
      <CaseDetailScreen />
    </WorkspaceProvider>
  );
}
async function mount() {
  const screen = await render(tree());
  await act(async () => {
    await jest.advanceTimersByTimeAsync(0);
  });
  return screen;
}
function writes(path: string) {
  return requests.mock.calls.filter(
    ([input, init]) => String(input).endsWith(path) && init?.method === "POST",
  );
}
function result(status: LiveCaseDetail["status"], verified = false) {
  const value = detail(status, current.version + 1);
  value.actions[0] = {
    ...value.actions[0],
    status: verified ? "SUCCEEDED" : "VERIFYING",
    verified,
    result_ref: "google-calendar:primary:synthetic-event",
    html_url: verified
      ? "https://calendar.google.com/calendar/event?eid=synthetic-event"
      : null,
    result_summary: verified ? "합성 검토 일정을 등록하고 확인했어요." : null,
    error_code: verified ? null : "CALENDAR_RESULT_UNCONFIRMED",
  };
  return value;
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  current = detail();
  detailFailure = false;
  approvalExpired = false;
  listeners = new Set();
  jest
    .mocked(useAuth)
    .mockReturnValue({ user: { userId: "synthetic-owner" } } as never);
  jest
    .mocked(usePrototype)
    .mockReturnValue({ snapshot: null, error: null, status: "ready" } as never);
  jest
    .spyOn(AppState, "addEventListener")
    .mockImplementation((_type, listener) => {
      listeners.add(listener);
      return { remove: () => listeners.delete(listener) };
    });
  jest.spyOn(Linking, "openURL").mockResolvedValue(undefined);
  requests = jest.fn(async (input, init) => {
    const url = new URL(String(input));
    let body: unknown;
    if (init?.method === "POST") {
      if (
        url.pathname.endsWith("/decision") ||
        url.pathname.endsWith("/retry")
      ) {
        if (
          url.pathname.endsWith("/retry") &&
          current.status === "DECISION_REQUIRED" &&
          current.plan?.actions.length === 0 &&
          current.actions.length === 0
        ) {
          current = {
            ...current,
            status: "PREPARING",
            version: current.version + 1,
          };
          body = current;
        } else if (url.pathname.endsWith("/retry") && approvalExpired) {
          current = {
            ...current,
            status: "DECISION_REQUIRED",
            version: current.version + 1,
            next_action: "같은 일정 내용을 다시 승인해 주세요.",
          };
          body = current;
        } else {
          current = result("VERIFYING");
          body = { ...current, status: "QUEUED" };
        }
      } else if (url.pathname.endsWith("/stop")) {
        current = {
          ...current,
          status: "STOPPED",
          version: current.version + 1,
        };
        body = current;
      } else throw new Error("Unexpected mutation in synthetic transport");
    } else if (url.pathname === "/v1/cases/calendar-case") {
      if (detailFailure) throw new TypeError("Synthetic offline response");
      body = current;
    } else if (url.pathname === "/v1/cases") {
      body = {
        cases: url.searchParams.get("bucket") === "active" ? [current] : [],
        next_cursor: null,
      };
    } else if (url.pathname === "/v1/suggestions")
      body = { suggestions: [], next_cursor: null };
    else if (url.pathname === "/v1/suggestion-groups")
      body = { groups: [], next_cursor: null };
    else throw new Error("Unexpected read in synthetic transport");
    return new Response(JSON.stringify(body), {
      status: init?.method === "POST" ? 202 : 200,
      headers: { "content-type": "application/json" },
    });
  });
  const real = jest.requireActual<typeof import("@/src/api/productApi")>(
    "@/src/api/productApi",
  );
  jest.mocked(createProductApi).mockReturnValue(
    real.createProductApi({
      accessToken: async () => "synthetic-token",
      baseUrl: "https://api.example.invalid",
      requester: requests,
    }),
  );
});

afterEach(async () => {
  await cleanup();
  jest.restoreAllMocks();
  jest.useRealTimers();
});

test("approves the displayed Korean schedule once, polls the real API adapter and shows only verified completion", async () => {
  const screen = await mount();
  expect(
    screen.getByText("Start: October 7, 2026 at 9:30 AM (Korea time)"),
  ).toBeTruthy();
  expect(
    screen.getByText("End: October 7, 2026 at 10:30 AM (Korea time)"),
  ).toBeTruthy();
  expect(screen.queryByText(/synthetic-private-ref/)).toBeNull();
  expect(screen.queryByLabelText("Recurring permission")).toBeNull();
  await fireEvent.press(
    screen.getByRole("button", { name: "Allow once to continue" }),
  );
  expect(writes("/decision")).toHaveLength(1);
  expect(JSON.parse(writes("/decision")[0][1].body)).toEqual({
    decision: "APPROVE",
    expected_version: 3,
    plan_version: 2,
    plan_hash: "b".repeat(64),
    grant_mode: "ONCE",
  });
  expect(screen.getByText("Verifying the result")).toBeTruthy();
  expect(screen.queryByText("Task completed")).toBeNull();
  await fireEvent.press(screen.getByLabelText("Request a change Expand"));
  expect(screen.getByLabelText("Task message").props.editable).toBe(false);
  expect(screen.getByLabelText("Send task message")).toBeDisabled();
  current = result("COMPLETED", true);
  await act(async () => {
    await jest.advanceTimersByTimeAsync(2500);
  });
  expect(screen.getByText("Task completed")).toBeTruthy();
  expect(
    screen.getByText("합성 검토 일정을 등록하고 확인했어요."),
  ).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("link", { name: "Open event in Google Calendar" }),
  );
  expect(Linking.openURL).toHaveBeenCalledWith(current.actions[0].html_url);
  const count = requests.mock.calls.length;
  await act(async () => {
    await jest.advanceTimersByTimeAsync(7500);
  });
  expect(requests).toHaveBeenCalledTimes(count);
  expect(writes("/decision")).toHaveLength(1);
});

test.each([
  ["2026-10-07", "2026-10-08", "Date: October 7, 2026 (all day)"],
  [
    "2026-12-31",
    "2027-01-03",
    "Date: December 31, 2026 ~ January 2, 2027 (all day)",
  ],
])(
  "shows the inclusive all-day dates for %s through exclusive %s",
  async (start, end, label) => {
    current.plan!.actions[0].parameters = {
      summary: "종일 합성 일정",
      start,
      end,
    };
    const screen = await mount();
    expect(screen.getByText(label)).toBeTruthy();
    expect(screen.queryByText(/날짜 경계/)).toBeNull();
  },
);

test.each(["FAILED", "VERIFYING", "QUEUED"] as const)(
  "retries the same LIVE operation from %s using its current version",
  async (status) => {
    current = result(status);
    if (status === "FAILED") current.actions[0].status = "FAILED";
    const expected = current.version;
    const screen = await mount();
    await fireEvent.press(
      screen.getByLabelText("Retry failed or waiting steps"),
    );
    expect(writes("/retry")).toHaveLength(1);
    expect(JSON.parse(writes("/retry")[0][1].body)).toEqual({
      expected_version: expected,
    });
    expect(writes("/decision")).toHaveLength(0);
    expect(current.plan!.hash).toBe("b".repeat(64));
  },
);

test("keeps an accepted approval visible when the follow-up read fails and recovers on foreground", async () => {
  const screen = await mount();
  detailFailure = true;
  await fireEvent.press(screen.getByLabelText("Allow once to continue"));
  expect(screen.queryByLabelText("Allow once to continue")).toBeNull();
  expect(screen.getByText("Waiting to start")).toBeTruthy();
  detailFailure = false;
  current = result("COMPLETED", true);
  await act(async () => {
    for (const listener of listeners) listener("background");
  });
  const before = requests.mock.calls.length;
  await act(async () => {
    await jest.advanceTimersByTimeAsync(5000);
  });
  expect(requests).toHaveBeenCalledTimes(before);
  await act(async () => {
    for (const listener of listeners) listener("active");
  });
  expect(screen.getByText("Task completed")).toBeTruthy();
  expect(writes("/decision")).toHaveLength(1);
});

test.each(["STOPPED", "PERMISSION_REVOKED"] as const)(
  "preserves a known external result after %s without offering a retry",
  async (status) => {
    current = result(status, true);
    const screen = await mount();
    expect(
      screen.getByText(
        /Stopping or revoking access does not delete existing events\. An in-flight request may already have been saved\./,
      ),
    ).toBeTruthy();
    expect(
      screen.getByText("합성 검토 일정을 등록하고 확인했어요."),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Open event in Google Calendar" }),
    ).toBeTruthy();
    expect(screen.queryByLabelText("Retry failed or waiting steps")).toBeNull();
  },
);

test("does not label an unverified Calendar result as completed or open a foreign result URL", async () => {
  current = result("COMPLETED");
  current.actions[0].status = "SUCCEEDED";
  current.actions[0].html_url =
    "https://foreign.example.invalid/calendar/event";
  const screen = await mount();
  expect(screen.queryByText("Task completed")).toBeNull();
  expect(screen.getByText("Review the completion result")).toBeTruthy();
  expect(screen.queryByRole("link")).toBeNull();
  await fireEvent.press(screen.getByLabelText("Prepared plan Expand"));
  expect(screen.getByText("Result needs review")).toBeTruthy();
  expect(screen.queryByText("Result verified")).toBeNull();
});

test.each(["VERIFYING", "FAILED", "SUCCEEDED"] as const)(
  "reapproves the original schedule after expiry with previous action status %s",
  async (status) => {
    current = result("FAILED", status === "SUCCEEDED");
    current.actions[0].status = status;
    // A fully verified success does not need retry; expiry is already reported on reopen.
    if (status === "SUCCEEDED") current.status = "DECISION_REQUIRED";
    approvalExpired = true;
    const screen = await mount();
    if (status !== "SUCCEEDED")
      await fireEvent.press(
        screen.getByLabelText("Retry failed or waiting steps"),
      );
    expect(
      screen.getByText(
        "Check the event you already approved. An existing event will not be duplicated.",
      ),
    ).toBeTruthy();
    const expectedVersion = current.version;
    await fireEvent.press(screen.getByLabelText("Allow once to continue"));
    expect(JSON.parse(writes("/decision")[0][1].body)).toMatchObject({
      expected_version: expectedVersion,
      grant_mode: "ONCE",
      plan_hash: "b".repeat(64),
      plan_version: 2,
    });
  },
);

test("rechecks revoked Calendar permission through a new exact approval while preserving the failure", async () => {
  current = result("PERMISSION_REVOKED");
  current.actions[0].status = "FAILED";
  current.actions[0].error_code = "CALENDAR_PERMISSION_CHANGED";
  const screen = await mount();
  expect(
    screen.getByText("Calendar permissions changed. Execution stopped."),
  ).toBeTruthy();
  expect(screen.getByLabelText("Check connection")).toBeTruthy();
  await fireEvent.press(screen.getByLabelText("Allow once to continue"));
  expect(writes("/decision")).toHaveLength(1);
  expect(writes("/retry")).toHaveLength(0);
});

test("refetches after sign-out and return without replaying approval", async () => {
  current = result("VERIFYING");
  const screen = await mount();
  jest.mocked(useAuth).mockReturnValue({ user: null } as never);
  await screen.rerender(tree());
  expect(screen.queryByText("검토 일정 준비")).toBeNull();
  current = result("COMPLETED", true);
  jest
    .mocked(useAuth)
    .mockReturnValue({ user: { userId: "synthetic-owner" } } as never);
  await screen.rerender(tree());
  await act(async () => {
    await jest.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText("Task completed")).toBeTruthy();
  expect(writes("/decision")).toHaveLength(0);
});

test("keeps nested preparation metadata private and unsupported preparation unexecutable", async () => {
  current.plan!.actions[0].connector = "quietpilot";
  current.plan!.actions[0].verb = "prepare_reminder";
  current.plan!.actions[0].parameters = {
    calendar_event: {
      start: "2026-10-07T00:30:00Z",
      source_ref: "gmail:hidden-preparation",
    },
    calendar_preparation_status: "READY",
    source_ref: "gmail:hidden-preparation",
  };
  const screen = await mount();
  expect(screen.queryByLabelText("Allow once to continue")).toBeNull();
  await fireEvent.press(screen.getByLabelText("Review plan"));
  expect(
    screen.queryByText(
      /hidden-preparation|calendar_event|calendar_preparation_status|source_ref/,
    ),
  ).toBeNull();
  expect(writes("/decision")).toHaveLength(0);
});

test("stops further work without hiding an unconfirmed external result", async () => {
  current = result("VERIFYING");
  const version = current.version;
  const screen = await mount();
  await fireEvent.press(screen.getByLabelText("Stop remaining work"));
  expect(JSON.parse(writes("/stop")[0][1].body)).toEqual({
    expected_version: version,
  });
  expect(
    screen.getByText(
      /Stopping or revoking access does not delete existing events\. An in-flight request may already have been saved\./,
    ),
  ).toBeTruthy();
  expect(
    screen.getByText(
      "The event may exist, but its result is not yet verified.",
    ),
  ).toBeTruthy();
  expect(screen.queryByLabelText("Allow once to continue")).toBeNull();
  expect(screen.queryByLabelText("Retry failed or waiting steps")).toBeNull();
});

test("prepares a failed empty plan again on the same Case, retaining evidence and requiring approval afterward", async () => {
  current.plan!.actions = [];
  current.plan!.available_grant_modes = [];
  current.actions = [];
  current.evidence = [
    {
      evidence_id: "source-evidence",
      evidence_ref: "gmail:synthetic-source",
      revision: 1,
      provider: "google",
      label: "일정 안내",
      detail: "합성 검토 일정의 근거",
    },
  ];
  const evidence = current.evidence;
  const expectedVersion = current.version;
  const screen = await mount();
  expect(screen.getByText("No action prepared")).toBeTruthy();
  expect(screen.queryByLabelText("Allow once to continue")).toBeNull();
  await fireEvent.press(screen.getByLabelText("Prepare again"));
  expect(writes("/retry")).toHaveLength(1);
  expect(JSON.parse(writes("/retry")[0][1].body)).toEqual({
    expected_version: expectedVersion,
  });
  expect(current.case_id).toBe("calendar-case");
  expect(current.evidence).toEqual(evidence);
  expect(screen.getByText("Preparing your request")).toBeTruthy();
  expect(screen.queryByLabelText("Prepare again")).toBeNull();
  current = { ...detail("DECISION_REQUIRED", current.version + 1), evidence };
  await act(async () => {
    await jest.advanceTimersByTimeAsync(2500);
  });
  expect(screen.getByLabelText("Allow once to continue")).toBeTruthy();
  expect(writes("/decision")).toHaveLength(0);
  expect(writes("/v1/cases")).toHaveLength(0);
});

test.each([
  "RUNNING",
  "VERIFYING",
  "COMPLETED",
  "STOPPED",
  "PERMISSION_REVOKED",
] as const)("does not offer empty-plan preparation for %s", async (status) => {
  current.status = status;
  current.plan!.actions = [];
  current.actions = [];
  const screen = await mount();
  expect(screen.queryByLabelText("Prepare again")).toBeNull();
  expect(writes("/retry")).toHaveLength(0);
});
