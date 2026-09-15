import type {
  LiveCandidate,
  LiveCaseDetail,
  LiveCaseSummary,
  LiveSuggestionGroup,
} from "@/src/api/productApi";
import type { PrototypeAction, PrototypeProvider } from "@/src/prototype/types";
import type {
  WorkspaceCandidate,
  WorkspaceCandidateGroup,
  WorkspaceCase,
} from "@/src/workspace/types";
import { systemCopy } from "@/src/workspace/plainLanguage";

export function isOnceCalendarPlan(plan: WorkspaceCase["currentPlan"]) {
  return Boolean(
    plan &&
    plan.actions.length === 1 &&
    plan.actions[0].connector === "google" &&
    plan.actions[0].verb === "calendar_event_create" &&
    plan.availableGrantModes.length === 1 &&
    plan.availableGrantModes[0] === "ONCE",
  );
}

export function canRetryLiveCalendar(item: WorkspaceCase) {
  return (
    isOnceCalendarPlan(item.currentPlan) &&
    ["FAILED", "VERIFYING", "QUEUED"].includes(item.status) &&
    !item.currentPlan!.actions.every(
      (action) => action.status === "SUCCEEDED" && action.verified,
    )
  );
}

export function liveCandidateToWorkspace(
  record: LiveCandidate,
): WorkspaceCandidate {
  return {
    candidateId: record.candidate_id,
    caseTypeHint: record.source_type,
    confidence: record.confidence,
    createdAt: record.created_at,
    dataSource: "LIVE",
    eventAt: null,
    evidenceSummary: `Gmail sources ${record.evidence_refs.length} items`,
    mailProfileVersion: record.mail_profile_version ?? null,
    mailScanId: record.mail_scan_id ?? null,
    mailDerived: record.evidence_refs.some((ref) => ref.startsWith("gmail:")),
    outcome: record.outcome,
    opportunityType: record.opportunity_type,
    primaryGroupId: record.primary_group_id,
    proposedActions: record.proposed_actions.map((action, index) => ({
      actionId: `${record.candidate_id}-proposal-${index + 1}`,
      connector: actionProvider(action.connector),
      label: candidateActionLabel(action.verb),
      parameters: Object.fromEntries(
        Object.entries(action.parameters).filter(
          (entry): entry is [string, string | number | boolean] =>
            typeof entry[1] === "string" ||
            typeof entry[1] === "number" ||
            typeof entry[1] === "boolean",
        ),
      ),
      requiredScopes: action.required_scopes,
      resultSummary: null,
      reversible: action.reversible,
      risk: action.risk,
      status: "PROPOSED",
      target: action.target_resource,
      verb: action.verb,
    })),
    provider: record.provider,
    requiresApproval: record.risk !== "LOW",
    risk: record.risk,
    safetyState: null,
    safetySummary: null,
    status: record.status,
    summary: record.summary,
    tags: record.tags,
    updatedAt: record.updated_at,
    version: record.version,
    whyNow: record.why_now,
  };
}

function candidateActionLabel(verb: string): string {
  return (
    {
      prepare_reminder: "Prepare reminder",
      prepare_reply: "Draft reply",
      prepare_task: "Prepare checklist",
    }[verb] ?? "Prepare next steps"
  );
}

export function liveGroupToWorkspace(
  record: LiveSuggestionGroup,
  candidates: WorkspaceCandidate[],
): WorkspaceCandidateGroup {
  return {
    candidateIds: candidates
      .filter((item) => item.primaryGroupId === record.group_id)
      .map((item) => item.candidateId),
    dataSource: "LIVE",
    groupId: record.group_id,
    icon: "email-outline",
    label: record.label,
    provider: "google",
    reason: record.reason,
  };
}

export function liveCaseSummaryToWorkspace(
  record: LiveCaseSummary,
): WorkspaceCase {
  return {
    caseId: record.case_id,
    caseType: record.case_type,
    createdAt: record.updated_at,
    currentPlan: null,
    dataSource: "LIVE",
    evidence: [],
    goal: record.goal,
    messages: [],
    nextAction: systemCopy(record.next_action ?? "Checking the latest status."),
    partialFailure: null,
    planChange: null,
    policyIds: [],
    priority: record.priority,
    providers: record.providers,
    risk: record.risk,
    status: record.status,
    summary: record.summary,
    timeline: [],
    updatedAt: record.updated_at,
    version: record.version,
    whyNow: systemCopy(
      record.why_now ?? "Prepared from new source information.",
    ),
  };
}

export function liveCaseDetailToWorkspace(
  record: LiveCaseDetail,
): WorkspaceCase {
  const summary = liveCaseSummaryToWorkspace(record);
  return {
    ...summary,
    currentPlan: record.plan
      ? {
          actions: record.plan.actions.map((action) => {
            const result = record.actions.find(
              (item) => item.action_id === action.action_id,
            );
            return liveActionToPrototype(
              result
                ? {
                    ...action,
                    status: result.status,
                    result_summary: result.result_summary,
                    result_ref: result.result_ref,
                    html_url: result.html_url,
                    verified: result.verified,
                    error_code: result.error_code,
                  }
                : action,
            );
          }),
          availableGrantModes: record.plan.available_grant_modes,
          localPreparationStatus: record.plan.local_preparation_status,
          expectedOutcome: record.plan.expected_outcome,
          hash: record.plan.hash,
          reason: systemCopy(record.plan.reason),
          requiredScopes: record.plan.required_scopes,
          reversibility: record.plan.reversibility,
          risk: record.plan.risk,
          version: record.plan.version,
        }
      : null,
    evidence: record.evidence.map((item) => ({
      detail: item.detail,
      evidenceId: item.evidence_id,
      label: item.label,
      provider: evidenceProvider(item.provider),
      revision: item.revision,
    })),
    messages: record.messages.map((item) => ({
      author: item.author,
      createdAt: item.created_at,
      messageId: item.message_id,
      text: item.author === "USER" ? item.text : systemCopy(item.text),
    })),
    timeline: record.timeline.map((item) => ({
      body: systemCopy(item.body),
      eventId: item.event_id,
      label: systemCopy(item.label),
      occurredAt: item.occurred_at,
      state: item.state,
    })),
  };
}

function liveActionToPrototype(
  action: LiveCaseDetail["actions"][number],
): PrototypeAction {
  return {
    actionId: action.action_id,
    connector: actionProvider(action.connector),
    label: systemCopy(action.label),
    parameters: Object.fromEntries(
      Object.entries(action.parameters).map(([key, value]) => [
        key,
        typeof value === "string" ||
        typeof value === "number" ||
        typeof value === "boolean"
          ? value
          : JSON.stringify(value),
      ]),
    ),
    requiredScopes: action.required_scopes,
    resultSummary: action.result_summary
      ? systemCopy(action.result_summary)
      : action.result_summary,
    resultRef: action.result_ref,
    htmlUrl: action.html_url,
    verified: action.verified,
    errorCode: action.error_code,
    reversible: action.reversible,
    risk: action.risk,
    status: actionStatus(action.status),
    target: action.target,
    verb: action.verb,
  };
}

function actionStatus(
  value: LiveCaseDetail["actions"][number]["status"],
): PrototypeAction["status"] {
  if (value === "BLOCKED") return "FAILED";
  if (value === "APPROVED" || value === "QUEUED") {
    return "PENDING";
  }
  return value;
}

function evidenceProvider(value: string): PrototypeProvider | "direct" {
  if (value === "google" || value === "smartthings" || value === "sms") {
    return value;
  }
  return "direct";
}

function actionProvider(value: string): PrototypeProvider | "quietpilot" {
  if (value === "google" || value === "smartthings" || value === "sms") {
    return value;
  }
  return "quietpilot";
}
