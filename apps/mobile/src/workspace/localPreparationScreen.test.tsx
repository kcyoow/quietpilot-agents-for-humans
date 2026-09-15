import { act, cleanup, fireEvent, render } from "@testing-library/react-native";

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
  useLocalSearchParams: () => ({ caseId: "local-case" }),
}));

let current: LiveCaseDetail;
let requests: jest.Mock;
let allowRefinement = false;

function detail(): LiveCaseDetail {
  return {
    case_id: "local-case",
    case_type: "CONNECTED_SIGNAL",
    goal: "메일에서 필요한 내용 준비",
    status: "COMPLETED",
    version: 2,
    next_action: null,
    priority: 50,
    providers: ["google"],
    risk: "LOW",
    summary: "요청한 내용을 정리했어요.",
    updated_at: "2026-09-14T00:00:00Z",
    why_now: "메일에서 준비할 내용을 확인했어요.",
    current_plan_hash: "a".repeat(64),
    current_plan_version: 1,
    plan: {
      local_preparation_status: "NO_ACTION",
      actions: [],
      available_grant_modes: [],
      expected_outcome: "필요한 내용 준비",
      hash: "a".repeat(64),
      reason: "현재 메일에서 필요한 내용을 확인했어요.",
      required_scopes: [],
      reversibility: "외부 변경 없음",
      risk: "LOW",
      version: 1,
    },
    actions: [],
    evidence: [],
    messages: [],
    timeline: [],
  };
}

function localAction(verb: string, artifactType: string, content: string) {
  const action: LiveCaseDetail["actions"][number] = {
    action_id: "internal-local-action",
    connector: "quietpilot",
    label: "내용 준비",
    parameters: {
      source_ref: "gmail:internal-source",
      artifact_type: artifactType,
      title: "검토 안내에 필요한 내용",
      content,
    },
    required_scopes: [],
    result_summary: "메일을 참고해 필요한 내용을 준비했어요.",
    reversible: true,
    risk: "LOW",
    status: "SUCCEEDED",
    target: "case:internal-target",
    verb,
  };
  current.actions = [action];
  current.plan!.actions = [action];
  current.plan!.local_preparation_status = "READY";
  return action;
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  current = detail();
  allowRefinement = false;
  jest
    .mocked(useAuth)
    .mockReturnValue({ user: { userId: "synthetic-owner" } } as never);
  jest
    .mocked(usePrototype)
    .mockReturnValue({ snapshot: null, error: null, status: "ready" } as never);
  requests = jest.fn(async (input, init) => {
    const url = new URL(String(input));
    let body: unknown;
    if (init?.method && init.method !== "GET") {
      if (!allowRefinement || !url.pathname.endsWith("/messages"))
        throw new Error("Preparation display must not mutate a service");
      const sent = JSON.parse(init.body);
      current = {
        ...current,
        status: "PREPARING",
        version: current.version + 1,
        messages: [
          ...current.messages,
          {
            author: "USER",
            text: sent.text,
            message_id: "refinement-message",
            created_at: "2026-09-14T00:01:00Z",
          },
        ],
      };
      body = {
        case: current,
        message_id: "refinement-message",
        planning_job_id: "local-plan-2",
      };
    } else if (url.pathname === "/v1/cases/local-case") body = current;
    else if (url.pathname === "/v1/cases")
      body = {
        cases:
          (url.searchParams.get("bucket") === "history") ===
          (current.status === "COMPLETED")
            ? [current]
            : [],
        next_cursor: null,
      };
    else if (url.pathname === "/v1/suggestions")
      body = { suggestions: [], next_cursor: null };
    else if (url.pathname === "/v1/suggestion-groups")
      body = { groups: [], next_cursor: null };
    else throw new Error("Unexpected synthetic read");
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
  jest.useRealTimers();
});

async function mount() {
  const screen = await render(
    <WorkspaceProvider>
      <CaseDetailScreen />
    </WorkspaceProvider>,
  );
  await act(async () => {
    await jest.advanceTimersByTimeAsync(0);
  });
  return screen;
}

test.each([
  [
    "prepare_reply",
    "REPLY_DRAFT",
    "Reply draft ready",
    "안녕하세요.\n검토 요청을 확인했습니다. 감사합니다.",
  ],
  [
    "prepare_task",
    "CHECKLIST",
    "Checklist ready",
    "[ ] 요청 자료 확인\n[ ] 검토 의견 정리",
  ],
  [
    "prepare_reminder",
    "REMINDER",
    "Reminder draft ready",
    "10월 7일 오전 9시 검토 일정을 확인하세요.",
  ],
])(
  "shows selectable %s content immediately without claiming an external execution",
  async (verb, artifactType, title, content) => {
    localAction(verb, artifactType, content);
    const screen = await mount();
    expect(screen.getByText(title)).toBeTruthy();
    expect(screen.getByText(content).props.selectable).toBe(true);
    expect(screen.getByText("Press and hold to select or copy.")).toBeTruthy();
    expect(screen.queryByText("Task completed")).toBeNull();
    expect(screen.queryByLabelText("Allow once to continue")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(
      screen.queryByText(
        /internal-source|internal-local-action|internal-target|REPLY_DRAFT|CHECKLIST|REMINDER|source_ref/,
      ),
    ).toBeNull();
    expect(
      requests.mock.calls.every(
        ([, init]) => !init?.method || init.method === "GET",
      ),
    ).toBe(true);
    await fireEvent.press(screen.getByLabelText("Prepared plan Expand"));
    expect(screen.getByText("Prepared")).toBeTruthy();
    expect(screen.queryByText("Result verified")).toBeNull();
  },
);

test("shows why NO_ACTION needs no further work without claiming something was executed", async () => {
  current.summary = "참고용 안내이며 회신이나 추가 조치를 요청하지 않았어요.";
  const screen = await mount();
  expect(screen.getByText("No further action needed")).toBeTruthy();
  expect(screen.getAllByText(current.summary).length).toBeGreaterThan(0);
  expect(screen.queryByText("Task completed")).toBeNull();
  expect(screen.queryByLabelText("Allow once to continue")).toBeNull();
  expect(screen.queryByRole("link")).toBeNull();
});

test("shows the exact NEEDS_INPUT question before opening the message composer", async () => {
  current.status = "DECISION_REQUIRED";
  current.plan!.local_preparation_status = "NEEDS_INPUT";
  current.next_action = "답장에 포함할 참석 가능 시간을 알려주시겠어요?";
  const screen = await mount();
  expect(screen.getByText(current.next_action)).toBeTruthy();
  expect(screen.queryByLabelText("Allow once to continue")).toBeNull();
  expect(screen.queryByText("내용 준비 완료")).toBeNull();
  await fireEvent.press(screen.getByLabelText("Clarify request"));
  expect(screen.getByLabelText("Task message").props.editable).toBe(true);
});

test.each(["READY", "NO_ACTION"] as const)(
  "refines verified %s through the actual API adapter on the same Case",
  async (status) => {
    if (status === "READY")
      localAction("prepare_reply", "REPLY_DRAFT", "이전 합성 답장 초안");
    allowRefinement = true;
    const screen = await mount();
    const version = current.version;
    await fireEvent.press(screen.getByLabelText("Request a change Expand"));
    expect(screen.getByLabelText("Task message").props.editable).toBe(true);
    await fireEvent.changeText(
      screen.getByLabelText("Task message"),
      "짧은 답장 초안으로 정리해 주세요.",
    );
    await fireEvent.press(screen.getByLabelText("Send task message"));
    const writes = requests.mock.calls.filter(
      ([, init]) => init?.method === "POST",
    );
    expect(writes).toHaveLength(1);
    expect(String(writes[0][0])).toBe(
      "https://api.example.invalid/v1/cases/local-case/messages",
    );
    expect(JSON.parse(writes[0][1].body)).toEqual({
      expected_version: version,
      text: "짧은 답장 초안으로 정리해 주세요.",
    });
    expect(current.case_id).toBe("local-case");
    expect(screen.getByText("Preparing your request")).toBeTruthy();
    current.status = "COMPLETED";
    current.version += 1;
    current.current_plan_version = current.plan!.version = 2;
    current.current_plan_hash = current.plan!.hash = "b".repeat(64);
    localAction("prepare_reply", "REPLY_DRAFT", "안내 감사합니다.");
    await act(async () => {
      await jest.advanceTimersByTimeAsync(2500);
    });
    expect(screen.getByText("안내 감사합니다.").props.selectable).toBe(true);
    expect(screen.queryByLabelText("Allow once to continue")).toBeNull();
    expect(
      requests.mock.calls.filter(([, init]) => init?.method === "POST"),
    ).toHaveLength(1);
  },
);

test.each(["missing_marker", "external_action", "scopes", "empty_artifact"])(
  "keeps a completed %s result locked against refinement",
  async (change) => {
    const action = localAction(
      "prepare_reply",
      "REPLY_DRAFT",
      "기존 준비 내용",
    );
    if (change === "missing_marker")
      delete current.plan!.local_preparation_status;
    if (change === "external_action") {
      action.connector = "google";
      action.verb = "calendar_event_create";
    }
    if (change === "scopes") action.required_scopes = ["calendar.events.owned"];
    if (change === "empty_artifact") delete action.parameters.content;
    const screen = await mount();
    await fireEvent.press(screen.getByLabelText("Request a change Expand"));
    expect(screen.getByLabelText("Task message").props.editable).toBe(false);
    expect(screen.getByLabelText("Send task message")).toBeDisabled();
  },
);

test("does not certify a legacy preparation result without its actual content", async () => {
  const action = localAction("prepare_reply", "REPLY_DRAFT", "");
  delete action.parameters.content;
  const screen = await mount();
  expect(screen.getByText("Could not verify preparation")).toBeTruthy();
  expect(screen.queryByText("Reply draft ready")).toBeNull();
  expect(screen.queryByText("Task completed")).toBeNull();
});
