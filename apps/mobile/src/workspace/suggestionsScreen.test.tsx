import { fireEvent, render } from "@testing-library/react-native";
import { router } from "expo-router";
import { act } from "react";
import { StyleSheet } from "react-native";

import ProgressScreen from "@/app/(tabs)/suggestions";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";
import type {
  WorkspaceCase,
  WorkspaceSnapshot,
  WorkspaceStatus,
} from "@/src/workspace/types";

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
  router: { push: jest.fn() },
  useFocusEffect: (callback: () => void) => callback(),
}));

const mockedWorkspace = jest.mocked(useWorkspace);

function caseItem(
  caseId: string,
  goal: string,
  status: WorkspaceCase["status"],
  evidenceLabel: string,
): WorkspaceCase {
  return {
    caseId,
    caseType: "CONNECTED_SIGNAL",
    createdAt: "2026-09-01T00:00:00Z",
    currentPlan: null,
    dataSource: "SCENARIO",
    evidence: [
      {
        detail: "확인할 내용",
        evidenceId: `evidence-${caseId}`,
        label: evidenceLabel,
        provider: "google",
        revision: 1,
      },
    ],
    goal,
    messages: [],
    nextAction: "다음 내용을 확인해 주세요.",
    partialFailure: null,
    planChange: null,
    policyIds: [],
    priority: 50,
    providers: ["google"],
    risk: "LOW",
    status,
    summary: "준비 상태",
    timeline: [],
    updatedAt: "2026-09-02T00:00:00Z",
    version: 1,
    whyNow: "지금 확인할 수 있어요.",
  };
}

async function renderWith(
  cases: WorkspaceCase[],
  state: { error?: string | null; status?: WorkspaceStatus } = {},
) {
  const snapshot: WorkspaceSnapshot = {
    candidateGroups: [],
    candidates: [],
    cases,
    dataSource: "SCENARIO",
    loadedScenario: "FULL",
  };
  mockedWorkspace.mockReturnValue({
    error: state.error ?? null,
    refresh: jest.fn().mockResolvedValue(undefined),
    snapshot,
    status: state.status ?? "ready",
  } as never);
  return render(<ProgressScreen />);
}

beforeEach(() => jest.clearAllMocks());

test("shows only preparing and waiting Cases", async () => {
  const screen = await renderWith([
    caseItem("running", "팀 보고 준비", "RUNNING", "Gmail 보고 요청"),
    caseItem("waiting", "일정 응답 대기", "PAUSED", "캘린더 일정"),
    caseItem("decision", "결정 필요", "DECISION_REQUIRED", "Gmail 요청"),
  ]);

  expect(screen.getByText("팀 보고 준비")).toBeTruthy();
  expect(screen.getByText("일정 응답 대기")).toBeTruthy();
  expect(screen.queryByText("Decision needed")).toBeNull();
  expect(screen.getByText("Mail")).toBeTruthy();
  expect(screen.getByText("Calendar")).toBeTruthy();
});

test("opens the selected Case detail", async () => {
  const screen = await renderWith([
    caseItem("running", "팀 보고 준비", "RUNNING", "Gmail 보고 요청"),
  ]);

  fireEvent.press(screen.getByLabelText("팀 보고 준비, In progress"));
  expect(router.push).toHaveBeenCalledWith({
    pathname: "/cases/[caseId]",
    params: { caseId: "running" },
  });
});

test("keeps an empty progress state calm", async () => {
  const screen = await renderWith([]);
  expect(screen.getByText("No active tasks")).toBeTruthy();
});

test.each(["booting", "updating"] as const)(
  "does not call a non-null empty snapshot empty while %s",
  async (status) => {
    const screen = await renderWith([], { status });

    expect(
      screen.getByRole("progressbar", { name: "Loading active tasks" }),
    ).toBeTruthy();
    expect(screen.queryByText("No active tasks")).toBeNull();
    expect(screen.queryByText(/0건의/)).toBeNull();
  },
);

test("shows failed retrieval instead of an empty state and retries the read", async () => {
  const screen = await renderWith([], { error: "연결을 확인해 주세요." });
  const refresh = jest.mocked(
    mockedWorkspace.mock.results.at(-1)!.value.refresh,
  );
  refresh.mockClear();

  expect(screen.getByText("Could not load progress")).toBeTruthy();
  expect(screen.queryByText("No active tasks")).toBeNull();
  expect(screen.queryByText(/0건의/)).toBeNull();
  await act(async () => {
    fireEvent.press(screen.getByRole("button", { name: "Reload progress" }));
  });
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("keeps last-known work visible while marking the latest state as uncertain", async () => {
  const screen = await renderWith(
    [caseItem("running", "팀 보고 준비", "RUNNING", "Gmail 보고 요청")],
    { error: "최신 응답을 불러오지 못했어요." },
  );

  expect(screen.getByText("팀 보고 준비")).toBeTruthy();
  expect(screen.getByText("Could not refresh status")).toBeTruthy();
  expect(screen.getByText("Showing the last saved view.")).toBeTruthy();
  expect(screen.queryByText("No active tasks")).toBeNull();
  expect(screen.queryByText(/건의 준비와 결과 확인/)).toBeNull();
});

test("keeps last-known work visible during refresh", async () => {
  const screen = await renderWith(
    [caseItem("running", "팀 보고 준비", "RUNNING", "Gmail 보고 요청")],
    { status: "booting" },
  );

  expect(screen.getByText("팀 보고 준비")).toBeTruthy();
  expect(
    screen.getByRole("progressbar", { name: "Updating progress" }),
  ).toBeTruthy();
  expect(screen.queryByText("No active tasks")).toBeNull();
  expect(screen.queryByText(/건의 준비와 결과 확인/)).toBeNull();
});

test("opens history through a labeled navigation target at least 48dp high", async () => {
  const screen = await renderWith([]);
  const history = screen.getByRole("button", { name: "View history" });

  expect(
    StyleSheet.flatten(history.props.style).minHeight,
  ).toBeGreaterThanOrEqual(48);
  await act(async () => fireEvent.press(history));
  expect(router.push).toHaveBeenCalledWith("/history");
});
