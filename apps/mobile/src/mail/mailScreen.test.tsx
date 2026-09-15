import {
  act,
  cleanup,
  fireEvent,
  render,
  waitFor,
  within,
} from "@testing-library/react-native";
import { router } from "expo-router";

import MailScreen from "@/app/mail";
import MailInterestsScreen from "@/app/mail-interests";
import { useAuth } from "@/src/auth/AuthProvider";
import { disconnectedGoogle } from "@/src/connections/googleConnectionView";
import {
  createGoogleConnectionsApi,
  GoogleConnectionApiError,
  type GoogleConnectionRecord,
} from "@/src/connections/googleApi";
import { useGoogleConnection } from "@/src/connections/useGoogleConnection";
import {
  createMailApi,
  type MailInterestState,
  type MailProfileInput,
  type MailResult,
  type MailResults,
} from "@/src/mail/mailApi";
import { MailInterestProvider } from "@/src/mail/MailInterestProvider";
import { MailFocusActions, MailFocusCard } from "@/src/mail/MailFocusCard";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";

jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("aws-amplify/auth", () => ({ fetchAuthSession: jest.fn() }));
jest.mock("expo-web-browser", () => ({ openAuthSessionAsync: jest.fn() }));
jest.mock("@/src/connections/googleApi", () => ({
  ...jest.requireActual("@/src/connections/googleApi"),
  createGoogleConnectionsApi: jest.fn(),
}));
jest.mock("@/src/connections/useGoogleConnection", () => ({
  useGoogleConnection: jest.fn(),
}));
jest.mock("@/src/workspace/WorkspaceProvider", () => ({
  useWorkspace: jest.fn(),
}));
jest.mock("@/src/mail/mailApi", () => ({
  ...jest.requireActual("@/src/mail/mailApi"),
  createMailApi: jest.fn(),
}));
jest.mock("@/src/theme/useAppTheme", () => {
  const { appColors } =
    jest.requireActual<typeof import("@/src/theme/tokens")>(
      "@/src/theme/tokens",
    );
  return { useAppTheme: () => ({ colors: appColors.light }) };
});
jest.mock("expo-router", () => {
  const React = jest.requireActual<typeof import("react")>("react");
  const listeners = new Set<() => void>();
  let focused = true;
  return {
    router: {
      back: jest.fn(),
      canGoBack: () => true,
      push: jest.fn(),
      replace: jest.fn(),
    },
    useFocusEffect: function useFocusEffect(effect: () => void | (() => void)) {
      const isFocused = React.useSyncExternalStore(
        React.useCallback((listener: () => void) => {
          listeners.add(listener);
          return () => listeners.delete(listener);
        }, []),
        () => focused,
      );
      React.useEffect(() => {
        if (isFocused) return effect();
      }, [effect, isFocused]);
    },
    setTestFocus(next: boolean) {
      focused = next;
      listeners.forEach((listener) => listener());
    },
  };
});

const mockedAuth = jest.mocked(useAuth);
const mockedGoogle = jest.mocked(useGoogleConnection);
const mockedWorkspace = jest.mocked(useWorkspace);
const mockedFactory = jest.mocked(createMailApi);
const mockedGoogleFactory = jest.mocked(createGoogleConnectionsApi);

function focus(next: boolean) {
  jest.requireMock("expo-router").setTestFocus(next);
}

function connection(
  status: GoogleConnectionRecord["status"],
): GoogleConnectionRecord {
  return {
    provider: "google",
    label: "Google",
    status,
    version: 1,
    granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
    lookback_days: 7,
    scan_progress: 100,
    error_code: null,
    last_checked_at: null,
    last_sync_mode: null,
    next_renewal_due_at: null,
    watch_expires_at: null,
    watch_renewed_at: null,
  };
}

function realGoogle(initial: GoogleConnectionRecord["status"] = "CONNECTED") {
  const client = {
    configured: true,
    get: jest.fn().mockResolvedValue(connection(initial)),
  };
  mockedGoogleFactory.mockReturnValue(client as never);
  mockedGoogle.mockImplementation(
    jest.requireActual<typeof import("@/src/connections/useGoogleConnection")>(
      "@/src/connections/useGoogleConnection",
    ).useGoogleConnection,
  );
  return client;
}

function deferredConnection() {
  let resolve!: (value: GoogleConnectionRecord) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<GoogleConnectionRecord>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function state({
  profile = {},
  recommendations = {},
  scan = {},
}: {
  profile?: Partial<MailInterestState["profile"]>;
  recommendations?: Partial<MailInterestState["recommendations"]>;
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
      ...recommendations,
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

const informationalMail: MailResult = {
  evidence_ref: "gmail:library-information",
  title: "도서관 공간 정보",
  summary: "조용한 열람실과 협업 공간을 소개하는 안내입니다.",
  reason: "등록한 학교 관심 태그와 관련된 정보입니다.",
  sender_domain: "university.example.invalid",
  received_at: null,
  matched_tags: ["학교"],
};

function readyState() {
  return state({
    profile: { tags: ["학교"], description: "학교 소식", version: 2 },
    scan: {
      status: "READY",
      scan_id: "scan-2",
      matched_count: 1,
      processed_count: 4,
    },
  });
}

function page(
  initial: MailInterestState,
  items: MailResult[] = [],
): MailResults {
  return {
    profile_version: initial.profile.version,
    scan_id: initial.scan.scan_id,
    status:
      initial.scan.status === "PENDING" || initial.scan.status === "ERROR"
        ? initial.scan.status
        : "READY",
    items,
    next_cursor: null,
  };
}

function api(initial = state()) {
  return {
    configured: true,
    getState: jest.fn().mockResolvedValue(initial),
    getResults: jest.fn().mockResolvedValue(page(initial)),
    save: jest.fn().mockImplementation(async (input: MailProfileInput) =>
      state({
        profile: {
          tags: input.tags,
          description: input.description,
          version: input.expected_version + 1,
        },
        scan: {
          status:
            input.tags.length || input.description.trim()
              ? "PENDING"
              : "NOT_STARTED",
          scan_id: "scan-" + (input.expected_version + 1),
        },
      }),
    ),
    recommend: jest.fn().mockResolvedValue(
      state({
        profile: initial.profile,
        recommendations: { status: "PENDING", request_id: "recommendation-2" },
        scan: initial.scan,
      }),
    ),
    scan: jest.fn().mockResolvedValue(
      state({
        profile: initial.profile,
        scan: { status: "PENDING", scan_id: "scan-next" },
      }),
    ),
  };
}

async function mount(client = api(), Surface = MailScreen) {
  mockedFactory.mockReturnValue(client);
  const screen = await render(
    <MailInterestProvider>
      <Surface />
    </MailInterestProvider>,
  );
  return { screen, client };
}

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  mockedFactory.mockReset();
  mockedGoogleFactory.mockReset();
  focus(true);
  mockedAuth.mockReturnValue({ user: { userId: "mail-user" } } as never);
  mockedWorkspace.mockReturnValue({
    source: "LIVE",
    refresh: jest.fn().mockResolvedValue(undefined),
  } as never);
  mockedGoogle.mockReturnValue({
    connection: { ...disconnectedGoogle(), status: "CONNECTED" },
    checking: false,
    error: null,
    refresh: jest.fn().mockResolvedValue(undefined),
  } as never);
});

afterEach(async () => {
  await cleanup();
  jest.useRealTimers();
});

test.each([
  ["reader", MailScreen],
  ["interests", MailInterestsScreen],
] as const)(
  "the real connection hook refreshes the mounted %s on focus without duplicate initial reads",
  async (name, Surface) => {
    const google = realGoogle("DISCONNECTED");
    const initial = readyState();
    initial.recommendations.status = "NOT_STARTED";
    const client = api(initial);
    client.getResults.mockResolvedValue(page(initial, [informationalMail]));
    const { screen } = await mount(client, Surface);
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0);
    });
    expect(google.get).toHaveBeenCalledTimes(1);
    expect(client.recommend).not.toHaveBeenCalled();
    if (name === "interests") {
      await fireEvent.changeText(
        screen.getByLabelText("Enter interest tags"),
        "#취업",
      );
    } else {
      expect(screen.getByText("Connect mail to continue")).toBeTruthy();
      expect(screen.queryByTestId("mail-result-0")).toBeNull();
    }
    await act(() => focus(false));
    google.get.mockResolvedValue(connection("CONNECTED"));
    await act(() => focus(true));
    await act(async () => {
      await jest.advanceTimersByTimeAsync(0);
    });
    expect(google.get).toHaveBeenCalledTimes(2);
    if (name === "interests") {
      expect(client.recommend).toHaveBeenCalledTimes(1);
      expect(screen.getByLabelText("Enter interest tags").props.value).toBe(
        "#취업",
      );
    } else {
      expect(screen.getByTestId("mail-result-0")).toBeTruthy();
      expect(client.recommend).not.toHaveBeenCalled();
    }
    await act(async () => {
      await jest.advanceTimersByTimeAsync(300);
    });
    expect(google.get).toHaveBeenCalledTimes(2);
    expect(client.save).not.toHaveBeenCalled();
  },
);

test("an initial connection lookup stays distinct from disconnection and can retry its failure", async () => {
  const google = realGoogle();
  const first = deferredConnection();
  google.get.mockReturnValueOnce(first.promise);
  const initial = readyState();
  const client = api(initial);
  client.getResults.mockResolvedValue(page(initial, [informationalMail]));
  const { screen } = await mount(client);
  expect(screen.getByText("Checking mail connection")).toBeTruthy();
  expect(screen.queryByText("Connect mail to continue")).toBeNull();
  await act(async () => {
    first.reject(new Error("연결 조회 네트워크 오류"));
  });
  expect(screen.getByText("Could not verify mail connection")).toBeTruthy();
  expect(screen.queryByText("Connect mail to continue")).toBeNull();
  await fireEvent.press(
    screen.getByRole("button", { name: "Recheck connection" }),
  );
  expect(screen.getByTestId("mail-result-0")).toBeTruthy();
  expect(google.get).toHaveBeenCalledTimes(2);
});

test.each(["disconnect", "authorization"] as const)(
  "a real connection refresh preserves rows while reading but hides them after %s",
  async (boundary) => {
    const google = realGoogle();
    const initial = readyState();
    const client = api(initial);
    client.getResults.mockResolvedValue(page(initial, [informationalMail]));
    const { screen } = await mount(client);
    await fireEvent.press(screen.getByTestId("mail-result-0"));
    const next = deferredConnection();
    await act(() => focus(false));
    google.get.mockReturnValueOnce(next.promise);
    await act(() => focus(true));
    expect(google.get).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("mail-result-0")).toBeTruthy();
    expect(screen.getByLabelText("Close mail")).toBeTruthy();
    await act(async () => {
      if (boundary === "disconnect") next.resolve(connection("DISCONNECTED"));
      else {
        const error = new GoogleConnectionApiError("다시 로그인해 주세요.");
        error.status = 401;
        error.code = "AUTH_REQUIRED";
        next.reject(error);
      }
    });
    expect(screen.queryByTestId("mail-result-0")).toBeNull();
    expect(screen.queryByLabelText("Close mail")).toBeNull();
  },
);

test("a failed connection refresh preserves verified rows and shows a retry", async () => {
  const google = realGoogle();
  const initial = readyState();
  const client = api(initial);
  client.getResults.mockResolvedValue(page(initial, [informationalMail]));
  const { screen } = await mount(client);
  await act(() => focus(false));
  google.get.mockRejectedValueOnce(new Error("연결 조회 네트워크 오류"));
  await act(() => focus(true));
  expect(screen.getByTestId("mail-result-0")).toBeTruthy();
  expect(screen.getByText("연결 조회 네트워크 오류")).toBeTruthy();
  expect(screen.queryByText("Connect mail to continue")).toBeNull();
});

test("automatic recommendations wait for a successful connection recheck and keep the draft", async () => {
  const google = realGoogle();
  const initial = readyState();
  const { screen, client } = await mount(api(initial), MailInterestsScreen);
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "#취업",
  );
  await act(() => focus(false));
  const next = deferredConnection();
  google.get.mockReturnValueOnce(next.promise);
  client.getState.mockResolvedValue({
    ...initial,
    recommendations: { ...initial.recommendations, status: "NOT_STARTED" },
  });
  await act(() => focus(true));
  expect(client.recommend).not.toHaveBeenCalled();
  await act(async () => {
    next.reject(new Error("연결 조회 네트워크 오류"));
  });
  expect(client.recommend).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Enter interest tags").props.value).toBe(
    "#취업",
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Recheck connection" }),
  );
  expect(client.recommend).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Enter interest tags").props.value).toBe(
    "#취업",
  );
});

test("a current Google authorization-error record hides previously verified mail", async () => {
  const google = realGoogle();
  const initial = readyState();
  const client = api(initial);
  client.getResults.mockResolvedValue(page(initial, [informationalMail]));
  const { screen } = await mount(client);
  expect(screen.getByTestId("mail-result-0")).toBeTruthy();
  await act(() => focus(false));
  google.get.mockResolvedValue({
    ...connection("ERROR"),
    error_code: "GOOGLE_AUTH_REQUIRED",
  });
  await act(() => focus(true));
  expect(screen.queryByTestId("mail-result-0")).toBeNull();
  expect(screen.getByText("Sign in to Google again")).toBeTruthy();
});

test("a failed connection lookup does not auto-recommend or block a manual draft", async () => {
  const google = realGoogle();
  google.get.mockRejectedValue(new Error("연결 조회 네트워크 오류"));
  const initial = state({ recommendations: { status: "NOT_STARTED" } });
  const { screen, client } = await mount(api(initial), MailInterestsScreen);
  expect(screen.getByText("연결 조회 네트워크 오류")).toBeTruthy();
  expect(client.recommend).not.toHaveBeenCalled();
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "#학교",
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Start with these interests" }),
  );
  expect(client.save).toHaveBeenCalledWith({
    tags: ["학교"],
    description: "",
    expected_version: 0,
  });
  expect(client.recommend).not.toHaveBeenCalled();
});

test("an unconfigured reader keeps a small setup entry without editing or recommending", async () => {
  const { screen, client } = await mount(
    api(
      state({
        recommendations: { status: "NOT_STARTED", request_id: null },
      }),
    ),
  );
  await waitFor(() =>
    expect(screen.getByText("Set your interests first.")).toBeTruthy(),
  );
  expect(screen.queryByLabelText("Enter interest tags")).toBeNull();
  expect(screen.queryByText("Relevant mail")).toBeNull();
  expect(client.recommend).not.toHaveBeenCalled();
  await fireEvent.press(screen.getByLabelText("Set mail interests"));
  expect(router.push).toHaveBeenCalledWith("/mail-interests");
  expect(client.save).not.toHaveBeenCalled();
});

test("the separate interests page recommends once and permits description-only saving while pending", async () => {
  const initial = state({
    recommendations: { status: "NOT_STARTED", request_id: null },
  });
  const { screen, client } = await mount(api(initial), MailInterestsScreen);
  await waitFor(() => expect(client.recommend).toHaveBeenCalledTimes(1));
  expect(screen.getByText("Retry request")).toBeTruthy();
  expect(screen.queryByRole("checkbox")).toBeNull();
  const description =
    "학교의 정보성 공지도 보고 싶어요.\n광고는 제외해 주세요.";
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    description,
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Start with these interests" }),
  );
  expect(client.save).toHaveBeenCalledWith({
    tags: [],
    description,
    expected_version: 0,
  });
  await waitFor(() => expect(router.back).toHaveBeenCalledTimes(1));
});

test("the reader opens informational results as a summary and reason without sample content or actions", async () => {
  const initial = readyState();
  const client = api(initial);
  client.getResults.mockResolvedValue(page(initial, [informationalMail]));
  const { screen } = await mount(client);
  await waitFor(() =>
    expect(
      screen.getByLabelText("도서관 공간 정보, view mail details"),
    ).toBeTruthy(),
  );
  expect(screen.getByText("Time unavailable")).toBeTruthy();
  expect(screen.queryByText("예시 메일")).toBeNull();
  expect(screen.queryByText("화면 미리보기")).toBeNull();

  await fireEvent.press(
    screen.getByLabelText("도서관 공간 정보, view mail details"),
  );
  expect(screen.getByText("Mail summary")).toBeTruthy();
  expect(screen.getByText("Why this matched")).toBeTruthy();
  expect(screen.getAllByText(informationalMail.summary).length).toBeGreaterThan(
    0,
  );
  expect(screen.getByText(informationalMail.reason)).toBeTruthy();
  expect(screen.queryByRole("button", { name: /승인|메일 전송/ })).toBeNull();
  await fireEvent.press(screen.getByLabelText("Close mail"));
  expect(screen.queryByText("Why this matched")).toBeNull();
});

test.each([undefined, "NORMAL", "LOW"] as const)(
  "%s importance preserves ordinary row rendering without local exclusion",
  async (importance) => {
    const initial = readyState();
    const item: MailResult = {
      ...informationalMail,
      matched_tags: [],
      ...(importance ? { importance } : {}),
    };
    const client = api(initial);
    client.getResults.mockResolvedValue(page(initial, [item]));
    const { screen } = await mount(client);
    await waitFor(() =>
      expect(
        screen.getByLabelText(`${item.title}, view mail details`),
      ).toBeTruthy(),
    );
    const row = within(
      screen.getByLabelText(`${item.title}, view mail details`),
    );
    expect(row.queryByText("Priority")).toBeNull();
    expect(row.getByText("Matches your interests")).toBeTruthy();
  },
);

test("high importance is marked in the row and detail without claiming an unmatched saved topic", async () => {
  const initial = readyState();
  const item: MailResult = {
    ...informationalMail,
    importance: "HIGH",
    matched_tags: [],
  };
  const client = api(initial);
  client.getResults.mockResolvedValue(page(initial, [item]));
  const { screen } = await mount(client);
  await waitFor(() =>
    expect(
      screen.getByLabelText(`${item.title}, view mail details`),
    ).toBeTruthy(),
  );
  const row = within(screen.getByLabelText(`${item.title}, view mail details`));
  expect(row.getByText("Priority")).toBeTruthy();
  expect(row.getByText("Priority mail")).toBeTruthy();
  expect(row.queryByText("Matches your interests")).toBeNull();
  await fireEvent.press(
    screen.getByLabelText(`${item.title}, view mail details`),
  );
  expect(screen.getByText("Why this matched")).toBeTruthy();
  expect(screen.getAllByText("Priority")).toHaveLength(2);
  expect(screen.getAllByText("Priority mail")).toHaveLength(2);
  expect(client.save).not.toHaveBeenCalled();
});

test("mail rows and appended pages preserve incoming server order without local importance sorting", async () => {
  const initial = readyState();
  initial.scan.matched_count = 3;
  const items: MailResult[] = [
    {
      ...informationalMail,
      evidence_ref: "server-first",
      title: "서버 첫 번째",
      importance: "NORMAL",
    },
    {
      ...informationalMail,
      evidence_ref: "server-second",
      title: "서버 두 번째",
      importance: "HIGH",
    },
    {
      ...informationalMail,
      evidence_ref: "server-third",
      title: "서버 세 번째",
      importance: "LOW",
    },
  ];
  const client = api(initial);
  client.getResults
    .mockResolvedValueOnce({
      ...page(initial, items.slice(0, 2)),
      next_cursor: "next-page",
    })
    .mockResolvedValueOnce(page(initial, items.slice(2)));
  const { screen } = await mount(client);
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Load more mail" })).toBeTruthy(),
  );
  await fireEvent.press(screen.getByRole("button", { name: "Load more mail" }));
  await waitFor(() =>
    expect(
      screen.getByLabelText("서버 세 번째, view mail details"),
    ).toBeTruthy(),
  );
  expect(
    screen
      .getAllByRole("button", { name: /, view mail details$/ })
      .map((row) => row.props.accessibilityLabel),
  ).toEqual(items.map((item) => `${item.title}, view mail details`));
  expect(client.getResults).toHaveBeenLastCalledWith("next-page");
});

test("a verified empty result is separate from loading and can open interest editing", async () => {
  const { screen } = await mount(api(readyState()));
  await waitFor(() =>
    expect(screen.getByText("No matching mail")).toBeTruthy(),
  );
  expect(screen.queryByText("Loading mail")).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Edit interests" }));

  expect(router.push).toHaveBeenCalledWith("/mail-interests");
  expect(screen.queryByLabelText("Enter interest tags")).toBeNull();
});

test("pending results display the first 30 with progress and open details before completion", async () => {
  const initial = readyState();
  initial.scan = {
    ...initial.scan,
    status: "PENDING",
    processed_count: 37,
    matched_count: 35,
  };
  const items = Array.from({ length: 35 }, (_, index): MailResult => ({
    ...informationalMail,
    evidence_ref: `partial-${index}`,
    title: `부분 결과 ${index + 1}`,
  }));
  const client = api(initial);
  client.getResults.mockResolvedValue({
    ...page(initial, items),
    next_cursor: "partial-next",
  });
  const { screen } = await mount(client);
  await waitFor(() =>
    expect(screen.getByTestId("mail-result-29")).toBeTruthy(),
  );
  expect(screen.queryByTestId("mail-result-30")).toBeNull();
  expect(screen.getByText(/37 messages checked\./)).toBeTruthy();
  expect(
    screen.getByText("30 early results · Order may change during the scan."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Load more mail" })).toBeNull();
  expect(screen.queryByText("No matching mail")).toBeNull();
  await fireEvent.press(screen.getByTestId("mail-result-0"));
  expect(screen.getByText("Why this matched")).toBeTruthy();
  await fireEvent.press(screen.getByLabelText("Close mail"));
  const completed = {
    ...initial,
    scan: { ...initial.scan, status: "READY" as const },
  };
  client.getState.mockResolvedValue(completed);
  client.getResults.mockResolvedValue({
    ...page(completed, items.slice(0, 30)),
    next_cursor: "completed-next",
  });
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1800);
  });
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "Load more mail" })).toBeTruthy(),
  );
  expect(screen.queryByText("Finding relevant mail")).toBeNull();
  expect(client.save).not.toHaveBeenCalled();
});

test("partial rows and their detail remain visible while the next batch page is loading", async () => {
  const initial = readyState();
  initial.scan = { ...initial.scan, status: "PENDING", processed_count: 4 };
  const client = api(initial);
  let complete!: (value: MailResults) => void;
  client.getResults
    .mockResolvedValueOnce(page(initial, [informationalMail]))
    .mockReturnValueOnce(
      new Promise<MailResults>((resolve) => {
        complete = resolve;
      }),
    );
  const { screen } = await mount(client);
  await waitFor(() => expect(screen.getByTestId("mail-result-0")).toBeTruthy());
  await fireEvent.press(screen.getByTestId("mail-result-0"));
  const next = { ...initial, scan: { ...initial.scan, processed_count: 12 } };
  client.getState.mockResolvedValue(next);
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1800);
  });
  expect(screen.getByTestId("mail-result-0")).toBeTruthy();
  expect(screen.getByText("Why this matched")).toBeTruthy();
  expect(screen.getByText(/12 messages checked\./)).toBeTruthy();
  await act(() =>
    complete(
      page(next, [
        informationalMail,
        { ...informationalMail, evidence_ref: "second", title: "추가 결과" },
      ]),
    ),
  );
  expect(screen.getByTestId("mail-result-1")).toBeTruthy();
});

test("failed scans display stored partial results and details alongside the error until an explicit retry", async () => {
  const initial = readyState();
  initial.scan = {
    ...initial.scan,
    status: "ERROR",
    processed_count: 32,
    matched_count: 12,
    error_code: "SCAN_FAILED",
  };
  const items = Array.from({ length: 12 }, (_, index): MailResult => ({
    ...informationalMail,
    evidence_ref: `stored-${index}`,
    title: `저장된 메일 ${index + 1}`,
  }));
  const client = api(initial);
  client.getResults.mockResolvedValue({
    ...page(initial, items),
    next_cursor: "saved-next",
  });
  const { screen } = await mount(client);
  await waitFor(() =>
    expect(screen.getByTestId("mail-result-11")).toBeTruthy(),
  );
  expect(screen.getByText("Mail scan incomplete")).toBeTruthy();
  expect(
    screen.getByText(
      "The scan stopped with an error. Showing verified partial results.",
    ),
  ).toBeTruthy();
  expect(screen.getByText("12 partial results · Scan incomplete")).toBeTruthy();
  expect(screen.queryByText("Finding relevant mail")).toBeNull();
  expect(screen.queryByText("No matching mail")).toBeNull();
  expect(screen.queryByLabelText("Rescan relevant mail")).toBeNull();
  expect(client.scan).not.toHaveBeenCalled();
  await fireEvent.press(screen.getByTestId("mail-result-0"));
  expect(screen.getByText("Why this matched")).toBeTruthy();
  await fireEvent.press(screen.getByLabelText("Close mail"));
  client.getResults.mockResolvedValueOnce(
    page(initial, [
      {
        ...informationalMail,
        evidence_ref: "stored-next",
        title: "다음 저장 메일",
      },
    ]),
  );
  await fireEvent.press(screen.getByRole("button", { name: "Load more mail" }));
  await waitFor(() => expect(screen.getByText("다음 저장 메일")).toBeTruthy());
  expect(client.getResults).toHaveBeenLastCalledWith("saved-next");
  expect(screen.getByText("13 partial results · Scan incomplete")).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Scan again" }));
  await waitFor(() => expect(client.scan).toHaveBeenCalledTimes(1));
  expect(screen.queryByTestId("mail-result-0")).toBeNull();
  expect(client.save).not.toHaveBeenCalled();
  expect(client.recommend).not.toHaveBeenCalled();
});

test("a failed scan keeps the current partial detail when its final results read also fails", async () => {
  const initial = readyState();
  initial.scan = { ...initial.scan, status: "PENDING" };
  const client = api(initial);
  client.getResults.mockResolvedValueOnce({
    ...page(initial, [informationalMail]),
    next_cursor: "unfinished-cursor",
  });
  const { screen } = await mount(client);
  await waitFor(() => expect(screen.getByTestId("mail-result-0")).toBeTruthy());
  await fireEvent.press(screen.getByTestId("mail-result-0"));
  client.getState.mockResolvedValue({
    ...initial,
    scan: { ...initial.scan, status: "ERROR", error_code: "SCAN_FAILED" },
  });
  client.getResults.mockRejectedValueOnce(new Error("결과 조회 실패"));
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1800);
  });
  expect(screen.getByTestId("mail-result-0")).toBeTruthy();
  expect(screen.getByText("Why this matched")).toBeTruthy();
  expect(screen.getByText("Mail scan incomplete")).toBeTruthy();
  expect(screen.getByText("1 partial results · Scan incomplete")).toBeTruthy();
  expect(screen.queryByText("Finding relevant mail")).toBeNull();
  expect(screen.queryByText("No matching mail")).toBeNull();
  expect(screen.queryByRole("button", { name: "Load more mail" })).toBeNull();
  expect(client.scan).not.toHaveBeenCalled();
});

test.each(
  (["disconnect", "authorization"] as const).flatMap((boundary) =>
    (["PENDING", "ERROR"] as const).map((status) => ({ boundary, status })),
  ),
)(
  "$boundary hides an open $status partial mail detail immediately",
  async ({ boundary, status }) => {
    const initial = readyState();
    initial.scan = { ...initial.scan, status };
    const client = api(initial);
    client.getResults.mockResolvedValue(page(initial, [informationalMail]));
    const { screen } = await mount(client);
    await waitFor(() =>
      expect(screen.getByTestId("mail-result-0")).toBeTruthy(),
    );
    await fireEvent.press(screen.getByTestId("mail-result-0"));
    if (boundary === "disconnect") {
      mockedGoogle.mockReturnValue({
        connection: disconnectedGoogle(),
        checking: false,
        error: null,
        refresh: jest.fn().mockResolvedValue(undefined),
      } as never);
      await screen.rerender(
        <MailInterestProvider>
          <MailScreen />
        </MailInterestProvider>,
      );
    } else {
      client.getState.mockResolvedValue({
        ...initial,
        scan: { ...initial.scan, error_code: "GOOGLE_AUTH_REQUIRED" },
      });
      if (status === "ERROR") {
        await act(() => focus(false));
        await act(() => focus(true));
      } else {
        await act(async () => {
          await jest.advanceTimersByTimeAsync(1800);
        });
      }
    }
    expect(screen.queryByTestId("mail-result-0")).toBeNull();
    expect(screen.queryByText("Why this matched")).toBeNull();
    expect(screen.queryByText("No matching mail")).toBeNull();
  },
);

test("a failed result fetch never becomes an empty result and retries through the provider", async () => {
  const initial = readyState();
  const client = api(initial);
  client.getResults.mockRejectedValueOnce(
    new Error("메일 응답을 읽지 못했어요."),
  );
  const { screen } = await mount(client);
  await waitFor(() =>
    expect(screen.getByText("Could not load results")).toBeTruthy(),
  );
  expect(screen.queryByText("No matching mail")).toBeNull();
  expect(screen.getByRole("alert")).toBeTruthy();
  client.getResults.mockResolvedValue(page(initial, [informationalMail]));
  await fireEvent.press(screen.getByRole("button", { name: "Reload" }));
  await waitFor(() =>
    expect(
      screen.getByLabelText("도서관 공간 정보, view mail details"),
    ).toBeTruthy(),
  );
  expect(client.getResults).toHaveBeenCalledTimes(2);
});

test.each(["READY", "PENDING"] as const)(
  "%s action preparation warnings preserve mail matching and a recovery action",
  async (status) => {
    const initial = readyState();
    initial.scan.status = status;
    initial.scan.error_code = "MAIL_ACTION_PREPARATION_INCOMPLETE";
    const client = api(initial);
    client.getResults.mockResolvedValue(page(initial, [informationalMail]));
    const { screen } = await mount(client);
    await waitFor(() =>
      expect(
        screen.getByText(
          "Mail found. Some task suggestions are still incomplete.",
        ),
      ).toBeTruthy(),
    );
    expect(screen.queryByText("Mail scan incomplete")).toBeNull();
    expect(screen.queryByText("No matching mail")).toBeNull();
    if (status === "READY") {
      await waitFor(() =>
        expect(
          screen.getByLabelText("도서관 공간 정보, view mail details"),
        ).toBeTruthy(),
      );
      await fireEvent.press(screen.getByRole("button", { name: "Scan again" }));
      expect(client.scan).toHaveBeenCalledTimes(1);
    } else {
      expect(screen.getByText("Finding relevant mail")).toBeTruthy();
      await fireEvent.press(
        screen.getByRole("button", { name: "Refresh status" }),
      );
      expect(client.getState).toHaveBeenCalledTimes(2);
      expect(client.scan).not.toHaveBeenCalled();
    }
  },
);

test.each(["READY", "PENDING"] as const)(
  "the mail summary explains a %s action preparation warning and keeps mail navigation",
  async (status) => {
    const initial = readyState();
    initial.scan.status = status;
    initial.scan.error_code = "MAIL_ACTION_PREPARATION_INCOMPLETE";
    const { screen } = await mount(api(initial), () => (
      <>
        <MailFocusActions connectionStatus="CONNECTED" checking={false} />
        <MailFocusCard connectionStatus="CONNECTED" checking={false} />
      </>
    ));
    await waitFor(() =>
      expect(
        screen.getByText(
          "Mail found. Some task suggestions are still incomplete.",
        ),
      ).toBeTruthy(),
    );
    await fireEvent.press(
      screen.getByRole("button", { name: "Browse mail by interest" }),
    );
    expect(router.push).toHaveBeenCalledWith("/mail");
  },
);

test("a pending scan retains its state and offers a visible retry after a read failure", async () => {
  const initial = readyState();
  initial.scan.status = "PENDING";
  const { screen, client } = await mount(api(initial));
  await waitFor(() =>
    expect(screen.getByText("Finding relevant mail")).toBeTruthy(),
  );
  client.getState.mockRejectedValueOnce(new Error("연결이 잠시 끊겼어요."));
  await act(async () => {
    jest.advanceTimersByTime(1800);
  });
  await waitFor(() =>
    expect(screen.getByText("연결이 잠시 끊겼어요.")).toBeTruthy(),
  );
  expect(screen.queryByText("No matching mail")).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Check again" }));
  await waitFor(() =>
    expect(screen.queryByText("연결이 잠시 끊겼어요.")).toBeNull(),
  );
  expect(client.getState).toHaveBeenCalledTimes(3);
  expect(client.scan).not.toHaveBeenCalled();
});

test.each(["scan", "recommend"] as const)(
  "a pending %s can resend through the actual provider method without duplicate POSTs",
  async (kind) => {
    const initial = readyState();
    if (kind === "scan") initial.scan.status = "PENDING";
    else initial.recommendations.status = "PENDING";
    const client = api(initial);
    let complete!: (value: MailInterestState) => void;
    client[kind].mockReturnValueOnce(
      new Promise<MailInterestState>((resolve) => {
        complete = resolve;
      }),
    );
    const { screen } = await mount(
      client,
      kind === "scan" ? MailScreen : MailInterestsScreen,
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", {
          name: "Retry request",
          disabled: false,
        }),
      ).toBeTruthy(),
    );
    await act(async () => {
      jest.advanceTimersByTime(1800);
    });
    expect(client[kind]).not.toHaveBeenCalled();
    const retry = screen.getByRole("button", {
      name: "Retry request",
      disabled: false,
    });
    await fireEvent.press(retry);
    await fireEvent.press(retry);
    expect(client[kind]).toHaveBeenCalledTimes(1);
    expect(
      screen.getByRole("button", {
        name: kind === "scan" ? "Retry request" : "Finding suggestions",
        disabled: true,
      }),
    ).toBeTruthy();
    await act(() => complete(initial));
    await waitFor(() =>
      expect(
        screen.getByRole("button", {
          name: "Retry request",
          disabled: false,
        }),
      ).toBeTruthy(),
    );
    expect(client[kind]).toHaveBeenCalledTimes(1);
  },
);

test.each([
  ["PENDING", "Finding relevant mail"],
  ["ERROR", "Mail scan incomplete"],
  ["NOT_STARTED", "Find mail that matters to you"],
] as const)(
  "%s remains distinct and interests can be edited while scanning is unfinished",
  async (status, title) => {
    const initial = state({
      profile: { tags: ["학교"], description: "학교 소식", version: 2 },
      scan: {
        status,
        scan_id: "scan-2",
        processed_count: 3,
        error_code: status === "ERROR" ? "SCAN_FAILED" : null,
      },
    });
    const { screen, client } = await mount(api(initial));
    await waitFor(() => expect(screen.getByText(title)).toBeTruthy());
    expect(screen.queryByText("No matching mail")).toBeNull();
    if (status === "PENDING" || status === "ERROR")
      expect(client.getResults).toHaveBeenCalledTimes(1);
    else expect(client.getResults).not.toHaveBeenCalled();
    await fireEvent.press(screen.getByLabelText("Review and edit interests"));
    expect(router.push).toHaveBeenCalledWith("/mail-interests");
  },
);

test("the separate page keeps saved criteria and returns only after a versioned save", async () => {
  const initial = readyState();
  const { screen, client } = await mount(api(initial), MailInterestsScreen);
  await waitFor(() =>
    expect(screen.getByLabelText("Remove interest tag 학교")).toBeTruthy(),
  );
  await fireEvent.press(screen.getByLabelText("Remove interest tag 학교"));
  const description =
    "  취업 소식만 보고 싶어요.\n광고와 유료 강좌는 제외해 주세요.  ";
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    description,
  );
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  expect(client.save).toHaveBeenCalledWith({
    tags: [],
    description,
    expected_version: 2,
  });
  await waitFor(() => expect(router.back).toHaveBeenCalledTimes(1));
});

test("a failed save keeps the separate page and its draft open", async () => {
  const client = api(readyState());
  client.save.mockRejectedValueOnce(new Error("최신 설정을 확인해 주세요."));
  const { screen } = await mount(client, MailInterestsScreen);
  await waitFor(() =>
    expect(screen.getByLabelText("Describe mail interests")).toBeTruthy(),
  );
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    "변경 중인 설명",
  );
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  expect(screen.getByLabelText("Describe mail interests").props.value).toBe(
    "변경 중인 설명",
  );
  expect(screen.getByRole("alert")).toBeTruthy();
  expect(router.back).not.toHaveBeenCalled();
});

test("disconnecting keeps saved criteria available and offers reconnection instead of showing old results", async () => {
  mockedGoogle.mockReturnValue({
    connection: disconnectedGoogle(),
    checking: false,
    error: null,
    refresh: jest.fn().mockResolvedValue(undefined),
  } as never);
  const initial = readyState();
  const client = api(initial);
  client.getResults.mockResolvedValue(page(initial, [informationalMail]));
  const { screen } = await mount(client);
  await waitFor(() =>
    expect(screen.getByText("Connect mail to continue")).toBeTruthy(),
  );
  expect(screen.getByText("#학교")).toBeTruthy();
  expect(
    screen.queryByLabelText("도서관 공간 정보, view mail details"),
  ).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Connect mail" }));
  expect(router.push).toHaveBeenCalledWith("/connections");
});

test("account changes clear an open detail and show only the next owner's profile", async () => {
  const initial = readyState();
  const first = api(initial);
  first.getResults.mockResolvedValue(page(initial, [informationalMail]));
  const second = api(state());
  mockedFactory.mockImplementation((options) =>
    options?.ownerId === "other-user" ? second : first,
  );
  const screen = await render(
    <MailInterestProvider>
      <MailScreen />
    </MailInterestProvider>,
  );
  await waitFor(() =>
    expect(
      screen.getByLabelText("도서관 공간 정보, view mail details"),
    ).toBeTruthy(),
  );
  await fireEvent.press(
    screen.getByLabelText("도서관 공간 정보, view mail details"),
  );
  mockedAuth.mockReturnValue({ user: { userId: "other-user" } } as never);
  await screen.rerender(
    <MailInterestProvider>
      <MailScreen />
    </MailInterestProvider>,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Set mail interests")).toBeTruthy(),
  );

  expect(screen.queryByText("Why this matched")).toBeNull();
  expect(screen.queryByText(informationalMail.title)).toBeNull();
  expect(screen.queryByLabelText("Remove interest tag 학교")).toBeNull();
  expect(mockedFactory).toHaveBeenLastCalledWith({ ownerId: "other-user" });
});

test("loading the profile does not display registration or an empty-result claim prematurely", async () => {
  const pending = new Promise<MailInterestState>(() => undefined);
  const client = api();
  client.getState.mockReturnValueOnce(pending);
  const { screen } = await mount(client);

  expect(screen.getByText("Loading interests.")).toBeTruthy();
  expect(screen.queryByLabelText("Enter interest tags")).toBeNull();
  expect(screen.queryByText("No matching mail")).toBeNull();
  expect(client.getResults).not.toHaveBeenCalled();
});

test("the separate page accepts a keyword during token-available setup without a duplicate recommendation", async () => {
  mockedGoogle.mockReturnValue({
    connection: {
      ...disconnectedGoogle(),
      status: "CONNECTING",
      grantedScopes: ["https://www.googleapis.com/auth/gmail.readonly"],
    },
    checking: false,
    error: null,
    refresh: jest.fn().mockResolvedValue(undefined),
  } as never);
  const initial = state({
    recommendations: {
      status: "PENDING",
      request_id: "queued-setup-recommendation",
    },
  });
  const { screen, client } = await mount(api(initial), MailInterestsScreen);
  await waitFor(() =>
    expect(screen.getByLabelText("Enter interest tags")).toBeTruthy(),
  );
  expect(screen.getByText("Retry request")).toBeTruthy();
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "학교",
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Start with these interests" }),
  );
  expect(client.save).toHaveBeenCalledWith({
    tags: ["학교"],
    description: "",
    expected_version: 0,
  });
  await waitFor(() => expect(router.back).toHaveBeenCalledTimes(1));
  expect(client.recommend).not.toHaveBeenCalled();
});

test("cancelling the separate page never saves the draft", async () => {
  const { screen, client } = await mount(
    api(readyState()),
    MailInterestsScreen,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Describe mail interests")).toBeTruthy(),
  );
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    "저장하지 않을 내용",
  );
  await fireEvent.press(screen.getByRole("button", { name: "Cancel" }));
  expect(router.back).toHaveBeenCalledTimes(1);
  expect(client.save).not.toHaveBeenCalled();
});

test("switching accounts clears the separate editor draft", async () => {
  const first = api(readyState());
  const second = api(state());
  mockedFactory.mockImplementation((options) =>
    options?.ownerId === "other-user" ? second : first,
  );
  const screen = await render(
    <MailInterestProvider>
      <MailInterestsScreen />
    </MailInterestProvider>,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Remove interest tag 학교")).toBeTruthy(),
  );
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    "첫 계정의 초안",
  );
  mockedAuth.mockReturnValue({ user: { userId: "other-user" } } as never);
  await screen.rerender(
    <MailInterestProvider>
      <MailInterestsScreen />
    </MailInterestProvider>,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Describe mail interests").props.value).toBe(
      "",
    ),
  );
  expect(screen.queryByLabelText("Remove interest tag 학교")).toBeNull();
  expect(first.save).not.toHaveBeenCalled();
  expect(second.save).not.toHaveBeenCalled();
});
