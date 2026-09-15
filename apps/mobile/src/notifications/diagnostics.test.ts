import { logNotificationTap, notificationDataShape } from "./diagnostics";

test("diagnostics contain only known key names, types, fixed enums and booleans", () => {
  const source = {
    case_id: "private-case-value",
    event_id: "b".repeat(64),
    kind: "ACTION_FAILED",
    scopeKey: "private-scope-value",
    projectId: "private-project-value",
    private_unknown_key: "ExpoPushToken[private_token_value]",
  };
  const output = JSON.stringify(notificationDataShape(source));
  for (const value of [
    source.case_id,
    source.event_id,
    source.scopeKey,
    source.projectId,
    source.private_unknown_key,
    "private_unknown_key",
  ])
    expect(output).not.toContain(value);
  expect(notificationDataShape(source)).toMatchObject({
    kind: "ACTION_FAILED",
    unknown_key_count: 1,
    case_id_valid: true,
    event_id_valid: true,
  });
  const log = jest.spyOn(console, "info").mockImplementation(() => {});
  try {
    logNotificationTap("CASE_LOOKUP", source, { scope: "LIVE", enabled: true });
    expect(log.mock.calls[0][0]).toBe("QP_NOTIFICATION_TAP");
    expect(JSON.stringify(log.mock.calls)).not.toContain(source.case_id);
    expect(JSON.stringify(log.mock.calls)).not.toContain(
      source.private_unknown_key,
    );
  } finally {
    log.mockRestore();
  }
});

test("unknown kind values are never logged", () => {
  expect(notificationDataShape({ kind: "private-kind-value" }).kind).toBe(
    "UNKNOWN",
  );
});
