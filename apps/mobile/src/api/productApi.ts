import { z } from "zod";

const serviceTimestampSchema = z.iso.datetime({ offset: true });
const riskSchema = z.enum(["LOW", "MEDIUM", "HIGH"]);
const caseTypeSchema = z.enum([
  "CONNECTED_SIGNAL",
  "DIRECT_DELEGATION",
  "ROUTINE_DISCOVERY",
  "EXCEPTION_APPROVAL",
]);
const caseStatusSchema = z.enum([
  "PREPARING",
  "DECISION_REQUIRED",
  "APPROVED",
  "QUEUED",
  "RUNNING",
  "VERIFYING",
  "COMPLETED",
  "FAILED",
  "PAUSED",
  "PERMISSION_REVOKED",
  "STOPPED",
]);
const grantModeSchema = z.enum(["ONCE", "CONDITIONAL", "STANDING"]);

const candidateActionSchema = z.object({
  connector: z.string().min(1),
  parameters: z.record(z.string(), z.unknown()),
  required_scopes: z.array(z.string()),
  reversible: z.boolean(),
  risk: riskSchema,
  target_resource: z.string().min(1),
  verb: z.string().min(1),
  verification_method: z.string().min(1),
});

export const liveCandidateSchema = z.object({
  candidate_id: z.string().min(1),
  confidence: z.number().min(0).max(1),
  mail_profile_version: z.number().int().min(1).optional(),
  mail_scan_id: z.string().min(1).max(256).optional(),
  created_at: serviceTimestampSchema,
  evidence_refs: z.array(z.string()),
  outcome: z.string().min(1),
  opportunity_type: z.enum(["APPOINTMENT", "DEADLINE", "FOLLOW_UP"]),
  primary_group_id: z.string().min(1),
  proposed_actions: z.array(candidateActionSchema).min(1).max(3),
  provider: z.literal("google"),
  risk: riskSchema,
  source_type: z.literal("CONNECTED_SIGNAL"),
  status: z.literal("VISIBLE"),
  summary: z.string().min(1),
  tags: z.array(z.string()),
  updated_at: serviceTimestampSchema,
  version: z.number().int().min(1),
  why_now: z.string().min(1),
});

export const liveSuggestionGroupSchema = z.object({
  candidate_count: z.number().int().nonnegative(),
  group_id: z.string().min(1),
  highest_risk: riskSchema,
  label: z.string().min(1),
  reason: z.string().min(1),
});

const liveCaseSummarySchema = z.object({
  case_id: z.string().min(1),
  case_type: caseTypeSchema,
  goal: z.string().min(1),
  next_action: z.string().nullable(),
  priority: z.number().int(),
  providers: z.array(z.enum(["google", "smartthings", "sms"])),
  risk: riskSchema,
  status: caseStatusSchema,
  summary: z.string().min(1),
  updated_at: serviceTimestampSchema,
  version: z.number().int().min(1),
  why_now: z.string().nullable(),
});

const liveEvidenceSchema = z.object({
  detail: z.string(),
  evidence_id: z.string().min(1),
  evidence_ref: z.string().min(1),
  label: z.string().min(1),
  provider: z.enum(["google", "smartthings", "sms", "direct"]),
  revision: z.number().int().min(1),
});

const liveActionSchema = z.object({
  action_id: z.string().min(1),
  connector: z.string().min(1),
  label: z.string().min(1),
  parameters: z.record(z.string(), z.unknown()),
  required_scopes: z.array(z.string()),
  result_summary: z.string().nullable(),
  result_ref: z.string().nullable().optional(),
  html_url: z.string().nullable().optional(),
  verified: z.boolean().optional(),
  error_code: z.string().nullable().optional(),
  reversible: z.boolean(),
  risk: riskSchema,
  status: z.enum([
    "PROPOSED",
    "APPROVED",
    "QUEUED",
    "RUNNING",
    "VERIFYING",
    "SUCCEEDED",
    "BLOCKED",
    "CANCELLED",
    "FAILED",
  ]),
  target: z.string().min(1),
  verb: z.string().min(1),
});

const livePlanSchema = z.object({
  actions: z.array(liveActionSchema),
  available_grant_modes: z.array(grantModeSchema),
  local_preparation_status: z
    .enum(["READY", "NO_ACTION", "NEEDS_INPUT"])
    .optional(),
  expected_outcome: z.string().min(1),
  hash: z.string().regex(/^[0-9a-f]{64}$/),
  reason: z.string().min(1),
  required_scopes: z.array(z.string()),
  reversibility: z.string().min(1),
  risk: riskSchema,
  version: z.number().int().min(1),
});

const liveTimelineSchema = z.object({
  body: z.string(),
  event_id: z.string().min(1),
  label: z.string().min(1),
  occurred_at: serviceTimestampSchema,
  state: z.enum(["DONE", "CURRENT", "PENDING", "FAILED"]),
});

const liveMessageSchema = z.object({
  author: z.enum(["USER", "QUIETPILOT"]),
  created_at: serviceTimestampSchema,
  message_id: z.string().min(1),
  text: z.string(),
});

export const liveCaseDetailSchema = liveCaseSummarySchema.extend({
  actions: z.array(liveActionSchema),
  current_plan_hash: z
    .string()
    .regex(/^[0-9a-f]{64}$/)
    .nullable(),
  current_plan_version: z.number().int().min(1).nullable(),
  evidence: z.array(liveEvidenceSchema),
  messages: z.array(liveMessageSchema),
  plan: livePlanSchema.nullable(),
  timeline: z.array(liveTimelineSchema),
});

const listSuggestionsSchema = z.object({
  next_cursor: z.string().nullable(),
  suggestions: z.array(liveCandidateSchema),
});
const listSuggestionGroupsSchema = z.object({
  groups: z.array(liveSuggestionGroupSchema),
  next_cursor: z.string().nullable(),
});
const listCasesSchema = z.object({
  cases: z.array(liveCaseSummarySchema),
  next_cursor: z.string().nullable(),
});
const caseReferenceSchema = z.object({ case_id: z.string().min(1) });
const createdCaseSchema = caseReferenceSchema.extend({
  status: z.literal("PREPARING"),
});
const messageAcceptedSchema = z.object({
  case: liveCaseSummarySchema,
  message_id: z.string().min(1),
  planning_job_id: z.string().min(1),
});
const suggestionFeedbackSchema = z.object({
  affected_candidate_ids: z.array(z.string()),
  candidate: z.unknown().nullable(),
  mode: z.enum(["HIDE_ONCE", "REDUCE_SIMILAR", "ADJUST_SCOPE"]),
  preview: z.string().optional(),
  rule_id: z.string().nullable(),
});
const suppressionUndoSchema = z.object({
  restored_candidate_ids: z.array(z.string()),
});
const errorSchema = z.object({ error: z.string().min(1) });

export const liveRoutineSchema = z.object({
  routine_id: z.string().min(1),
  version: z.number().int().min(1),
  status: z.enum(["PROPOSED", "ACTIVE", "PAUSED"]),
  effective_status: z.enum(["PROPOSED", "ACTIVE", "PAUSED", "REVIEW_REQUIRED"]),
  source_case_id: z.string().min(1),
  title: z.string().min(1),
  description: z.string().min(1),
  sender_domain: z.string().min(1),
  opportunity_type: z.enum(["DEADLINE", "APPOINTMENT"]),
  mode: z.literal("PREPARE_ONLY"),
  activated_at: serviceTimestampSchema.nullable(),
  created_at: serviceTimestampSchema,
  updated_at: serviceTimestampSchema,
  review_reason: z.string().nullable(),
});
const routineResponseSchema = z.object({ routine: liveRoutineSchema });
const pushDeviceSchema = z.object({
  device_id: z.string().min(8).max(128),
  registered: z.boolean(),
  app_version: z.string().optional(),
  platform: z.literal("android").optional(),
  updated_at: serviceTimestampSchema.optional(),
});
const routineListSchema = z.object({
  routines: z.array(liveRoutineSchema).max(32),
});

export type LiveCandidate = z.infer<typeof liveCandidateSchema>;
export type LiveSuggestionGroup = z.infer<typeof liveSuggestionGroupSchema>;
export type LiveCaseSummary = z.infer<typeof liveCaseSummarySchema>;
export type LiveCaseDetail = z.infer<typeof liveCaseDetailSchema>;
export type LiveRoutine = z.infer<typeof liveRoutineSchema>;

type Requester = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;
type AccessTokenProvider = () => Promise<string>;

export class ProductApiError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ProductApiError";
  }
}

let requestSequence = 0;

export function createProductApi({
  accessToken = currentAccessToken,
  baseUrl = process.env.EXPO_PUBLIC_CONTROL_API_URL,
  requester = fetch,
}: {
  accessToken?: AccessTokenProvider;
  baseUrl?: string;
  requester?: Requester;
} = {}) {
  const normalizedBaseUrl = baseUrl?.trim().replace(/\/+$/, "") ?? "";

  async function request<T>(
    path: string,
    schema: z.ZodType<T>,
    init?: RequestInit,
  ): Promise<T> {
    if (!normalizedBaseUrl) {
      throw new ProductApiError(
        "QuietPilot is not connected to the server.",
        "API_NOT_CONFIGURED",
        0,
      );
    }
    const token = await accessToken();
    let response: Response;
    try {
      response = await requester(`${normalizedBaseUrl}${path}`, {
        ...init,
        headers: {
          accept: "application/json",
          authorization: `Bearer ${token}`,
          ...(init?.body ? { "content-type": "application/json" } : {}),
          ...init?.headers,
        },
      });
    } catch (caught) {
      if (caught instanceof ProductApiError) throw caught;
      const code = transportErrorCode(caught);
      throw new ProductApiError(errorMessage(code), code, 0);
    }
    let value: unknown;
    try {
      value =
        response.status === 204
          ? undefined
          : ((await response.json()) as unknown);
    } catch (caught) {
      const code = transportErrorCode(caught);
      if (code !== "NETWORK_ERROR") {
        throw new ProductApiError(errorMessage(code), code, response.status);
      }
      // HTTP failures can have HTML/empty bodies; retain their status below.
      // A successful response still has to meet this endpoint's JSON contract.
    }
    if (!response.ok) {
      const parsed = errorSchema.safeParse(value);
      const code = parsed.success
        ? parsed.data.error
        : httpErrorCode(response.status);
      throw new ProductApiError(errorMessage(code), code, response.status);
    }
    const parsed = schema.safeParse(value);
    if (!parsed.success) {
      throw new ProductApiError(
        "This response could not be displayed safely. Please refresh.",
        "INVALID_RESPONSE",
        response.status,
      );
    }
    return parsed.data;
  }

  return {
    configured: Boolean(normalizedBaseUrl),
    async createDirectCase(prompt: string) {
      return request("/v1/cases", createdCaseSchema, {
        body: JSON.stringify({ prompt }),
        headers: { "Idempotency-Key": idempotencyKey("direct") },
        method: "POST",
      });
    },
    async convertCandidates(
      candidates: { candidateId: string; version: number }[],
    ) {
      return request("/v1/suggestions/convert", caseReferenceSchema, {
        body: JSON.stringify({
          candidate_ids: candidates.map((item) => item.candidateId),
          expected_versions: candidates.map((item) => item.version),
        }),
        headers: { "Idempotency-Key": idempotencyKey("convert") },
        method: "POST",
      });
    },
    async decideCase(
      caseId: string,
      input: {
        decision: "APPROVE" | "DEFER" | "REJECT" | "STOP";
        expectedVersion: number;
        grantMode: "ONCE" | "CONDITIONAL" | "STANDING";
        planHash: string;
        planVersion: number;
      },
    ) {
      return request(
        `/v1/cases/${encodeURIComponent(caseId)}/decision`,
        liveCaseSummarySchema,
        {
          body: JSON.stringify({
            decision: input.decision,
            expected_version: input.expectedVersion,
            grant_mode: input.grantMode,
            plan_hash: input.planHash,
            plan_version: input.planVersion,
          }),
          headers: { "Idempotency-Key": idempotencyKey("decision") },
          method: "POST",
        },
      );
    },
    async getCase(caseId: string) {
      return request(
        `/v1/cases/${encodeURIComponent(caseId)}`,
        liveCaseDetailSchema,
      );
    },
    async listRoutines() {
      return (await request("/v1/routines", routineListSchema)).routines;
    },
    async pushDevice(deviceId: string) {
      return request(
        `/v1/mobile/push-token?device_id=${encodeURIComponent(deviceId)}`,
        pushDeviceSchema,
      );
    },
    async registerPushToken(
      input: { deviceId: string; expoPushToken: string; appVersion: string },
      signal?: AbortSignal,
    ) {
      await request("/v1/mobile/push-token", z.void(), {
        method: "PUT",
        signal,
        body: JSON.stringify({
          device_id: input.deviceId,
          expo_push_token: input.expoPushToken,
          app_version: input.appVersion,
          platform: "android",
        }),
      });
    },
    async unregisterPushToken(deviceId: string, signal?: AbortSignal) {
      await request("/v1/mobile/push-token", z.void(), {
        method: "DELETE",
        signal,
        body: JSON.stringify({ device_id: deviceId }),
      });
    },
    async proposeRoutine(caseId: string, expectedVersion: number) {
      const value = await request("/v1/routines", routineResponseSchema, {
        method: "POST",
        body: JSON.stringify({
          case_id: caseId,
          expected_version: expectedVersion,
        }),
      });
      return value.routine;
    },
    async activateRoutine(routineId: string, expectedVersion: number) {
      const value = await request(
        `/v1/routines/${encodeURIComponent(routineId)}/activate`,
        routineResponseSchema,
        {
          method: "POST",
          body: JSON.stringify({ expected_version: expectedVersion }),
        },
      );
      return value.routine;
    },
    async pauseRoutine(routineId: string, expectedVersion: number) {
      const value = await request(
        `/v1/routines/${encodeURIComponent(routineId)}/pause`,
        routineResponseSchema,
        {
          method: "POST",
          body: JSON.stringify({ expected_version: expectedVersion }),
        },
      );
      return value.routine;
    },
    async listCases(bucket: "active" | "history") {
      const value = await request(
        `/v1/cases?bucket=${bucket}`,
        listCasesSchema,
      );
      return value.cases;
    },
    async listSuggestionGroups() {
      const value = await request(
        "/v1/suggestion-groups",
        listSuggestionGroupsSchema,
      );
      return value.groups;
    },
    async listSuggestions() {
      const value = await request("/v1/suggestions", listSuggestionsSchema);
      return value.suggestions;
    },
    async postCaseMessage(caseId: string, text: string, version: number) {
      return request(
        `/v1/cases/${encodeURIComponent(caseId)}/messages`,
        messageAcceptedSchema,
        {
          body: JSON.stringify({ expected_version: version, text }),
          headers: { "Idempotency-Key": idempotencyKey("message") },
          method: "POST",
        },
      );
    },
    async submitSuggestionFeedback(
      candidateId: string,
      mode: "HIDE_ONCE" | "REDUCE_SIMILAR" | "ADJUST_SCOPE",
      expectedVersion: number,
    ) {
      return request(
        `/v1/suggestions/${encodeURIComponent(candidateId)}/feedback`,
        suggestionFeedbackSchema,
        {
          body: JSON.stringify({
            expected_version: expectedVersion,
            mode,
          }),
          headers: { "Idempotency-Key": idempotencyKey("feedback") },
          method: "POST",
        },
      );
    },
    async retryCase(caseId: string, version: number) {
      return request(
        `/v1/cases/${encodeURIComponent(caseId)}/retry`,
        liveCaseSummarySchema,
        {
          body: JSON.stringify({ expected_version: version }),
          headers: { "Idempotency-Key": idempotencyKey("retry") },
          method: "POST",
        },
      );
    },
    async stopCase(caseId: string, version: number) {
      return request(
        `/v1/cases/${encodeURIComponent(caseId)}/stop`,
        liveCaseSummarySchema,
        {
          body: JSON.stringify({ expected_version: version }),
          headers: { "Idempotency-Key": idempotencyKey("stop") },
          method: "POST",
        },
      );
    },
    async undoSuggestionSuppression(ruleId: string) {
      return request(
        `/v1/suppressions/${encodeURIComponent(ruleId)}/undo`,
        suppressionUndoSchema,
        {
          headers: { "Idempotency-Key": idempotencyKey("undo") },
          method: "POST",
        },
      );
    },
  };
}

function idempotencyKey(kind: string) {
  requestSequence += 1;
  return `${kind}:${Date.now().toString(36)}:${requestSequence.toString(36)}:${Math.random().toString(36).slice(2, 10)}`;
}

async function currentAccessToken() {
  const { fetchAuthSession } = await import("aws-amplify/auth");
  const session = await fetchAuthSession();
  const token = session.tokens?.accessToken?.toString();
  if (!token) {
    throw new ProductApiError(
      "Your session expired. Please sign in again.",
      "AUTH_REQUIRED",
      401,
    );
  }
  return token;
}

function transportErrorCode(caught: unknown): string {
  const name =
    caught && typeof caught === "object" && "name" in caught
      ? caught.name
      : null;
  if (name === "AbortError") return "REQUEST_CANCELLED";
  if (name === "TimeoutError") return "REQUEST_TIMEOUT";
  return "NETWORK_ERROR";
}

function httpErrorCode(status: number): string {
  if (status === 401) return "AUTH_REQUIRED";
  if (status >= 500) return "SERVICE_UNAVAILABLE";
  return "UNKNOWN";
}

function errorMessage(code: string) {
  return (
    {
      ACTION_EXECUTION_NOT_READY:
        "Execution is unavailable. Your plan has been saved.",
      AUTH_REQUIRED: "Your session expired. Please sign in again.",
      PUSH_INPUT_INVALID: "Check your notification settings.",
      PUSH_REGISTRATION_CONFLICT:
        "Device registration changed. Please try again.",
      PUSH_SERVICE_UNAVAILABLE:
        "Could not set up notifications. Try again shortly.",
      ROUTINE_INPUT_INVALID: "Review the preparation routine.",
      ROUTINE_NOT_FOUND: "Routine not found. Refresh the list.",
      ROUTINE_VERSION_CONFLICT:
        "The routine or connection changed. Please refresh.",
      ROUTINE_NOT_READY:
        "Create a routine from a verified Calendar event prepared from mail.",
      ROUTINE_LIMIT_EXCEEDED: "Could not load all routines. Try again shortly.",
      PLAN_CHANGED: "The plan changed. Review it before approving again.",
      CALENDAR_PERMISSION_CHANGED: "Check your Google Calendar permissions.",
      CALENDAR_ACCOUNT_CHANGED:
        "The Google account changed since approval. Check your connection.",
      GOOGLE_AUTH_REQUIRED: "Check your Google Calendar connection.",
      NETWORK_ERROR: "Could not confirm the result. Check your connection.",
      REQUEST_CANCELLED: "The request was interrupted. Refresh its status.",
      REQUEST_TIMEOUT: "The request timed out. Refresh its status.",
      INVALID_WORKSPACE_INPUT: "Check the details you entered.",
      INVALID_SUGGESTION_FEEDBACK: "Check your suggestion feedback.",
      SERVICE_UNAVAILABLE: "Could not load QuietPilot. Try again shortly.",
      WORKSPACE_RECORD_NOT_FOUND: "Item not found.",
      WORKSPACE_VERSION_CONFLICT: "The status changed. Please refresh.",
      SUGGESTION_RULE_NOT_FOUND: "The suggestion filter could not be found.",
      SUGGESTION_VERSION_CONFLICT: "Suggestions changed. Refresh the list.",
    }[code] ?? "Could not complete the request."
  );
}
