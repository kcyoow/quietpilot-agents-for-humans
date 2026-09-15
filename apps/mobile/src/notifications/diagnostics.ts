const KEYS = [
  "case_id",
  "event_id",
  "kind",
  "scopeKey",
  "experienceId",
  "projectId",
  "body",
  "data",
  "title",
  "message",
  "channelId",
  "collapseKey",
  "google.message_id",
  "google.sent_time",
  "google.ttl",
];
type Stage =
  "RESPONSE" | "GUARD" | "CASE_LOOKUP" | "CASE_RESULT" | "OPENED" | "RETRY";
type Details = {
  origin?: "LISTENER" | "RECOVERY";
  action?: "DEFAULT" | "OTHER";
  scope?: "LIVE" | "SCENARIO" | "NO_OWNER";
  enabled?: boolean;
  busy?: boolean;
  valid_notice?: boolean;
  outcome?: "OPENED" | "IGNORED" | "RETRY";
  record_present?: boolean;
  same_case?: boolean;
  live_case?: boolean;
};

export function notificationDataShape(data: unknown) {
  const object =
    data && typeof data === "object" && !Array.isArray(data)
      ? (data as Record<string, unknown>)
      : {};
  const keys = Object.keys(object);
  const kind = object.kind;
  return {
    data_type:
      data === null ? "null" : Array.isArray(data) ? "array" : typeof data,
    known_keys: KEYS.filter((key) => Object.hasOwn(object, key)),
    unknown_key_count: keys.filter((key) => !KEYS.includes(key)).length,
    case_id_type: typeof object.case_id,
    case_id_valid:
      typeof object.case_id === "string" &&
      /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(object.case_id),
    event_id_type: typeof object.event_id,
    event_id_valid:
      typeof object.event_id === "string" &&
      /^[0-9a-f]{64}$/.test(object.event_id),
    kind:
      typeof kind === "string" &&
      ["DECISION_REQUIRED", "ACTION_FAILED", "PLAN_CHANGED"].includes(kind)
        ? kind
        : "UNKNOWN",
  };
}

export function logNotificationTap(
  stage: Stage,
  data: unknown,
  details: Details = {},
) {
  if (__DEV__) {
    console.info(
      "QP_NOTIFICATION_TAP",
      JSON.stringify({ stage, ...notificationDataShape(data), ...details }),
    );
  }
}
