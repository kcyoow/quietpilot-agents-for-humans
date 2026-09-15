import {
  PROTOTYPE_MODE,
  PROTOTYPE_SCHEMA_VERSION,
  type PrototypeAction,
  type PrototypeCandidate,
  type PrototypeCandidateGroup,
  type PrototypeCase,
  type PrototypeConnection,
  type PrototypeGrantMode,
  type PrototypeInventoryItem,
  type PrototypePlan,
  type PrototypePolicy,
  type PrototypeProvider,
  type PrototypeRisk,
  type PrototypeState,
} from "@/src/prototype/types";
import { prototypeProviderLabel } from "@/src/prototype/providerMeta";

const FIXTURE_EPOCH = "2026-08-24T09:00:00.000Z";

type ProviderFixtures = {
  candidateGroups: PrototypeCandidateGroup[];
  candidates: PrototypeCandidate[];
  cases: PrototypeCase[];
};

type CandidateGroupSeed = {
  groupId: string;
  label: string;
  reason: string;
  icon: string;
  outcomes: string[];
  tags: string[];
  count: number;
  caseTypeHint: PrototypeCandidate["caseTypeHint"];
};

const groupSeeds: Record<PrototypeProvider, CandidateGroupSeed[]> = {
  google: [
    {
      groupId: "google-schedule",
      label: "일정과 충돌 정리",
      reason: "날짜와 참석자가 겹치는 Gmail·Calendar 신호를 묶었어요.",
      icon: "calendar-clock-outline",
      outcomes: [
        "겹치는 약속 검토",
        "이동 시간을 포함한 일정 조정",
        "변경된 회의 시간 반영",
      ],
      tags: ["일정", "충돌", "Calendar"],
      count: 24,
      caseTypeHint: "CONNECTED_SIGNAL",
    },
    {
      groupId: "google-follow-up",
      label: "답장과 후속 작업",
      reason: "같은 결과로 이어지는 회신 요청과 후속 Task를 묶었어요.",
      icon: "email-fast-outline",
      outcomes: ["회신 기한 챙기기", "답장 뒤 Task 만들기", "미응답 요청 정리"],
      tags: ["Mail", "후속", "회신"],
      count: 24,
      caseTypeHint: "CONNECTED_SIGNAL",
    },
    {
      groupId: "google-deadline",
      label: "마감과 제출 준비",
      reason: "메일 속 마감과 기존 할 일을 같은 제출 결과로 묶었어요.",
      icon: "clipboard-clock-outline",
      outcomes: ["제출 전 점검", "마감 Task 준비", "누락 자료 확인"],
      tags: ["마감", "제출", "Task"],
      count: 24,
      caseTypeHint: "CONNECTED_SIGNAL",
    },
  ],
  smartthings: [
    {
      groupId: "smartthings-comfort",
      label: "집 상태와 쾌적함",
      reason: "API에서 확인 가능한 상태와 반복 패턴만 묶었어요.",
      icon: "home-thermometer-outline",
      outcomes: ["실내 온도 점검", "귀가 전 상태 확인", "반복 조건 제안"],
      tags: ["집", "온도", "Routine"],
      count: 24,
      caseTypeHint: "ROUTINE_DISCOVERY",
    },
    {
      groupId: "smartthings-energy",
      label: "에너지와 기기 점검",
      reason: "공개 capability와 읽기 가능한 상태에서 절약 후보를 찾았어요.",
      icon: "lightning-bolt-outline",
      outcomes: ["대기 전력 확인", "반복 사용 패턴 검토", "절약 Routine 제안"],
      tags: ["에너지", "Devices", "Routine"],
      count: 24,
      caseTypeHint: "ROUTINE_DISCOVERY",
    },
  ],
  sms: [
    {
      groupId: "sms-appointments",
      label: "예약과 방문 일정",
      reason:
        "기관 발신, 명확한 미래 날짜와 의심 링크 부재를 확인한 문자만 묶었어요.",
      icon: "calendar-check-outline",
      outcomes: [
        "병원 예약 알림 준비",
        "수리 방문 일정 챙기기",
        "예매 일정 미리 알리기",
      ],
      tags: ["SMS", "예약", "방문", "미래 일정"],
      count: 12,
      caseTypeHint: "CONNECTED_SIGNAL",
    },
    {
      groupId: "sms-deadlines",
      label: "마감과 갱신 알림",
      reason:
        "만료·수령·신청일처럼 놓치기 쉬운 미래 날짜를 안전 조건별로 묶었어요.",
      icon: "message-alert-outline",
      outcomes: [
        "수령 마감 알림 준비",
        "갱신 날짜 미리 알리기",
        "신청 기한 챙기기",
      ],
      tags: ["SMS", "마감", "갱신", "미래 일정"],
      count: 12,
      caseTypeHint: "CONNECTED_SIGNAL",
    },
  ],
};

function riskFor(index: number, provider: PrototypeProvider): PrototypeRisk {
  if (provider === "smartthings" && index % 11 === 0) return "HIGH";
  if (provider === "sms") return index % 5 === 0 ? "MEDIUM" : "LOW";
  return index % 4 === 0 ? "MEDIUM" : "LOW";
}

function eventAtFor(offset: number): string {
  const date = new Date(FIXTURE_EPOCH);
  date.setUTCDate(date.getUTCDate() + offset + 1);
  date.setUTCHours(5 + (offset % 7), offset % 2 === 0 ? 0 : 30, 0, 0);
  return date.toISOString();
}

function hashFixture(seed: string): string {
  return Array.from({ length: 8 }, (_, part) => {
    let hash = 2166136261 ^ part;
    for (let index = 0; index < seed.length; index += 1) {
      hash ^= seed.charCodeAt(index);
      hash = Math.imul(hash, 16777619);
    }
    return (hash >>> 0).toString(16).padStart(8, "0");
  }).join("");
}

function createAction(
  actionId: string,
  connector: PrototypeAction["connector"],
  label: string,
  risk: PrototypeRisk,
  status: PrototypeAction["status"] = "PROPOSED",
): PrototypeAction {
  return {
    actionId,
    connector,
    label,
    target: `prototype://${connector}/${actionId}`,
    verb: connector === "smartthings" ? "COMMAND" : "PREPARE",
    parameters: { fixture: true, sequence: actionId },
    risk,
    reversible: risk !== "HIGH",
    requiredScopes:
      connector === "google"
        ? ["calendar.events.owned"]
        : connector === "smartthings"
          ? ["devices:execute"]
          : connector === "sms"
            ? [
                "prototype.sms.selected.read",
                "prototype.notifications.schedule",
              ]
            : [],
    status,
    resultSummary:
      status === "SUCCEEDED" ? "프로토타입 결과를 이미 확인했어요." : null,
  };
}

function planFor(
  caseId: string,
  version: number,
  actions: PrototypeAction[],
  risk: PrototypeRisk,
  expectedOutcome: string,
): PrototypePlan {
  return {
    version,
    hash: hashFixture(
      `${caseId}:${version}:${actions.map((item) => item.actionId).join(",")}`,
    ),
    reason: "fixture 근거를 결과 중심 계획으로 정리했어요.",
    expectedOutcome,
    risk,
    reversibility:
      risk === "HIGH"
        ? "실행 뒤 자동으로 되돌릴 수 없어 매번 별도 승인이 필요해요."
        : "프로토타입 상태에서 중지하거나 초기화할 수 있어요.",
    requiredScopes: [
      ...new Set(actions.flatMap((action) => action.requiredScopes)),
    ],
    availableGrantModes: (risk === "HIGH"
      ? ["ONCE"]
      : ["ONCE", "CONDITIONAL", "STANDING"]) as PrototypeGrantMode[],
    actions,
  };
}

function candidateFixtures(provider: PrototypeProvider) {
  const candidates: PrototypeCandidate[] = [];
  const candidateGroups: PrototypeCandidateGroup[] = [];

  groupSeeds[provider].forEach((seed, groupIndex) => {
    const candidateIds: string[] = [];
    for (let offset = 1; offset <= seed.count; offset += 1) {
      const padded = offset.toString().padStart(3, "0");
      const candidateId = `candidate-${seed.groupId}-${padded}`;
      const risk = riskFor(groupIndex * seed.count + offset, provider);
      const featuredDeadline =
        provider === "google" &&
        seed.groupId === "google-deadline" &&
        offset === 1;
      candidateIds.push(candidateId);
      candidates.push({
        candidateId,
        provider,
        primaryGroupId: seed.groupId,
        caseTypeHint: seed.caseTypeHint,
        outcome: featuredDeadline
          ? "9월 3일 프로젝트 제출 준비"
          : offset === 1
            ? seed.outcomes[0]
            : `${seed.outcomes[(offset - 1) % seed.outcomes.length]} ${padded}`,
        summary: `${seed.label}에서 확인한 ${offset}번째 근거 있는 프로토타입 후보예요.`,
        whyNow: featuredDeadline
          ? "제출 마감까지 4일 남아, 지금 정리하면 빠뜨릴 일을 줄일 수 있어요."
          : "지금 준비하면 기한이나 후속 작업을 놓치지 않을 수 있어요.",
        opportunityType: seed.groupId.includes("deadline")
          ? "DEADLINE"
          : seed.groupId.includes("follow-up")
            ? "FOLLOW_UP"
            : "APPOINTMENT",
        proposedActions: [
          createAction(
            `candidate-action-${seed.groupId}-${padded}`,
            provider,
            provider === "smartthings"
              ? "루틴 초안 준비"
              : provider === "sms"
                ? "Prepare reminder"
                : seed.groupId.includes("deadline")
                  ? "Prepare checklist"
                  : seed.groupId.includes("follow-up")
                    ? "Draft reply"
                    : "일정 알림 준비",
            risk,
          ),
        ],
        evidenceSummary:
          provider === "google"
            ? "최근 7일 Gmail·Calendar fixture 신호"
            : provider === "smartthings"
              ? "API-visible SmartThings capability fixture"
              : "온디바이스 문자 안전 분석 fixture · 원문 외부 전송 없음",
        safetyState:
          provider === "sms"
            ? offset % 5 === 0
              ? "REVIEW"
              : "ELIGIBLE"
            : null,
        safetySummary:
          provider === "sms"
            ? offset % 5 === 0
              ? "날짜 후보가 둘이라 자동 등록 전 확인이 필요해요."
              : "기관 발신 · 의심 링크/OTP/결제 문구 없음 · 미래 날짜 명확"
            : null,
        eventAt:
          provider === "sms" ? eventAtFor(groupIndex * 12 + offset) : null,
        confidence: Number(
          (0.72 + ((offset * 7 + groupIndex) % 24) / 100).toFixed(2),
        ),
        risk,
        requiresApproval:
          risk !== "LOW" || provider === "smartthings" || provider === "sms",
        tags: [...seed.tags],
        status: "VISIBLE",
        version: 1,
        createdAt: FIXTURE_EPOCH,
        updatedAt: FIXTURE_EPOCH,
      });
    }
    candidateGroups.push({
      groupId: seed.groupId,
      provider,
      label: seed.label,
      reason: seed.reason,
      icon: seed.icon,
      candidateIds,
    });
  });

  return { candidates, candidateGroups };
}

function googleCases(): PrototypeCase[] {
  const staleAction = createAction(
    "action-calendar-reschedule",
    "google",
    "변경된 시간으로 캘린더 일정 준비",
    "MEDIUM",
  );
  const stalePlan = planFor(
    "case-connected-signal-stale",
    2,
    [staleAction],
    "MEDIUM",
    "겹치는 약속을 새 시간으로 정리",
  );

  const succeeded = createAction(
    "action-task-created",
    "google",
    "제출 확인 Task 준비",
    "LOW",
    "SUCCEEDED",
  );
  const failed = createAction(
    "action-calendar-timeout",
    "google",
    "검토 일정 준비",
    "MEDIUM",
    "FAILED",
  );
  const pending = createAction(
    "action-follow-up-pending",
    "google",
    "후속 알림 준비",
    "LOW",
    "PENDING",
  );
  const failurePlan = planFor(
    "case-partial-failure",
    1,
    [succeeded, failed, pending],
    "MEDIUM",
    "완료된 준비는 유지하고 실패한 단계부터 재개",
  );

  return [
    {
      caseId: "case-connected-signal-stale",
      caseType: "CONNECTED_SIGNAL",
      goal: "변경된 회의와 기존 약속을 다시 맞출까요?",
      summary: "승인 뒤 시간이 바뀌어 이전 계획과 현재 계획을 비교해야 해요.",
      status: "DECISION_REQUIRED",
      risk: "MEDIUM",
      priority: 95,
      providers: ["google"],
      whyNow: "새 회의 시간이 기존 승인 뒤 변경됐어요.",
      nextAction: "변경된 대상과 시간을 확인하고 다시 결정해 주세요.",
      evidence: [
        {
          evidenceId: "evidence-mail-change",
          provider: "google",
          label: "회의 변경 메일",
          detail: "회의가 14:00에서 15:00로 바뀐 fixture 근거예요.",
          revision: 2,
        },
      ],
      currentPlan: stalePlan,
      timeline: [
        {
          eventId: "event-stale-detected",
          label: "중요 변경 발견",
          body: "대상 시간이 바뀌어 기존 승인을 무효화했어요.",
          state: "CURRENT",
          occurredAt: FIXTURE_EPOCH,
        },
      ],
      messages: [],
      planChange: {
        previousVersion: 1,
        previousHash: hashFixture("case-connected-signal-stale:1"),
        previousSummary: "14:00 회의 기준으로 일정 정리",
        currentSummary: "15:00 변경 회의 기준으로 일정 정리",
        reason: "회의 변경 메일의 근거 revision이 올라갔어요.",
      },
      partialFailure: null,
      policyIds: ["policy-calendar-preparation"],
      version: 3,
      createdAt: FIXTURE_EPOCH,
      updatedAt: FIXTURE_EPOCH,
    },
    {
      caseId: "case-partial-failure",
      caseType: "EXCEPTION_APPROVAL",
      goal: "끝난 준비를 유지하고 실패 지점부터 이어갈까요?",
      summary:
        "세 단계 중 하나는 끝났고, 하나는 실패했으며, 하나는 아직 시작 전이에요.",
      status: "FAILED",
      risk: "MEDIUM",
      priority: 90,
      providers: ["google"],
      whyNow:
        "프로토타입 실행 중 두 번째 단계에서 timeout fixture가 발생했어요.",
      nextAction: "실패·대기 단계만 다시 실행하거나 Case를 중지할 수 있어요.",
      evidence: [
        {
          evidenceId: "evidence-partial-fixture",
          provider: "google",
          label: "부분 실패 fixture",
          detail: "중복 없이 실패 이후 단계만 재개하는 화면을 위한 근거예요.",
          revision: 1,
        },
      ],
      currentPlan: failurePlan,
      timeline: [
        {
          eventId: "event-first-succeeded",
          label: "첫 단계 완료",
          body: "Task 준비 결과를 프로토타입 상태에서 확인했어요.",
          state: "DONE",
          occurredAt: FIXTURE_EPOCH,
        },
        {
          eventId: "event-second-failed",
          label: "두 번째 단계 실패",
          body: "Calendar timeout fixture로 실행을 멈췄어요.",
          state: "FAILED",
          occurredAt: FIXTURE_EPOCH,
        },
      ],
      messages: [],
      planChange: null,
      partialFailure: {
        succeededActionIds: [succeeded.actionId],
        failedActionIds: [failed.actionId],
        pendingActionIds: [pending.actionId],
        explanation: "완료된 첫 단계는 반복하지 않고 나머지만 재시도해요.",
      },
      policyIds: [],
      version: 2,
      createdAt: FIXTURE_EPOCH,
      updatedAt: FIXTURE_EPOCH,
    },
  ];
}

function smartThingsCases(): PrototypeCase[] {
  const action = createAction(
    "action-routine-proposal",
    "smartthings",
    "API-visible 에어컨 Routine 제안",
    "HIGH",
  );
  return [
    {
      caseId: "case-routine-discovery",
      caseType: "ROUTINE_DISCOVERY",
      goal: "반복되는 실내 온도 확인을 Routine으로 만들까요?",
      summary: "공개 API에서 보이는 capability와 상태만 이용한 제안이에요.",
      status: "DECISION_REQUIRED",
      risk: "HIGH",
      priority: 80,
      providers: ["smartthings"],
      whyNow: "비슷한 상태 확인이 반복된 fixture 패턴을 찾았어요.",
      nextAction: "트리거·조건·동작을 확인하고 이번 한 번만 승인할 수 있어요.",
      evidence: [
        {
          evidenceId: "evidence-visible-device",
          provider: "smartthings",
          label: "API-visible capability",
          detail: "읽기 가능한 온도와 제어 capability fixture만 사용했어요.",
          revision: 1,
        },
      ],
      currentPlan: planFor(
        "case-routine-discovery",
        1,
        [action],
        "HIGH",
        "명시적으로 활성화되기 전까지 실행되지 않는 Routine 제안",
      ),
      timeline: [
        {
          eventId: "event-routine-proposed",
          label: "Routine 후보",
          body: "제안만 만들었고 기기 명령은 보내지 않았어요.",
          state: "CURRENT",
          occurredAt: FIXTURE_EPOCH,
        },
      ],
      messages: [],
      planChange: null,
      partialFailure: null,
      policyIds: [],
      version: 1,
      createdAt: FIXTURE_EPOCH,
      updatedAt: FIXTURE_EPOCH,
    },
  ];
}

function smsCases(): PrototypeCase[] {
  const action = createAction(
    "action-sms-safe-reminder-policy",
    "sms",
    "안전 조건과 일치한 미래 일정만 로컬 알림으로 등록",
    "MEDIUM",
  );
  action.verb = "PROPOSE_SAFE_REMINDER_POLICY";
  return [
    {
      caseId: "case-sms-routine-discovery",
      caseType: "ROUTINE_DISCOVERY",
      goal: "안전한 문자 일정만 자동 알림으로 등록할까요?",
      summary:
        "발신자·미래 날짜·링크·OTP·결제·중복을 모두 확인하는 좁은 정책 제안이에요.",
      status: "DECISION_REQUIRED",
      risk: "MEDIUM",
      priority: 84,
      providers: ["sms"],
      whyNow: "놓치기 쉬운 미래 날짜가 담긴 문자 fixture 패턴을 찾았어요.",
      nextAction: "자동 등록 조건과 제외 조건을 확인해 주세요.",
      evidence: [
        {
          evidenceId: "evidence-sms-safe-pattern",
          provider: "sms",
          label: "반복된 미래 일정 패턴",
          detail:
            "정상 기관 발신, 명확한 미래 날짜, 의심 링크 없음 조건만 집계한 fixture예요.",
          revision: 1,
        },
      ],
      currentPlan: planFor(
        "case-sms-routine-discovery",
        1,
        [action],
        "MEDIUM",
        "고신뢰·저위험 문자 일정에만 적용되는 취소 가능한 자동 알림 정책",
      ),
      timeline: [
        {
          eventId: "event-sms-policy-proposed",
          label: "안전 정책 후보",
          body: "정책만 제안했고 문자 접근 권한이나 실제 알림은 요청하지 않았어요.",
          state: "CURRENT",
          occurredAt: FIXTURE_EPOCH,
        },
      ],
      messages: [],
      planChange: null,
      partialFailure: null,
      policyIds: [],
      version: 1,
      createdAt: FIXTURE_EPOCH,
      updatedAt: FIXTURE_EPOCH,
    },
  ];
}

export function createSmsAutomationFixture(): {
  caseItem: PrototypeCase;
  policy: PrototypePolicy;
} {
  const action = createAction(
    "action-sms-local-reminder",
    "sms",
    "병원 예약 2시간 전 로컬 알림 등록",
    "LOW",
    "SUCCEEDED",
  );
  action.verb = "SCHEDULE_LOCAL_REMINDER";
  action.parameters = {
    fixture: true,
    eventAt: "2026-08-28T05:00:00.000Z",
    remindBeforeMinutes: 120,
  };
  action.resultSummary =
    "프로토타입 로컬 알림 readback이 정책·시간과 일치했어요. 실제 알림은 만들지 않았습니다.";

  const caseItem: PrototypeCase = {
    caseId: "case-sms-auto-reminder",
    caseType: "CONNECTED_SIGNAL",
    goal: "병원 예약 문자를 놓치지 않도록 미리 알리기",
    summary:
      "사전 허용 정책과 안전 조건이 모두 맞아 사람의 추가 입력 없이 처리한 fixture예요.",
    status: "COMPLETED",
    risk: "LOW",
    priority: 68,
    providers: ["sms"],
    whyNow: "기관 발신 문자에서 2026-08-28 14:00 예약을 확인했어요.",
    nextAction: "확인된 프로토타입 결과를 기록했어요.",
    evidence: [
      {
        evidenceId: "evidence-sms-appointment-safe",
        provider: "sms",
        label: "병원 예약 안내 metadata",
        detail:
          "기관 발신 · 미래 날짜 명확 · 의심 링크/OTP/결제 문구 없음 · 원문 외부 전송 없음",
        revision: 1,
      },
    ],
    currentPlan: planFor(
      "case-sms-auto-reminder",
      1,
      [action],
      "LOW",
      "예약 2시간 전에 취소 가능한 로컬 알림 등록",
    ),
    timeline: [
      {
        eventId: "event-sms-classified",
        label: "문자 안전 분석",
        body: "규칙과 모델이 정상 기관 일정으로 합의했어요.",
        state: "DONE",
        occurredAt: FIXTURE_EPOCH,
      },
      {
        eventId: "event-sms-guardrails",
        label: "자동화 조건 확인",
        body: "미래 날짜, 의심 링크 부재, 비결제, 비OTP, 중복 아님을 확인했어요.",
        state: "DONE",
        occurredAt: FIXTURE_EPOCH,
      },
      {
        eventId: "event-sms-policy-match",
        label: "사전 허용 정책 일치",
        body: "저위험 로컬 알림 정책의 대상·시간·영향 범위 안이에요.",
        state: "DONE",
        occurredAt: FIXTURE_EPOCH,
      },
      {
        eventId: "event-sms-reminder-verified",
        label: "알림 등록 확인",
        body: "프로토타입 readback이 예상 시간과 일치했어요. 실제 알림은 없어요.",
        state: "DONE",
        occurredAt: FIXTURE_EPOCH,
      },
    ],
    messages: [],
    planChange: null,
    partialFailure: null,
    policyIds: ["policy-sms-safe-future-reminders"],
    version: 1,
    createdAt: FIXTURE_EPOCH,
    updatedAt: FIXTURE_EPOCH,
  };

  return {
    caseItem,
    policy: {
      policyId: "policy-sms-safe-future-reminders",
      title: "안전한 미래 일정 문자는 자동 알림",
      description:
        "신뢰 발신자, 명확한 미래 날짜, 의심 링크·OTP·결제 없음, 비중복 조건을 모두 만족할 때만 적용해요.",
      grantMode: "STANDING",
      riskCeiling: "LOW",
      scope: "SMS fixture metadata -> cancellable local reminder only",
      status: "ACTIVE",
      affectedCaseIds: [caseItem.caseId],
      recentUse: FIXTURE_EPOCH,
      revokedAt: null,
      version: 1,
    },
  };
}

export function createDirectDelegationFixture(): PrototypeCase {
  const action = createAction(
    "action-direct-preparation",
    "quietpilot",
    "요청 맥락과 준비 항목 정리",
    "LOW",
    "RUNNING",
  );
  return {
    caseId: "case-direct-delegation",
    caseType: "DIRECT_DELEGATION",
    goal: "이번 주 제출 준비를 한곳에 정리해 줘",
    summary: "연결 없이 요청을 받아 세부 작업을 결과 중심으로 정리하고 있어요.",
    status: "RUNNING",
    risk: "LOW",
    priority: 70,
    providers: [],
    whyNow: "사용자가 직접 위임한 요청이에요.",
    nextAction: "준비 결과를 확인한 뒤 필요한 연결만 선택할 수 있어요.",
    evidence: [
      {
        evidenceId: "evidence-direct-request",
        provider: "direct",
        label: "Direct requests",
        detail: "연결 없이 입력한 프로토타입 요청이에요.",
        revision: 1,
      },
    ],
    currentPlan: planFor(
      "case-direct-delegation",
      1,
      [action],
      "LOW",
      "제출 준비 상태와 남은 결정을 한 Case로 정리",
    ),
    timeline: [
      {
        eventId: "event-direct-running",
        label: "요청 정리 중",
        body: "외부 연결 없이 가능한 준비를 진행하고 있어요.",
        state: "CURRENT",
        occurredAt: FIXTURE_EPOCH,
      },
    ],
    messages: [],
    planChange: null,
    partialFailure: null,
    policyIds: [],
    version: 1,
    createdAt: FIXTURE_EPOCH,
    updatedAt: FIXTURE_EPOCH,
  };
}

function connectionFor(provider: PrototypeProvider): PrototypeConnection {
  return {
    provider,
    label: prototypeProviderLabel(provider),
    status: "DISCONNECTED",
    grantedScopes: [],
    lookbackDays: 7,
    scanProgress: 0,
    discoveryRevision: 0,
    accessible: [],
    unavailable: [],
    error: null,
    lastCheckedAt: null,
    lastSyncMode: null,
    nextRenewalDueAt: null,
    watchExpiresAt: null,
    watchRenewedAt: null,
    version: 1,
  };
}

export function inventoryFor(provider: PrototypeProvider): {
  accessible: PrototypeInventoryItem[];
  unavailable: PrototypeInventoryItem[];
  grantedScopes: string[];
} {
  if (provider === "google") {
    return {
      accessible: [
        {
          itemId: "gmail-recent",
          label: "Gmail from the last 7 days",
          detail: "읽기 전용 fixture 범위",
          capability: "gmail.readonly",
        },
        {
          itemId: "calendar-owned",
          label: "내 Calendar 일정",
          detail: "정확한 승인 뒤 준비되는 fixture 동작",
          capability: "calendar.events.owned",
        },
      ],
      unavailable: [
        {
          itemId: "google-admin",
          label: "조직 관리자 데이터",
          detail: "허용 범위 밖이라 접근할 수 없어요.",
          capability: "unavailable",
        },
      ],
      grantedScopes: ["gmail.readonly", "calendar.events.owned"],
    };
  }
  if (provider === "sms") {
    return {
      accessible: [
        {
          itemId: "sms-selected-history",
          label: "선택·허용된 문자 fixture 91개",
          detail: "온디바이스 분석을 가정하며 원문은 외부로 보내지 않아요.",
          capability: "prototype.sms.selected.read",
        },
        {
          itemId: "sms-future-events",
          label: "미래 일정 후보 24개",
          detail: "스팸·만료·OTP·결제·의심 링크 67개는 후보에서 제외했어요.",
          capability: "prototype.message-safety.classify",
        },
        {
          itemId: "sms-local-reminders",
          label: "로컬 알림 등록",
          detail: "사전 허용 정책과 고신뢰 조건이 모두 맞을 때만 가능해요.",
          capability: "prototype.notifications.schedule",
        },
      ],
      unavailable: [
        {
          itemId: "sms-cloud-export",
          label: "전체 대화 원문의 클라우드 전송",
          detail: "기본 처리 범위에서 제외하며 자동 학습 데이터로 쓰지 않아요.",
          capability: "unavailable",
        },
        {
          itemId: "sms-dangerous-actions",
          label: "OTP·결제·의심 링크·자동 답장",
          detail: "알림 자동 등록 대상이 아니며 필요하면 결정 필요로 격리해요.",
          capability: "unavailable",
        },
      ],
      grantedScopes: [
        "prototype.sms.selected.read",
        "prototype.notifications.schedule",
      ],
    };
  }
  return {
    accessible: [
      {
        itemId: "living-room-ac",
        label: "거실 에어컨",
        detail: "API-visible 온도와 동작 capability fixture",
        capability: "temperatureMeasurement, switch",
      },
      {
        itemId: "bedroom-ac",
        label: "침실 에어컨",
        detail: "API-visible 상태 fixture",
        capability: "temperatureMeasurement, switch",
      },
    ],
    unavailable: [
      {
        itemId: "mobile-routine",
        label: "SmartThings 모바일 Routine",
        detail: "공개 API에서 보이지 않아 제어할 수 없어요.",
        capability: "unavailable",
      },
    ],
    grantedScopes: ["devices:read", "devices:execute"],
  };
}

export function createInitialPrototypeState(
  now = FIXTURE_EPOCH,
): PrototypeState {
  return {
    schemaVersion: PROTOTYPE_SCHEMA_VERSION,
    mode: PROTOTYPE_MODE,
    externalSideEffects: false,
    revision: 0,
    nextId: 1,
    createdAt: now,
    updatedAt: now,
    loadedScenario: "LIVE",
    connections: {
      google: connectionFor("google"),
      smartthings: connectionFor("smartthings"),
      sms: connectionFor("sms"),
    },
    candidateGroups: [],
    candidates: [],
    cases: [],
    policies: [],
    suppressionRules: [],
  };
}

export function createProviderFixtures(
  provider: PrototypeProvider,
): ProviderFixtures {
  const candidates = candidateFixtures(provider);
  return {
    ...candidates,
    cases:
      provider === "google"
        ? googleCases()
        : provider === "smartthings"
          ? smartThingsCases()
          : smsCases(),
  };
}

export function createFixturePolicies(): PrototypePolicy[] {
  return [
    {
      policyId: "policy-calendar-preparation",
      title: "내 일정 준비는 조건부 허용",
      description:
        "정확한 대상과 시간이 유지될 때만 낮은 영향의 준비를 허용해요.",
      grantMode: "CONDITIONAL",
      riskCeiling: "MEDIUM",
      scope: "Google Calendar fixture preparation",
      status: "ACTIVE",
      affectedCaseIds: ["case-connected-signal-stale"],
      recentUse: null,
      revokedAt: null,
      version: 1,
    },
  ];
}

export function fixtureHash(seed: string): string {
  return hashFixture(seed);
}
