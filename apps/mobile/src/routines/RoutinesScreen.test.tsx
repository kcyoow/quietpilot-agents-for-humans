import { act, fireEvent, render, screen } from "@testing-library/react-native";
import RoutinesScreen from "./RoutinesScreen";

const mockApi = {
  listRoutines: jest.fn(),
  getCase: jest.fn(),
  proposeRoutine: jest.fn(),
  activateRoutine: jest.fn(),
  pauseRoutine: jest.fn(),
};
let mockUser = { userId: "owner-a" };
let mockSource = "LIVE";
let mockParams: { caseId?: string } = {};
jest.mock("@/src/api/productApi", () => ({ createProductApi: () => mockApi }));
jest.mock("@/src/auth/AuthProvider", () => ({
  useAuth: () => ({ user: mockUser, status: "ready" }),
}));
jest.mock("@/src/workspace/WorkspaceProvider", () => ({
  useWorkspace: () => ({ source: mockSource }),
}));
jest.mock("expo-router", () => ({
  useLocalSearchParams: () => mockParams,
  useFocusEffect: (fn: () => void) => {
    // jest factories cannot close over the imported React binding.
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    return require("react").useEffect(fn, [fn]);
  },
  Redirect: () => null,
}));
jest.mock("@/src/components/BackHeader", () => ({ BackHeader: () => null }));
jest.mock("@/src/theme/useAppTheme", () => ({
  useAppTheme: () => ({
    colors: {
      background: "white",
      surface: "white",
      border: "gray",
      text: "black",
      textMuted: "gray",
      danger: "red",
      warning: "orange",
      success: "green",
      accent: "blue",
      onAccent: "white",
    },
  }),
}));
const proposal = {
  routine_id: "routine-a",
  version: 1,
  status: "PROPOSED",
  effective_status: "PROPOSED",
  source_case_id: "case-a",
  title: "example.test 마감 준비",
  description: "같은 발신자의 마감 준비를 반복해요.",
  sender_domain: "example.test",
  opportunity_type: "DEADLINE",
  mode: "PREPARE_ONLY",
  activated_at: null,
  created_at: "2026-09-15T00:00:00Z",
  updated_at: "2026-09-15T00:00:00Z",
  review_reason: null,
};
beforeEach(() => {
  jest.clearAllMocks();
  mockUser = { userId: "owner-a" };
  mockSource = "LIVE";
  mockParams = {};
  mockApi.listRoutines.mockResolvedValue([]);
  mockApi.getCase.mockResolvedValue({
    case_id: "case-a",
    version: 7,
    goal: "완료한 일정",
  });
});

test("proposal and activation each require a separate visible user action", async () => {
  mockParams = { caseId: "case-a" };
  mockApi.proposeRoutine.mockResolvedValue(proposal);
  mockApi.activateRoutine.mockResolvedValue({
    ...proposal,
    status: "ACTIVE",
    effective_status: "ACTIVE",
    version: 2,
  });
  await render(<RoutinesScreen />);
  await screen.findByText("Create preparation routine");
  expect(mockApi.proposeRoutine).not.toHaveBeenCalled();
  await fireEvent.press(screen.getByText("Create preparation routine"));
  await screen.findByText("Enable this routine");
  expect(mockApi.proposeRoutine).toHaveBeenCalledWith("case-a", 7);
  expect(mockApi.activateRoutine).not.toHaveBeenCalled();
  await fireEvent.press(screen.getByText("Enable this routine"));
  await screen.findByText("Active");
  expect(mockApi.activateRoutine).toHaveBeenCalledWith("routine-a", 1);
});

test("pause sends the displayed version and retains the saved routine", async () => {
  mockApi.listRoutines.mockResolvedValue([
    { ...proposal, status: "ACTIVE", effective_status: "ACTIVE", version: 3 },
  ]);
  mockApi.pauseRoutine.mockResolvedValue({
    ...proposal,
    status: "PAUSED",
    effective_status: "PAUSED",
    version: 4,
  });
  await render(<RoutinesScreen />);
  await fireEvent.press(await screen.findByText("Pause"));
  await screen.findByText("Resume");
  expect(mockApi.pauseRoutine).toHaveBeenCalledWith("routine-a", 3);
  expect(screen.getByText("Deadline mail from example.test")).toBeTruthy();
  expect(proposal.title).toBe("example.test 마감 준비");
});

test("changed connection scope cannot be activated", async () => {
  mockApi.listRoutines.mockResolvedValue([
    {
      ...proposal,
      status: "PAUSED",
      effective_status: "REVIEW_REQUIRED",
      review_reason: "Google 연결이 바뀌었어요.",
    },
  ]);
  await render(<RoutinesScreen />);
  await screen.findByText("Google 연결이 바뀌었어요.");
  await fireEvent.press(screen.getByText("Resume"));
  expect(mockApi.activateRoutine).not.toHaveBeenCalled();
});

test("late previous-owner data is discarded", async () => {
  let resolve!: (value: unknown) => void;
  mockApi.listRoutines
    .mockImplementationOnce(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    )
    .mockResolvedValue([]);
  const view = await render(<RoutinesScreen />);
  mockUser = { userId: "owner-b" };
  await view.rerender(<RoutinesScreen />);
  await act(async () => {
    resolve([proposal]);
  });
  expect(screen.queryByText(proposal.title)).toBeNull();
});

test("scenario mode makes no real API request", async () => {
  mockSource = "SCENARIO";
  await render(<RoutinesScreen />);
  await screen.findByText(
    "Sign in to the live workspace to use preparation routines.",
  );
  expect(mockApi.listRoutines).not.toHaveBeenCalled();
  expect(mockApi.getCase).not.toHaveBeenCalled();
});
