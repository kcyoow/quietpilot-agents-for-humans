import {
  createProductApi,
  liveCandidateSchema,
  ProductApiError,
} from "@/src/api/productApi";

function response(body: object, status = 200) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      headers: { "content-type": "application/json" },
      status,
    }),
  );
}

const caseSummary = {
  case_id: "case-live",
  case_type: "DIRECT_DELEGATION",
  goal: "제출 준비",
  next_action: "계획 확인",
  priority: 50,
  providers: [],
  risk: "LOW",
  status: "PREPARING",
  summary: "요청을 정리하고 있어요.",
  updated_at: "2026-08-30T00:00:00Z",
  version: 1,
  why_now: "직접 요청",
};

function completedCalendarCase(timestamp: string) {
  const action = {
    action_id: "calendar-action",
    connector: "google",
    label: "일정 등록",
    parameters: {
      summary: "합성 일정",
      start: "2030-01-05T10:00:00+09:00",
      end: "2030-01-05T11:00:00+09:00",
      source_ref: "gmail:synthetic-source",
    },
    required_scopes: ["https://www.googleapis.com/auth/calendar.events.owned"],
    result_summary: "승인한 일정이 Calendar에 저장된 것을 확인했어요.",
    result_ref: "google-calendar:primary:qp" + "a".repeat(64),
    html_url: "https://calendar.google.com/calendar/event?eid=synthetic-event",
    verified: true,
    error_code: null,
    reversible: true,
    risk: "MEDIUM",
    status: "SUCCEEDED",
    target: "primary",
    verb: "calendar_event_create",
  };
  return {
    ...caseSummary,
    status: "COMPLETED",
    providers: ["google"],
    updated_at: timestamp,
    actions: [action],
    current_plan_hash: "b".repeat(64),
    current_plan_version: 1,
    evidence: [],
    messages: [
      {
        author: "QUIETPILOT",
        created_at: timestamp,
        message_id: "message-1",
        text: "계획을 확인해 주세요.",
      },
    ],
    timeline: [
      {
        body: "결과 확인 완료",
        event_id: "event-1",
        label: "일정 결과 확인",
        occurred_at: timestamp,
        state: "DONE",
      },
    ],
    plan: {
      actions: [action],
      available_grant_modes: ["ONCE"],
      expected_outcome: "일정 등록",
      hash: "b".repeat(64),
      reason: "요청한 일정",
      required_scopes: action.required_scopes,
      reversibility: "일정 삭제 가능",
      risk: "MEDIUM",
      version: 1,
    },
  };
}

test.each(["Z", "+00:00", "+09:00", "-04:00"])(
  "reads a verified completed Calendar response with %s service timestamps without rewriting values",
  async (offset) => {
    const timestamp = `2026-09-14T15:36:30.123456${offset}`;
    const detail = completedCalendarCase(timestamp);
    const requester = jest
      .fn()
      .mockImplementation(async () => response(detail));
    const api = createProductApi({
      accessToken: async () => "synthetic-token",
      baseUrl: "https://api.example.invalid",
      requester,
    });
    await expect(api.getCase("case-live")).resolves.toEqual(detail);
    expect(requester).toHaveBeenCalledTimes(1);
    expect(requester.mock.calls[0][1]?.method).toBeUndefined();
    const timestamps = liveCandidateSchema.pick({
      created_at: true,
      updated_at: true,
    });
    expect(
      timestamps.parse({ created_at: timestamp, updated_at: timestamp }),
    ).toEqual({ created_at: timestamp, updated_at: timestamp });
  },
);

test("reads completed Case summaries with the Worker's explicit UTC offset", async () => {
  const summary = {
    ...caseSummary,
    status: "COMPLETED",
    updated_at: "2026-09-14T15:36:30.123456+00:00",
  };
  const api = createProductApi({
    accessToken: async () => "synthetic-token",
    baseUrl: "https://api.example.invalid",
    requester: async () => response({ cases: [summary], next_cursor: null }),
  });
  await expect(api.listCases("history")).resolves.toEqual([summary]);
});

test.each([
  "2026-09-14T15:36:30",
  "2026-02-30T15:36:30+00:00",
  "2026-09-14T25:36:30+00:00",
  "2026-09-14T15:36:30+24:00",
  "not-a-date",
])(
  "rejects naive or invalid service timestamps consistently: %s",
  async (timestamp) => {
    const timestamps = liveCandidateSchema.pick({
      created_at: true,
      updated_at: true,
    });
    expect(
      timestamps.safeParse({ created_at: timestamp, updated_at: timestamp })
        .success,
    ).toBe(false);
    for (const field of ["updated_at", "occurred_at", "created_at"] as const) {
      const detail = completedCalendarCase("2026-09-14T15:36:30Z");
      if (field === "updated_at") detail.updated_at = timestamp;
      else if (field === "occurred_at")
        detail.timeline[0].occurred_at = timestamp;
      else detail.messages[0].created_at = timestamp;
      const api = createProductApi({
        accessToken: async () => "synthetic-token",
        baseUrl: "https://api.example.invalid",
        requester: async () => response(detail),
      });
      await expect(api.getCase("case-live")).rejects.toMatchObject({
        code: "INVALID_RESPONSE",
      });
    }
  },
);

test("reads runtime-validated live Case summaries", async () => {
  const api = createProductApi({
    accessToken: async () => "access-token",
    baseUrl: "https://api.example.com/",
    requester: async () =>
      response({ cases: [caseSummary], next_cursor: null }),
  });

  await expect(api.listCases("active")).resolves.toEqual([caseSummary]);
});

test("sends direct requests with auth and a bounded idempotency key", async () => {
  let request: { input: string; init?: RequestInit } = { input: "" };
  const api = createProductApi({
    accessToken: async () => "access-token",
    baseUrl: "https://api.example.com",
    requester: async (input, init) => {
      request = { input: String(input), init };
      return response({ case_id: "case-live", status: "PREPARING" }, 202);
    },
  });

  await api.createDirectCase("제출 준비를 정리해 줘");

  expect(request?.input).toBe("https://api.example.com/v1/cases");
  expect(request?.init?.method).toBe("POST");
  expect(new Headers(request?.init?.headers).get("authorization")).toBe(
    "Bearer access-token",
  );
  expect(new Headers(request?.init?.headers).get("Idempotency-Key")).toMatch(
    /^direct:/,
  );
  expect(JSON.parse(String(request?.init?.body))).toEqual({
    prompt: "제출 준비를 정리해 줘",
  });
});

test("rejects a live response that drifts from the public contract", async () => {
  const api = createProductApi({
    accessToken: async () => "access-token",
    baseUrl: "https://api.example.com",
    requester: async () => response({ cases: [{ case_id: "partial" }] }),
  });

  await expect(api.listCases("active")).rejects.toMatchObject<
    Partial<ProductApiError>
  >({ code: "INVALID_RESPONSE" });
});

test.each([
  [200, "INVALID_RESPONSE"],
  [401, "AUTH_REQUIRED"],
  [503, "SERVICE_UNAVAILABLE"],
] as const)(
  "keeps HTTP %s meaningful when its body is not JSON",
  async (status, code) => {
    const api = createProductApi({
      accessToken: async () => "access-token",
      baseUrl: "https://api.example.com",
      requester: async () =>
        new Response("<html>gateway error</html>", { status }),
    });
    await expect(api.listCases("active")).rejects.toMatchObject({
      code,
      status,
    });
  },
);

test.each([
  { error: "WORKSPACE_VERSION_CONFLICT" },
  { error: "WORKSPACE_VERSION_CONFLICT", request_id: "request-1" },
  { error: "NEW_SERVER_CODE", request_id: null },
])("retains the structured server error code %#", async (body) => {
  const api = createProductApi({
    accessToken: async () => "access-token",
    baseUrl: "https://api.example.com",
    requester: async () => response(body, 409),
  });
  await expect(api.listCases("active")).rejects.toMatchObject({
    code: body.error,
    status: 409,
  });
});

test.each([
  ["TypeError", "NETWORK_ERROR"],
  ["AbortError", "REQUEST_CANCELLED"],
  ["TimeoutError", "REQUEST_TIMEOUT"],
])("normalizes %s without retrying a mutation", async (name, code) => {
  const requester = jest
    .fn()
    .mockRejectedValue(
      Object.assign(new Error("raw transport detail"), { name }),
    );
  const api = createProductApi({
    accessToken: async () => "access-token",
    baseUrl: "https://api.example.com",
    requester,
  });
  const pending = api.createDirectCase("요청을 정리해 줘");
  await expect(pending).rejects.toMatchObject({ code, status: 0 });
  await expect(pending).rejects.not.toHaveProperty(
    "message",
    "raw transport detail",
  );
  expect(requester).toHaveBeenCalledTimes(1);
  expect(
    new Headers(requester.mock.calls[0][1].headers).get("Idempotency-Key"),
  ).toMatch(/^direct:/);
});

test("does not make a request or rewrite an authentication-provider failure", async () => {
  const failure = new ProductApiError(
    "로그인이 필요해요.",
    "AUTH_REQUIRED",
    401,
  );
  const requester = jest.fn();
  const api = createProductApi({
    accessToken: async () => {
      throw failure;
    },
    baseUrl: "https://api.example.com",
    requester,
  });
  await expect(api.listCases("active")).rejects.toBe(failure);
  expect(requester).not.toHaveBeenCalled();
});
