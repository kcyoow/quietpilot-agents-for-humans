import type {
  WorkspaceCandidate,
  WorkspaceCase,
  WorkspaceSnapshot,
} from "@/src/workspace/types";
import { humanizeWorkTerms } from "@/src/workspace/plainLanguage";

export type ActionQueueStance = "DECIDE" | "PREPARING" | "WAITING" | "DONE";

export type ActionQueueSource =
  "MAIL" | "CALENDAR" | "MESSAGE" | "DEVICE" | "DIRECT";

export type ActionQueueTone = "accent" | "danger" | "muted" | "warning";

export type ActionQueueItem = {
  body: string;
  caseId: string | null;
  groupId: string | null;
  id: string;
  kind: "CANDIDATE" | "CASE";
  relatedCount: number;
  source: ActionQueueSource;
  sourceDetail: string;
  stance: ActionQueueStance;
  stanceLabel: string;
  timeLabel: string;
  title: string;
  tone: ActionQueueTone;
  weight: number;
};

export const actionQueueStances: {
  key: ActionQueueStance;
  label: string;
}[] = [
  { key: "DECIDE", label: "Decide now" },
  { key: "PREPARING", label: "Preparing" },
  { key: "WAITING", label: "Waiting" },
  { key: "DONE", label: "Completed" },
];

export const actionQueueSources: {
  icon:
    | "calendar-month-outline"
    | "email-outline"
    | "home-automation"
    | "message-text-outline"
    | "message-processing-outline";
  key: ActionQueueSource;
  label: string;
}[] = [
  { icon: "email-outline", key: "MAIL", label: "Mail" },
  { icon: "calendar-month-outline", key: "CALENDAR", label: "Calendar" },
  { icon: "message-text-outline", key: "MESSAGE", label: "Messages" },
  { icon: "home-automation", key: "DEVICE", label: "Devices" },
  {
    icon: "message-processing-outline",
    key: "DIRECT",
    label: "Direct requests",
  },
];

export function buildActionQueue(
  snapshot: WorkspaceSnapshot | null,
): ActionQueueItem[] {
  if (!snapshot) return [];

  const candidates = snapshot.candidates
    .filter(
      (candidate) =>
        candidate.status === "VISIBLE" && candidate.proposedActions.length > 0,
    )
    .map(candidateQueueItem);
  const cases = snapshot.cases.map(caseQueueItem);

  return [...candidates, ...cases].sort(
    (left, right) =>
      right.weight - left.weight ||
      right.timeLabel.localeCompare(left.timeLabel),
  );
}

export function matchesActionQueueSearch(
  item: ActionQueueItem,
  search: string,
): boolean {
  const query = search.trim().toLocaleLowerCase("ko");
  if (!query) return true;
  return [item.title, item.body, item.sourceDetail, item.stanceLabel]
    .join(" ")
    .toLocaleLowerCase("ko")
    .includes(query);
}

function candidateQueueItem(candidate: WorkspaceCandidate): ActionQueueItem {
  const source = candidateSource(candidate);
  const actionVerb = candidate.proposedActions[0]?.verb ?? "";
  const stance = candidateStance(actionVerb);
  const relatedCount = evidenceCount(candidate.evidenceSummary);
  return {
    body: candidate.whyNow,
    caseId: null,
    groupId: candidate.primaryGroupId,
    id: `candidate:${candidate.candidateId}`,
    kind: "CANDIDATE",
    relatedCount,
    source,
    sourceDetail: sourceDetail(source, relatedCount),
    stance: "DECIDE",
    stanceLabel: stance.label,
    timeLabel: eventLabel(candidate.eventAt ?? candidate.updatedAt),
    title: candidate.outcome,
    tone: stance.tone,
    weight:
      1_000 +
      riskWeight(candidate.risk) +
      Math.round(candidate.confidence * 10),
  };
}

function caseQueueItem(item: WorkspaceCase): ActionQueueItem {
  const source = caseSource(item);
  const stance = caseStance(item.status);
  return {
    body: humanActionCopy(item.nextAction || item.whyNow || item.summary),
    caseId: item.caseId,
    groupId: null,
    id: `case:${item.caseId}`,
    kind: "CASE",
    relatedCount: Math.max(item.evidence.length, 1),
    source,
    sourceDetail: sourceDetail(source, Math.max(item.evidence.length, 1)),
    stance: stance.stance,
    stanceLabel: stance.label,
    timeLabel: eventLabel(item.updatedAt),
    title: item.goal,
    tone: stance.tone,
    weight: item.priority + (stance.stance === "DECIDE" ? 500 : 0),
  };
}

function humanActionCopy(value: string): string {
  return humanizeWorkTerms(value)
    .replace(
      /fixture 근거를 결과 중심 계획으로 정리했어요\./gi,
      "A plan was prepared from example sources.",
    )
    .replace(/프로토타입 상태/gi, "Current tasks")
    .replace(/프로토타입\s+(.+?)\s+readback이/gi, "$1 results")
    .replace(/프로토타입\s+readback이/gi, "results")
    .replace(/readback이/gi, "results")
    .replace(/readback으로/gi, "using results")
    .replace(/fixture 근거/gi, "example sources")
    .replace(/API-visible/gi, "available from your connections")
    .replace(/readback/gi, "Results")
    .replace(/프로토타입/gi, "Example")
    .replace(/fixture/gi, "Example data")
    .replace(/확인된\s+확인(?:된)?\s+결과/g, "Results");
}

function candidateSource(candidate: WorkspaceCandidate): ActionQueueSource {
  if (candidate.provider === "smartthings") return "DEVICE";
  if (candidate.provider === "sms") return "MESSAGE";
  const hints = [candidate.primaryGroupId, ...candidate.tags]
    .join(" ")
    .toLocaleLowerCase("ko");
  return /calendar|캘린더|appointment|약속|일정/.test(hints)
    ? "CALENDAR"
    : "MAIL";
}

function evidenceCount(summary: string): number {
  const matched = summary.match(/(\d+)\s*(?:개|items?\b)/);
  return matched ? Math.max(Number(matched[1]), 1) : 1;
}

function caseSource(item: WorkspaceCase): ActionQueueSource {
  if (item.providers.includes("smartthings")) return "DEVICE";
  if (item.providers.includes("sms")) return "MESSAGE";
  if (item.providers.length === 0 || item.caseType === "DIRECT_DELEGATION") {
    return "DIRECT";
  }
  const hints = [
    ...item.evidence.flatMap((evidence) => [evidence.label, evidence.detail]),
    ...(item.currentPlan?.actions.flatMap((action) => [
      action.label,
      action.target,
      action.verb,
    ]) ?? []),
  ]
    .join(" ")
    .toLocaleLowerCase("ko");
  return /calendar|캘린더|일정/.test(hints) ? "CALENDAR" : "MAIL";
}

function candidateStance(verb: string): {
  label: string;
  tone: ActionQueueTone;
} {
  if (/reply/i.test(verb)) return { label: "Review response", tone: "warning" };
  if (/reminder|calendar|schedule/i.test(verb)) {
    return { label: "Review event", tone: "accent" };
  }
  return { label: "Review plan", tone: "accent" };
}

function caseStance(status: WorkspaceCase["status"]): {
  label: string;
  stance: ActionQueueStance;
  tone: ActionQueueTone;
} {
  const presentations = {
    APPROVED: { label: "Approved", stance: "PREPARING", tone: "accent" },
    COMPLETED: { label: "Completed", stance: "DONE", tone: "muted" },
    DECISION_REQUIRED: {
      label: "View details",
      stance: "DECIDE",
      tone: "warning",
    },
    FAILED: { label: "Review issue", stance: "DECIDE", tone: "danger" },
    PAUSED: { label: "Waiting", stance: "WAITING", tone: "muted" },
    PERMISSION_REVOKED: {
      label: "Check connection",
      stance: "WAITING",
      tone: "warning",
    },
    PREPARING: { label: "Preparing", stance: "PREPARING", tone: "accent" },
    QUEUED: { label: "Preparing", stance: "PREPARING", tone: "accent" },
    RUNNING: { label: "In progress", stance: "PREPARING", tone: "accent" },
    STOPPED: { label: "Stopped", stance: "DONE", tone: "muted" },
    VERIFYING: { label: "View result", stance: "PREPARING", tone: "accent" },
  } satisfies Record<
    WorkspaceCase["status"],
    {
      label: string;
      stance: ActionQueueStance;
      tone: ActionQueueTone;
    }
  >;
  return presentations[status];
}

function sourceDetail(source: ActionQueueSource, count: number): string {
  return {
    CALENDAR: count > 1 ? `Related event ${count} items` : "Calendar",
    DEVICE:
      count > 1 ? `Related information ${count} items` : "Device information",
    DIRECT: "Direct requests",
    MAIL: count > 1 ? `Relevant mail ${count} items` : "Mail",
    MESSAGE: count > 1 ? `Related message ${count} items` : "Messages",
  }[source];
}

function eventLabel(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Last";
  return date.toLocaleDateString("en-US", { day: "numeric", month: "short" });
}

function riskWeight(risk: WorkspaceCandidate["risk"]): number {
  return { HIGH: 30, LOW: 10, MEDIUM: 20 }[risk];
}
