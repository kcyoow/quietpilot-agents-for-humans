import { fetchAuthSession } from "aws-amplify/auth";

import {
  createMailApi,
  MailApiError,
  type MailInterestState,
  type MailResults,
} from "@/src/mail/mailApi";

jest.mock("aws-amplify/auth", () => ({ fetchAuthSession: jest.fn() }));
const mockedSession = jest.mocked(fetchAuthSession);

function freeze<T>(value: T): T {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) freeze(child);
    Object.freeze(value);
  }
  return value;
}

const state: MailInterestState = freeze({
  profile: {
    tags: ["학교"],
    description: "학교 소식, 광고는 제외해 주세요.",
    version: 3,
    updated_at: "2026-09-09T00:00:00Z",
  },
  recommendations: {
    status: "READY",
    request_id: "recommendation-1",
    tags: [{ tag: "학교", evidence_refs: ["subject-ref-1"] }],
    title_count: 1,
    generated_at: "2026-09-09T00:00:00Z",
    error_code: null,
  },
  scan: {
    status: "READY",
    scan_id: "scan-3",
    profile_version: 3,
    processed_count: 1,
    matched_count: 1,
    completed_at: "2026-09-09T00:00:00Z",
    error_code: null,
  },
});

const page: MailResults = freeze({
  profile_version: 3,
  scan_id: "scan-3",
  status: "READY",
  items: [
    {
      evidence_ref: "message-ref-1",
      title: "도서관 이용 안내",
      summary: "열람 공간을 소개합니다.",
      reason: "학교 생활과 관련된 정보입니다.",
      sender_domain: "example.invalid",
      received_at: null,
      matched_tags: ["학교"],
    },
  ],
  next_cursor: null,
});

function response(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function client(requester: typeof fetch) {
  return createMailApi({
    ownerId: "owner-a",
    accessToken: async () => "injected-access-token",
    baseUrl: " https://api.example.invalid/// ",
    requester,
  });
}

beforeEach(() => mockedSession.mockReset());

test("reads validated state and results with JWT auth and safely encoded cursors", async () => {
  const requester = jest
    .fn()
    .mockResolvedValueOnce(response(state))
    .mockResolvedValueOnce(response(page));
  const api = client(requester);
  await expect(api.getState()).resolves.toEqual(state);
  await expect(api.getResults("owner/gen+page=&?")).resolves.toEqual(page);

  expect(requester.mock.calls[0][0]).toBe(
    "https://api.example.invalid/v1/mail/interests",
  );
  expect(requester.mock.calls[1][0]).toBe(
    "https://api.example.invalid/v1/mail/results?cursor=owner%2Fgen%2Bpage%3D%26%3F",
  );
  expect(
    new Headers(requester.mock.calls[0][1].headers).get("authorization"),
  ).toBe("Bearer injected-access-token");
  expect(new Headers(requester.mock.calls[0][1].headers).get("accept")).toBe(
    "application/json",
  );
  expect(requester.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
});

test.each([undefined, "HIGH", "NORMAL", "LOW"] as const)(
  "accepts optional result importance %s while legacy absence means normal",
  async (importance) => {
    const item = { ...page.items[0], ...(importance ? { importance } : {}) };
    const received = await client(
      jest.fn().mockResolvedValue(response({ ...page, items: [item] })),
    ).getResults();
    expect(received.items[0].importance ?? "NORMAL").toBe(
      importance ?? "NORMAL",
    );
    expect(received.items[0]).toEqual(item);
  },
);

test.each(["URGENT", "high", null])(
  "rejects invalid result importance %s",
  async (importance) => {
    const requester = jest
      .fn()
      .mockResolvedValue(
        response({ ...page, items: [{ ...page.items[0], importance }] }),
      );
    await expect(client(requester).getResults()).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 200,
    });
  },
);

test("saves the exact description and baseline version and uses separate POST endpoints", async () => {
  const requester = jest.fn().mockImplementation(async () => response(state));
  const api = client(requester);
  const input = {
    tags: [],
    description: "  학교 소식\n광고는 제외  ",
    expected_version: 3,
  };
  await api.save(input);
  await api.recommend();
  await api.scan();

  expect(requester.mock.calls[0][1].method).toBe("PUT");
  expect(JSON.parse(requester.mock.calls[0][1].body)).toEqual(input);
  expect(
    new Headers(requester.mock.calls[0][1].headers).get("content-type"),
  ).toBe("application/json");
  expect(requester.mock.calls[1][0]).toBe(
    "https://api.example.invalid/v1/mail/recommendations",
  );
  expect(requester.mock.calls[1][1].method).toBe("POST");
  expect(requester.mock.calls[2][0]).toBe(
    "https://api.example.invalid/v1/mail/scan",
  );
  expect(requester.mock.calls[2][1].method).toBe("POST");
});

test("Unicode limits use code points and nullable received timestamps remain null", async () => {
  const unicodeState = freeze({
    ...state,
    profile: {
      ...state.profile,
      tags: ["🤖".repeat(32)],
      description: "🤖".repeat(1000),
    },
  });
  const requester = jest
    .fn()
    .mockResolvedValueOnce(response(unicodeState))
    .mockResolvedValueOnce(response(page));
  const api = client(requester);

  await expect(api.getState()).resolves.toMatchObject({
    profile: unicodeState.profile,
  });
  await expect(api.getResults()).resolves.toMatchObject({
    items: [{ received_at: null }],
  });
});

test.each([
  [
    "missing subject evidence",
    {
      ...state,
      recommendations: {
        ...state.recommendations,
        tags: [{ tag: "학교", evidence_refs: [] }],
      },
    },
  ],
  [
    "too many sampled titles",
    {
      ...state,
      recommendations: { ...state.recommendations, title_count: 33 },
    },
  ],
  [
    "too many profile tags",
    {
      ...state,
      profile: {
        ...state.profile,
        tags: Array.from({ length: 9 }, (_, i) => "태그" + i),
      },
    },
  ],
  [
    "overlong description",
    { ...state, profile: { ...state.profile, description: "가".repeat(1001) } },
  ],
  [
    "missing explicit nullable field",
    { ...state, profile: { tags: ["학교"], description: "", version: 1 } },
  ],
])("rejects a state response with %s", async (_, body) => {
  const api = client(jest.fn().mockResolvedValue(response(body)));
  await expect(api.getState()).rejects.toMatchObject({
    code: "INVALID_RESPONSE",
    status: 200,
  });
});

test.each([" ", "#학교", "학교 공지", "C#"])(
  "rejects noncanonical internal tag %p instead of exposing it as a saved interest",
  async (tag) => {
    const api = client(
      jest.fn().mockResolvedValue(
        response({
          ...state,
          profile: { ...state.profile, tags: [tag] },
        }),
      ),
    );
    await expect(api.getState()).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
      status: 200,
    });
  },
);

test("rejects an invalid result timestamp rather than inventing a date", async () => {
  const api = client(
    jest.fn().mockResolvedValue(
      response({
        ...page,
        items: [{ ...page.items[0], received_at: "yesterday" }],
      }),
    ),
  );
  await expect(api.getResults()).rejects.toMatchObject({
    code: "INVALID_RESPONSE",
    status: 200,
  });
});

test.each([
  {
    status: 401,
    body: { error: "AUTH_REQUIRED" },
    code: "AUTH_REQUIRED",
    message: "Your session expired. Please sign in again.",
  },
  {
    status: 409,
    body: { error: "MAIL_PROFILE_VERSION_CONFLICT" },
    code: "MAIL_PROFILE_VERSION_CONFLICT",
    message: "Your interests changed. Load the latest settings.",
  },
  {
    status: 503,
    body: { error: "MAIL_UNAVAILABLE" },
    code: "MAIL_UNAVAILABLE",
    message: "Could not check mail. Try again shortly.",
  },
])(
  "preserves structured HTTP $status errors without retrying mutations",
  async ({ status, body, code, message }) => {
    const requester = jest.fn().mockResolvedValue(response(body, status));
    const api = client(requester);
    const pending = api.save({
      tags: ["학교"],
      description: "",
      expected_version: 3,
    });
    await expect(pending).rejects.toMatchObject({ code, status });
    await expect(pending).rejects.toThrow(message);
    expect(requester).toHaveBeenCalledTimes(1);
  },
);

test.each([200, 401, 503])(
  "keeps HTTP %s meaningful for non-JSON bodies",
  async (status) => {
    const api = client(
      jest
        .fn()
        .mockResolvedValue(
          new Response("<html>unavailable</html>", { status }),
        ),
    );
    await expect(api.getState()).rejects.toMatchObject({
      code: status === 200 ? "INVALID_RESPONSE" : "HTTP_ERROR",
      status,
    });
  },
);

test("normalizes network failures without retrying a mutation", async () => {
  const requester = jest
    .fn()
    .mockRejectedValue(new TypeError("raw network details"));
  const pending = client(requester).recommend();
  await expect(pending).rejects.toMatchObject({
    code: "NETWORK_ERROR",
    status: 0,
  });
  await expect(pending).rejects.not.toHaveProperty(
    "message",
    "raw network details",
  );
  expect(requester).toHaveBeenCalledTimes(1);
});

test.each(["missing", "changed"] as const)(
  "default authentication blocks a %s token before fetching",
  async (kind) => {
    mockedSession.mockResolvedValue(
      kind === "missing"
        ? ({} as never)
        : ({
            tokens: {
              accessToken: {
                payload: { sub: "owner-b" },
                toString: () => "other-owner-token",
              },
            },
          } as never),
    );
    const requester = jest.fn();
    const api = createMailApi({
      ownerId: "owner-a",
      baseUrl: "https://api.example.invalid",
      requester,
    });

    await expect(api.getState()).rejects.toMatchObject({
      code: kind === "missing" ? "AUTH_REQUIRED" : "OWNER_CHANGED",
      status: kind === "missing" ? 401 : 409,
    });
    expect(requester).not.toHaveBeenCalled();
  },
);

test("the default token path sends only the matching owner's access token", async () => {
  mockedSession.mockResolvedValue({
    tokens: {
      accessToken: {
        payload: { sub: "owner-a" },
        toString: () => "matching-owner-token",
      },
    },
  } as never);
  const requester = jest.fn().mockResolvedValue(response(state));
  const api = createMailApi({
    ownerId: "owner-a",
    baseUrl: "https://api.example.invalid",
    requester,
  });

  await expect(api.getState()).resolves.toEqual(state);
  expect(
    new Headers(requester.mock.calls[0][1].headers).get("authorization"),
  ).toBe("Bearer matching-owner-token");
});

test("an unconfigured API does not read credentials or make a request", async () => {
  const accessToken = jest.fn();
  const requester = jest.fn();
  const api = createMailApi({ baseUrl: "", accessToken, requester });

  expect(api.configured).toBe(false);
  await expect(api.getState()).rejects.toBeInstanceOf(MailApiError);
  expect(accessToken).not.toHaveBeenCalled();
  expect(requester).not.toHaveBeenCalled();
});
