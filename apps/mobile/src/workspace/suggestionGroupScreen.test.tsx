import { fireEvent, render, waitFor } from "@testing-library/react-native";

import SuggestionGroupScreen from "@/app/suggestions/[groupId]";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";
import type {
  WorkspaceCandidate,
  WorkspaceSnapshot,
} from "@/src/workspace/types";

let mockGroupParams: { groupId: string; suggestionId?: string | string[] } = {
  groupId: "group-live",
};

jest.mock("@/src/workspace/WorkspaceProvider", () => ({
  useWorkspace: jest.fn(),
}));
jest.mock("@/src/theme/useAppTheme", () => {
  const { appColors } = jest.requireActual("@/src/theme/tokens");
  return { useAppTheme: () => ({ colors: appColors.light }) };
});
jest.mock("expo-router", () => ({
  __esModule: true,
  router: { back: jest.fn(), replace: jest.fn() },
  useLocalSearchParams: () => mockGroupParams,
}));

const mockedWorkspace = jest.mocked(useWorkspace);

function liveCandidate(index: number): WorkspaceCandidate {
  return {
    candidateId: `candidate-${index}`,
    caseTypeHint: "CONNECTED_SIGNAL",
    confidence: 0.88,
    createdAt: "2026-08-30T00:00:00Z",
    dataSource: "LIVE",
    eventAt: null,
    evidenceSummary: "Gmail 근거 1개",
    opportunityType: "DEADLINE",
    outcome: `마감 준비 ${index}`,
    primaryGroupId: "group-live",
    proposedActions: [
      {
        actionId: `action-${index}`,
        connector: "quietpilot",
        label: "Prepare checklist",
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
    summary: `후보 ${index} 설명`,
    tags: ["deadline"],
    updatedAt: "2026-08-30T00:00:00Z",
    version: 1,
    whyNow: "마감 전에 준비할 수 있어요.",
  };
}

function groupWorkspace(candidates = [liveCandidate(1), liveCandidate(2)]) {
  const snapshot: WorkspaceSnapshot = {
    candidateGroups: [
      {
        candidateIds: candidates
          .filter((item) => item.primaryGroupId === "group-live")
          .map((item) => item.candidateId),
        dataSource: "LIVE",
        groupId: "group-live",
        icon: "email-outline",
        label: "마감 준비",
        provider: "google",
        reason: "같은 결과로 준비할 제안을 묶었어요.",
      },
    ],
    candidates,
    cases: [],
    dataSource: "LIVE",
    loadedScenario: null,
  };
  const workspace = {
    convertCandidates: jest.fn().mockResolvedValue({ caseId: "case-created" }),
    error: null,
    hideCandidate: jest.fn().mockResolvedValue(undefined),
    snapshot,
    status: "ready" as const,
  };
  mockedWorkspace.mockReturnValue(workspace as never);
  return workspace;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockGroupParams = { groupId: "group-live" };
  groupWorkspace();
});

test("shows action-ready items without search, filters, or multi-selection", async () => {
  const workspace = groupWorkspace();
  const screen = await render(<SuggestionGroupScreen />);

  expect(screen.getAllByText("Prepare checklist")).toHaveLength(2);
  expect(screen.queryByLabelText("그룹 안에서 검색")).toBeNull();
  expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  await fireEvent.press(screen.getAllByText("Prepare plan")[0]);
  await waitFor(() =>
    expect(workspace.convertCandidates).toHaveBeenCalledWith(["candidate-1"]),
  );
});

test("shows and highlights the selected candidate first without preparing it automatically", async () => {
  mockGroupParams.suggestionId = "candidate-2";
  const workspace = groupWorkspace();
  const screen = await render(<SuggestionGroupScreen />);
  const prepareButtons = screen.getAllByRole("button", {
    name: /, prepare plan$/,
  });

  expect(
    prepareButtons.map((button) => button.props.accessibilityLabel),
  ).toEqual(["마감 준비 2, prepare plan", "마감 준비 1, prepare plan"]);
  expect(
    screen.getByRole("header", { name: "Selected suggestion: 마감 준비 2" }),
  ).toBeTruthy();
  expect(screen.getAllByText("Selected suggestion")).toHaveLength(1);
  expect(workspace.convertCandidates).not.toHaveBeenCalled();

  await fireEvent.press(prepareButtons[0]);
  expect(workspace.convertCandidates).toHaveBeenCalledWith(["candidate-2"]);
});

test.each([
  { suggestionId: undefined },
  { suggestionId: "not-found" },
  { suggestionId: ["candidate-2"] },
  { suggestionId: "candidate-3" },
  { suggestionId: "candidate-4" },
  { suggestionId: "candidate-5" },
])(
  "ignores a missing, malformed, foreign, hidden, or actionless selection (%p)",
  async ({ suggestionId }) => {
    mockGroupParams.suggestionId = suggestionId;
    const foreign = { ...liveCandidate(3), primaryGroupId: "other-group" };
    const hidden = { ...liveCandidate(4), status: "HIDDEN" as const };
    const actionless = { ...liveCandidate(5), proposedActions: [] };
    const workspace = groupWorkspace([
      liveCandidate(1),
      liveCandidate(2),
      foreign,
      hidden,
      actionless,
    ]);
    const screen = await render(<SuggestionGroupScreen />);
    const buttons = screen.getAllByRole("button", { name: /, prepare plan$/ });

    expect(buttons.map((button) => button.props.accessibilityLabel)).toEqual([
      "마감 준비 1, prepare plan",
      "마감 준비 2, prepare plan",
    ]);
    expect(screen.queryByText("Selected suggestion")).toBeNull();
    expect(workspace.convertCandidates).not.toHaveBeenCalled();
  },
);

test("hiding a selected candidate removes its highlight without changing the remaining group", async () => {
  mockGroupParams.suggestionId = "candidate-2";
  const workspace = groupWorkspace();
  const screen = await render(<SuggestionGroupScreen />);
  await fireEvent.press(
    screen.getByRole("button", { name: "마감 준비 2, dismiss" }),
  );
  expect(workspace.hideCandidate).toHaveBeenCalledWith("candidate-2");

  mockedWorkspace.mockReturnValue({
    ...workspace,
    snapshot: {
      ...workspace.snapshot,
      candidates: workspace.snapshot.candidates.map((item) =>
        item.candidateId === "candidate-2"
          ? { ...item, status: "HIDDEN" }
          : item,
      ),
    },
  } as never);
  await screen.rerender(<SuggestionGroupScreen />);
  expect(screen.queryByText("Selected suggestion")).toBeNull();
  expect(
    screen.getAllByRole("button", { name: /, prepare plan$/ }),
  ).toHaveLength(1);
});

test("keeps loading distinct from a missing group and exposes preparation failures", async () => {
  const workspace = groupWorkspace();
  mockedWorkspace.mockReturnValue({
    ...workspace,
    snapshot: null,
    status: "booting",
  } as never);
  const screen = await render(<SuggestionGroupScreen />);
  expect(
    screen.getByRole("progressbar", { name: "Loading suggestions" }),
  ).toBeTruthy();
  expect(screen.queryByText("Suggestion not found")).toBeNull();

  workspace.convertCandidates.mockRejectedValueOnce(
    new Error("준비 요청을 완료하지 못했어요."),
  );
  mockedWorkspace.mockReturnValue(workspace as never);
  await screen.rerender(<SuggestionGroupScreen />);
  await fireEvent.press(
    screen.getAllByRole("button", { name: /, prepare plan$/ })[0],
  );
  expect(
    screen.getByRole("alert", { name: "준비 요청을 완료하지 못했어요." }),
  ).toBeTruthy();
});
