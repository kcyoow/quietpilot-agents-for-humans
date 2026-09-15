import {
  liveCandidateToWorkspace,
  liveCaseDetailToWorkspace,
  liveGroupToWorkspace,
} from "@/src/workspace/adapters";

test("marks live candidates and groups without mixing verification fixtures", () => {
  const candidate = liveCandidateToWorkspace({
    candidate_id: "candidate-live",
    confidence: 0.91,
    created_at: "2026-08-30T00:00:00Z",
    evidence_refs: ["gmail:one"],
    outcome: "제출 마감 확인",
    opportunity_type: "DEADLINE",
    primary_group_id: "gmail-mail-schedule",
    proposed_actions: [
      {
        connector: "quietpilot",
        parameters: { source_ref: "gmail:one" },
        required_scopes: [],
        reversible: true,
        risk: "LOW",
        target_resource: "case:deadline",
        verb: "prepare_task",
        verification_method: "case_plan_readback",
      },
    ],
    provider: "google",
    risk: "LOW",
    source_type: "CONNECTED_SIGNAL",
    status: "VISIBLE",
    summary: "Gmail에서 확인했어요.",
    tags: ["gmail"],
    updated_at: "2026-08-30T00:00:00Z",
    version: 2,
    why_now: "마감 전에 준비할 수 있어요.",
  });
  const group = liveGroupToWorkspace(
    {
      candidate_count: 1,
      group_id: "gmail-mail-schedule",
      highest_risk: "LOW",
      label: "메일·일정",
      reason: "같은 결과의 메일을 묶었어요.",
    },
    [candidate],
  );

  expect(candidate.dataSource).toBe("LIVE");
  expect(group.dataSource).toBe("LIVE");
  expect(group.candidateIds).toEqual(["candidate-live"]);
});

test("maps exact live plan, evidence, timeline and scoped chat", () => {
  const item = liveCaseDetailToWorkspace({
    actions: [],
    case_id: "case-live",
    case_type: "DIRECT_DELEGATION",
    current_plan_hash: "a".repeat(64),
    current_plan_version: 1,
    evidence: [
      {
        detail: "이번 주 제출 준비",
        evidence_id: "evidence-1",
        evidence_ref: "direct:one",
        label: "직접 요청",
        provider: "direct",
        revision: 1,
      },
    ],
    goal: "제출 준비",
    messages: [
      {
        author: "QUIETPILOT",
        created_at: "2026-08-30T00:00:01Z",
        message_id: "message-1",
        text: "이 범위로 준비할까요?",
      },
    ],
    next_action: "범위 확인",
    plan: {
      actions: [],
      available_grant_modes: ["ONCE"],
      expected_outcome: "제출 준비",
      hash: "a".repeat(64),
      reason: "요청을 정리했어요.",
      required_scopes: [],
      reversibility: "외부 변경 없음",
      risk: "LOW",
      version: 1,
    },
    priority: 50,
    providers: [],
    risk: "LOW",
    status: "DECISION_REQUIRED",
    summary: "요청을 정리했어요.",
    timeline: [
      {
        body: "계획을 준비했어요.",
        event_id: "event-1",
        label: "계획 준비 완료",
        occurred_at: "2026-08-30T00:00:01Z",
        state: "CURRENT",
      },
    ],
    updated_at: "2026-08-30T00:00:01Z",
    version: 2,
    why_now: "사용자가 직접 맡겼어요.",
  });

  expect(item.dataSource).toBe("LIVE");
  expect(item.currentPlan?.hash).toBe("a".repeat(64));
  expect(item.evidence[0].provider).toBe("direct");
  expect(item.timeline[0].state).toBe("CURRENT");
  expect(item.messages[0].author).toBe("QUIETPILOT");
});
