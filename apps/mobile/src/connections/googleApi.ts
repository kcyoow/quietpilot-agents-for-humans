import { z } from "zod";

import {
  liveCandidateSchema,
  liveSuggestionGroupSchema,
} from "@/src/api/productApi";

export type GoogleConnectionStatus =
  | "DISCONNECTED"
  | "CONNECTING"
  | "SCANNING"
  | "CONNECTED"
  | "REVOKING"
  | "ERROR";

export type GoogleConnectionRecord = {
  calendar?: { status: "CONNECTED" | "DISCONNECTED"; granted_scopes: string[] };
  discovery_revision?: number;
  error_code: string | null;
  granted_scopes: string[];
  label: "Google";
  last_checked_at: string | null;
  last_sync_mode: "INITIAL_7_DAY" | "INCREMENTAL" | "BOUNDED_FULL_SYNC" | null;
  lookback_days: number;
  next_renewal_due_at: string | null;
  provider: "google";
  scan_progress: number;
  status: GoogleConnectionStatus;
  version: number;
  watch_expires_at: string | null;
  watch_renewed_at: string | null;
};

export type LiveSuggestionRecord = {
  candidate_id: string;
  confidence: number;
  created_at: string;
  evidence_refs: string[];
  outcome: string;
  opportunity_type: "APPOINTMENT" | "DEADLINE" | "FOLLOW_UP";
  primary_group_id: string;
  proposed_actions: {
    connector: string;
    parameters: Record<string, unknown>;
    required_scopes: string[];
    reversible: boolean;
    risk: "LOW" | "MEDIUM" | "HIGH";
    target_resource: string;
    verb: string;
    verification_method: string;
  }[];
  provider: "google";
  risk: "LOW" | "MEDIUM" | "HIGH";
  source_type: "CONNECTED_SIGNAL";
  status: "VISIBLE";
  summary: string;
  tags: string[];
  updated_at: string;
  version: number;
  why_now: string;
};

export type LiveSuggestionGroupRecord = {
  candidate_count: number;
  group_id: string;
  highest_risk: "LOW" | "MEDIUM" | "HIGH";
  label: string;
  reason: string;
};

const nullableTimestampSchema = z.iso.datetime({ offset: true }).nullable();
const googleConnectionSchema: z.ZodType<GoogleConnectionRecord> = z.object({
  calendar: z
    .object({
      status: z.enum(["CONNECTED", "DISCONNECTED"]),
      granted_scopes: z.array(z.string()),
    })
    .optional(),
  // Older deployed responses omit this field. Keep it absent so presentation
  // reports incompatible discovery instead of certifying a completed scan.
  discovery_revision: z.number().int().nonnegative().optional(),
  error_code: z.string().nullable(),
  granted_scopes: z.array(z.string()),
  label: z.literal("Google"),
  last_checked_at: nullableTimestampSchema,
  last_sync_mode: z
    .enum(["INITIAL_7_DAY", "INCREMENTAL", "BOUNDED_FULL_SYNC"])
    .nullable(),
  lookback_days: z.number().int().min(1).max(30),
  next_renewal_due_at: nullableTimestampSchema,
  provider: z.literal("google"),
  scan_progress: z.number().int().min(0).max(100),
  status: z.enum([
    "DISCONNECTED",
    "CONNECTING",
    "SCANNING",
    "CONNECTED",
    "REVOKING",
    "ERROR",
  ]),
  version: z.number().int().nonnegative(),
  watch_expires_at: nullableTimestampSchema,
  watch_renewed_at: nullableTimestampSchema,
});
const connectionResponseSchema = z.object({
  connection: googleConnectionSchema,
});
const authorizeResponseSchema = connectionResponseSchema
  .extend({
    authorization_url: z
      .string()
      .max(4096)
      .url()
      .refine((value) => {
        try {
          return new URL(value).protocol === "https:";
        } catch {
          return false;
        }
      })
      .optional(),
  })
  .refine(
    (value) =>
      value.connection.status !== "CONNECTING" ||
      Boolean(value.authorization_url) ||
      value.connection.granted_scopes.includes(
        "https://www.googleapis.com/auth/gmail.readonly",
      ),
  );
const listConnectionsSchema = z.object({
  connections: z.array(
    z.union([
      googleConnectionSchema,
      // The shared endpoint also permits SmartThings; this client only reads
      // its provider discriminator and never treats it as a Google connection.
      z.object({ provider: z.literal("smartthings") }).passthrough(),
    ]),
  ),
});
const listSuggestionsSchema = z.object({
  next_cursor: z.string().nullable(),
  suggestions: z.array(liveCandidateSchema),
});
const listSuggestionGroupsSchema = z.object({
  groups: z.array(liveSuggestionGroupSchema),
  next_cursor: z.string().nullable(),
});

type Requester = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;
type AccessTokenProvider = () => Promise<string>;

export class GoogleConnectionApiError extends Error {
  override name = "GoogleConnectionApiError";
  code = "UNKNOWN";
  status = 0;
}

type GoogleOAuthCompleter = {
  complete(code: string): Promise<GoogleConnectionRecord>;
};

type GoogleOAuthCompletionCache = Map<string, Promise<GoogleConnectionRecord>>;
const googleOAuthCompletionsByOwner = new Map<
  string,
  GoogleOAuthCompletionCache
>();
const googleOAuthCompletionsByApi = new WeakMap<
  GoogleOAuthCompleter,
  GoogleOAuthCompletionCache
>();

export function completeGoogleOAuthOnce(
  api: GoogleOAuthCompleter,
  code: string,
  ownerKey?: string,
): Promise<GoogleConnectionRecord> {
  // Explicit owners preserve cross-screen dedupe; legacy calls share only their API object.
  let cache =
    ownerKey === undefined
      ? googleOAuthCompletionsByApi.get(api)
      : googleOAuthCompletionsByOwner.get(ownerKey);
  if (!cache) {
    cache = new Map();
    if (ownerKey === undefined) googleOAuthCompletionsByApi.set(api, cache);
    else googleOAuthCompletionsByOwner.set(ownerKey, cache);
  }
  const completions = cache;
  const existing = completions.get(code);
  if (existing) return existing;
  const pending = api.complete(code).catch((error: unknown) => {
    if (completions.get(code) === pending) completions.delete(code);
    throw error;
  });
  completions.set(code, pending);
  return pending;
}

export function createGoogleConnectionsApi({
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
      throw new GoogleConnectionApiError(
        "Google connections are not configured.",
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
      if (caught instanceof GoogleConnectionApiError) throw caught;
      const code = transportErrorCode(caught);
      throw googleApiError(code, 0);
    }
    let value: unknown;
    try {
      value = (await response.json()) as unknown;
    } catch (caught) {
      const code = transportErrorCode(caught);
      if (code !== "NETWORK_ERROR") {
        throw googleApiError(code, response.status);
      }
      // HTTP failures can have HTML/empty bodies; retain their status below.
      // A successful response still has to meet this endpoint's JSON contract.
    }
    if (!response.ok) {
      const code =
        value &&
        typeof value === "object" &&
        "error" in value &&
        typeof value.error === "string" &&
        value.error.length > 0
          ? value.error
          : httpErrorCode(response.status);
      throw googleApiError(code, response.status);
    }
    const parsed = schema.safeParse(value);
    if (!parsed.success) {
      throw googleApiError("INVALID_RESPONSE", response.status);
    }
    return parsed.data;
  }

  return {
    configured: Boolean(normalizedBaseUrl),
    async authorize(capability?: "calendar") {
      return request<{
        authorization_url?: string;
        connection: GoogleConnectionRecord;
      }>("/v1/connections/google/authorize", authorizeResponseSchema, {
        method: "POST",
        ...(capability ? { body: JSON.stringify({ capability }) } : {}),
      });
    },
    async complete(code: string) {
      const result = await request<{ connection: GoogleConnectionRecord }>(
        "/v1/connections/google/complete",
        connectionResponseSchema,
        {
          method: "POST",
          body: JSON.stringify({ code }),
        },
      );
      return result.connection;
    },
    async disconnect() {
      const result = await request<{ connection: GoogleConnectionRecord }>(
        "/v1/connections/google",
        connectionResponseSchema,
        { method: "DELETE" },
      );
      return result.connection;
    },
    async scan(lookbackDays = 7) {
      const result = await request<{ connection: GoogleConnectionRecord }>(
        "/v1/connections/google/scan",
        connectionResponseSchema,
        {
          method: "POST",
          body: JSON.stringify({ lookback_days: lookbackDays }),
        },
      );
      return result.connection;
    },
    async get() {
      const result = await request("/v1/connections", listConnectionsSchema);
      return (
        result.connections.find(
          (item): item is GoogleConnectionRecord => item.provider === "google",
        ) ?? null
      );
    },
    async listSuggestions() {
      const result = await request<{
        next_cursor: string | null;
        suggestions: LiveSuggestionRecord[];
      }>("/v1/suggestions", listSuggestionsSchema);
      return result.suggestions;
    },
    async listSuggestionGroups() {
      const result = await request<{
        groups: LiveSuggestionGroupRecord[];
        next_cursor: string | null;
      }>("/v1/suggestion-groups", listSuggestionGroupsSchema);
      return result.groups;
    },
  };
}

async function currentAccessToken() {
  const { fetchAuthSession } = await import("aws-amplify/auth");
  const session = await fetchAuthSession();
  const token = session.tokens?.accessToken?.toString();
  if (!token) {
    throw new GoogleConnectionApiError("Sign in to connect Google.");
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
  if (status >= 500) return "CONNECTION_SERVICE_UNAVAILABLE";
  return "UNKNOWN";
}

function googleApiError(
  code: string,
  status: number,
): GoogleConnectionApiError {
  const error = new GoogleConnectionApiError(connectionErrorMessage(code));
  error.code = code;
  error.status = status;
  return error;
}

function connectionErrorMessage(code: string) {
  return (
    {
      AUTH_REQUIRED: "Your session expired. Please sign in again.",
      INVALID_RESPONSE: "Could not read the Google connection status.",
      NETWORK_ERROR: "Could not confirm the result. Check your connection.",
      REQUEST_CANCELLED: "The request was interrupted. Refresh its status.",
      REQUEST_TIMEOUT: "The request timed out. Refresh its status.",
      CONNECTION_FLOW_CONFLICT:
        "Your account changed during setup. Connect again.",
      CONNECTION_FLOW_NOT_FOUND: "Google connection setup expired. Try again.",
      CONNECTION_SERVICE_UNAVAILABLE:
        "Could not verify Google. Try again shortly.",
      INVALID_CONNECTION_INPUT:
        "Invalid Google connection response. Connect again.",
    }[code] ?? "Could not complete the Google connection."
  );
}
