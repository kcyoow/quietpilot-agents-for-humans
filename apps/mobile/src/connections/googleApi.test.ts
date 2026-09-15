import {
  completeGoogleOAuthOnce,
  createGoogleConnectionsApi,
  GoogleConnectionApiError,
} from "@/src/connections/googleApi";
import { toGoogleConnection } from "@/src/connections/googleConnectionView";
import { googleDiscoveryPresentation } from "@/src/connections/googleDiscoveryPresentation";

function response(body: unknown, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
}

const disconnected = {
  discovery_revision: 0,
  provider: "google" as const,
  label: "Google" as const,
  status: "DISCONNECTED" as const,
  granted_scopes: [],
  lookback_days: 7,
  scan_progress: 0,
  error_code: null,
  last_checked_at: null,
  last_sync_mode: null,
  next_renewal_due_at: null,
  watch_expires_at: null,
  watch_renewed_at: null,
  version: 1,
};

test("uses the Cognito access token and never sends identity in the body", async () => {
  const calls: [RequestInfo | URL, RequestInit | undefined][] = [];
  const requester = (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push([input, init]);
    return response({ connections: [disconnected] });
  };
  const api = createGoogleConnectionsApi({
    accessToken: async () => "cognito-access-token",
    baseUrl: "https://api.example.com/",
    requester,
  });

  await api.get();

  expect(calls[0]?.[0]).toBe("https://api.example.com/v1/connections");
  expect(calls[0]?.[1]?.headers).toEqual(
    expect.objectContaining({ authorization: "Bearer cognito-access-token" }),
  );
  expect(calls[0]?.[1]?.body).toBeUndefined();
});

test("complete sends only the one-time app return code", async () => {
  const calls: [RequestInfo | URL, RequestInit | undefined][] = [];
  const requester = (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push([input, init]);
    return response({ connection: disconnected }, 202);
  };
  const api = createGoogleConnectionsApi({
    accessToken: async () => "cognito-access-token",
    baseUrl: "https://api.example.com",
    requester,
  });

  await api.complete("one-time-code");

  const body = JSON.parse(String(calls[0]?.[1]?.body));
  expect(body).toEqual({ code: "one-time-code" });
  expect(JSON.stringify(body)).not.toContain("access-token");
});

test("rescan sends only the bounded lookback and returns the checking state", async () => {
  const calls: [RequestInfo | URL, RequestInit | undefined][] = [];
  const scanning = {
    ...disconnected,
    discovery_revision: 1,
    scan_progress: 5,
    status: "SCANNING" as const,
  };
  const requester = (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push([input, init]);
    return response({ connection: scanning }, 202);
  };
  const api = createGoogleConnectionsApi({
    accessToken: async () => "cognito-access-token",
    baseUrl: "https://api.example.com",
    requester,
  });

  await expect(api.scan(7)).resolves.toEqual(scanning);

  expect(calls[0]?.[0]).toBe(
    "https://api.example.com/v1/connections/google/scan",
  );
  expect(JSON.parse(String(calls[0]?.[1]?.body))).toEqual({
    lookback_days: 7,
  });
});

test("reads live suggestion and group summaries with the Cognito token", async () => {
  const calls: [RequestInfo | URL, RequestInit | undefined][] = [];
  const requester = (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push([input, init]);
    const path = String(input);
    return path.endsWith("/v1/suggestions")
      ? response({ suggestions: [], next_cursor: null })
      : response({ groups: [], next_cursor: null });
  };
  const api = createGoogleConnectionsApi({
    accessToken: async () => "cognito-access-token",
    baseUrl: "https://api.example.com",
    requester,
  });

  await api.listSuggestions();
  await api.listSuggestionGroups();

  expect(calls.map(([input]) => String(input))).toEqual([
    "https://api.example.com/v1/suggestions",
    "https://api.example.com/v1/suggestion-groups",
  ]);
  for (const [, init] of calls) {
    expect(init?.headers).toEqual(
      expect.objectContaining({ authorization: "Bearer cognito-access-token" }),
    );
    expect(init?.body).toBeUndefined();
  }
});

test("single-flights concurrent completion for the same return code", async () => {
  const complete = jest.fn().mockResolvedValue(disconnected);
  const api = { complete };
  const code = "single-flight-return-code";

  const first = completeGoogleOAuthOnce(api, code);
  const second = completeGoogleOAuthOnce(api, code);

  expect(first).toBe(second);
  await expect(first).resolves.toEqual(disconnected);
  expect(complete).toHaveBeenCalledTimes(1);
  expect(complete).toHaveBeenCalledWith(code);
});

test.each([
  [200, "INVALID_RESPONSE"],
  [401, "AUTH_REQUIRED"],
  [502, "CONNECTION_SERVICE_UNAVAILABLE"],
] as const)(
  "keeps HTTP %s meaningful when its body is not JSON",
  async (status, code) => {
    const api = createGoogleConnectionsApi({
      accessToken: async () => "access-token",
      baseUrl: "https://api.example.com",
      requester: async () =>
        new Response("<html>gateway error</html>", { status }),
    });
    await expect(api.get()).rejects.toMatchObject({ code, status });
  },
);

test("retains a structured server code and HTTP status", async () => {
  const api = createGoogleConnectionsApi({
    accessToken: async () => "access-token",
    baseUrl: "https://api.example.com",
    requester: async () => response({ error: "CONNECTION_FLOW_CONFLICT" }, 409),
  });
  await expect(api.complete("return-code")).rejects.toMatchObject({
    code: "CONNECTION_FLOW_CONFLICT",
    status: 409,
  });
});

test.each([
  ["TypeError", "NETWORK_ERROR"],
  ["AbortError", "REQUEST_CANCELLED"],
  ["TimeoutError", "REQUEST_TIMEOUT"],
])("normalizes %s without retrying completion", async (name, code) => {
  const requester = jest
    .fn()
    .mockRejectedValue(
      Object.assign(new Error("raw transport detail"), { name }),
    );
  const api = createGoogleConnectionsApi({
    accessToken: async () => "access-token",
    baseUrl: "https://api.example.com",
    requester,
  });
  const pending = api.complete("return-code");
  await expect(pending).rejects.toMatchObject({ code, status: 0 });
  await expect(pending).rejects.not.toHaveProperty(
    "message",
    "raw transport detail",
  );
  expect(requester).toHaveBeenCalledTimes(1);
});

test("does not make a request or rewrite an authentication-provider failure", async () => {
  const failure = new GoogleConnectionApiError("로그인이 필요해요.");
  const requester = jest.fn();
  const api = createGoogleConnectionsApi({
    accessToken: async () => {
      throw failure;
    },
    baseUrl: "https://api.example.com",
    requester,
  });
  await expect(api.get()).rejects.toBe(failure);
  expect(requester).not.toHaveBeenCalled();
});

test("allows only an explicit retry after a failed single-flight completion", async () => {
  const failure = new GoogleConnectionApiError("연결이 중단됐어요.");
  const complete = jest
    .fn()
    .mockRejectedValueOnce(failure)
    .mockResolvedValue(disconnected);
  const api = { complete };
  const code = "explicit-retry-return-code";
  const first = completeGoogleOAuthOnce(api, code);
  expect(completeGoogleOAuthOnce(api, code)).toBe(first);
  await expect(first).rejects.toBe(failure);
  expect(complete).toHaveBeenCalledTimes(1);
  await expect(completeGoogleOAuthOnce(api, code)).resolves.toEqual(
    disconnected,
  );
  expect(complete).toHaveBeenCalledTimes(2);
});

function apiWithResponse(body: unknown) {
  return createGoogleConnectionsApi({
    accessToken: async () => "access-token",
    baseUrl: "https://api.example.com",
    requester: async () => response(body),
  });
}

test.each([
  null,
  {},
  { connections: null },
  { connections: [null] },
  { connections: [{ provider: "google", status: "CONNECTED" }] },
  { connections: [{ ...disconnected, status: "READY" }] },
  { connections: [{ ...disconnected, scan_progress: "100" }] },
  { connections: [{ ...disconnected, scan_progress: 101 }] },
  { connections: [{ ...disconnected, discovery_revision: "1" }] },
  { connections: [{ ...disconnected, watch_expires_at: "not-a-time" }] },
])(
  "rejects malformed successful connection lists %# before presentation",
  async (body) => {
    await expect(apiWithResponse(body).get()).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 200,
    });
  },
);

test.each(["complete", "disconnect", "scan"] as const)(
  "%s rejects a missing connection envelope without returning undefined",
  async (operation) => {
    const api = apiWithResponse({ connection: null });
    const pending =
      operation === "complete" ? api.complete("return-code") : api[operation]();
    await expect(pending).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 200,
    });
  },
);

test("keeps the old discovery revision absence visibly incompatible", async () => {
  const { discovery_revision, ...legacy } = {
    ...disconnected,
    status: "CONNECTED" as const,
    scan_progress: 100,
    last_checked_at: "2026-09-06T00:00:00Z",
  };
  void discovery_revision;
  const record = await apiWithResponse({ connections: [legacy] }).get();
  expect(record).toEqual(legacy);
  expect(record).not.toHaveProperty("discovery_revision");
  const presentation = googleDiscoveryPresentation({
    candidateCount: 0,
    connection: toGoogleConnection(record!),
    connectionChecking: false,
    connectionError: null,
    workspaceError: null,
    workspaceStatus: "ready",
  });
  expect(presentation.kind).toBe("INCOMPATIBLE_SERVER");
});

test("accepts the server's version-zero disconnected record and skips SmartThings", async () => {
  const connection = { ...disconnected, version: 0 };
  const api = apiWithResponse({
    connections: [
      { provider: "smartthings", label: "SmartThings" },
      connection,
    ],
  });
  await expect(api.get()).resolves.toEqual(connection);
  await expect(apiWithResponse({ connections: [] }).get()).resolves.toBeNull();
});

test.each([
  {
    authorization_url: "not a url",
    connection: { ...disconnected, status: "CONNECTING" },
  },
  {
    authorization_url: "http://accounts.example.com/authorize",
    connection: { ...disconnected, status: "CONNECTING" },
  },
  {
    authorization_url: 123,
    connection: { ...disconnected, status: "CONNECTING" },
  },
  { authorization_url: "https://accounts.example.com/authorize" },
  { connection: { ...disconnected, status: "CONNECTING" } },
  {
    connection: {
      ...disconnected,
      status: "CONNECTING",
      granted_scopes: ["https://www.googleapis.com/auth/calendar.readonly"],
    },
  },
])("rejects an unusable authorize response %#", async (body) => {
  await expect(apiWithResponse(body).authorize()).rejects.toMatchObject({
    code: "INVALID_RESPONSE",
    status: 200,
  });
});

test("accepts both actual server authorization branches", async () => {
  const browserFlow = {
    authorization_url: "https://accounts.example.com/authorize?state=opaque",
    connection: { ...disconnected, status: "CONNECTING" },
  };
  const tokenAlreadyAvailable = {
    connection: {
      ...disconnected,
      status: "CONNECTING",
      granted_scopes: ["https://www.googleapis.com/auth/gmail.readonly"],
    },
  };
  await expect(apiWithResponse(browserFlow).authorize()).resolves.toEqual(
    browserFlow,
  );
  await expect(
    apiWithResponse(tokenAlreadyAvailable).authorize(),
  ).resolves.toEqual(tokenAlreadyAvailable);
  await expect(
    apiWithResponse({
      connection: { ...disconnected, status: "SCANNING", scan_progress: 5 },
    }).authorize(),
  ).resolves.toMatchObject({ connection: { status: "SCANNING" } });
});

test.each([
  { suggestions: null, next_cursor: null },
  {
    suggestions: [{ candidate_id: "legacy-without-actions" }],
    next_cursor: null,
  },
])(
  "rejects malformed or legacy suggestion payloads %# with the shared contract",
  async (body) => {
    await expect(apiWithResponse(body).listSuggestions()).rejects.toMatchObject(
      {
        code: "INVALID_RESPONSE",
        status: 200,
      },
    );
  },
);

test("rejects a malformed group rather than returning an undefined label", async () => {
  await expect(
    apiWithResponse({
      groups: [{ group_id: "partial" }],
      next_cursor: null,
    }).listSuggestionGroups(),
  ).rejects.toMatchObject({
    code: "INVALID_RESPONSE",
    status: 200,
  });
});

test("coalesces the same account's callback across screen/API instances without changing the return code", async () => {
  let resolve!: (value: typeof disconnected) => void;
  const pending = new Promise<typeof disconnected>((accept) => {
    resolve = accept;
  });
  const firstApi = { complete: jest.fn().mockReturnValue(pending) };
  const secondApi = { complete: jest.fn().mockResolvedValue(disconnected) };
  const code = "cross-screen-owner-callback";
  const first = completeGoogleOAuthOnce(firstApi, code, "callback-user-one");
  const second = completeGoogleOAuthOnce(secondApi, code, "callback-user-one");
  expect(second).toBe(first);
  expect(firstApi.complete).toHaveBeenCalledWith(code);
  expect(secondApi.complete).not.toHaveBeenCalled();
  resolve(disconnected);
  await expect(second).resolves.toEqual(disconnected);
});

test("never reuses another account's completed callback promise", async () => {
  const firstApi = {
    complete: jest.fn().mockResolvedValue({ ...disconnected, version: 11 }),
  };
  const secondApi = {
    complete: jest.fn().mockResolvedValue({ ...disconnected, version: 22 }),
  };
  const code = "account-separated-callback";
  const first = completeGoogleOAuthOnce(firstApi, code, "callback-user-a");
  await first;
  const second = completeGoogleOAuthOnce(secondApi, code, "callback-user-b");
  expect(second).not.toBe(first);
  await expect(second).resolves.toMatchObject({ version: 22 });
  expect(secondApi.complete).toHaveBeenCalledWith(code);
});

test("ownerless legacy callers share only the same completer object", async () => {
  const firstApi = { complete: jest.fn().mockResolvedValue(disconnected) };
  const secondApi = {
    complete: jest.fn().mockResolvedValue({ ...disconnected, version: 2 }),
  };
  const code = "legacy-completer-local-callback";
  const first = completeGoogleOAuthOnce(firstApi, code);
  expect(completeGoogleOAuthOnce(firstApi, code)).toBe(first);
  const second = completeGoogleOAuthOnce(secondApi, code);
  expect(second).not.toBe(first);
  await expect(second).resolves.toMatchObject({ version: 2 });
  expect(firstApi.complete).toHaveBeenCalledTimes(1);
  expect(secondApi.complete).toHaveBeenCalledTimes(1);
});

test("a failed owner callback does not evict another owner's pending completion", async () => {
  let resolve!: (value: typeof disconnected) => void;
  const otherPending = new Promise<typeof disconnected>((accept) => {
    resolve = accept;
  });
  const failedApi = {
    complete: jest
      .fn()
      .mockRejectedValueOnce(new Error("completion failed"))
      .mockResolvedValue(disconnected),
  };
  const otherApi = { complete: jest.fn().mockReturnValue(otherPending) };
  const code = "failed-owner-cache-isolation";
  const failed = completeGoogleOAuthOnce(failedApi, code, "failing-owner");
  const other = completeGoogleOAuthOnce(otherApi, code, "pending-owner");
  await expect(failed).rejects.toThrow("completion failed");
  expect(completeGoogleOAuthOnce(otherApi, code, "pending-owner")).toBe(other);
  await expect(
    completeGoogleOAuthOnce(failedApi, code, "failing-owner"),
  ).resolves.toEqual(disconnected);
  resolve(disconnected);
  await expect(other).resolves.toEqual(disconnected);
  expect(otherApi.complete).toHaveBeenCalledTimes(1);
});
