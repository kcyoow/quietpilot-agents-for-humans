import { fireEvent, render } from "@testing-library/react-native";
import { router } from "expo-router";
import { act } from "react";
import { StyleSheet } from "react-native";

import HistoryScreen from "@/app/history";
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
    colors: jest.requireActual("@/src/theme/tokens").appColors.light,
  }),
}));
jest.mock("expo-router", () => ({
  __esModule: true,
  router: { back: jest.fn(), push: jest.fn() },
  useFocusEffect: (callback: () => void) => callback(),
}));

const mockedWorkspace = jest.mocked(useWorkspace);

function caseItem(
  caseId: string,
  status: WorkspaceCase["status"],
  updatedAt: string,
): WorkspaceCase {
  return {
    caseId,
    caseType: "DIRECT_DELEGATION",
    createdAt: "2026-09-01T00:00:00Z",
    currentPlan: null,
    dataSource: "LIVE",
    evidence: [],
    goal: `${caseId} 작업`,
    messages: [],
    nextAction: "결과를 확인해 주세요.",
    partialFailure: null,
    planChange: null,
    policyIds: [],
    priority: 50,
    providers: [],
    risk: "LOW",
    status,
    summary: "저장된 결과 요약",
    timeline: [],
    updatedAt,
    version: 1,
    whyNow: "결과를 다시 볼 수 있어요.",
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
    dataSource: "LIVE",
    loadedScenario: null,
  };
  const refresh = jest.fn().mockResolvedValue(undefined);
  mockedWorkspace.mockReturnValue({
    error: state.error ?? null,
    refresh,
    snapshot,
    source: "LIVE",
    status: state.status ?? "ready",
  } as never);
  return { screen: await render(<HistoryScreen />), refresh };
}

beforeEach(() => jest.clearAllMocks());

test.each(["booting", "updating"] as const)(
  "does not claim an empty history before the %s state settles",
  async (status) => {
    const { screen } = await renderWith([], { status });

    expect(
      screen.getByRole("progressbar", { name: "Loading history" }),
    ).toBeTruthy();
    expect(screen.queryByText("No history yet")).toBeNull();
  },
);

test("keeps retrieval failure distinct from no history and offers a read-only retry", async () => {
  const { screen, refresh } = await renderWith([], {
    error: "연결을 확인해 주세요.",
  });
  refresh.mockClear();

  expect(screen.getByText("Could not load history")).toBeTruthy();
  expect(screen.queryByText("No history yet")).toBeNull();
  await act(async () => {
    fireEvent.press(screen.getByRole("button", { name: "Reload history" }));
  });
  expect(refresh).toHaveBeenCalledTimes(1);
});

test("shows a confirmed empty history after a successful read", async () => {
  const { screen } = await renderWith([]);

  expect(screen.getByText("No history yet")).toBeTruthy();
  expect(
    screen.queryByRole("progressbar", { name: "Loading history" }),
  ).toBeNull();
  expect(screen.queryByText("Could not load history")).toBeNull();
});

test.each([
  { status: "booting" as const, error: null },
  { status: "ready" as const, error: "최신 응답을 불러오지 못했어요." },
])(
  "preserves last-known history while refresh state is $status",
  async (state) => {
    const { screen } = await renderWith(
      [caseItem("완료", "COMPLETED", "2026-09-03T00:00:00Z")],
      state,
    );

    expect(screen.getByText("완료 작업")).toBeTruthy();
    expect(screen.queryByText("No history yet")).toBeNull();
    if (state.error) {
      expect(screen.getByText("Could not refresh history")).toBeTruthy();
      expect(screen.getByText("Showing the last loaded history.")).toBeTruthy();
    }
  },
);

test("lists terminal work newest first and keeps detail/back navigation accessible", async () => {
  const { screen } = await renderWith([
    caseItem("이전 완료", "COMPLETED", "2026-09-02T00:00:00Z"),
    caseItem("최근 중지", "STOPPED", "2026-09-03T00:00:00Z"),
    caseItem("진행", "RUNNING", "2026-09-04T00:00:00Z"),
  ]);
  const records = screen
    .getAllByRole("button")
    .filter((item) =>
      /작업, (Completed|Stopped)$/.test(item.props.accessibilityLabel ?? ""),
    );
  expect(records.map((item) => item.props.accessibilityLabel)).toEqual([
    "최근 중지 작업, Stopped",
    "이전 완료 작업, Completed",
  ]);
  expect(screen.queryByText("진행 작업")).toBeNull();
  const back = screen.getByRole("button", { name: "Back" });
  expect(StyleSheet.flatten(back.props.style).minHeight).toBeGreaterThanOrEqual(
    48,
  );
  await act(async () => fireEvent.press(records[0]));
  expect(router.push).toHaveBeenCalledWith({
    pathname: "/cases/[caseId]",
    params: { caseId: "최근 중지" },
  });
  await act(async () => fireEvent.press(back));
  expect(router.back).toHaveBeenCalledTimes(1);
});
