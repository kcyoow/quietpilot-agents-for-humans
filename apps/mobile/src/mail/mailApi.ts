import { fetchAuthSession } from "aws-amplify/auth";
import { z } from "zod";

const timestamp = z.iso.datetime({ offset: true }).nullable();
const stage = z.enum(["NOT_STARTED", "PENDING", "READY", "ERROR"]);
const tag = z
  .string()
  .min(1)
  .regex(/^[^\s#]+$/u)
  .refine((value) => Array.from(value).length <= 32);
const errorCode = z.string().max(128).nullable();
const identifier = z.string().min(1).max(256).nullable();

export const mailProfileSchema = z.object({
  tags: z.array(tag).max(8),
  description: z.string().refine((value) => Array.from(value).length <= 1000),
  version: z.number().int().nonnegative(),
  updated_at: timestamp,
});

export const mailInterestStateSchema = z.object({
  profile: mailProfileSchema,
  recommendations: z.object({
    status: stage,
    request_id: identifier,
    tags: z
      .array(
        z.object({
          tag,
          evidence_refs: z.array(z.string().min(1)).min(1).max(32),
        }),
      )
      .max(8),
    title_count: z.number().int().min(0).max(32),
    generated_at: timestamp,
    error_code: errorCode,
  }),
  scan: z.object({
    status: stage,
    scan_id: identifier,
    profile_version: z.number().int().nonnegative(),
    processed_count: z.number().int().nonnegative(),
    matched_count: z.number().int().nonnegative(),
    completed_at: timestamp,
    error_code: errorCode,
  }),
});

const mailResultSchema = z.object({
  evidence_ref: z.string().min(1).max(256),
  title: z.string().min(1).max(1000),
  summary: z.string().max(2000),
  reason: z.string().max(2000),
  sender_domain: z.string().max(255),
  received_at: timestamp,
  matched_tags: z.array(tag).max(8),
  importance: z.enum(["HIGH", "NORMAL", "LOW"]).optional(),
});

export const mailResultsSchema = z.object({
  profile_version: z.number().int().nonnegative(),
  scan_id: identifier,
  status: stage,
  items: z.array(mailResultSchema).max(100),
  next_cursor: z.string().min(1).max(4096).nullable(),
});

export type MailProfile = z.infer<typeof mailProfileSchema>;
export type MailInterestState = z.infer<typeof mailInterestStateSchema>;
export type MailResults = z.infer<typeof mailResultsSchema>;
export type MailResult = MailResults["items"][number];
export type MailProfileInput = Pick<MailProfile, "tags" | "description"> & {
  expected_version: number;
};

export function isMailConfigured(
  profile: Pick<MailProfile, "tags" | "description">,
) {
  return profile.tags.length > 0 || profile.description.trim().length > 0;
}

export class MailApiError extends Error {
  constructor(
    readonly code: string,
    readonly status: number,
  ) {
    super(mailErrorMessage(code, status));
    this.name = "MailApiError";
  }
}

export function createMailApi({
  ownerId,
  baseUrl = process.env.EXPO_PUBLIC_CONTROL_API_URL,
  accessToken = () => currentAccessToken(ownerId),
  requester = fetch,
}: {
  ownerId?: string;
  baseUrl?: string;
  accessToken?: () => Promise<string>;
  requester?: typeof fetch;
} = {}) {
  const base = baseUrl?.trim().replace(/\/+$/, "") ?? "";

  async function request<T>(
    path: string,
    schema: z.ZodType<T>,
    init?: RequestInit,
  ): Promise<T> {
    if (!base) throw new MailApiError("API_NOT_CONFIGURED", 0);
    const token = await accessToken();
    let response: Response;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 25_000);
    try {
      response = await requester(`${base}${path}`, {
        ...init,
        signal: controller.signal,
        headers: {
          accept: "application/json",
          authorization: `Bearer ${token}`,
          ...(init?.body ? { "content-type": "application/json" } : {}),
        },
      });
      let value: unknown;
      try {
        value = await response.json();
      } catch {
        if (response.ok)
          throw new MailApiError("INVALID_RESPONSE", response.status);
      }
      if (!response.ok) {
        const code =
          value &&
          typeof value === "object" &&
          "error" in value &&
          typeof value.error === "string"
            ? value.error
            : "HTTP_ERROR";
        throw new MailApiError(code, response.status);
      }
      const parsed = schema.safeParse(value);
      if (!parsed.success)
        throw new MailApiError("INVALID_RESPONSE", response.status);
      return parsed.data;
    } catch (caught) {
      if (caught instanceof MailApiError) throw caught;
      throw new MailApiError(
        controller.signal.aborted ? "REQUEST_TIMEOUT" : "NETWORK_ERROR",
        0,
      );
    } finally {
      clearTimeout(timeout);
    }
  }

  return {
    configured: Boolean(base),
    getState: () => request("/v1/mail/interests", mailInterestStateSchema),
    save: (input: MailProfileInput) =>
      request("/v1/mail/interests", mailInterestStateSchema, {
        method: "PUT",
        body: JSON.stringify(input),
      }),
    recommend: () =>
      request("/v1/mail/recommendations", mailInterestStateSchema, {
        method: "POST",
      }),
    scan: () =>
      request("/v1/mail/scan", mailInterestStateSchema, { method: "POST" }),
    getResults: (cursor?: string) =>
      request(
        `/v1/mail/results${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`,
        mailResultsSchema,
      ),
  };
}

async function currentAccessToken(ownerId?: string) {
  const session = await fetchAuthSession();
  const accessToken = session.tokens?.accessToken;
  if (!accessToken) throw new MailApiError("AUTH_REQUIRED", 401);
  if (ownerId && accessToken.payload.sub !== ownerId)
    throw new MailApiError("OWNER_CHANGED", 409);
  return accessToken.toString();
}

function mailErrorMessage(code: string, status: number) {
  if (code === "OWNER_CHANGED") return "Your account changed. Please refresh.";
  if (status === 401) return "Your session expired. Please sign in again.";
  if (status === 409)
    return "Your interests changed. Load the latest settings.";
  if (status === 404) return "Mail search is unavailable. Try again shortly.";
  if (code === "INVALID_RESPONSE")
    return "Could not read mail results. Please try again.";
  if (code === "REQUEST_TIMEOUT")
    return "The request timed out. Please try again.";
  if (code === "NETWORK_ERROR") return "Check your connection and try again.";
  if (code === "API_NOT_CONFIGURED")
    return "Check your connection to mail search.";
  if (status === 400) return "Check your interests and mail connection.";
  return "Could not check mail. Try again shortly.";
}
