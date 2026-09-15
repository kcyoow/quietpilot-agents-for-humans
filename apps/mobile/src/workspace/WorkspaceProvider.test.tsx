import { act, cleanup, renderHook } from "@testing-library/react-native";
import type { PropsWithChildren } from "react";
import { createProductApi, type LiveCaseDetail } from "@/src/api/productApi";
import { useAuth } from "@/src/auth/AuthProvider";
import { usePrototype } from "@/src/prototype/PrototypeProvider";
import { liveCaseDetailToWorkspace } from "@/src/workspace/adapters";
import {
  WorkspaceProvider,
  useWorkspace,
} from "@/src/workspace/WorkspaceProvider";

jest.mock("@/src/api/productApi", () => ({ createProductApi: jest.fn() }));
jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("@/src/prototype/PrototypeProvider", () => ({
  usePrototype: jest.fn(),
}));

const mockedApiFactory = jest.mocked(createProductApi);
const mockedAuth = jest.mocked(useAuth);
const mockedPrototype = jest.mocked(usePrototype);

function detail(
  goal = "상세 결과",
  version = 1,
  caseId = "case-live",
): LiveCaseDetail {
  return {
    case_id: caseId,
    case_type: "DIRECT_DELEGATION",
    goal,
    next_action: "내용을 확인해 주세요.",
    priority: 50,
    providers: [],
    risk: "LOW",
    status: "PREPARING",
    summary: "저장된 상세 내용",
    updated_at: "2026-09-06T00:00:00Z",
    version,
    why_now: "요청한 내용을 정리해요.",
    actions: [],
    current_plan_hash: null,
    current_plan_version: null,
    evidence: [
      {
        detail: "사용자 요청",
        evidence_id: "evidence-one",
        evidence_ref: "direct:one",
        label: "직접 요청",
        provider: "direct",
        revision: 1,
      },
    ],
    messages: [],
    plan: null,
    timeline: [],
  };
}

function api(goal = "상세 결과") {
  return {
    configured: true,
    createDirectCase: jest.fn().mockResolvedValue({ case_id: "case-live" }),
    getCase: jest.fn().mockResolvedValue(detail(goal)),
    listSuggestions: jest.fn().mockResolvedValue([]),
    listSuggestionGroups: jest.fn().mockResolvedValue([]),
    listCases: jest.fn().mockResolvedValue([]),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function user(userId: string | null) {
  mockedAuth.mockReturnValue({
    user: userId
      ? {
          userId,
          displayName: "사용자",
          email: "audit@example.invalid",
          mode: "cognito",
        }
      : null,
  } as never);
}

function scenario(goal: string | null) {
  mockedPrototype.mockReturnValue({
    snapshot:
      goal === null
        ? null
        : {
            candidateGroups: [],
            candidates: [],
            cases: [liveCaseDetailToWorkspace(detail(goal))],
            loadedScenario: "FULL",
          },
    error: null,
    status: "ready",
  } as never);
}

function Wrapper({ children }: PropsWithChildren) {
  return <WorkspaceProvider>{children}</WorkspaceProvider>;
}

async function flushScheduledRefresh() {
  // Commit rerender first; then run the provider's zero-delay refresh inside act.
  await act(async () => {
    await jest.advanceTimersByTimeAsync(0);
  });
}

async function mount(client = api(), cases: LiveCaseDetail[] = []) {
  client.listCases.mockResolvedValueOnce(cases).mockResolvedValueOnce([]);
  mockedApiFactory.mockReturnValue(client as never);
  const hook = await renderHook(() => useWorkspace(), { wrapper: Wrapper });
  await flushScheduledRefresh();
  expect(hook.result.current.status).toBe("ready");
  return hook;
}

function queueRefresh(
  client: ReturnType<typeof api>,
  cases: LiveCaseDetail[] = [],
) {
  const response = deferred<[]>();
  client.listSuggestions.mockReturnValueOnce(response.promise);
  client.listSuggestionGroups.mockResolvedValueOnce([]);
  client.listCases.mockResolvedValueOnce(cases).mockResolvedValueOnce([]);
  return response;
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useFakeTimers();
  user("user-a");
  scenario(null);
});

afterEach(async () => {
  await cleanup();
  jest.useRealTimers();
});

test("keeps the LIVE detail loader stable when a successful read updates the snapshot", async () => {
  const client = api();
  const hook = await mount(client);
  const loadCase = hook.result.current.loadCase;
  const snapshot = hook.result.current.snapshot;
  await act(async () => {
    await loadCase("case-live");
  });
  expect(hook.result.current.snapshot).not.toBe(snapshot);
  expect(hook.result.current.snapshot?.cases[0].goal).toBe("상세 결과");
  expect(hook.result.current.snapshot?.cases[0].evidence).toHaveLength(1);
  expect(hook.result.current.loadCase).toBe(loadCase);
  expect(client.getCase).toHaveBeenCalledTimes(1);
});

test("retains the LIVE loader for the same account even if the auth object is refreshed", async () => {
  const hook = await mount();
  const loadCase = hook.result.current.loadCase;
  user("user-a");
  await hook.rerender(undefined);
  await flushScheduledRefresh();
  expect(hook.result.current.loadCase).toBe(loadCase);
});

test("updates the loader on sign-out and blocks both current and saved loaders", async () => {
  const client = api();
  const hook = await mount(client);
  const oldLoader = hook.result.current.loadCase;
  user(null);
  await hook.rerender(undefined);
  await flushScheduledRefresh();
  expect(hook.result.current.loadCase).not.toBe(oldLoader);
  await act(async () => {
    expect(await hook.result.current.loadCase("case-live")).toBeNull();
    expect(await oldLoader("case-live")).toBeNull();
  });
  expect(client.getCase).not.toHaveBeenCalled();
  expect(hook.result.current.snapshot?.cases).toEqual([]);
});

test("uses the API instance belonging to a newly mounted provider", async () => {
  const firstClient = api("첫 번째 API");
  const first = await mount(firstClient);
  const firstLoader = first.result.current.loadCase;
  await first.unmount();
  const secondClient = api("다음 API");
  const second = await mount(secondClient);
  expect(second.result.current.loadCase).not.toBe(firstLoader);
  await act(async () => {
    await second.result.current.loadCase("case-live");
  });
  expect(second.result.current.snapshot?.cases[0].goal).toBe("다음 API");
  expect(secondClient.getCase).toHaveBeenCalledTimes(1);
  expect(firstClient.getCase).not.toHaveBeenCalled();
});

test("resolves scenario details from the current snapshot without a live API read", async () => {
  const client = api();
  mockedApiFactory.mockReturnValue(client as never);
  scenario("처음 모의 내용");
  const hook = await renderHook(() => useWorkspace(), { wrapper: Wrapper });
  expect(hook.result.current.source).toBe("SCENARIO");
  await expect(
    hook.result.current.loadCase("case-live"),
  ).resolves.toMatchObject({ goal: "처음 모의 내용", dataSource: "SCENARIO" });
  scenario("최신 모의 내용");
  await hook.rerender(undefined);
  await expect(
    hook.result.current.loadCase("case-live"),
  ).resolves.toMatchObject({ goal: "최신 모의 내용", dataSource: "SCENARIO" });
  await expect(hook.result.current.loadCase("missing")).resolves.toBeNull();
  expect(client.getCase).not.toHaveBeenCalled();
});

test("masks the previous user's cached data before the new user's refresh finishes", async () => {
  const client = api();
  const hook = await mount(client, [detail("user-a data")]);
  const next = queueRefresh(client, [detail("user-b data")]);
  user("user-b");
  await hook.rerender(undefined);
  expect(hook.result.current.snapshot?.cases).toEqual([]);
  expect(hook.result.current.error).toBeNull();
  expect(hook.result.current.status).toBe("booting");
  await flushScheduledRefresh();
  await act(async () => {
    next.resolve([]);
    await jest.advanceTimersByTimeAsync(0);
  });
  expect(hook.result.current.snapshot?.cases[0].goal).toBe("user-b data");
});

test.each(["resolve", "reject"] as const)(
  "ignores a pending detail %s after sign-out",
  async (outcome) => {
    const client = api();
    const hook = await mount(client);
    const response = deferred<LiveCaseDetail>();
    client.getCase.mockReturnValueOnce(response.promise);
    let result!: Promise<unknown>;
    await act(async () => {
      result = hook.result.current
        .loadCase("case-live")
        .catch((error) => error);
    });
    user(null);
    await hook.rerender(undefined);
    await flushScheduledRefresh();
    await act(async () => {
      if (outcome === "resolve") response.resolve(detail("old private data"));
      else response.reject(new Error("old private error"));
      expect(await result).toBeNull();
    });
    expect(hook.result.current.snapshot?.cases).toEqual([]);
    expect(hook.result.current.error).toBeNull();
    expect(hook.result.current.status).toBe("ready");
  },
);

test("ignores a previous-account refresh that finishes after the new account", async () => {
  const client = api();
  const hook = await mount(client);
  const previous = queueRefresh(client, [detail("previous account")]);
  let oldRefresh!: Promise<void>;
  await act(async () => {
    oldRefresh = hook.result.current.refresh();
  });
  const current = queueRefresh(client, [detail("current account")]);
  user("user-b");
  await hook.rerender(undefined);
  await flushScheduledRefresh();
  await act(async () => {
    current.resolve([]);
    await jest.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    previous.resolve([]);
    await oldRefresh;
  });
  expect(hook.result.current.snapshot?.cases[0].goal).toBe("current account");
  expect(hook.result.current.status).toBe("ready");
});

test.each(["resolve", "reject"] as const)(
  "an older refresh %s cannot replace the newer request's data, error, or loading state",
  async (outcome) => {
    const client = api();
    const hook = await mount(client);
    const older = queueRefresh(client, [detail("old")]);
    let olderRun!: Promise<void>;
    await act(async () => {
      olderRun = hook.result.current.refresh();
    });
    const newer = queueRefresh(client, [detail("new")]);
    let newerRun!: Promise<void>;
    await act(async () => {
      newerRun = hook.result.current.refresh();
    });
    await act(async () => {
      if (outcome === "resolve") older.resolve([]);
      else older.reject(new Error("old refresh failed"));
      await olderRun;
    });
    expect(hook.result.current.snapshot?.cases).toEqual([]);
    expect(hook.result.current.error).toBeNull();
    expect(hook.result.current.status).toBe("booting");
    await act(async () => {
      newer.resolve([]);
      await newerRun;
    });
    expect(hook.result.current.snapshot?.cases[0].goal).toBe("new");
    expect(hook.result.current.status).toBe("ready");
  },
);

test("the newest detail request wins while different case reads remain independent", async () => {
  const client = api();
  const hook = await mount(client);
  const old = deferred<LiveCaseDetail>();
  const newest = deferred<LiveCaseDetail>();
  const other = deferred<LiveCaseDetail>();
  client.getCase
    .mockReturnValueOnce(old.promise)
    .mockReturnValueOnce(newest.promise)
    .mockReturnValueOnce(other.promise);
  let oldRun!: Promise<unknown>;
  let newestRun!: Promise<unknown>;
  let otherRun!: Promise<unknown>;
  await act(async () => {
    oldRun = hook.result.current.loadCase("case-live");
    newestRun = hook.result.current.loadCase("case-live");
    otherRun = hook.result.current.loadCase("case-other");
  });
  await act(async () => {
    newest.resolve(detail("latest", 2));
    await newestRun;
  });
  await act(async () => {
    other.resolve(detail("other", 1, "case-other"));
    await otherRun;
  });
  await act(async () => {
    old.resolve(detail("old", 1));
    expect(await oldRun).toBeNull();
  });
  expect(hook.result.current.snapshot?.cases.map((item) => item.goal)).toEqual([
    "latest",
    "other",
  ]);
});

test.each(["older summary", "omitted case"] as const)(
  "a refresh started before a detail read cannot remove that detail via %s",
  async (variant) => {
    const client = api();
    const hook = await mount(client);
    const refresh = queueRefresh(
      client,
      variant === "older summary" ? [detail("summary", 1)] : [],
    );
    let refreshRun!: Promise<void>;
    await act(async () => {
      refreshRun = hook.result.current.refresh();
    });
    client.getCase.mockResolvedValueOnce(detail("new detail", 2));
    await act(async () => {
      await hook.result.current.loadCase("case-live");
    });
    await act(async () => {
      refresh.resolve([]);
      await refreshRun;
    });
    expect(hook.result.current.snapshot?.cases).toHaveLength(1);
    expect(hook.result.current.snapshot?.cases[0]).toMatchObject({
      goal: "new detail",
      version: 2,
    });
    expect(hook.result.current.snapshot?.cases[0].evidence).toHaveLength(1);
  },
);

test("a late lower-version detail cannot overwrite a newer list version", async () => {
  const client = api();
  const hook = await mount(client);
  const older = deferred<LiveCaseDetail>();
  client.getCase.mockReturnValueOnce(older.promise);
  let olderRun!: Promise<unknown>;
  await act(async () => {
    olderRun = hook.result.current.loadCase("case-live");
  });
  const refresh = queueRefresh(client, [detail("new list version", 3)]);
  await act(async () => {
    const refreshRun = hook.result.current.refresh();
    refresh.resolve([]);
    await refreshRun;
  });
  await act(async () => {
    older.resolve(detail("old detail", 1));
    expect(await olderRun).toMatchObject({ version: 3 });
  });
  expect(hook.result.current.snapshot?.cases[0]).toMatchObject({
    goal: "new list version",
    version: 3,
  });
});

test("an old detail error does not overwrite feedback from a newer successful refresh", async () => {
  const client = api();
  const hook = await mount(client);
  const response = deferred<LiveCaseDetail>();
  client.getCase.mockReturnValueOnce(response.promise);
  let pending!: Promise<unknown>;
  await act(async () => {
    pending = hook.result.current.loadCase("case-live").catch((error) => error);
  });
  await act(async () => {
    await hook.result.current.refresh();
  });
  await act(async () => {
    response.reject(new Error("earlier detail error"));
    await pending;
  });
  expect(hook.result.current.error).toBeNull();
});

test("a LIVE response from before a source switch is not restored on return to LIVE", async () => {
  const client = api();
  const hook = await mount(client);
  const response = deferred<LiveCaseDetail>();
  client.getCase.mockReturnValueOnce(response.promise);
  let pending!: Promise<unknown>;
  await act(async () => {
    pending = hook.result.current.loadCase("case-live");
  });
  scenario("current scenario");
  await hook.rerender(undefined);
  await act(async () => {
    response.resolve(detail("previous live response"));
    expect(await pending).toBeNull();
  });
  expect(hook.result.current.source).toBe("SCENARIO");
  expect(hook.result.current.snapshot?.cases[0].goal).toBe("current scenario");
  const refresh = queueRefresh(client);
  scenario(null);
  await hook.rerender(undefined);
  expect(hook.result.current.source).toBe("LIVE");
  expect(hook.result.current.snapshot?.cases).toEqual([]);
  await flushScheduledRefresh();
  await act(async () => {
    refresh.resolve([]);
    await jest.advanceTimersByTimeAsync(0);
  });
});

test("an old mutation completion cannot continue readback or change the new account's status", async () => {
  const client = api();
  const hook = await mount(client);
  const response = deferred<{ case_id: string }>();
  client.createDirectCase.mockReturnValueOnce(response.promise);
  let pending!: Promise<unknown>;
  await act(async () => {
    pending = hook.result.current
      .createDirectCase("old request")
      .catch((error) => error);
  });
  user("user-b");
  await hook.rerender(undefined);
  await flushScheduledRefresh();
  await act(async () => {
    response.resolve({ case_id: "old-case" });
    expect(await pending).toBeInstanceOf(Error);
  });
  expect(client.getCase).not.toHaveBeenCalled();
  expect(hook.result.current.snapshot?.cases).toEqual([]);
  expect(hook.result.current.status).toBe("ready");
  expect(hook.result.current.error).toBeNull();
});

test("a finished refresh does not clear an ongoing mutation's updating state", async () => {
  const client = api();
  const hook = await mount(client);
  const response = deferred<{ case_id: string }>();
  client.createDirectCase.mockReturnValueOnce(response.promise);
  let pending!: Promise<unknown>;
  await act(async () => {
    pending = hook.result.current.createDirectCase("request");
  });
  await act(async () => {
    await hook.result.current.refresh();
  });
  expect(hook.result.current.status).toBe("updating");
  await act(async () => {
    response.resolve({ case_id: "case-live" });
    await pending;
  });
  expect(hook.result.current.status).toBe("ready");
});
