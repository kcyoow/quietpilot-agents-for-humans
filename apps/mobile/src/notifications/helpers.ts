export type AttentionNotice = {
  case_id: string;
  event_id: string;
  kind: "DECISION_REQUIRED" | "ACTION_FAILED" | "PLAN_CHANGED";
};

export function parseAttentionNotice(value: unknown): AttentionNotice | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const item = value as Record<string, unknown>;
  if (
    Object.keys(item).some(
      (key) => !["case_id", "event_id", "kind"].includes(key),
    ) ||
    typeof item.case_id !== "string" ||
    !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(item.case_id) ||
    typeof item.event_id !== "string" ||
    !/^[0-9a-f]{64}$/.test(item.event_id) ||
    typeof item.kind !== "string" ||
    !["DECISION_REQUIRED", "ACTION_FAILED", "PLAN_CHANGED"].includes(item.kind)
  )
    return null;
  return item as AttentionNotice;
}

export function preferenceKey(owner: string) {
  let encoded = "";
  for (let i = 0; i < owner.length; i += 1) {
    encoded += owner.charCodeAt(i).toString(16).padStart(4, "0");
  }
  return `quietpilot.notifications.owner.${encoded}`;
}

export function setupReady(
  platform: string,
  projectId: unknown,
  version: unknown,
) {
  return (
    platform === "android" &&
    typeof projectId === "string" &&
    /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      projectId,
    ) &&
    typeof version === "string" &&
    /^[A-Za-z0-9][A-Za-z0-9.+_-]{0,63}$/.test(version)
  );
}
