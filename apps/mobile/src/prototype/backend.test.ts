import { PrototypeBackend } from "@/src/prototype/backend";
import type { PrototypeClock, PrototypeStorage } from "@/src/prototype/types";

class MemoryStorage implements PrototypeStorage {
  readonly values = new Map<string, string>();

  async getItem(key: string): Promise<string | null> {
    return this.values.get(key) ?? null;
  }

  async setItem(key: string, value: string): Promise<void> {
    this.values.set(key, value);
  }

  async removeItem(key: string): Promise<void> {
    this.values.delete(key);
  }
}

const fixedClock: PrototypeClock = {
  now: () => new Date("2026-08-24T12:00:00.000Z"),
};

function createBackend(storage = new MemoryStorage()) {
  return {
    backend: new PrototypeBackend({ clock: fixedClock, storage }),
    storage,
  };
}

describe("PrototypeBackend", () => {
  it("provides an explicit one-candidate verification state", async () => {
    const { backend } = createBackend();

    const result = await backend.loadScenario("ONE");

    expect(result.snapshot.candidates).toHaveLength(1);
    expect(result.snapshot.candidateGroups).toHaveLength(1);
    expect(result.snapshot.candidateGroups[0].candidateIds).toEqual([
      result.snapshot.candidates[0].candidateId,
    ]);
    expect(result.snapshot.cases).toEqual([]);
  });

  it("starts as an honest empty prototype while keeping direct delegation available", async () => {
    const { backend } = createBackend();

    const initial = await backend.getSnapshot();
    expect(initial).toMatchObject({
      mode: "prototype",
      externalSideEffects: false,
      loadedScenario: "LIVE",
      revision: 0,
    });
    expect(initial.candidates).toEqual([]);
    expect(initial.candidateGroups).toEqual([]);
    expect(initial.cases).toEqual([]);
    expect(initial.connections.google.status).toBe("DISCONNECTED");
    expect(initial.connections.smartthings.status).toBe("DISCONNECTED");
    expect(initial.connections.sms.status).toBe("DISCONNECTED");

    const created =
      await backend.createDirectCase("다음 주 발표 준비를 정리해 줘");
    expect(created.value.caseType).toBe("DIRECT_DELEGATION");
    expect(created.value.providers).toEqual([]);
    expect(created.snapshot.candidates).toHaveLength(0);
    await expect(backend.listCases()).resolves.toHaveLength(1);
  });

  it("loads only provider-backed fixtures after the connection lifecycle completes", async () => {
    const { backend } = createBackend();

    await backend.startConnection("google");
    expect((await backend.getSnapshot()).candidates).toHaveLength(0);

    const scanning = await backend.advanceConnection("google");
    expect(scanning.value.status).toBe("SCANNING");
    expect(scanning.value.scanProgress).toBe(35);
    expect(scanning.snapshot.candidates).toHaveLength(0);

    const connected = await backend.advanceConnection("google");
    expect(connected.value.status).toBe("CONNECTED");
    expect(connected.value.accessible.length).toBeGreaterThan(0);
    expect(connected.value.unavailable.length).toBeGreaterThan(0);

    const candidates = await backend.listCandidates();
    expect(candidates).toHaveLength(72);
    expect(candidates.every((item) => item.provider === "google")).toBe(true);
    await expect(backend.listCandidateGroups()).resolves.toHaveLength(3);
    expect(
      (await backend.listCases("all")).map((item) => item.caseType),
    ).toEqual(
      expect.arrayContaining(["CONNECTED_SIGNAL", "EXCEPTION_APPROVAL"]),
    );
    expect(
      (await backend.listCases("all")).some((item) =>
        item.providers.includes("smartthings"),
      ),
    ).toBe(false);
  });

  it("keeps SMS connection proposal-only and demonstrates policy-bound automatic reminders explicitly", async () => {
    const { backend } = createBackend();

    await backend.startConnection("sms");
    await backend.advanceConnection("sms");
    const connected = await backend.advanceConnection("sms");
    expect(connected.value.status).toBe("CONNECTED");
    expect(await backend.listCandidates({ provider: "sms" })).toHaveLength(24);
    expect(await backend.listCandidateGroups({ provider: "sms" })).toHaveLength(
      2,
    );
    expect(
      (await backend.listCandidates({ provider: "sms" })).every(
        (item) =>
          item.safetyState === "ELIGIBLE" || item.safetyState === "REVIEW",
      ),
    ).toBe(true);
    expect((await backend.listPolicies()).length).toBe(0);
    expect(await backend.getCase("case-sms-auto-reminder")).toBeNull();
    expect((await backend.getCase("case-sms-routine-discovery"))?.status).toBe(
      "DECISION_REQUIRED",
    );

    const scenario = await backend.loadScenario("SMS");
    const automatic = await backend.getCase("case-sms-auto-reminder");
    expect(scenario.snapshot.loadedScenario).toBe("SMS");
    expect(automatic).toMatchObject({
      status: "COMPLETED",
      providers: ["sms"],
      policyIds: ["policy-sms-safe-future-reminders"],
    });
    expect(automatic?.timeline.map((item) => item.label)).toEqual([
      "문자 안전 분석",
      "자동화 조건 확인",
      "사전 허용 정책 일치",
      "알림 등록 확인",
    ]);
    expect(automatic?.currentPlan?.actions[0]).toMatchObject({
      connector: "sms",
      status: "SUCCEEDED",
      verb: "SCHEDULE_LOCAL_REMINDER",
    });
    expect((await backend.listPolicies())[0]).toMatchObject({
      grantMode: "STANDING",
      riskCeiling: "LOW",
      status: "ACTIVE",
    });
    expect(scenario.snapshot.externalSideEffects).toBe(false);
  });

  it("generates 144 candidates and all four Case types in the explicit full scenario", async () => {
    const { backend } = createBackend();
    const result = await backend.loadScenario("FULL");

    expect(result.snapshot.loadedScenario).toBe("FULL");
    expect(result.snapshot.candidates).toHaveLength(144);
    expect(result.snapshot.candidateGroups).toHaveLength(7);
    expect(result.snapshot.cases).toHaveLength(6);
    expect(new Set(result.snapshot.cases.map((item) => item.caseType))).toEqual(
      new Set([
        "CONNECTED_SIGNAL",
        "DIRECT_DELEGATION",
        "ROUTINE_DISCOVERY",
        "EXCEPTION_APPROVAL",
      ]),
    );
    expect(
      result.snapshot.cases.find(
        (item) => item.caseId === "case-connected-signal-stale",
      )?.planChange,
    ).not.toBeNull();
    expect(
      result.snapshot.cases.find(
        (item) => item.caseId === "case-partial-failure",
      )?.partialFailure,
    ).not.toBeNull();
    expect(result.snapshot.externalSideEffects).toBe(false);
  });

  it("persists state and safely resets an invalid or requested local state", async () => {
    const storage = new MemoryStorage();
    const first = new PrototypeBackend({ clock: fixedClock, storage });
    await first.loadScenario("GOOGLE");
    await first.hideCandidate("candidate-google-schedule-001");

    const restored = new PrototypeBackend({ clock: fixedClock, storage });
    expect(
      (await restored.getSnapshot()).candidates.find(
        (item) => item.candidateId === "candidate-google-schedule-001",
      )?.status,
    ).toBe("HIDDEN");

    const reset = await restored.reset();
    expect(reset.candidates).toEqual([]);
    expect(reset.cases).toEqual([]);
    expect(storage.values.size).toBe(0);

    storage.values.set("quietpilot.prototype-state.v2", "not-json");
    const recovered = new PrototypeBackend({ clock: fixedClock, storage });
    expect(await recovered.getSnapshot()).toMatchObject({
      mode: "prototype",
      externalSideEffects: false,
      loadedScenario: "LIVE",
    });
  });

  it("hides, suppresses with undo, and converts candidates without an external effect", async () => {
    const { backend } = createBackend();
    await backend.loadScenario("GOOGLE");

    const hidden = await backend.hideCandidate("candidate-google-schedule-001");
    expect(hidden.value.status).toBe("HIDDEN");

    const reduced = await backend.reduceSimilar(
      "candidate-google-follow-up-001",
    );
    expect(reduced.value.affectedCandidateIds).toHaveLength(23);
    expect(
      (await backend.listCandidates({ groupId: "google-follow-up" })).length,
    ).toBe(1);
    const undone = await backend.undoSuppression(reduced.value.ruleId);
    expect(undone.value).toHaveLength(23);

    const converted = await backend.convertCandidates([
      "candidate-google-deadline-001",
    ]);
    expect(converted.value.caseType).toBe("CONNECTED_SIGNAL");
    expect(converted.value.status).toBe("DECISION_REQUIRED");
    expect(converted.snapshot.externalSideEffects).toBe(false);
    expect(
      converted.snapshot.candidates.find(
        (item) => item.candidateId === "candidate-google-deadline-001",
      )?.status,
    ).toBe("CONVERTED");
  });

  it("rejects stale approval and reconciles RUNNING to VERIFYING to COMPLETED", async () => {
    const { backend } = createBackend();
    const created =
      await backend.createDirectCase("제출 체크리스트를 정리해 줘");
    const item = created.value;
    const plan = item.currentPlan!;

    await expect(
      backend.approveCase(item.caseId, {
        planVersion: plan.version,
        planHash: "0".repeat(64),
        grantMode: "ONCE",
      }),
    ).rejects.toMatchObject({ code: "PLAN_CHANGED" });
    expect((await backend.getCase(item.caseId))?.status).toBe(
      "DECISION_REQUIRED",
    );

    const approved = await backend.approveCase(item.caseId, {
      planVersion: plan.version,
      planHash: plan.hash,
      grantMode: "ONCE",
    });
    expect(approved.value.status).toBe("RUNNING");

    const verifying = await backend.advanceCaseExecution(item.caseId);
    expect(verifying.value.status).toBe("VERIFYING");
    expect(verifying.value.currentPlan?.actions[0].status).toBe("VERIFYING");

    const completed = await backend.advanceCaseExecution(item.caseId);
    expect(completed.value.status).toBe("COMPLETED");
    expect(completed.value.currentPlan?.actions[0]).toMatchObject({
      status: "SUCCEEDED",
      resultSummary: expect.stringContaining("No external calls were made"),
    });
    expect(completed.snapshot.externalSideEffects).toBe(false);
  });

  it("limits high-risk approval to ONCE and preserves successful work on retry", async () => {
    const { backend } = createBackend();
    await backend.loadScenario("FULL");

    const routine = (await backend.getCase("case-routine-discovery"))!;
    await expect(
      backend.approveCase(routine.caseId, {
        planVersion: routine.currentPlan!.version,
        planHash: routine.currentPlan!.hash,
        grantMode: "STANDING",
      }),
    ).rejects.toMatchObject({ code: "UNSUPPORTED_GRANT" });

    const retried = await backend.retryCase("case-partial-failure");
    const actionStatuses = Object.fromEntries(
      retried.value.currentPlan!.actions.map((action) => [
        action.actionId,
        action.status,
      ]),
    );
    expect(actionStatuses["action-task-created"]).toBe("SUCCEEDED");
    expect(actionStatuses["action-calendar-timeout"]).toBe("RUNNING");
    expect(actionStatuses["action-follow-up-pending"]).toBe("RUNNING");

    await backend.advanceCaseExecution("case-partial-failure");
    const completed = await backend.advanceCaseExecution(
      "case-partial-failure",
    );
    expect(completed.value.status).toBe("COMPLETED");
    expect(completed.value.partialFailure).toBeNull();
    expect(
      completed.value.currentPlan!.actions.every(
        (action) => action.status === "SUCCEEDED",
      ),
    ).toBe(true);
  });

  it("supports Case messages, policy revocation, and connection revocation", async () => {
    const { backend } = createBackend();
    await backend.loadScenario("FULL");

    const messages = await backend.postCaseMessage(
      "case-direct-delegation",
      "이 Case에서만 자료 순서를 바꿔 줘",
    );
    expect(messages.value.slice(-2).map((item) => item.author)).toEqual([
      "USER",
      "QUIETPILOT",
    ]);

    const revoked = await backend.revokePolicy("policy-calendar-preparation");
    expect(revoked.value.status).toBe("REVOKED");
    expect((await backend.getCase("case-connected-signal-stale"))?.status).toBe(
      "PERMISSION_REVOKED",
    );

    await backend.disconnectConnection("smartthings");
    expect(await backend.listCandidates({ provider: "smartthings" })).toEqual(
      [],
    );
    expect((await backend.getCase("case-routine-discovery"))?.status).toBe(
      "PERMISSION_REVOKED",
    );
  });

  it("serializes concurrent mutations without duplicate identifiers", async () => {
    const { backend } = createBackend();
    const [first, second] = await Promise.all([
      backend.createDirectCase("첫 번째 요청"),
      backend.createDirectCase("두 번째 요청"),
    ]);

    expect(first.value.caseId).not.toBe(second.value.caseId);
    expect(await backend.listCases("all")).toHaveLength(2);
    expect((await backend.getSnapshot()).revision).toBe(2);
  });
});
