import {
  fireEvent,
  render,
  waitFor,
  within,
} from "@testing-library/react-native";
import { act } from "react";
import { router, useLocalSearchParams } from "expo-router";
import { Keyboard } from "react-native";

import ActionQueueScreen from "@/app/(tabs)/index";
import { useAuth } from "@/src/auth/AuthProvider";
import { type MailInterestState } from "@/src/mail/mailApi";
import { useMailInterestState } from "@/src/mail/MailInterestProvider";
import { disconnectedGoogle } from "@/src/connections/googleConnectionView";
import { useGoogleConnection } from "@/src/connections/useGoogleConnection";
import type { PrototypeConnection } from "@/src/prototype/types";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";
import type { WorkspaceCase, WorkspaceSnapshot } from "@/src/workspace/types";

jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("aws-amplify/auth", () => ({ fetchAuthSession: jest.fn() }));
jest.mock("@/src/mail/MailInterestProvider", () => ({
  useMailInterestState: jest.fn(),
}));
jest.mock("@/src/workspace/WorkspaceProvider", () => ({
  useWorkspace: jest.fn(),
}));
jest.mock("@/src/connections/useGoogleConnection", () => ({
  useGoogleConnection: jest.fn(),
}));
jest.mock("@/src/theme/useAppTheme", () => ({
  useAppTheme: () => ({
    colors: {
      accent: "#3366ee",
      accentBorder: "#aabbff",
      accentSoft: "#eef2ff",
      background: "#ffffff",
      border: "#dddddd",
      danger: "#cc3344",
      dangerSoft: "#fff0f2",
      onAccent: "#ffffff",
      success: "#228855",
      successSoft: "#eaf8ef",
      surface: "#ffffff",
      surfaceMuted: "#f4f4f4",
      text: "#111111",
      textMuted: "#666666",
      textSubtle: "#888888",
      warning: "#aa7700",
      warningBorder: "#eed59b",
      warningSoft: "#fff8dd",
    },
  }),
}));
jest.mock("expo-router", () => ({
  __esModule: true,
  router: { push: jest.fn(), replace: jest.fn() },
  useLocalSearchParams: jest.fn(),
  useFocusEffect: function useFocusEffect(effect: () => void | (() => void)) {
    const { useEffect } = jest.requireActual("react");
    useEffect(effect, [effect]);
  },
}));

const mockedWorkspace = jest.mocked(useWorkspace);
const mockedGoogle = jest.mocked(useGoogleConnection);
const mockedAuth = jest.mocked(useAuth);
const mockedMail = jest.mocked(useMailInterestState);
const mockedParams = jest.mocked(useLocalSearchParams);

function mailState({
  profile = {},
  scan = {},
}: {
  profile?: Partial<MailInterestState["profile"]>;
  scan?: Partial<MailInterestState["scan"]>;
} = {}): MailInterestState {
  const nextProfile = {
    tags: [],
    description: "",
    version: 0,
    updated_at: null,
    ...profile,
  };
  return {
    profile: nextProfile,
    recommendations: {
      status: "READY",
      request_id: "recommendation-1",
      tags: [],
      title_count: 0,
      generated_at: null,
      error_code: null,
    },
    scan: {
      status: "NOT_STARTED",
      scan_id: null,
      profile_version: nextProfile.version,
      processed_count: 0,
      matched_count: 0,
      completed_at: null,
      error_code: null,
      ...scan,
    },
  };
}

function mailContext(
  overrides: Partial<ReturnType<typeof useMailInterestState>> = {},
) {
  const value = {
    state: null,
    results: null,
    loading: false,
    loadingMore: false,
    saving: false,
    requesting: false,
    error: null,
    enabled: false,
    refresh: jest.fn().mockResolvedValue(undefined),
    save: jest.fn().mockResolvedValue(undefined),
    recommend: jest.fn().mockResolvedValue(undefined),
    scan: jest.fn().mockResolvedValue(undefined),
    loadMore: jest.fn().mockResolvedValue(undefined),
    ...overrides,
  };
  mockedMail.mockReturnValue(value);
  return value;
}

function activeMailCase(): WorkspaceCase {
  return {
    caseId: "active-mail-case",
    caseType: "CONNECTED_SIGNAL",
    createdAt: "2026-09-01T00:00:00Z",
    currentPlan: null,
    dataSource: "LIVE",
    evidence: [
      {
        detail: "이미 확인한 메일 근거",
        evidenceId: "mail-evidence",
        label: "Gmail 근거",
        provider: "google",
        revision: 1,
      },
    ],
    goal: "이미 진행 중인 메일 일",
    messages: [],
    nextAction: "준비된 내용을 확인해 주세요.",
    partialFailure: null,
    planChange: null,
    policyIds: [],
    priority: 80,
    providers: ["google"],
    risk: "LOW",
    status: "DECISION_REQUIRED",
    summary: "진행 중인 메일 관련 작업",
    timeline: [],
    updatedAt: "2026-09-02T00:00:00Z",
    version: 1,
    whyNow: "현재 검토할 수 있어요.",
  };
}

function liveWorkspace(value = liveSnapshot(false)) {
  const workspace = {
    createDirectCase: jest.fn(),
    error: null,
    refresh: jest.fn().mockResolvedValue(undefined),
    snapshot: value,
    source: "LIVE",
    status: "ready",
  };
  mockedWorkspace.mockReturnValue(workspace as never);
  mockedGoogle.mockReturnValue({
    checking: false,
    connection: { ...disconnectedGoogle(), status: "CONNECTED" },
    error: null,
    refresh: jest.fn().mockResolvedValue(undefined),
    rescan: jest.fn().mockResolvedValue(undefined),
  } as never);
  return workspace;
}

function snapshot(): WorkspaceSnapshot {
  return {
    candidateGroups: [
      {
        candidateIds: ["candidate-1"],
        dataSource: "SCENARIO",
        groupId: "mail-group",
        icon: "email-outline",
        label: "메일",
        provider: "google",
        reason: "같은 결과",
      },
    ],
    candidates: [
      {
        candidateId: "candidate-1",
        caseTypeHint: "CONNECTED_SIGNAL",
        confidence: 0.93,
        createdAt: "2026-09-01T00:00:00Z",
        dataSource: "SCENARIO",
        eventAt: null,
        evidenceSummary: "Gmail 근거",
        opportunityType: "DEADLINE",
        outcome: "장학금 서류 제출",
        primaryGroupId: "mail-group",
        proposedActions: [
          {
            actionId: "action-1",
            connector: "quietpilot",
            label: "체크 항목 준비",
            parameters: {},
            requiredScopes: [],
            resultSummary: null,
            reversible: true,
            risk: "LOW",
            status: "PROPOSED",
            target: "case:deadline",
            verb: "prepare_task",
          },
        ],
        provider: "google",
        requiresApproval: false,
        risk: "LOW",
        safetyState: null,
        safetySummary: null,
        status: "VISIBLE",
        summary: "제출 안내",
        tags: ["deadline"],
        updatedAt: "2026-09-01T00:00:00Z",
        version: 1,
        whyNow: "마감이 다가오고 있어요.",
      },
    ],
    cases: [],
    dataSource: "SCENARIO",
    loadedScenario: "FULL",
  };
}

function liveSnapshot(withWork = true): WorkspaceSnapshot {
  const value = snapshot();
  return {
    ...value,
    candidateGroups: withWork
      ? value.candidateGroups.map((item) => ({
          ...item,
          dataSource: "LIVE" as const,
        }))
      : [],
    candidates: withWork
      ? value.candidates.map((item) => ({
          ...item,
          dataSource: "LIVE" as const,
        }))
      : [],
    dataSource: "LIVE",
    loadedScenario: null,
  };
}

function scanningGoogle(progress = 42): PrototypeConnection {
  return {
    ...disconnectedGoogle(),
    discoveryRevision: 1,
    scanProgress: progress,
    status: "SCANNING",
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockedAuth.mockReturnValue({ user: { userId: "home-user" } } as never);
  mockedParams.mockReturnValue({});
  mailContext();
  mockedWorkspace.mockReturnValue({
    createDirectCase: jest.fn(),
    error: null,
    refresh: jest.fn().mockResolvedValue(undefined),
    snapshot: snapshot(),
    source: "SCENARIO",
    status: "ready",
  } as never);
  mockedGoogle.mockReturnValue({
    checking: false,
    connection: disconnectedGoogle(),
    error: null,
    refresh: jest.fn(),
    rescan: jest.fn(),
  } as never);
});

test("shows stance filters and source-grouped work", async () => {
  const screen = await render(<ActionQueueScreen />);

  expect(screen.getByText("Decide now")).toBeTruthy();
  expect(screen.getByText("Preparing")).toBeTruthy();
  expect(screen.getAllByText("Mail").length).toBeGreaterThan(0);
  expect(screen.getByText("장학금 서류 제출")).toBeTruthy();
});

test("opens a grouped item and can collapse its source", async () => {
  const screen = await render(<ActionQueueScreen />);

  await act(async () => {
    fireEvent.press(screen.getByLabelText("장학금 서류 제출, Review plan"));
  });
  expect(router.push).toHaveBeenCalledWith({
    pathname: "/suggestions/[groupId]",
    params: { groupId: "mail-group", suggestionId: "candidate-1" },
  });

  await act(async () => {
    fireEvent.press(screen.getByLabelText("Mail 1 items Collapse"));
  });
  await waitFor(() =>
    expect(screen.queryByText("장학금 서류 제출")).toBeNull(),
  );
});

test("keeps counts on nonempty filters without repeating the total in the header", async () => {
  const screen = await render(<ActionQueueScreen />);

  expect(screen.getByRole("tab", { name: "Decide now, 1 items" })).toBeTruthy();
  expect(screen.getByRole("tab", { name: "Preparing" })).toBeTruthy();
  expect(screen.queryByText("0")).toBeNull();
  expect(screen.queryByText(/지금 결정이 필요한/)).toBeNull();
});

test("header search filters the queue and closing it clears the hidden query", async () => {
  const dismissKeyboard = jest.spyOn(Keyboard, "dismiss");
  const screen = await render(<ActionQueueScreen />);
  const searchButton = screen.getByRole("button", {
    name: "Search and filters",
  });

  expect(screen.queryByLabelText("Search tasks to review")).toBeNull();
  await act(async () => fireEvent.press(searchButton));
  expect(searchButton.props.accessibilityState).toEqual({ expanded: true });
  await act(async () => {
    fireEvent.changeText(
      screen.getByLabelText("Search tasks to review"),
      "없는 검색어",
    );
  });
  expect(screen.queryByText("장학금 서류 제출")).toBeNull();
  expect(screen.getByText("No matching tasks")).toBeTruthy();

  await act(async () => fireEvent.press(searchButton));
  expect(screen.queryByLabelText("Search tasks to review")).toBeNull();
  expect(screen.getByText("장학금 서류 제출")).toBeTruthy();
  expect(searchButton.props.accessibilityState).toEqual({ expanded: false });
  expect(dismissKeyboard).toHaveBeenCalledTimes(1);
  dismissKeyboard.mockRestore();
});

test("live registration stays a small entry and does not expose stale mail or run recommendations", async () => {
  liveWorkspace(liveSnapshot());
  const initial = mailState();
  initial.recommendations.status = "NOT_STARTED";
  const mail = mailContext({ enabled: true, state: initial });
  const screen = await render(<ActionQueueScreen />);
  expect(screen.queryByText("Set your interests first.")).toBeNull();
  expect(screen.queryByRole("header", { name: "Relevant mail" })).toBeNull();
  expect(screen.getByLabelText("Mail 0 items Collapse")).toBeTruthy();
  expect(screen.queryByText("장학금 서류 제출")).toBeNull();
  expect(screen.queryByText("No new tasks")).toBeNull();
  expect(screen.queryByLabelText("Enter interest tags")).toBeNull();
  await fireEvent.press(screen.getByLabelText("Set mail interests"));
  expect(router.push).toHaveBeenCalledWith("/mail-interests");
  expect(mail.save).not.toHaveBeenCalled();
  expect(mail.recommend).not.toHaveBeenCalled();
});

test("OAuth return keeps the main screen compact after profile hydration", async () => {
  liveWorkspace();
  mockedParams.mockReturnValue({ mailSetup: "1" });
  mailContext({ enabled: true, loading: true, state: null });
  const screen = await render(<ActionQueueScreen />);
  expect(screen.queryByLabelText("Enter interest tags")).toBeNull();
  const initial = mailState();
  initial.recommendations.status = "NOT_STARTED";
  const mail = mailContext({ enabled: true, state: initial });
  await screen.rerender(<ActionQueueScreen />);
  expect(screen.getByLabelText("Set mail interests")).toBeTruthy();
  expect(screen.queryByLabelText("Enter interest tags")).toBeNull();
  expect(screen.queryByLabelText("Describe mail interests")).toBeNull();
  expect(mail.recommend).not.toHaveBeenCalled();
  expect(router.push).not.toHaveBeenCalled();
});

test("the main card moves from scan progress to related results and keeps interests editable", async () => {
  liveWorkspace();
  mockedGoogle.mockReturnValue({
    ...mockedGoogle(),
    connection: scanningGoogle(18),
  });
  mailContext({
    enabled: true,
    state: mailState({
      profile: { tags: ["학교"], description: "학교 소식", version: 2 },
      scan: { status: "PENDING", scan_id: "scan-2", processed_count: 4 },
    }),
  });
  const screen = await render(<ActionQueueScreen />);
  expect(screen.getByText("Finding relevant mail")).toBeTruthy();
  expect(screen.queryByText("확인 중 · 18%")).toBeNull();
  expect(screen.queryByText("No new tasks")).toBeNull();

  mailContext({
    enabled: true,
    state: mailState({
      profile: { tags: ["학교"], description: "학교 소식", version: 2 },
      scan: {
        status: "READY",
        scan_id: "scan-2",
        processed_count: 6,
        matched_count: 3,
      },
    }),
  });
  await screen.rerender(<ActionQueueScreen />);
  expect(screen.queryByRole("header", { name: "Relevant mail" })).toBeNull();
  await fireEvent.press(screen.getByLabelText("Browse mail by interest"));
  expect(router.push).toHaveBeenCalledWith("/mail");
  await fireEvent.press(screen.getByLabelText("Review mail interests"));
  expect(router.push).toHaveBeenLastCalledWith("/mail-interests");
  expect(screen.queryByLabelText("Enter interest tags")).toBeNull();
});

test.each([
  "unconfigured",
  "pending",
  "wrong-profile",
  "ready-zero-stale",
] as const)(
  "%s hides old mail Candidates before source grouping while preserving Cases and non-mail work",
  async (kind) => {
    const value = liveSnapshot();
    const original = value.candidates[0];
    value.candidates = [
      {
        ...original,
        mailProfileVersion: 1,
        mailScanId: "scan-1",
        mailDerived: true,
      },
      {
        ...original,
        candidateId: "stale-calendar-mail",
        outcome: "이전 메일에서 찾은 일정",
        tags: ["calendar"],
        mailProfileVersion: 1,
        mailScanId: "scan-1",
        mailDerived: true,
      },
      {
        ...original,
        candidateId: "non-mail-calendar",
        outcome: "캘린더 일정 확인",
        tags: ["calendar"],
        mailDerived: false,
        mailProfileVersion: null,
        mailScanId: null,
      },
      {
        ...original,
        candidateId: "message-work",
        outcome: "문자 요청 확인",
        provider: "sms",
        mailDerived: false,
      },
    ];
    value.candidateGroups[0].candidateIds = value.candidates.map(
      (candidate) => candidate.candidateId,
    );
    value.cases = [activeMailCase()];
    liveWorkspace(value);
    mailContext({
      enabled: true,
      state: mailState({
        profile: { tags: kind === "unconfigured" ? [] : ["취업"], version: 2 },
        scan: {
          status: kind === "pending" ? "PENDING" : "READY",
          profile_version: kind === "wrong-profile" ? 1 : 2,
          scan_id: "scan-2",
          matched_count: 0,
        },
      }),
    });
    const screen = await render(<ActionQueueScreen />);

    expect(screen.queryByText("장학금 서류 제출")).toBeNull();
    expect(screen.queryByText("이전 메일에서 찾은 일정")).toBeNull();
    expect(screen.getByText("이미 진행 중인 메일 일")).toBeTruthy();
    expect(screen.getByText("캘린더 일정 확인")).toBeTruthy();
    expect(screen.getByText("문자 요청 확인")).toBeTruthy();
  },
);

test("only Candidates carrying the current mail profile and scan enter the live queue", async () => {
  const value = liveSnapshot();
  value.candidates.push({
    ...value.candidates[0],
    candidateId: "current-mail",
    outcome: "현재 관심에 맞는 제출",
    mailProfileVersion: 2,
    mailScanId: "scan-2",
    mailDerived: true,
  });
  value.candidateGroups[0].candidateIds.push("current-mail");
  liveWorkspace(value);
  mailContext({
    enabled: true,
    state: mailState({
      profile: { tags: ["학교"], version: 2 },
      scan: { status: "READY", scan_id: "scan-2", matched_count: 1 },
    }),
  });
  const screen = await render(<ActionQueueScreen />);

  expect(screen.getByText("현재 관심에 맞는 제출")).toBeTruthy();
  expect(screen.queryByText("장학금 서류 제출")).toBeNull();
  expect(screen.getByRole("tab", { name: "Decide now, 1 items" })).toBeTruthy();
});

test("pull-to-refresh updates workspace, mail state and Google connection together", async () => {
  const workspace = liveWorkspace();
  const mail = mailContext({ enabled: true, state: mailState() });
  const google = mockedGoogle();
  const screen = await render(<ActionQueueScreen />);
  const previousMailReads = jest.mocked(mail.refresh).mock.calls.length;
  const scrollViews = screen.container.queryAll((node) =>
    Boolean(node.props.refreshControl),
  );
  expect(scrollViews).toHaveLength(1);
  // The native control mock drops props; use the control passed to ScrollView.
  const refreshControl = scrollViews[0].props.refreshControl;
  await act(async () => refreshControl.props.onRefresh());

  expect(workspace.refresh).toHaveBeenCalledTimes(1);
  expect(google.refresh).toHaveBeenCalledTimes(1);
  expect(mail.refresh).toHaveBeenCalledTimes(previousMailReads + 1);
});

test("token-available setup offers the separate page without showing the pending editor", async () => {
  liveWorkspace();
  mockedGoogle.mockReturnValue({
    ...mockedGoogle(),
    connection: {
      ...disconnectedGoogle(),
      status: "CONNECTING",
      grantedScopes: ["https://www.googleapis.com/auth/gmail.readonly"],
    },
  });
  mockedParams.mockReturnValue({ mailSetup: "1" });
  const pending = mailState();
  pending.recommendations = {
    ...pending.recommendations,
    status: "PENDING",
    request_id: "queued-setup-recommendation",
  };
  const mail = mailContext({ enabled: true, state: pending });
  const screen = await render(<ActionQueueScreen />);
  expect(screen.queryByRole("button", { name: "Connect mail" })).toBeNull();
  expect(screen.queryByLabelText("Enter interest tags")).toBeNull();
  await fireEvent.press(screen.getByLabelText("Set mail interests"));
  expect(router.push).toHaveBeenCalledWith("/mail-interests");
  expect(mail.save).not.toHaveBeenCalled();
  expect(mail.recommend).not.toHaveBeenCalled();
});

test("a disconnected account keeps both reconnect and saved-interest entry points", async () => {
  liveWorkspace();
  mockedGoogle.mockReturnValue({
    ...mockedGoogle(),
    connection: { ...disconnectedGoogle(), version: 3 },
  });
  const mail = mailContext({
    enabled: true,
    state: mailState({
      profile: { tags: ["학교"], description: "학교 소식", version: 2 },
    }),
  });
  const screen = await render(<ActionQueueScreen />);
  await fireEvent.press(screen.getByRole("button", { name: "Reconnect" }));
  expect(router.push).toHaveBeenLastCalledWith("/connections");
  await fireEvent.press(screen.getByLabelText("Review mail interests"));
  expect(router.push).toHaveBeenLastCalledWith("/mail-interests");
  expect(mail.save).not.toHaveBeenCalled();
  expect(mail.recommend).not.toHaveBeenCalled();
});

test("a new account sees Connect Google without a recovery or saved-interest claim", async () => {
  liveWorkspace();
  mockedGoogle.mockReturnValue({
    ...mockedGoogle(),
    connection: disconnectedGoogle(),
  });
  const state = mailState();
  state.recommendations = {
    ...state.recommendations,
    status: "NOT_STARTED",
    request_id: null,
  };
  const mail = mailContext({ enabled: true, state });
  const screen = await render(<ActionQueueScreen />);
  expect(
    screen.getByText("Connect Google to find relevant mail."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Reconnect" })).toBeNull();
  expect(screen.queryByText(/Your saved interests will be kept/)).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Connect Google" }));
  expect(router.push).toHaveBeenLastCalledWith("/connections");
  expect(mail.save).not.toHaveBeenCalled();
  expect(mail.recommend).not.toHaveBeenCalled();
  expect(mail.scan).not.toHaveBeenCalled();
});

test.each(["previous-record", "authorization-error"])(
  "a disconnected account with %s keeps recovery wording even with no interests",
  async (reason) => {
    liveWorkspace();
    mockedGoogle.mockReturnValue({
      ...mockedGoogle(),
      connection: {
        ...disconnectedGoogle(),
        version: reason === "previous-record" ? 3 : 0,
      },
    });
    const state = mailState();
    if (reason === "authorization-error")
      state.scan.error_code = "GOOGLE_AUTH_REQUIRED";
    mailContext({ enabled: true, state });
    const screen = await render(<ActionQueueScreen />);
    expect(
      screen.getByText("Reconnect Google to resume mail checks."),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reconnect" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect Google" })).toBeNull();
  },
);

test("zero results and a search with no matches never remove the interest entry", async () => {
  liveWorkspace(liveSnapshot(false));
  mailContext({
    enabled: true,
    state: mailState({
      profile: { tags: ["학교"], description: "", version: 2 },
      scan: {
        status: "READY",
        profile_version: 2,
        matched_count: 0,
        scan_id: "scan-empty",
      },
    }),
  });
  const screen = await render(<ActionQueueScreen />);
  const mailHeader = screen.getByLabelText("Mail 0 items Collapse");
  expect(
    within(mailHeader).queryByLabelText("Review mail interests"),
  ).toBeNull();
  await fireEvent.press(screen.getByLabelText("Search and filters"));
  await fireEvent.changeText(
    screen.getByLabelText("Search tasks to review"),
    "없는 검색어",
  );
  expect(screen.getByLabelText("Mail 0 items Collapse")).toBeTruthy();
  await fireEvent.press(screen.getByRole("tab", { name: "Waiting" }));
  expect(screen.getByLabelText("Mail 0 items Collapse")).toBeTruthy();
  await fireEvent.press(screen.getByLabelText("Mail 0 items Collapse"));
  expect(screen.getByLabelText("Mail 0 items Expand")).toBeTruthy();
  await fireEvent.press(screen.getByLabelText("Review mail interests"));
  expect(router.push).toHaveBeenLastCalledWith("/mail-interests");
  expect(screen.getByLabelText("Mail 0 items Expand")).toBeTruthy();
});

async function pendingDirectRequest() {
  const workspace = liveWorkspace();
  let resolve!: (value: WorkspaceCase) => void;
  let reject!: (reason: Error) => void;
  const response = new Promise<WorkspaceCase>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  workspace.createDirectCase.mockReturnValue(response);
  const screen = await render(<ActionQueueScreen />);
  await fireEvent.changeText(
    screen.getByLabelText("Make a direct request"),
    "처음 요청",
  );
  const sending = fireEvent.press(
    screen.getByRole("button", { name: "Send request" }),
  );
  await waitFor(() =>
    expect(workspace.createDirectCase).toHaveBeenCalledWith("처음 요청"),
  );
  return { screen, workspace, sending, resolve, reject };
}

test.each([
  { edits: ["새로 쓰는 요청"], expected: "새로 쓰는 요청" },
  { edits: ["다른 내용", "처음 요청"], expected: "처음 요청" },
  { edits: [], expected: "" },
])(
  "direct request completion preserves later edits: $edits",
  async ({ edits, expected }) => {
    const { screen, workspace, sending, resolve } =
      await pendingDirectRequest();
    for (const edit of edits) {
      await fireEvent.changeText(
        screen.getByLabelText("Make a direct request"),
        edit,
      );
    }
    await act(async () => {
      resolve(activeMailCase());
      await sending;
    });
    expect(screen.getByLabelText("Make a direct request").props.value).toBe(
      expected,
    );
    expect(workspace.createDirectCase).toHaveBeenCalledTimes(1);
    expect(router.push).toHaveBeenCalledWith({
      pathname: "/cases/[caseId]",
      params: { caseId: "active-mail-case" },
    });
  },
);

test("a second press while direct creation is unresolved cannot create another task", async () => {
  const { screen, workspace, sending, resolve } = await pendingDirectRequest();
  const second = fireEvent.press(
    screen.getByRole("button", { name: "Send request" }),
  );
  await act(async () => {
    resolve(activeMailCase());
    await Promise.all([sending, second]);
  });
  expect(workspace.createDirectCase).toHaveBeenCalledTimes(1);
  expect(router.push).toHaveBeenCalledTimes(1);
});

test("a failed direct request cannot mark a later draft as failed", async () => {
  const { screen, sending, reject } = await pendingDirectRequest();
  await fireEvent.changeText(
    screen.getByLabelText("Make a direct request"),
    "다음 요청",
  );
  await act(async () => {
    reject(new Error("이전 요청 실패"));
    await sending;
  });
  expect(screen.getByLabelText("Make a direct request").props.value).toBe(
    "다음 요청",
  );
  expect(screen.queryByText("이전 요청 실패")).toBeNull();
  expect(router.push).not.toHaveBeenCalled();
});

test.each(["owner", "source"] as const)(
  "a direct response cannot reopen an old task after the %s changes and returns",
  async (boundary) => {
    const { screen, workspace, sending, resolve } =
      await pendingDirectRequest();
    if (boundary === "owner")
      mockedAuth.mockReturnValue({ user: { userId: "another-user" } } as never);
    else
      mockedWorkspace.mockReturnValue({
        ...workspace,
        source: "SCENARIO",
      } as never);
    await screen.rerender(<ActionQueueScreen />);
    expect(screen.getByLabelText("Make a direct request").props.value).toBe("");
    if (boundary === "owner")
      mockedAuth.mockReturnValue({ user: { userId: "home-user" } } as never);
    else mockedWorkspace.mockReturnValue(workspace as never);
    await screen.rerender(<ActionQueueScreen />);
    await fireEvent.changeText(
      screen.getByLabelText("Make a direct request"),
      "현재 화면의 요청",
    );
    await act(async () => {
      resolve(activeMailCase());
      await sending;
    });
    expect(router.push).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Make a direct request").props.value).toBe(
      "현재 화면의 요청",
    );
  },
);
