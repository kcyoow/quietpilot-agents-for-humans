import { fireEvent, render, waitFor } from "@testing-library/react-native";
import { act } from "react";

import CaseDetailScreen from "@/app/cases/[caseId]";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";
import type { WorkspaceCase, WorkspaceSnapshot } from "@/src/workspace/types";

let mockSelectedCaseId = "case-connected";

jest.mock("@/src/workspace/WorkspaceProvider", () => ({
  useWorkspace: jest.fn(),
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
      surface: "#ffffff",
      surfaceMuted: "#f4f4f4",
      text: "#111111",
      textMuted: "#666666",
      textSubtle: "#888888",
      warning: "#aa7700",
      warningBorder: "#e8cc77",
      warningSoft: "#fff8dd",
    },
  }),
}));
jest.mock("expo-router", () => ({
  __esModule: true,
  router: { back: jest.fn(), push: jest.fn() },
  useFocusEffect: (callback: () => void) => {
    const { useEffect } = jest.requireActual("react");
    useEffect(callback, [callback]);
  },
  useLocalSearchParams: () => ({ caseId: mockSelectedCaseId }),
}));

const mockedWorkspace = jest.mocked(useWorkspace);

function caseItem(
  caseId: string,
  caseType: WorkspaceCase["caseType"],
): WorkspaceCase {
  return {
    caseId,
    caseType,
    createdAt: "2026-08-30T00:00:00Z",
    currentPlan: {
      actions: [
        {
          actionId: `action-${caseId}`,
          connector: "google",
          label: "일정 초안 만들기",
          parameters: { title: "프로젝트 제출 준비" },
          requiredScopes: ["calendar.events.owned"],
          resultSummary: null,
          reversible: true,
          risk: "LOW",
          status: "PROPOSED",
          target: "calendar://primary/draft",
          verb: "CREATE",
        },
      ],
      availableGrantModes: ["ONCE", "CONDITIONAL"],
      expectedOutcome: "제출 준비 일정을 확인합니다.",
      hash: "a".repeat(64),
      reason: "메일 마감과 직접 요청을 함께 확인했어요.",
      requiredScopes: ["calendar.events.owned"],
      reversibility: "생성한 일정은 삭제할 수 있어요.",
      risk: "LOW",
      version: 2,
    },
    dataSource: "SCENARIO",
    evidence: [
      {
        detail: "마감은 9월 3일이에요.",
        evidenceId: `evidence-${caseId}`,
        label: "Gmail 마감 안내",
        provider: "google",
        revision: 2,
      },
    ],
    goal: `${caseType} 목표`,
    messages: [],
    nextAction: "실행 범위를 확인해 주세요.",
    partialFailure: null,
    planChange: null,
    policyIds: [],
    priority: 50,
    providers: ["google"],
    risk: "LOW",
    status: "DECISION_REQUIRED",
    summary: "근거와 실행 범위를 하나로 정리했어요.",
    timeline: [
      {
        body: "근거에서 계획을 준비했어요.",
        eventId: `event-${caseId}`,
        label: "계획 준비 완료",
        occurredAt: "2026-08-30T00:00:01Z",
        state: "CURRENT",
      },
    ],
    updatedAt: "2026-08-30T00:00:01Z",
    version: 3,
    whyNow: "새로운 마감 근거를 확인했어요.",
  };
}

const cases = [
  caseItem("case-connected", "CONNECTED_SIGNAL"),
  caseItem("case-direct", "DIRECT_DELEGATION"),
  caseItem("case-routine", "ROUTINE_DISCOVERY"),
  {
    ...caseItem("case-exception", "EXCEPTION_APPROVAL"),
    partialFailure: {
      explanation: "첫 단계는 완료됐지만 두 번째 단계가 실패했어요.",
      failedActionIds: ["action-failed"],
      pendingActionIds: ["action-pending"],
      succeededActionIds: ["action-succeeded"],
    },
    planChange: {
      currentSummary: "마감 시간이 오후로 바뀌었어요.",
      previousHash: "b".repeat(64),
      previousSummary: "마감 시간이 오전이었어요.",
      previousVersion: 1,
      reason: "중요한 시간 근거가 바뀌어 재승인이 필요해요.",
    },
  },
] satisfies WorkspaceCase[];

function workspaceSnapshot(): WorkspaceSnapshot {
  return {
    candidateGroups: [],
    candidates: [],
    cases,
    dataSource: "SCENARIO",
    loadedScenario: "FULL",
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockSelectedCaseId = "case-connected";
  mockedWorkspace.mockReturnValue({
    approveCase: jest.fn(),
    deferCase: jest.fn(),
    error: null,
    loadCase: jest.fn().mockResolvedValue(null),
    postCaseMessage: jest.fn(),
    retryCase: jest.fn(),
    snapshot: workspaceSnapshot(),
    source: "SCENARIO",
    status: "ready",
    stopCase: jest.fn(),
  } as never);
});

test.each([
  ["case-connected", "From connections"],
  ["case-direct", "Direct requests"],
  ["case-routine", "Recurring information"],
  ["case-exception", "Refresh needed"],
])(
  "renders the %s Case through the shared detail surface",
  async (caseId, label) => {
    mockSelectedCaseId = caseId;
    const screen = await render(<CaseDetailScreen />);

    expect(screen.getByText(label)).toBeTruthy();
    expect(screen.queryByText("calendar.events.owned")).toBeNull();
    await act(async () => {
      fireEvent.press(screen.getByLabelText("Prepared plan Expand"));
    });
    await waitFor(() =>
      expect(
        screen.getByText("Only within your connected permissions"),
      ).toBeTruthy(),
    );
    expect(screen.getByText("프로젝트 제출 준비")).toBeTruthy();
  },
);

test("makes stale-plan and partial-failure recovery understandable", async () => {
  mockSelectedCaseId = "case-exception";
  const screen = await render(<CaseDetailScreen />);

  expect(screen.getByText("Previous approval is no longer valid")).toBeTruthy();
  expect(screen.getByText("Some steps are incomplete")).toBeTruthy();
  expect(screen.queryByText("Retry failed or waiting steps")).toBeNull();
  expect(screen.queryByText("이전 승인 재사용 차단 확인하기")).toBeNull();
});

function workspaceForCase(
  item: WorkspaceCase,
  source: "LIVE" | "SCENARIO" = "SCENARIO",
) {
  mockSelectedCaseId = item.caseId;
  const workspace = {
    approveCase: jest.fn().mockResolvedValue(item),
    deferCase: jest.fn().mockResolvedValue(item),
    error: null,
    loadCase: jest.fn().mockResolvedValue(item),
    postCaseMessage: jest.fn().mockResolvedValue([]),
    retryCase: jest.fn().mockResolvedValue(item),
    snapshot: {
      ...workspaceSnapshot(),
      cases: [{ ...item, dataSource: source }],
      dataSource: source,
      loadedScenario: source === "SCENARIO" ? "FULL" : null,
    },
    source,
    status: "ready" as const,
    stopCase: jest.fn().mockResolvedValue(item),
  };
  mockedWorkspace.mockReturnValue(workspace as never);
  return workspace;
}

const readyPlan = caseItem("case-audit", "CONNECTED_SIGNAL").currentPlan!;

test.each([
  { name: "no plan", plan: null },
  { name: "zero actions", plan: { ...readyPlan, actions: [] } },
  { name: "no grant modes", plan: { ...readyPlan, availableGrantModes: [] } },
  { name: "missing hash", plan: { ...readyPlan, hash: "" } },
  { name: "invalid version", plan: { ...readyPlan, version: 0 } },
  {
    name: "failed action",
    plan: {
      ...readyPlan,
      actions: [{ ...readyPlan.actions[0], status: "FAILED" as const }],
    },
  },
  {
    name: "cancelled action",
    plan: {
      ...readyPlan,
      actions: [{ ...readyPlan.actions[0], status: "CANCELLED" as const }],
    },
  },
])("offers recovery rather than approval for $name", async ({ plan }) => {
  const workspace = workspaceForCase({
    ...caseItem("case-audit", "CONNECTED_SIGNAL"),
    currentPlan: plan,
  });
  const screen = await render(<CaseDetailScreen />);

  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Clarify request", disabled: false }),
    ).toBeTruthy(),
  );
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
  expect(
    screen.queryByRole("button", { name: /Allow once to continue$/ }),
  ).toBeNull();
  expect(screen.queryByText("Approve this plan?")).toBeNull();
  expect(workspace.approveCase).not.toHaveBeenCalled();
  expect(workspace.retryCase).not.toHaveBeenCalled();
});

test("a failed case cannot approve a populated plan", async () => {
  const workspace = workspaceForCase({
    ...caseItem("case-audit", "CONNECTED_SIGNAL"),
    status: "FAILED",
  });
  const screen = await render(<CaseDetailScreen />);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Clarify request" }),
    ).toBeTruthy(),
  );
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
  expect(
    screen.queryByRole("button", { name: /Allow once to continue$/ }),
  ).toBeNull();
  expect(workspace.approveCase).not.toHaveBeenCalled();
});

test("an empty live plan opens the existing message editor and sends only the user's correction", async () => {
  const workspace = workspaceForCase(
    {
      ...caseItem("case-audit", "CONNECTED_SIGNAL"),
      currentPlan: { ...readyPlan, actions: [] },
      summary: "계획 출력이 안전 기준을 통과하지 못했어요.",
    },
    "LIVE",
  );
  const screen = await render(<CaseDetailScreen />);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Clarify request", disabled: false }),
    ).toBeTruthy(),
  );
  expect(
    screen.getByText("계획 출력이 안전 기준을 통과하지 못했어요."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: /재시도/ })).toBeNull();

  await fireEvent.press(
    screen.getByRole("button", { name: "Clarify request" }),
  );
  const send = screen.getByRole("button", {
    name: "Send task message",
    disabled: true,
  });
  await fireEvent.press(send);
  expect(workspace.postCaseMessage).not.toHaveBeenCalled();
  await fireEvent.changeText(
    screen.getByLabelText("Task message"),
    "보낼 초안에 필요한 항목만 정리해 주세요.",
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Send task message", disabled: false }),
  );

  expect(workspace.postCaseMessage).toHaveBeenCalledWith(
    "case-audit",
    "보낼 초안에 필요한 항목만 정리해 주세요.",
  );
  expect(workspace.approveCase).not.toHaveBeenCalled();
  expect(workspace.retryCase).not.toHaveBeenCalled();
});

test("a complete live plan explains execution limits without editable grant controls", async () => {
  const workspace = workspaceForCase(
    caseItem("case-audit", "CONNECTED_SIGNAL"),
    "LIVE",
  );
  const screen = await render(<CaseDetailScreen />);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Not now", disabled: false }),
    ).toBeTruthy(),
  );
  expect(
    screen.getByText(
      /Review the plan\. External execution is unavailable for this task\./,
    ),
  ).toBeTruthy();
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
  expect(
    screen.queryByRole("button", { name: /Allow once to continue$/ }),
  ).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Not now" }));
  expect(workspace.deferCase).toHaveBeenCalledWith("case-audit");
  expect(workspace.approveCase).not.toHaveBeenCalled();
});

test("shows the reason and prepared summary, then reviews a live plan without approving it", async () => {
  const item = caseItem("case-review", "CONNECTED_SIGNAL");
  const workspace = workspaceForCase(item, "LIVE");
  const screen = await render(<CaseDetailScreen />);

  expect(screen.getByText(item.whyNow)).toBeTruthy();
  expect(screen.getByText(item.currentPlan!.expectedOutcome)).toBeTruthy();
  expect(
    screen.queryByText("Only within your connected permissions"),
  ).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Review plan" }));

  expect(
    screen.getByRole("button", { name: "Prepared plan Collapse" }),
  ).toBeTruthy();
  expect(
    screen.getByText("Only within your connected permissions"),
  ).toBeTruthy();
  expect(workspace.approveCase).not.toHaveBeenCalled();
  expect(screen.queryAllByRole("radio")).toHaveLength(0);
});

test("keeps exact source evidence available without exposing its metadata in the main flow", async () => {
  const item = caseItem("case-evidence", "CONNECTED_SIGNAL");
  const detail =
    "sender_domain=example.com; received_at_unix_ms=42; scope=mail.read";
  workspaceForCase(
    { ...item, evidence: [{ ...item.evidence[0], detail }] },
    "LIVE",
  );
  const screen = await render(<CaseDetailScreen />);

  expect(screen.queryByText(detail)).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Sources Expand" }));
  expect(
    screen.getByText("Information verified through your connected service."),
  ).toBeTruthy();
  expect(screen.queryByText(detail)).toBeNull();
  await fireEvent.press(
    screen.getByRole("button", {
      name: "Gmail 마감 안내 Original source Expand",
    }),
  );
  expect(screen.getByText(detail)).toBeTruthy();
  await fireEvent.press(
    screen.getByRole("button", {
      name: "Gmail 마감 안내 Original source Collapse",
    }),
  );
  expect(screen.queryByText(detail)).toBeNull();
});

test("keeps version changes inspectable while leading with what changed", async () => {
  workspaceForCase(cases[3]);
  const screen = await render(<CaseDetailScreen />);

  expect(screen.getByText(cases[3].planChange!.previousSummary)).toBeTruthy();
  expect(screen.getByText(cases[3].planChange!.currentSummary)).toBeTruthy();
  expect(
    screen.queryByText("Previous version 1 → Current version 2"),
  ).toBeNull();
  await fireEvent.press(
    screen.getByRole("button", { name: "View change details" }),
  );
  expect(
    screen.getByText("Previous version 1 → Current version 2"),
  ).toBeTruthy();
});

test.each([
  ["APPROVED", "Request received"],
  ["VERIFYING", "Verifying the result"],
  ["COMPLETED", "Task completed"],
] as const)("distinguishes %s from completed work", async (status, title) => {
  workspaceForCase(
    { ...caseItem("case-status", "CONNECTED_SIGNAL"), status },
    "LIVE",
  );
  const screen = await render(<CaseDetailScreen />);

  expect(screen.getByText(title)).toBeTruthy();
  if (status !== "COMPLETED") {
    expect(screen.queryByText("Task completed")).toBeNull();
  }
});

test("a ready scenario plan preserves grant selection and the exact current plan approval", async () => {
  const item = caseItem("case-audit", "CONNECTED_SIGNAL");
  const workspace = workspaceForCase(item);
  const screen = await render(<CaseDetailScreen />);
  await waitFor(() =>
    expect(
      screen.getByRole("radio", {
        name: "Conditional permission",
        disabled: false,
      }),
    ).toBeTruthy(),
  );
  await fireEvent.press(
    screen.getByRole("radio", { name: "Conditional permission" }),
  );
  await fireEvent.press(
    screen.getByRole("button", {
      name: "Conditional permission to continue",
      disabled: false,
    }),
  );
  expect(workspace.approveCase).toHaveBeenCalledTimes(1);
  expect(workspace.approveCase).toHaveBeenCalledWith("case-audit", {
    grantMode: "CONDITIONAL",
    planHash: item.currentPlan!.hash,
    planVersion: item.currentPlan!.version,
  });
});

test.each(["LIVE", "SCENARIO"] as const)(
  "%s partial failures offer retry only when that runtime supports it",
  async (source) => {
    const workspace = workspaceForCase(
      {
        ...caseItem("case-audit", "CONNECTED_SIGNAL"),
        status: "FAILED",
        currentPlan: {
          ...readyPlan,
          actions: [{ ...readyPlan.actions[0], status: "FAILED" }],
        },
        partialFailure: {
          succeededActionIds: [],
          failedActionIds: [readyPlan.actions[0].actionId],
          pendingActionIds: [],
          explanation: "해당 단계에서 실패했어요.",
        },
      },
      source,
    );
    const screen = await render(<CaseDetailScreen />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", {
          name: "Clarify request",
          disabled: false,
        }),
      ).toBeTruthy(),
    );
    if (source === "SCENARIO") {
      await fireEvent.press(
        screen.getByRole("button", {
          name: "Retry failed or waiting steps",
          disabled: false,
        }),
      );
      expect(workspace.retryCase).toHaveBeenCalledWith("case-audit");
    } else {
      expect(screen.queryByRole("button", { name: /재시도/ })).toBeNull();
      expect(workspace.retryCase).not.toHaveBeenCalled();
    }
    expect(workspace.approveCase).not.toHaveBeenCalled();
  },
);

test("an empty-plan stop failure remains visible without losing the plan failure", async () => {
  const workspace = workspaceForCase(
    {
      ...caseItem("case-audit", "CONNECTED_SIGNAL"),
      currentPlan: { ...readyPlan, actions: [] },
    },
    "LIVE",
  );
  workspace.stopCase.mockRejectedValueOnce(
    new Error("작업을 중지하지 못했어요."),
  );
  const screen = await render(<CaseDetailScreen />);
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Stop task", disabled: false }),
    ).toBeTruthy(),
  );
  await fireEvent.press(screen.getByRole("button", { name: "Stop task" }));
  expect(
    screen.getByRole("alert", { name: "작업을 중지하지 못했어요." }),
  ).toBeTruthy();
  expect(screen.getByText("No action prepared")).toBeTruthy();
  expect(workspace.stopCase).toHaveBeenCalledTimes(1);
});

function pendingCaseMessage() {
  let resolve!: (value: WorkspaceCase["messages"]) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<WorkspaceCase["messages"]>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

async function renderPendingMessage() {
  const workspace = workspaceForCase(
    caseItem("case-message-race", "CONNECTED_SIGNAL"),
    "LIVE",
  );
  const pending = pendingCaseMessage();
  workspace.postCaseMessage.mockReturnValue(pending.promise);
  const screen = await render(<CaseDetailScreen />);
  await fireEvent.press(screen.getByLabelText("Request a change Expand"));
  await fireEvent.changeText(
    screen.getByLabelText("Task message"),
    "보내는 내용",
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", {
        name: "Send task message",
        disabled: false,
      }),
    ).toBeTruthy(),
  );
  // fireEvent waits for an async handler; keep its promise while the request is pending.
  const sending = fireEvent.press(
    screen.getByRole("button", { name: "Send task message" }),
  );
  await waitFor(() =>
    expect(workspace.postCaseMessage).toHaveBeenCalledWith(
      "case-message-race",
      "보내는 내용",
    ),
  );
  mockedWorkspace.mockReturnValue({
    ...workspace,
    status: "updating",
  } as never);
  await screen.rerender(<CaseDetailScreen />);
  return { workspace, pending, screen, sending };
}

test.each([
  ["a new draft", ["다음에 보낼 새 초안"], "다음에 보낼 새 초안"],
  ["an ABA edit", ["중간에 수정한 내용", "보내는 내용"], "보내는 내용"],
] as const)(
  "keeps %s when an earlier send finishes on the same Case",
  async (_name, edits, expected) => {
    const { workspace, pending, screen, sending } =
      await renderPendingMessage();
    for (const text of edits)
      await fireEvent.changeText(screen.getByLabelText("Task message"), text);
    await act(async () => pending.resolve([]));
    await sending;
    mockedWorkspace.mockReturnValue(workspace as never);
    await screen.rerender(<CaseDetailScreen />);
    expect(screen.getByLabelText("Task message").props.value).toBe(expected);
  },
);

test("does not attach an older send's local error to a newly edited draft", async () => {
  const { workspace, pending, screen, sending } = await renderPendingMessage();
  await fireEvent.changeText(
    screen.getByLabelText("Task message"),
    "수정한 새 초안",
  );
  await act(async () => pending.reject(new Error("이전 전송의 로컬 오류")));
  await sending;
  mockedWorkspace.mockReturnValue(workspace as never);
  await screen.rerender(<CaseDetailScreen />);
  expect(screen.getByLabelText("Task message").props.value).toBe(
    "수정한 새 초안",
  );
  expect(
    screen.queryByRole("alert", { name: "이전 전송의 로컬 오류" }),
  ).toBeNull();
});

test("still clears the submitted draft when there was no later edit", async () => {
  const { workspace, pending, screen, sending } = await renderPendingMessage();
  await act(async () => pending.resolve([]));
  await sending;
  mockedWorkspace.mockReturnValue(workspace as never);
  await screen.rerender(<CaseDetailScreen />);
  expect(screen.getByLabelText("Task message").props.value).toBe("");
});

test("still shows the current send failure when there was no later edit", async () => {
  const { workspace, pending, screen, sending } = await renderPendingMessage();
  await act(async () => pending.reject(new Error("현재 전송 실패")));
  await sending;
  mockedWorkspace.mockReturnValue(workspace as never);
  await screen.rerender(<CaseDetailScreen />);
  expect(screen.getByRole("alert", { name: "현재 전송 실패" })).toBeTruthy();
  expect(screen.getByLabelText("Task message").props.value).toBe("보내는 내용");
});
