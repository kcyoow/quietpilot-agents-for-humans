import {
  act,
  cleanup,
  renderHook,
  waitFor,
} from "@testing-library/react-native";
import { type PropsWithChildren } from "react";

import { useAuth } from "@/src/auth/AuthProvider";
import {
  MailInterestProvider,
  useMailInterestState,
} from "@/src/mail/MailInterestProvider";
import {
  createMailApi,
  MailApiError,
  type MailInterestState,
  type MailResult,
  type MailResults,
} from "@/src/mail/mailApi";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";

jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("aws-amplify/auth", () => ({ fetchAuthSession: jest.fn() }));
jest.mock("@/src/workspace/WorkspaceProvider", () => ({
  useWorkspace: jest.fn(),
}));
jest.mock("@/src/mail/mailApi", () => ({
  ...jest.requireActual("@/src/mail/mailApi"),
  createMailApi: jest.fn(),
}));

const mockedAuth = jest.mocked(useAuth);
const mockedWorkspace = jest.mocked(useWorkspace);
const mockedFactory = jest.mocked(createMailApi);
const workspaceRefresh = jest.fn().mockResolvedValue(undefined);

function freeze<T>(value: T): T {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
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
    tags: ["학교"],
    description: "",
    version: 1,
    updated_at: "2026-09-09T00:00:00Z",
    ...profile,
  };
  return freeze({
    profile: nextProfile,
    recommendations: {
      status: "NOT_STARTED",
      request_id: null,
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
  });
}

function result(evidence_ref: string, title = evidence_ref): MailResult {
  return freeze({
    evidence_ref,
    title,
    summary: "학교 관련 안내",
    reason: "관심 태그와 관련된 정보",
    sender_domain: "example.invalid",
    received_at: null,
    matched_tags: ["학교"],
  });
}

function page(overrides: Partial<MailResults> = {}): MailResults {
  return freeze({
    profile_version: 1,
    scan_id: "scan-1",
    status: "READY",
    items: [],
    next_cursor: null,
    ...overrides,
  });
}

function client(initial = state()) {
  return {
    configured: true,
    getState: jest.fn().mockResolvedValue(initial),
    save: jest.fn().mockResolvedValue(state({ profile: { version: 2 } })),
    recommend: jest.fn().mockResolvedValue(
      state({
        recommendations: { status: "PENDING", request_id: "recommend-1" },
      }),
    ),
    scan: jest.fn().mockResolvedValue(
      state({
        scan: { status: "PENDING", scan_id: "scan-next" },
      }),
    ),
    getResults: jest.fn().mockResolvedValue(
      page({
        scan_id: initial.scan.scan_id,
        profile_version: initial.profile.version,
        status:
          initial.scan.status === "PENDING" || initial.scan.status === "ERROR"
            ? initial.scan.status
            : "READY",
      }),
    ),
  };
}

function auth(
  ownerId: string | null = "owner-a",
  source: "LIVE" | "SCENARIO" = "LIVE",
) {
  mockedAuth.mockReturnValue({
    user: ownerId ? { userId: ownerId } : null,
  } as never);
  mockedWorkspace.mockReturnValue({
    source,
    refresh: workspaceRefresh,
  } as never);
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

function Wrapper({ children }: PropsWithChildren) {
  return <MailInterestProvider>{children}</MailInterestProvider>;
}

async function mount(api = client()) {
  mockedFactory.mockReturnValue(api);
  const hook = await renderHook(() => useMailInterestState(), {
    wrapper: Wrapper,
  });
  return { api, hook };
}

beforeEach(() => {
  jest.useFakeTimers();
  jest.clearAllMocks();
  mockedFactory.mockReset();
  auth();
});

afterEach(async () => {
  await cleanup();
  jest.useRealTimers();
});

test.each(["signed-out", "scenario"] as const)(
  "%s sessions never read live mail or allow a saved callback to submit",
  async (mode) => {
    auth(
      mode === "signed-out" ? null : "owner-a",
      mode === "scenario" ? "SCENARIO" : "LIVE",
    );
    const { api, hook } = await mount();

    expect(hook.result.current.enabled).toBe(false);
    expect(hook.result.current.state).toBeNull();
    expect(hook.result.current.results).toBeNull();
    expect(hook.result.current.loading).toBe(false);
    await expect(
      hook.result.current.save({
        tags: ["학교"],
        description: "",
        expected_version: 0,
      }),
    ).rejects.toThrow();
    await expect(hook.result.current.recommend()).rejects.toThrow();
    expect(api.getState).not.toHaveBeenCalled();
    expect(api.save).not.toHaveBeenCalled();
    expect(api.recommend).not.toHaveBeenCalled();
  },
);

test.each(
  (["owner", "source"] as const).flatMap((boundary) =>
    (["READY", "ERROR"] as const).map((status) => ({ boundary, status })),
  ),
)(
  "changing the $boundary clears $status results and ignores a former session's late read",
  async ({ boundary, status }) => {
    const oldApi = client(state({ scan: { status, scan_id: "scan-a" } }));
    oldApi.getResults.mockResolvedValue(
      page({
        scan_id: "scan-a",
        status,
        items: [result("old-owner-message")],
      }),
    );
    const newApi = client(state({ profile: { tags: ["취업"], version: 7 } }));
    mockedFactory.mockImplementation((options) =>
      options?.ownerId === "owner-b" ? newApi : oldApi,
    );
    const seen: {
      boundary: string;
      version: number | undefined;
      refs: string[];
    }[] = [];
    let label = "old";
    const hook = await renderHook(
      () => {
        const value = useMailInterestState();
        seen.push({
          boundary: label,
          version: value.state?.profile.version,
          refs: value.results?.items.map((item) => item.evidence_ref) ?? [],
        });
        return value;
      },
      { wrapper: Wrapper },
    );
    await waitFor(() =>
      expect(hook.result.current.results?.items).toHaveLength(1),
    );
    const delayed = deferred<MailInterestState>();
    oldApi.getState.mockReturnValueOnce(delayed.promise);
    let pending!: Promise<void>;
    const oldSave = hook.result.current.save;
    await act(() => {
      pending = hook.result.current.refresh();
    });

    label = "new";
    auth(
      boundary === "owner" ? "owner-b" : "owner-a",
      boundary === "source" ? "SCENARIO" : "LIVE",
    );
    await hook.rerender(undefined);
    if (boundary === "owner") {
      await waitFor(() =>
        expect(hook.result.current.state?.profile.version).toBe(7),
      );
    } else {
      expect(hook.result.current.enabled).toBe(false);
      expect(hook.result.current.state).toBeNull();
    }
    expect(hook.result.current.results).toBeNull();
    expect(
      seen
        .filter((snapshot) => snapshot.boundary === "new")
        .some(
          (snapshot) =>
            snapshot.refs.includes("old-owner-message") ||
            snapshot.version === 1,
        ),
    ).toBe(false);
    await act(async () => {
      delayed.resolve(state({ profile: { version: 99 } }));
      await pending;
    });
    await expect(
      oldSave({ tags: ["다른 관심"], description: "", expected_version: 1 }),
    ).rejects.toThrow();

    expect(oldApi.save).not.toHaveBeenCalled();
    expect(hook.result.current.state?.profile.version).toBe(
      boundary === "owner" ? 7 : undefined,
    );
    expect(hook.result.current.error).toBeNull();
  },
);

test("pending scan, loading ready results, and verified empty results remain distinct", async () => {
  const api = client(state({ scan: { status: "PENDING", scan_id: "scan-1" } }));
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  expect(hook.result.current.state?.scan.status).toBe("PENDING");
  expect(hook.result.current.results).toEqual(page({ status: "PENDING" }));
  expect(api.getResults).toHaveBeenCalledTimes(1);

  api.getState.mockResolvedValue(
    state({ scan: { status: "READY", scan_id: "scan-1" } }),
  );
  const delayed = deferred<MailResults>();
  api.getResults.mockReturnValueOnce(delayed.promise);
  let pending!: Promise<void>;
  await act(() => {
    pending = hook.result.current.refresh();
  });
  expect(hook.result.current.loading).toBe(true);
  expect(hook.result.current.state?.scan.status).toBe("PENDING");
  expect(hook.result.current.results).toEqual(page({ status: "PENDING" }));
  await act(async () => {
    delayed.resolve(page());
    await pending;
  });

  expect(hook.result.current.loading).toBe(false);
  expect(hook.result.current.state?.scan.status).toBe("READY");
  expect(hook.result.current.results).toEqual(page());
  expect(hook.result.current.error).toBeNull();
});

test("pending polls retain validated rows and do not expose foreground loading or pagination", async () => {
  const api = client(
    state({
      scan: { status: "PENDING", scan_id: "scan-1", processed_count: 4 },
    }),
  );
  const partial = page({
    status: "PENDING",
    items: [result("first")],
    next_cursor: "partial-cursor",
  });
  api.getResults.mockResolvedValueOnce(partial);
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.results).toEqual(partial));
  await act(() => hook.result.current.loadMore());
  expect(api.getResults).toHaveBeenCalledTimes(1);
  const later = deferred<MailResults>();
  api.getState.mockResolvedValue(
    state({
      scan: { status: "PENDING", scan_id: "scan-1", processed_count: 12 },
    }),
  );
  api.getResults.mockReturnValueOnce(later.promise);
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1800);
  });
  expect(hook.result.current.state?.scan.processed_count).toBe(12);
  expect(hook.result.current.results).toEqual(partial);
  expect(hook.result.current.loading).toBe(false);
  await act(() =>
    later.resolve(
      page({
        status: "PENDING",
        items: [result("new-first"), result("first")],
      }),
    ),
  );
  expect(
    hook.result.current.results?.items.map((item) => item.evidence_ref),
  ).toEqual(["new-first", "first"]);
  expect(workspaceRefresh).not.toHaveBeenCalled();
});

test.each(["ERROR", "PENDING", "READY"] as const)(
  "failed scans retain partial results through a delayed %s page without claiming completion",
  async (pageStatus) => {
    const api = client(
      state({ scan: { status: "PENDING", scan_id: "scan-1" } }),
    );
    const partial = page({ status: "PENDING", items: [result("first")] });
    api.getResults.mockResolvedValueOnce(partial);
    const { hook } = await mount(api);
    await waitFor(() => expect(hook.result.current.results).toEqual(partial));

    const failed = state({
      scan: {
        status: "ERROR",
        scan_id: "scan-1",
        processed_count: 32,
        matched_count: 12,
        error_code: "SCAN_FAILED",
      },
    });
    const later = deferred<MailResults>();
    api.getState.mockResolvedValue(failed);
    api.getResults.mockReturnValueOnce(later.promise);
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1800);
    });
    expect(hook.result.current.state).toEqual(failed);
    expect(hook.result.current.results).toEqual(partial);
    await act(() =>
      later.resolve(
        page({
          status: pageStatus,
          items: [result("first"), result("second")],
        }),
      ),
    );
    expect(hook.result.current.state?.scan.status).toBe("ERROR");
    expect(hook.result.current.results?.status).toBe("ERROR");
    expect(hook.result.current.results?.items).toHaveLength(2);
    expect(hook.result.current.error).toBeNull();
    expect(workspaceRefresh).not.toHaveBeenCalled();
    const reads = api.getState.mock.calls.length;
    await act(async () => {
      await jest.advanceTimersByTimeAsync(5400);
    });
    expect(api.getState).toHaveBeenCalledTimes(reads);
    expect(api.scan).not.toHaveBeenCalled();
  },
);

test("an already failed scan loads and paginates stored results without a completion notification", async () => {
  const api = client(state({ scan: { status: "ERROR", scan_id: "scan-1" } }));
  api.getResults
    .mockResolvedValueOnce(
      page({
        status: "ERROR",
        items: [result("first")],
        next_cursor: "saved-next",
      }),
    )
    .mockResolvedValueOnce(
      page({ status: "ERROR", items: [result("second")] }),
    );
  const { hook } = await mount(api);
  await waitFor(() =>
    expect(hook.result.current.results?.items).toHaveLength(1),
  );
  await act(() => hook.result.current.loadMore());
  expect(api.getResults).toHaveBeenLastCalledWith("saved-next");
  expect(
    hook.result.current.results?.items.map((item) => item.evidence_ref),
  ).toEqual(["first", "second"]);
  expect(hook.result.current.state?.scan.status).toBe("ERROR");
  expect(hook.result.current.results?.status).toBe("ERROR");
  expect(workspaceRefresh).not.toHaveBeenCalled();
  expect(api.scan).not.toHaveBeenCalled();
});

test.each(["PENDING", "READY"] as const)(
  "an ERROR page after a %s state read keeps its rows and the failed status",
  async (status) => {
    const api = client(state({ scan: { status, scan_id: "scan-1" } }));
    api.getResults.mockResolvedValue(
      page({ status: "ERROR", items: [result("stored")] }),
    );
    const { hook } = await mount(api);
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    expect(hook.result.current.state?.scan.status).toBe("ERROR");
    expect(hook.result.current.results?.status).toBe("ERROR");
    expect(hook.result.current.results?.items).toHaveLength(1);
    expect(hook.result.current.error).toBeNull();
    expect(workspaceRefresh).not.toHaveBeenCalled();
  },
);

test("normal pending batch conflicts keep rows and continue beyond three polls without a fatal error", async () => {
  const pending = state({
    scan: { status: "PENDING", scan_id: "scan-1", matched_count: 1 },
  });
  const api = client(pending);
  const partial = page({ status: "PENDING", items: [result("first")] });
  api.getResults.mockResolvedValueOnce(partial);
  for (let i = 0; i < 4; i += 1)
    api.getResults.mockRejectedValueOnce(
      new MailApiError("MAIL_VERSION_CONFLICT", 409),
    );
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.results).toEqual(partial));
  for (let i = 0; i < 4; i += 1) {
    await act(async () => {
      await jest.advanceTimersByTimeAsync(1800);
    });
    expect(hook.result.current.results).toEqual(partial);
    expect(hook.result.current.error).toBeNull();
  }
  expect(api.getResults).toHaveBeenCalledTimes(5);
  api.getResults.mockResolvedValue(page({ items: [result("first")] }));
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1800);
  });
  expect(hook.result.current.results?.status).toBe("READY");
  expect(hook.result.current.state?.scan.status).toBe("PENDING");
  expect(workspaceRefresh).not.toHaveBeenCalled();
  api.getState.mockResolvedValue(
    state({ scan: { status: "READY", scan_id: "scan-1", matched_count: 1 } }),
  );
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1800);
  });
  expect(hook.result.current.state?.scan.status).toBe("READY");
  expect(workspaceRefresh).toHaveBeenCalledTimes(1);
});

test("a result becoming pending after the state read cannot claim completed work", async () => {
  const api = client(state({ scan: { status: "READY", scan_id: "scan-1" } }));
  api.getResults.mockResolvedValueOnce(
    page({ status: "PENDING", items: [result("partial")] }),
  );
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  expect(hook.result.current.state?.scan.status).toBe("PENDING");
  expect(hook.result.current.results?.items[0].evidence_ref).toBe("partial");
  expect(hook.result.current.error).toBeNull();
  expect(workspaceRefresh).not.toHaveBeenCalled();
});

test("background recommendation checks preserve completed pagination", async () => {
  const initial = state({
    recommendations: { status: "PENDING" },
    scan: { status: "READY", scan_id: "scan-1" },
  });
  const api = client(initial);
  api.getResults
    .mockResolvedValueOnce(
      page({ items: [result("one")], next_cursor: "cursor-1" }),
    )
    .mockResolvedValueOnce(page({ items: [result("two")] }));
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  await act(() => hook.result.current.loadMore());
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1800);
  });
  expect(api.getState).toHaveBeenCalledTimes(2);
  expect(api.getResults).toHaveBeenCalledTimes(2);
  expect(
    hook.result.current.results?.items.map((item) => item.evidence_ref),
  ).toEqual(["one", "two"]);
  expect(hook.result.current.loading).toBe(false);
});

test.each(
  (["profile", "scan", "authorization"] as const).flatMap((change) =>
    (["PENDING", "ERROR"] as const).map((status) => ({ change, status })),
  ),
)(
  "$status partial results hide immediately when $change changes",
  async ({ change, status }) => {
    const initial = state({ scan: { status, scan_id: "scan-1" } });
    const api = client(initial);
    api.getResults.mockResolvedValueOnce(
      page({ status, items: [result("old")] }),
    );
    const { hook } = await mount(api);
    await waitFor(() =>
      expect(hook.result.current.results?.items).toHaveLength(1),
    );
    const next = state({
      profile: { version: change === "profile" ? 2 : 1 },
      scan: {
        status: "PENDING",
        scan_id: change === "scan" ? "scan-2" : "scan-1",
        error_code: change === "authorization" ? "GOOGLE_AUTH_REQUIRED" : null,
      },
    });
    const delayed = deferred<MailResults>();
    api.getState.mockResolvedValue(next);
    api.getResults.mockReturnValueOnce(delayed.promise);
    let refreshing!: Promise<void>;
    await act(() => {
      refreshing = hook.result.current.refresh();
    });
    expect(hook.result.current.results).toBeNull();
    if (change !== "authorization")
      await act(async () => {
        delayed.resolve(
          page({
            status: "PENDING",
            scan_id: next.scan.scan_id,
            profile_version: next.profile.version,
          }),
        );
        await refreshing;
      });
    else {
      await refreshing;
      expect(api.getResults).toHaveBeenCalledTimes(1);
    }
  },
);

test("an authentication failure hides already displayed partial rows", async () => {
  const api = client(state({ scan: { status: "PENDING", scan_id: "scan-1" } }));
  api.getResults
    .mockResolvedValueOnce(
      page({ status: "PENDING", items: [result("private-row")] }),
    )
    .mockRejectedValueOnce(new MailApiError("AUTH_REQUIRED", 401));
  const { hook } = await mount(api);
  await waitFor(() =>
    expect(hook.result.current.results?.items).toHaveLength(1),
  );
  await act(async () => {
    await jest.advanceTimersByTimeAsync(1800);
  });
  expect(hook.result.current.results).toBeNull();
  expect(hook.result.current.error).toContain("sign in");
});

test("authorization loss while resolving a save conflict also hides partial rows", async () => {
  const api = client(state({ scan: { status: "PENDING", scan_id: "scan-1" } }));
  api.getResults.mockResolvedValueOnce(
    page({ status: "PENDING", items: [result("old-row")] }),
  );
  const { hook } = await mount(api);
  await waitFor(() =>
    expect(hook.result.current.results?.items).toHaveLength(1),
  );
  api.save.mockRejectedValueOnce(
    new MailApiError("MAIL_VERSION_CONFLICT", 409),
  );
  api.getState.mockRejectedValueOnce(new MailApiError("AUTH_REQUIRED", 401));
  await act(async () => {
    await expect(
      hook.result.current.save({
        tags: ["학교"],
        description: "",
        expected_version: 1,
      }),
    ).rejects.toMatchObject({ code: "MAIL_VERSION_CONFLICT" });
  });
  expect(hook.result.current.results).toBeNull();
  await act(async () => {
    await jest.advanceTimersByTimeAsync(60_000);
  });
  expect(api.getState).toHaveBeenCalledTimes(2);
});

test("coalesces refreshes during an old read into one fresh read after it finishes", async () => {
  const { api, hook } = await mount();
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  const older = deferred<MailInterestState>();
  const latest = state({
    scan: { status: "READY", scan_id: "scan-1", matched_count: 1 },
  });
  api.getState.mockReturnValueOnce(older.promise).mockResolvedValue(latest);
  api.getResults.mockResolvedValue(page({ items: [result("newly-matched")] }));
  let firstRead!: Promise<void>;
  await act(() => {
    firstRead = hook.result.current.refresh();
  });
  await act(async () => {
    await hook.result.current.refresh();
    await hook.result.current.refresh();
  });
  expect(api.getState).toHaveBeenCalledTimes(2);

  await act(async () => {
    older.resolve(state());
    await firstRead;
  });

  expect(api.getState).toHaveBeenCalledTimes(3);
  expect(hook.result.current.state).toEqual(latest);
  expect(hook.result.current.results?.items[0].evidence_ref).toBe(
    "newly-matched",
  );
  expect(hook.result.current.loading).toBe(false);
});

test.each(["save", "recommend", "scan"] as const)(
  "a refresh during %s reads the latest state after the mutation settles",
  async (kind) => {
    const { api, hook } = await mount();
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    const pending = deferred<MailInterestState>();
    const latest = state({ profile: { tags: ["취업"], version: 2 } });
    api[kind].mockReturnValueOnce(pending.promise);
    api.getState.mockResolvedValue(latest);
    let mutation!: Promise<void>;
    await act(() => {
      mutation =
        kind === "save"
          ? hook.result.current.save({
              tags: ["취업"],
              description: "",
              expected_version: 1,
            })
          : hook.result.current[kind]();
    });
    await act(() => hook.result.current.refresh());
    expect(api.getState).toHaveBeenCalledTimes(1);
    await act(async () => {
      pending.resolve(state());
      await mutation;
    });
    await waitFor(() => expect(hook.result.current.state).toEqual(latest));
    expect(api.getState).toHaveBeenCalledTimes(2);
  },
);

test("a queued refresh cannot replay in a former owner's session", async () => {
  const firstApi = client();
  const secondApi = client(
    state({ profile: { tags: ["새 계정"], version: 7 } }),
  );
  mockedFactory.mockImplementation((options) =>
    options?.ownerId === "owner-b" ? secondApi : firstApi,
  );
  const hook = await renderHook(useMailInterestState, { wrapper: Wrapper });
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  const older = deferred<MailInterestState>();
  firstApi.getState.mockReturnValueOnce(older.promise);
  let reading!: Promise<void>;
  await act(() => {
    reading = hook.result.current.refresh();
  });
  await act(() => hook.result.current.refresh());
  auth("owner-b");
  await hook.rerender(undefined);
  await waitFor(() =>
    expect(hook.result.current.state?.profile.version).toBe(7),
  );
  await act(async () => {
    older.resolve(state());
    await reading;
  });
  expect(firstApi.getState).toHaveBeenCalledTimes(2);
  expect(hook.result.current.state?.profile.tags).toEqual(["새 계정"]);
});

test.each([
  ["NETWORK_ERROR", 0],
  ["REQUEST_TIMEOUT", 0],
  ["HTTP_ERROR", 429],
  ["HTTP_ERROR", 500],
  ["HTTP_ERROR", 503],
] as const)(
  "pending reads retry %s/%s twice, then allow an explicit recovery",
  async (code, status) => {
    const { api, hook } = await mount(
      client(state({ scan: { status: "PENDING", scan_id: "scan-1" } })),
    );
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    api.getState.mockRejectedValue(new MailApiError(code, status));
    let expectedReads = 1;
    for (const delay of [1800, 3000, 6000]) {
      await act(async () => {
        jest.advanceTimersByTime(delay);
      });
      expect(api.getState).toHaveBeenCalledTimes(++expectedReads);
    }
    expect(api.getState).toHaveBeenCalledTimes(4);
    expect(hook.result.current.state?.scan.status).toBe("PENDING");
    await act(async () => {
      jest.advanceTimersByTime(60_000);
    });
    expect(api.getState).toHaveBeenCalledTimes(4);

    api.getState.mockResolvedValue(
      state({ scan: { status: "READY", scan_id: "scan-1" } }),
    );
    api.getResults.mockResolvedValue(page({ items: [result("recovered")] }));
    await act(() => hook.result.current.refresh());
    expect(hook.result.current.error).toBeNull();
    expect(hook.result.current.results?.items[0].evidence_ref).toBe(
      "recovered",
    );
  },
);

test.each([
  ["API_NOT_CONFIGURED", 0],
  ["UNKNOWN_ERROR", 0],
  ["HTTP_ERROR", 400],
  ["HTTP_ERROR", 401],
  ["HTTP_ERROR", 403],
  ["INVALID_RESPONSE", 200],
] as const)(
  "pending reads stop automatic retries for %s/%s",
  async (code, status) => {
    const { api, hook } = await mount(
      client(state({ scan: { status: "PENDING", scan_id: "scan-1" } })),
    );
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    api.getState.mockRejectedValue(new MailApiError(code, status));
    await act(async () => {
      jest.advanceTimersByTime(1800);
    });
    expect(api.getState).toHaveBeenCalledTimes(2);
    expect(hook.result.current.error).toBeTruthy();
    await act(async () => {
      jest.advanceTimersByTime(60_000);
    });
    expect(api.getState).toHaveBeenCalledTimes(2);
    expect(hook.result.current.state?.scan.status).toBe("PENDING");
    if (status === 401 || status === 403)
      expect(hook.result.current.results).toBeNull();
    else expect(hook.result.current.results?.status).toBe("PENDING");
  },
);

test("a stale GET cannot overwrite a newer accepted profile save", async () => {
  const { api, hook } = await mount();
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  const delayed = deferred<MailInterestState>();
  api.getState.mockReturnValueOnce(delayed.promise);
  const saved = state({
    profile: { tags: ["취업"], version: 2 },
    scan: { status: "PENDING", scan_id: "scan-2" },
  });
  api.save.mockResolvedValue(saved);
  let pending!: Promise<void>;
  await act(() => {
    pending = hook.result.current.refresh();
  });
  await act(() =>
    hook.result.current.save({
      tags: ["취업"],
      description: "",
      expected_version: 1,
    }),
  );
  expect(hook.result.current.state).toEqual(saved);
  await act(async () => {
    delayed.resolve(state({ scan: { status: "READY", scan_id: "old-scan" } }));
    await pending;
  });

  expect(hook.result.current.state).toEqual(saved);
  expect(hook.result.current.results).toBeNull();
  expect(hook.result.current.loading).toBe(false);
  expect(api.getResults).not.toHaveBeenCalled();
  expect(workspaceRefresh).toHaveBeenCalledTimes(1);
});

test("a recommendation response cannot roll back a newer profile save", async () => {
  const { api, hook } = await mount();
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  const delayed = deferred<MailInterestState>();
  api.recommend.mockReturnValueOnce(delayed.promise);
  let recommendation!: Promise<void>;
  await act(() => {
    recommendation = hook.result.current.recommend();
  });
  expect(hook.result.current.requesting).toBe(true);
  const saved = state({
    profile: { version: 2, tags: ["취업"] },
    recommendations: { status: "PENDING", request_id: "recommend-1" },
    scan: { status: "PENDING", scan_id: "scan-2" },
  });
  api.save.mockResolvedValue(saved);
  await act(() =>
    hook.result.current.save({
      tags: ["취업"],
      description: "",
      expected_version: 1,
    }),
  );
  await act(async () => {
    delayed.resolve(
      state({
        recommendations: {
          status: "READY",
          request_id: "recommend-1",
          tags: [{ tag: "학교", evidence_refs: ["subject-ref-1"] }],
          title_count: 1,
        },
      }),
    );
    await recommendation;
  });

  expect(hook.result.current.state).toEqual(saved);
  expect(hook.result.current.requesting).toBe(false);
  expect(hook.result.current.saving).toBe(false);
  expect(hook.result.current.error).toBeNull();
});

test.each([
  { scan_id: "another-scan" },
  { profile_version: 2 },
  { status: "NOT_STARTED" as const },
])(
  "rejects a first result page whose generation/status differs: %p",
  async (mismatch) => {
    const api = client(state({ scan: { status: "READY", scan_id: "scan-1" } }));
    api.getResults.mockResolvedValue(
      page({ ...mismatch, items: [result("wrong-generation")] }),
    );
    const { hook } = await mount(api);
    await waitFor(() => expect(hook.result.current.loading).toBe(false));

    expect(hook.result.current.results).toBeNull();
    expect(hook.result.current.error).toContain("Refresh the results");
  },
);

test.each(["page", "conflict"] as const)(
  "a late cursor %s and retained loadMore callback cannot enter the next owner's session",
  async (completion) => {
    const oldApi = client(
      state({ scan: { status: "READY", scan_id: "scan-a" } }),
    );
    oldApi.getResults.mockResolvedValueOnce(
      page({
        scan_id: "scan-a",
        items: [result("a-1")],
        next_cursor: "cursor-a",
      }),
    );
    const delayed = deferred<MailResults>();
    oldApi.getResults.mockReturnValueOnce(delayed.promise);
    const newApi = client(
      state({
        profile: { version: 4, tags: ["취업"] },
        scan: { status: "READY", scan_id: "scan-b" },
      }),
    );
    newApi.getResults.mockResolvedValue(
      page({
        scan_id: "scan-b",
        profile_version: 4,
        items: [result("b-1")],
        next_cursor: "cursor-b",
      }),
    );
    mockedFactory.mockImplementation((options) =>
      options?.ownerId === "owner-b" ? newApi : oldApi,
    );
    const hook = await renderHook(() => useMailInterestState(), {
      wrapper: Wrapper,
    });
    await waitFor(() => expect(hook.result.current.loading).toBe(false));
    const oldLoadMore = hook.result.current.loadMore;
    let pending!: Promise<void>;
    await act(() => {
      pending = oldLoadMore();
    });

    auth("owner-b");
    await hook.rerender(undefined);
    await waitFor(() =>
      expect(hook.result.current.results?.scan_id).toBe("scan-b"),
    );
    await act(async () => {
      if (completion === "conflict")
        delayed.reject(new MailApiError("MAIL_VERSION_CONFLICT", 409));
      else delayed.resolve(page({ scan_id: "scan-a", items: [result("a-2")] }));
      await pending;
      await oldLoadMore();
    });

    expect(
      hook.result.current.results?.items.map((item) => item.evidence_ref),
    ).toEqual(["b-1"]);
    expect(hook.result.current.results?.next_cursor).toBe("cursor-b");
    expect(oldApi.getResults).toHaveBeenCalledTimes(2);
    expect(newApi.getResults).toHaveBeenCalledTimes(1);
    expect(hook.result.current.loadingMore).toBe(false);
  },
);

test("a changed result set reloads page one even when scan and profile IDs are unchanged", async () => {
  const api = client(state({ scan: { status: "READY", scan_id: "scan-1" } }));
  const refreshed = page({
    items: [result("new-high"), result("old-1")],
    next_cursor: "new-cursor",
  });
  api.getResults
    .mockResolvedValueOnce(
      page({
        items: [result("old-1"), result("old-2")],
        next_cursor: "old-cursor",
      }),
    )
    .mockRejectedValueOnce(new MailApiError("MAIL_VERSION_CONFLICT", 409))
    .mockResolvedValueOnce(refreshed)
    .mockResolvedValueOnce(page({ items: [result("old-2")] }));
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  await act(() => hook.result.current.loadMore());
  expect(hook.result.current.results).toEqual(refreshed);
  expect(api.getResults).toHaveBeenNthCalledWith(2, "old-cursor");
  expect(api.getResults).toHaveBeenNthCalledWith(3);
  expect(hook.result.current.error).toBeNull();
  await act(() => hook.result.current.loadMore());
  expect(api.getResults).toHaveBeenLastCalledWith("new-cursor");
  expect(
    hook.result.current.results?.items.map((item) => item.evidence_ref),
  ).toEqual(["new-high", "old-1", "old-2"]);
});

test("a failed first-page recovery cannot reuse the conflicted cursor or loop", async () => {
  const api = client(state({ scan: { status: "READY", scan_id: "scan-1" } }));
  api.getResults
    .mockResolvedValueOnce(
      page({ items: [result("old-1")], next_cursor: "old-cursor" }),
    )
    .mockRejectedValue(new MailApiError("MAIL_VERSION_CONFLICT", 409));
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  await act(() => hook.result.current.loadMore());
  expect(api.getResults).toHaveBeenCalledTimes(3);
  expect(hook.result.current.results?.next_cursor).toBeNull();
  expect(hook.result.current.error).toBeTruthy();
  await act(() => hook.result.current.loadMore());
  await act(async () => {
    jest.advanceTimersByTime(60_000);
  });
  expect(api.getResults).toHaveBeenCalledTimes(3);
});

test("an unrelated cursor 409 is reported without starting a first-page recovery", async () => {
  const api = client(state({ scan: { status: "READY", scan_id: "scan-1" } }));
  api.getResults
    .mockResolvedValueOnce(
      page({ items: [result("old-1")], next_cursor: "old-cursor" }),
    )
    .mockRejectedValueOnce(new MailApiError("OWNER_CHANGED", 409));
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  await act(() => hook.result.current.loadMore());
  expect(api.getResults).toHaveBeenCalledTimes(2);
  expect(api.getState).toHaveBeenCalledTimes(1);
  expect(hook.result.current.error).toBeTruthy();
});

test("saving a new interest generation invalidates an in-flight cursor page", async () => {
  const api = client(state({ scan: { status: "READY", scan_id: "scan-1" } }));
  api.getResults.mockResolvedValueOnce(
    page({ items: [result("old-1")], next_cursor: "cursor-1" }),
  );
  const delayed = deferred<MailResults>();
  api.getResults.mockReturnValueOnce(delayed.promise);
  api.save.mockResolvedValue(
    state({
      profile: { version: 2, tags: ["취업"] },
      scan: { status: "PENDING", scan_id: "scan-2" },
    }),
  );
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  let pending!: Promise<void>;
  await act(() => {
    pending = hook.result.current.loadMore();
  });
  await act(() =>
    hook.result.current.save({
      tags: ["취업"],
      description: "",
      expected_version: 1,
    }),
  );
  await act(async () => {
    delayed.resolve(page({ items: [result("old-2")] }));
    await pending;
  });

  expect(hook.result.current.state?.profile.version).toBe(2);
  expect(hook.result.current.results).toBeNull();
  expect(hook.result.current.loadingMore).toBe(false);
});

test.each([
  { scan_id: "another-scan" },
  { profile_version: 2 },
  { next_cursor: "cursor-1" },
])("rejects a mismatched or self-cycling cursor page: %p", async (mismatch) => {
  const api = client(state({ scan: { status: "READY", scan_id: "scan-1" } }));
  const initial = page({ items: [result("mail-1")], next_cursor: "cursor-1" });
  api.getResults.mockResolvedValueOnce(initial).mockResolvedValueOnce(
    page({
      ...mismatch,
      items: [result("wrong-page")],
    }),
  );
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  await act(() => hook.result.current.loadMore());

  expect(hook.result.current.results).toEqual(initial);
  expect(hook.result.current.error).toContain(
    "Mail results changed. Please refresh.",
  );
  expect(hook.result.current.loadingMore).toBe(false);
});

test("cursor pages deduplicate evidence, stop cycles, and reload the first page to recover", async () => {
  const api = client(state({ scan: { status: "READY", scan_id: "scan-1" } }));
  api.getResults
    .mockResolvedValueOnce(
      page({ items: [result("mail-1", "이전 제목")], next_cursor: "cursor-1" }),
    )
    .mockResolvedValueOnce(
      page({
        items: [result("mail-1", "갱신 제목"), result("mail-2")],
        next_cursor: "cursor-2",
      }),
    )
    .mockResolvedValueOnce(
      page({ items: [result("mail-3")], next_cursor: "cursor-1" }),
    );
  const { hook } = await mount(api);
  await waitFor(() => expect(hook.result.current.loading).toBe(false));
  await act(() => hook.result.current.loadMore());
  expect(hook.result.current.results?.items.map((item) => item.title)).toEqual([
    "갱신 제목",
    "mail-2",
  ]);
  await act(() => hook.result.current.loadMore());
  await act(() => hook.result.current.loadMore());

  expect(api.getResults).toHaveBeenCalledTimes(3);
  expect(api.getResults).toHaveBeenNthCalledWith(2, "cursor-1");
  expect(api.getResults).toHaveBeenNthCalledWith(3, "cursor-2");
  expect(
    hook.result.current.results?.items.map((item) => item.evidence_ref),
  ).toEqual(["mail-1", "mail-2", "mail-3"]);
  expect(hook.result.current.error).toContain(
    "Could not load more mail. Refresh the results.",
  );

  const refreshed = page({
    items: [result("mail-1", "이전 제목")],
    next_cursor: "cursor-1",
  });
  api.getResults.mockResolvedValueOnce(refreshed);
  await act(() => hook.result.current.refresh());
  expect(api.getResults).toHaveBeenLastCalledWith();
  expect(hook.result.current.results).toEqual(refreshed);
  expect(hook.result.current.error).toBeNull();

  api.getResults.mockResolvedValueOnce(page({ items: [result("mail-2")] }));
  await act(() => hook.result.current.loadMore());
  expect(api.getResults).toHaveBeenCalledTimes(5);
  expect(
    hook.result.current.results?.items.map((item) => item.evidence_ref),
  ).toEqual(["mail-1", "mail-2"]);
  expect(hook.result.current.error).toBeNull();
});
