import {
  buildActionQueue,
  matchesActionQueueSearch,
} from "@/src/workspace/actionQueue";
import type { WorkspaceSnapshot } from "@/src/workspace/types";
import { humanizeWorkTerms } from "@/src/workspace/plainLanguage";

const snapshot: WorkspaceSnapshot = {
  candidateGroups: [
    {
      candidateIds: ["mail-1", "mail-2"],
      dataSource: "SCENARIO",
      groupId: "mail-group",
      icon: "email-outline",
      label: "메일 마감",
      provider: "google",
      reason: "같은 제출 결과",
    },
  ],
  candidates: [
    {
      candidateId: "mail-1",
      caseTypeHint: "CONNECTED_SIGNAL",
      confidence: 0.94,
      createdAt: "2026-09-01T00:00:00Z",
      dataSource: "SCENARIO",
      eventAt: null,
      evidenceSummary: "Gmail 근거 2개",
      opportunityType: "DEADLINE",
      outcome: "장학금 서류 제출",
      primaryGroupId: "mail-group",
      proposedActions: [
        {
          actionId: "action-1",
          connector: "quietpilot",
          label: "체크 항목 준비",
          parameters: {},
          requiredScopes: [],
          resultSummary: null,
          reversible: true,
          risk: "LOW",
          status: "PROPOSED",
          target: "case:deadline",
          verb: "prepare_task",
        },
      ],
      provider: "google",
      requiresApproval: false,
      risk: "LOW",
      safetyState: null,
      safetySummary: null,
      status: "VISIBLE",
      summary: "제출 안내",
      tags: ["deadline"],
      updatedAt: "2026-09-01T00:00:00Z",
      version: 1,
      whyNow: "마감이 다가오고 있어요.",
    },
  ],
  cases: [
    {
      caseId: "calendar-case",
      caseType: "CONNECTED_SIGNAL",
      createdAt: "2026-09-01T00:00:00Z",
      currentPlan: null,
      dataSource: "SCENARIO",
      evidence: [
        {
          detail: "예약 시간이 바뀌었어요.",
          evidenceId: "calendar-evidence",
          label: "캘린더 일정 변경",
          provider: "google",
          revision: 1,
        },
      ],
      goal: "병원 예약 변경 확인",
      messages: [],
      nextAction: "새 시간을 확인해 주세요.",
      partialFailure: null,
      planChange: null,
      policyIds: [],
      priority: 80,
      providers: ["google"],
      risk: "LOW",
      status: "DECISION_REQUIRED",
      summary: "예약 변경",
      timeline: [],
      updatedAt: "2026-09-02T00:00:00Z",
      version: 1,
      whyNow: "오늘 확인이 필요해요.",
    },
  ],
  dataSource: "SCENARIO",
  loadedScenario: "FULL",
};

test("groups existing work by human source and normalizes stances", () => {
  const queue = buildActionQueue(snapshot);

  expect(queue.map((item) => [item.source, item.stanceLabel])).toEqual([
    ["MAIL", "Review plan"],
    ["CALENDAR", "View details"],
  ]);
  expect(queue[0].sourceDetail).toBe("Relevant mail 2 items");
});

test("searches only the compact user-facing queue presentation", () => {
  const queue = buildActionQueue(snapshot);

  expect(matchesActionQueueSearch(queue[0], "장학금")).toBe(true);
  expect(matchesActionQueueSearch(queue[0], "sender_domain")).toBe(false);
});

test("removes internal prototype wording from queue copy", () => {
  const completed = {
    ...snapshot.cases[0],
    nextAction: "확인된 프로토타입 결과를 기록했어요.",
    status: "COMPLETED" as const,
  };
  const queue = buildActionQueue({
    ...snapshot,
    candidates: [],
    cases: [completed],
  });

  expect(queue[0].body).toBe("Recorded the example result.");
  expect(queue[0].body).not.toMatch(/프로토타입|readback|fixture/i);
});

test("English evidence summaries preserve the source count", () => {
  const queue = buildActionQueue({
    ...snapshot,
    candidates: [
      { ...snapshot.candidates[0], evidenceSummary: "Gmail sources 3 items" },
    ],
    cases: [],
  });
  expect(queue[0].relatedCount).toBe(3);
});

test("uses an everyday word for work in recovery guidance", () => {
  const queue = buildActionQueue({
    ...snapshot,
    candidates: [],
    cases: [
      {
        ...snapshot.cases[0],
        status: "FAILED",
        nextAction: "실패한 단계부터 이어가거나 Case를 중지할 수 있어요.",
      },
    ],
  });
  expect(queue[0].body).toBe(
    "실패한 단계부터 이어가거나 작업을 중지할 수 있어요.",
  );
});

test("keeps Korean particles natural without changing longer identifiers", () => {
  expect(
    humanizeWorkTerms(
      "한 Case로 정리하고 Case가 끝나면 Case는 기록에 남아요. Case와 일정을 함께 봐요.",
    ),
  ).toBe(
    "한 작업으로 정리하고 작업이 끝나면 작업은 기록에 남아요. 작업과 일정을 함께 봐요.",
  );
  expect(humanizeWorkTerms("CaseStudy와 showcase는 제목이에요.")).toBe(
    "CaseStudy와 showcase는 제목이에요.",
  );
});
