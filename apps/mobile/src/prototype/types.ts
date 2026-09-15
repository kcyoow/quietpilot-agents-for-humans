export const PROTOTYPE_MODE = "prototype" as const;
export const PROTOTYPE_SCHEMA_VERSION = 3 as const;

export type PrototypeProvider = "google" | "smartthings" | "sms";
export type PrototypeScenario =
  "LIVE" | "EMPTY" | "ONE" | "GOOGLE" | "SMARTTHINGS" | "SMS" | "FULL";
export type PrototypeRisk = "LOW" | "MEDIUM" | "HIGH";
export type PrototypeSafetyState = "ELIGIBLE" | "REVIEW" | "BLOCKED";
export type PrototypeGrantMode = "ONCE" | "CONDITIONAL" | "STANDING";
export type PrototypeCaseType =
  | "CONNECTED_SIGNAL"
  | "DIRECT_DELEGATION"
  | "ROUTINE_DISCOVERY"
  | "EXCEPTION_APPROVAL";
export type PrototypeCaseStatus =
  | "PREPARING"
  | "DECISION_REQUIRED"
  | "APPROVED"
  | "QUEUED"
  | "RUNNING"
  | "VERIFYING"
  | "COMPLETED"
  | "FAILED"
  | "PAUSED"
  | "PERMISSION_REVOKED"
  | "STOPPED";
export type PrototypeActionStatus =
  | "PROPOSED"
  | "RUNNING"
  | "VERIFYING"
  | "SUCCEEDED"
  | "FAILED"
  | "PENDING"
  | "CANCELLED";
export type PrototypeCandidateStatus =
  "VISIBLE" | "HIDDEN" | "SUPPRESSED" | "CONVERTED" | "EXPIRED";
export type PrototypeConnectionStatus =
  | "DISCONNECTED"
  | "CONNECTING"
  | "SCANNING"
  | "CONNECTED"
  | "REVOKING"
  | "ERROR";

export type PrototypeInventoryItem = {
  itemId: string;
  label: string;
  detail: string;
  capability: string;
};

export type PrototypeConnection = {
  provider: PrototypeProvider;
  label: string;
  status: PrototypeConnectionStatus;
  grantedScopes: string[];
  lookbackDays: number;
  scanProgress: number;
  discoveryRevision: number | null;
  accessible: PrototypeInventoryItem[];
  unavailable: PrototypeInventoryItem[];
  error: string | null;
  lastCheckedAt: string | null;
  lastSyncMode: "INITIAL_7_DAY" | "INCREMENTAL" | "BOUNDED_FULL_SYNC" | null;
  nextRenewalDueAt: string | null;
  watchExpiresAt: string | null;
  watchRenewedAt: string | null;
  version: number;
};

export type PrototypeCandidateGroup = {
  groupId: string;
  provider: PrototypeProvider;
  label: string;
  reason: string;
  icon: string;
  candidateIds: string[];
};

export type PrototypeCandidate = {
  candidateId: string;
  provider: PrototypeProvider;
  primaryGroupId: string;
  caseTypeHint: "CONNECTED_SIGNAL" | "ROUTINE_DISCOVERY";
  outcome: string;
  summary: string;
  whyNow: string;
  opportunityType: "APPOINTMENT" | "DEADLINE" | "FOLLOW_UP";
  proposedActions: PrototypeAction[];
  evidenceSummary: string;
  safetyState: PrototypeSafetyState | null;
  safetySummary: string | null;
  eventAt: string | null;
  confidence: number;
  risk: PrototypeRisk;
  requiresApproval: boolean;
  tags: string[];
  status: PrototypeCandidateStatus;
  version: number;
  createdAt: string;
  updatedAt: string;
};

export type PrototypeEvidence = {
  evidenceId: string;
  provider: PrototypeProvider | "direct";
  label: string;
  detail: string;
  revision: number;
};

export type PrototypeAction = {
  actionId: string;
  connector: PrototypeProvider | "quietpilot";
  label: string;
  target: string;
  verb: string;
  parameters: Record<string, string | number | boolean>;
  risk: PrototypeRisk;
  reversible: boolean;
  requiredScopes: string[];
  status: PrototypeActionStatus;
  resultSummary: string | null;
  resultRef?: string | null;
  htmlUrl?: string | null;
  verified?: boolean;
  errorCode?: string | null;
};

export type PrototypePlan = {
  version: number;
  localPreparationStatus?: "READY" | "NO_ACTION" | "NEEDS_INPUT";
  hash: string;
  reason: string;
  expectedOutcome: string;
  risk: PrototypeRisk;
  reversibility: string;
  requiredScopes: string[];
  availableGrantModes: PrototypeGrantMode[];
  actions: PrototypeAction[];
};

export type PrototypePlanChange = {
  previousVersion: number;
  previousHash: string;
  previousSummary: string;
  currentSummary: string;
  reason: string;
};

export type PrototypePartialFailure = {
  succeededActionIds: string[];
  failedActionIds: string[];
  pendingActionIds: string[];
  explanation: string;
};

export type PrototypeTimelineItem = {
  eventId: string;
  label: string;
  body: string;
  state: "DONE" | "CURRENT" | "PENDING" | "FAILED";
  occurredAt: string;
};

export type PrototypeMessage = {
  messageId: string;
  author: "USER" | "QUIETPILOT";
  text: string;
  createdAt: string;
};

export type PrototypeCase = {
  caseId: string;
  caseType: PrototypeCaseType;
  goal: string;
  summary: string;
  status: PrototypeCaseStatus;
  risk: PrototypeRisk;
  priority: number;
  providers: PrototypeProvider[];
  whyNow: string;
  nextAction: string;
  evidence: PrototypeEvidence[];
  currentPlan: PrototypePlan | null;
  timeline: PrototypeTimelineItem[];
  messages: PrototypeMessage[];
  planChange: PrototypePlanChange | null;
  partialFailure: PrototypePartialFailure | null;
  policyIds: string[];
  version: number;
  createdAt: string;
  updatedAt: string;
};

export type PrototypePolicy = {
  policyId: string;
  title: string;
  description: string;
  grantMode: PrototypeGrantMode;
  riskCeiling: Exclude<PrototypeRisk, "HIGH">;
  scope: string;
  status: "ACTIVE" | "REVOKED";
  affectedCaseIds: string[];
  recentUse: string | null;
  revokedAt: string | null;
  version: number;
};

export type PrototypeSuppressionRule = {
  ruleId: string;
  sourceCandidateId: string;
  provider: PrototypeProvider;
  groupId: string;
  explanation: string;
  affectedCandidateIds: string[];
  createdAt: string;
};

export type PrototypeState = {
  schemaVersion: typeof PROTOTYPE_SCHEMA_VERSION;
  mode: typeof PROTOTYPE_MODE;
  externalSideEffects: false;
  revision: number;
  nextId: number;
  createdAt: string;
  updatedAt: string;
  loadedScenario: PrototypeScenario;
  connections: Record<PrototypeProvider, PrototypeConnection>;
  candidateGroups: PrototypeCandidateGroup[];
  candidates: PrototypeCandidate[];
  cases: PrototypeCase[];
  policies: PrototypePolicy[];
  suppressionRules: PrototypeSuppressionRule[];
};

export type PrototypeCandidateQuery = {
  groupId?: string;
  includeInactive?: boolean;
  provider?: PrototypeProvider;
  risk?: PrototypeRisk;
  search?: string;
};

export type PrototypeCaseBucket = "active" | "history" | "all";

export type PrototypeApprovalInput = {
  planVersion: number;
  planHash: string;
  grantMode: PrototypeGrantMode;
};

export type PrototypeStorage = {
  getItem(key: string): Promise<string | null>;
  setItem(key: string, value: string): Promise<void>;
  removeItem(key: string): Promise<void>;
};

export type PrototypeClock = {
  now(): Date;
};

export type PrototypeBackendOptions = {
  clock?: PrototypeClock;
  storage: PrototypeStorage;
  storageKey?: string;
};

export type PrototypeMutationResult<T> = {
  value: T;
  snapshot: PrototypeState;
};

export type PrototypeErrorCode =
  | "INVALID_INPUT"
  | "INVALID_TRANSITION"
  | "NOT_FOUND"
  | "PLAN_CHANGED"
  | "MIXED_SELECTION"
  | "UNSUPPORTED_GRANT";

export class PrototypeBackendError extends Error {
  constructor(
    readonly code: PrototypeErrorCode,
    message: string,
  ) {
    super(message);
    this.name = "PrototypeBackendError";
  }
}
