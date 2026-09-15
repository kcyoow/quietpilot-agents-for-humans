import {
  createDirectDelegationFixture,
  createFixturePolicies,
  createInitialPrototypeState,
  createProviderFixtures,
  createSmsAutomationFixture,
  fixtureHash,
  inventoryFor,
} from "@/src/prototype/fixtures";
import { PROTOTYPE_PROVIDER_ORDER } from "@/src/prototype/providerMeta";
import {
  PROTOTYPE_MODE,
  PROTOTYPE_SCHEMA_VERSION,
  PrototypeBackendError,
  type PrototypeAction,
  type PrototypeApprovalInput,
  type PrototypeBackendOptions,
  type PrototypeCandidate,
  type PrototypeCandidateGroup,
  type PrototypeCandidateQuery,
  type PrototypeCase,
  type PrototypeCaseBucket,
  type PrototypeConnection,
  type PrototypeGrantMode,
  type PrototypeMutationResult,
  type PrototypePolicy,
  type PrototypeProvider,
  type PrototypeRisk,
  type PrototypeScenario,
  type PrototypeState,
  type PrototypeSuppressionRule,
} from "@/src/prototype/types";

export const DEFAULT_PROTOTYPE_STORAGE_KEY = "quietpilot.prototype-state.v3";

const terminalStatuses = new Set<PrototypeCase["status"]>([
  "COMPLETED",
  "STOPPED",
]);

const defaultClock = {
  now: () => new Date(),
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function isPrototypeState(value: unknown): value is PrototypeState {
  if (!value || typeof value !== "object") return false;
  const state = value as Partial<PrototypeState>;
  return (
    state.schemaVersion === PROTOTYPE_SCHEMA_VERSION &&
    state.mode === PROTOTYPE_MODE &&
    state.externalSideEffects === false &&
    Array.isArray(state.candidates) &&
    Array.isArray(state.candidateGroups) &&
    Array.isArray(state.cases) &&
    Array.isArray(state.policies) &&
    Boolean(state.connections?.google) &&
    Boolean(state.connections?.smartthings) &&
    Boolean(state.connections?.sms)
  );
}

function uniqueById<T>(items: T[], key: (item: T) => string): T[] {
  const seen = new Set<string>();
  return items.filter((item) => {
    const id = key(item);
    if (seen.has(id)) return false;
    seen.add(id);
    return true;
  });
}

function riskRank(risk: PrototypeRisk): number {
  return risk === "HIGH" ? 3 : risk === "MEDIUM" ? 2 : 1;
}

function highestRisk(candidates: PrototypeCandidate[]): PrototypeRisk {
  return candidates.reduce<PrototypeRisk>(
    (current, candidate) =>
      riskRank(candidate.risk) > riskRank(current) ? candidate.risk : current,
    "LOW",
  );
}

function requireText(value: string, label: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 10_000) {
    throw new PrototypeBackendError(
      "INVALID_INPUT",
      `${label} must contain 1 to 10,000 characters.`,
    );
  }
  return trimmed;
}

function appendTimeline(
  state: PrototypeState,
  item: PrototypeCase,
  label: string,
  body: string,
  eventState: PrototypeCase["timeline"][number]["state"],
  now: string,
): void {
  item.timeline.push({
    eventId: `event-${state.nextId++}`,
    label,
    body,
    state: eventState,
    occurredAt: now,
  });
}

function findCase(state: PrototypeState, caseId: string): PrototypeCase {
  const item = state.cases.find((candidate) => candidate.caseId === caseId);
  if (!item) {
    throw new PrototypeBackendError("NOT_FOUND", `Task not found: ${caseId}`);
  }
  return item;
}

function findCandidate(
  state: PrototypeState,
  candidateId: string,
): PrototypeCandidate {
  const item = state.candidates.find(
    (candidate) => candidate.candidateId === candidateId,
  );
  if (!item) {
    throw new PrototypeBackendError(
      "NOT_FOUND",
      `Suggestion not found: ${candidateId}`,
    );
  }
  return item;
}

function findPolicy(state: PrototypeState, policyId: string): PrototypePolicy {
  const item = state.policies.find((policy) => policy.policyId === policyId);
  if (!item) {
    throw new PrototypeBackendError(
      "NOT_FOUND",
      `Permission not found: ${policyId}`,
    );
  }
  return item;
}

function installProviderFixtures(
  state: PrototypeState,
  provider: PrototypeProvider,
): void {
  const fixtures = createProviderFixtures(provider);
  state.candidateGroups = uniqueById(
    [...state.candidateGroups, ...fixtures.candidateGroups],
    (item) => item.groupId,
  );
  state.candidates = uniqueById(
    [...state.candidates, ...fixtures.candidates],
    (item) => item.candidateId,
  );
  state.cases = uniqueById(
    [...state.cases, ...fixtures.cases],
    (item) => item.caseId,
  );
  if (provider === "google") {
    state.policies = uniqueById(
      [...state.policies, ...createFixturePolicies()],
      (item) => item.policyId,
    );
  }
}

function activateProvider(
  state: PrototypeState,
  provider: PrototypeProvider,
  now: string,
): void {
  const inventory = inventoryFor(provider);
  const connection = state.connections[provider];
  connection.status = "CONNECTED";
  connection.scanProgress = 100;
  connection.discoveryRevision = 1;
  connection.accessible = inventory.accessible;
  connection.unavailable = inventory.unavailable;
  connection.grantedScopes = inventory.grantedScopes;
  connection.error = null;
  connection.lastCheckedAt = now;
  connection.version += 1;
  installProviderFixtures(state, provider);
}

function resetInto(state: PrototypeState, replacement: PrototypeState): void {
  Object.assign(state, replacement);
}

function availableGrantModes(risk: PrototypeRisk): PrototypeGrantMode[] {
  return risk === "HIGH" ? ["ONCE"] : ["ONCE", "CONDITIONAL", "STANDING"];
}

function filterCandidates(
  state: PrototypeState,
  query: PrototypeCandidateQuery,
): PrototypeCandidate[] {
  const search = query.search?.trim().toLocaleLowerCase("ko") ?? "";
  return state.candidates
    .filter(
      (candidate) =>
        state.connections[candidate.provider].status === "CONNECTED",
    )
    .filter(
      (candidate) => query.includeInactive || candidate.status === "VISIBLE",
    )
    .filter(
      (candidate) =>
        !query.groupId || candidate.primaryGroupId === query.groupId,
    )
    .filter(
      (candidate) => !query.provider || candidate.provider === query.provider,
    )
    .filter((candidate) => !query.risk || candidate.risk === query.risk)
    .filter(
      (candidate) =>
        !search ||
        [candidate.outcome, candidate.summary, ...candidate.tags]
          .join(" ")
          .toLocaleLowerCase("ko")
          .includes(search),
    );
}

function planActionForDirectCase(actionId: string): PrototypeAction {
  return {
    actionId,
    connector: "quietpilot",
    label: "Review the request and preparation steps",
    target: `prototype://quietpilot/${actionId}`,
    verb: "PREPARE",
    parameters: { fixture: true },
    risk: "LOW",
    reversible: true,
    requiredScopes: [],
    status: "PROPOSED",
    resultSummary: null,
  };
}

function refreshCase(item: PrototypeCase, now: string): void {
  item.version += 1;
  item.updatedAt = now;
}

export class PrototypeBackend {
  readonly mode = PROTOTYPE_MODE;
  readonly externalSideEffects = false as const;

  private readonly clock;
  private readonly storage;
  private readonly storageKey;
  private state: PrototypeState | null = null;
  private loadPromise: Promise<PrototypeState> | null = null;
  private writeQueue: Promise<void> = Promise.resolve();

  constructor(options: PrototypeBackendOptions) {
    this.clock = options.clock ?? defaultClock;
    this.storage = options.storage;
    this.storageKey = options.storageKey ?? DEFAULT_PROTOTYPE_STORAGE_KEY;
  }

  async getSnapshot(): Promise<PrototypeState> {
    await this.writeQueue;
    return clone(await this.loadState());
  }

  async listConnections(): Promise<PrototypeConnection[]> {
    const state = await this.getSnapshot();
    return PROTOTYPE_PROVIDER_ORDER.map(
      (provider) => state.connections[provider],
    );
  }

  async listCandidates(
    query: PrototypeCandidateQuery = {},
  ): Promise<PrototypeCandidate[]> {
    const state = await this.getSnapshot();
    return filterCandidates(state, query);
  }

  async listCandidateGroups(
    query: PrototypeCandidateQuery = {},
  ): Promise<PrototypeCandidateGroup[]> {
    const state = await this.getSnapshot();
    const visible = filterCandidates(state, query);
    const visibleIds = new Set(
      visible.map((candidate) => candidate.candidateId),
    );
    return state.candidateGroups
      .map((group) => ({
        ...group,
        candidateIds: group.candidateIds.filter((candidateId) =>
          visibleIds.has(candidateId),
        ),
      }))
      .filter((group) => group.candidateIds.length > 0);
  }

  async listCases(
    bucket: PrototypeCaseBucket = "active",
  ): Promise<PrototypeCase[]> {
    const state = await this.getSnapshot();
    return state.cases
      .filter((item) => {
        if (bucket === "all") return true;
        const terminal = terminalStatuses.has(item.status);
        return bucket === "history" ? terminal : !terminal;
      })
      .sort((left, right) => {
        const leftDecision = left.status === "DECISION_REQUIRED" ? 1 : 0;
        const rightDecision = right.status === "DECISION_REQUIRED" ? 1 : 0;
        return rightDecision - leftDecision || right.priority - left.priority;
      });
  }

  async getCase(caseId: string): Promise<PrototypeCase | null> {
    const state = await this.getSnapshot();
    return state.cases.find((item) => item.caseId === caseId) ?? null;
  }

  async listPolicies(): Promise<PrototypePolicy[]> {
    const state = await this.getSnapshot();
    return state.policies;
  }

  async loadScenario(
    scenario: PrototypeScenario,
  ): Promise<PrototypeMutationResult<PrototypeScenario>> {
    return this.mutate((state, now) => {
      resetInto(state, createInitialPrototypeState(now));
      state.loadedScenario = scenario;
      if (scenario === "ONE" || scenario === "GOOGLE" || scenario === "FULL") {
        activateProvider(state, "google", now);
      }
      if (scenario === "ONE") {
        const candidate = state.candidates.find(
          (item) => item.primaryGroupId === "google-deadline",
        );
        const group = state.candidateGroups.find(
          (item) => item.groupId === candidate?.primaryGroupId,
        );
        state.candidates = candidate ? [candidate] : [];
        state.candidateGroups =
          candidate && group
            ? [{ ...group, candidateIds: [candidate.candidateId] }]
            : [];
        state.cases = [];
        state.policies = [];
      }
      if (scenario === "SMARTTHINGS" || scenario === "FULL") {
        activateProvider(state, "smartthings", now);
      }
      if (scenario === "SMS" || scenario === "FULL") {
        activateProvider(state, "sms", now);
        const smsAutomation = createSmsAutomationFixture();
        state.cases = uniqueById(
          [...state.cases, smsAutomation.caseItem],
          (item) => item.caseId,
        );
        state.policies = uniqueById(
          [...state.policies, smsAutomation.policy],
          (item) => item.policyId,
        );
      }
      if (scenario === "FULL") {
        state.cases.push(createDirectDelegationFixture());
      }
      return scenario;
    });
  }

  async startConnection(
    provider: PrototypeProvider,
  ): Promise<PrototypeMutationResult<PrototypeConnection>> {
    return this.mutate((state, now) => {
      const connection = state.connections[provider];
      if (connection.status === "CONNECTED") return connection;
      connection.status = "CONNECTING";
      connection.scanProgress = 0;
      connection.discoveryRevision = 0;
      connection.error = null;
      connection.lastCheckedAt = now;
      connection.version += 1;
      return connection;
    });
  }

  async advanceConnection(
    provider: PrototypeProvider,
  ): Promise<PrototypeMutationResult<PrototypeConnection>> {
    return this.mutate((state, now) => {
      const connection = state.connections[provider];
      if (connection.status === "CONNECTING") {
        connection.status = "SCANNING";
        connection.scanProgress = 35;
        connection.lastCheckedAt = now;
        connection.version += 1;
        return connection;
      }
      if (connection.status === "SCANNING") {
        activateProvider(state, provider, now);
        return state.connections[provider];
      }
      if (connection.status === "CONNECTED") return connection;
      throw new PrototypeBackendError(
        "INVALID_TRANSITION",
        `${provider} connection is ${connection.status} and cannot continue.`,
      );
    });
  }

  async failConnection(
    provider: PrototypeProvider,
    message: string,
  ): Promise<PrototypeMutationResult<PrototypeConnection>> {
    const safeMessage = requireText(message, "Connection error");
    return this.mutate((state, now) => {
      const connection = state.connections[provider];
      connection.status = "ERROR";
      connection.scanProgress = 0;
      connection.error = safeMessage;
      connection.lastCheckedAt = now;
      connection.version += 1;
      return connection;
    });
  }

  async disconnectConnection(
    provider: PrototypeProvider,
  ): Promise<PrototypeMutationResult<PrototypeConnection>> {
    return this.mutate((state, now) => {
      const connection = state.connections[provider];
      connection.status = "DISCONNECTED";
      connection.scanProgress = 0;
      connection.discoveryRevision = 0;
      connection.grantedScopes = [];
      connection.error = null;
      connection.lastCheckedAt = now;
      connection.version += 1;

      for (const item of state.cases) {
        if (
          item.providers.includes(provider) &&
          !terminalStatuses.has(item.status)
        ) {
          item.status = "PERMISSION_REVOKED";
          item.nextAction = `${connection.label} Check the connection or stop the task.`;
          appendTimeline(
            state,
            item,
            "Disconnected",
            `${connection.label} Disconnected. The task stopped before its next action.`,
            "CURRENT",
            now,
          );
          refreshCase(item, now);
        }
      }
      return connection;
    });
  }

  async createDirectCase(
    prompt: string,
  ): Promise<PrototypeMutationResult<PrototypeCase>> {
    const safePrompt = requireText(prompt, "Direct requests");
    return this.mutate((state, now) => {
      const sequence = state.nextId++;
      const caseId = `case-direct-${sequence}`;
      const action = planActionForDirectCase(`action-direct-${sequence}`);
      const item: PrototypeCase = {
        caseId,
        caseType: "DIRECT_DELEGATION",
        goal: safePrompt,
        summary:
          "A plan separates what can be prepared now from actions needing a connection.",
        status: "DECISION_REQUIRED",
        risk: "LOW",
        priority: 75,
        providers: [],
        whyNow: "You requested this task.",
        nextAction: "Review the goal and preparation scope.",
        evidence: [
          {
            evidenceId: `evidence-direct-${sequence}`,
            provider: "direct",
            label: "Direct requests",
            detail: safePrompt,
            revision: 1,
          },
        ],
        currentPlan: {
          version: 1,
          hash: fixtureHash(`${caseId}:1:${safePrompt}`),
          reason: "Your request was organized into a clear plan.",
          expectedOutcome:
            "Separate preparation from actions that need a connection",
          risk: "LOW",
          reversibility: "You can stop or reset this example.",
          requiredScopes: [],
          availableGrantModes: availableGrantModes("LOW"),
          actions: [action],
        },
        timeline: [
          {
            eventId: `event-direct-${state.nextId++}`,
            label: "Request received",
            body: "Started with preparation that needs no external connection.",
            state: "CURRENT",
            occurredAt: now,
          },
        ],
        messages: [
          {
            messageId: `message-direct-${state.nextId++}`,
            author: "USER",
            text: safePrompt,
            createdAt: now,
          },
        ],
        planChange: null,
        partialFailure: null,
        policyIds: [],
        version: 1,
        createdAt: now,
        updatedAt: now,
      };
      state.cases.unshift(item);
      return item;
    });
  }

  async hideCandidate(
    candidateId: string,
  ): Promise<PrototypeMutationResult<PrototypeCandidate>> {
    return this.mutate((state, now) => {
      const candidate = findCandidate(state, candidateId);
      if (candidate.status === "CONVERTED") {
        throw new PrototypeBackendError(
          "INVALID_TRANSITION",
          "A suggestion already converted to a task cannot be hidden.",
        );
      }
      candidate.status = "HIDDEN";
      candidate.version += 1;
      candidate.updatedAt = now;
      return candidate;
    });
  }

  async reduceSimilar(
    candidateId: string,
  ): Promise<PrototypeMutationResult<PrototypeSuppressionRule>> {
    return this.mutate((state, now) => {
      const source = findCandidate(state, candidateId);
      if (source.status !== "VISIBLE") {
        throw new PrototypeBackendError(
          "INVALID_TRANSITION",
          "Only visible suggestions can be used to adjust this filter.",
        );
      }
      const affected = state.candidates.filter(
        (candidate) =>
          candidate.candidateId !== source.candidateId &&
          candidate.primaryGroupId === source.primaryGroupId &&
          candidate.status === "VISIBLE",
      );
      for (const candidate of affected) {
        candidate.status = "SUPPRESSED";
        candidate.version += 1;
        candidate.updatedAt = now;
      }
      const rule: PrototypeSuppressionRule = {
        ruleId: `suppression-${state.nextId++}`,
        sourceCandidateId: source.candidateId,
        provider: source.provider,
        groupId: source.primaryGroupId,
        explanation: `${source.primaryGroupId} will show fewer similar example suggestions.`,
        affectedCandidateIds: affected.map(
          (candidate) => candidate.candidateId,
        ),
        createdAt: now,
      };
      state.suppressionRules.push(rule);
      return rule;
    });
  }

  async undoSuppression(
    ruleId: string,
  ): Promise<PrototypeMutationResult<string[]>> {
    return this.mutate((state, now) => {
      const index = state.suppressionRules.findIndex(
        (rule) => rule.ruleId === ruleId,
      );
      if (index < 0) {
        throw new PrototypeBackendError(
          "NOT_FOUND",
          `Suggestion filter not found: ${ruleId}`,
        );
      }
      const [rule] = state.suppressionRules.splice(index, 1);
      const restored: string[] = [];
      for (const candidateId of rule.affectedCandidateIds) {
        const candidate = state.candidates.find(
          (item) => item.candidateId === candidateId,
        );
        if (candidate?.status === "SUPPRESSED") {
          candidate.status = "VISIBLE";
          candidate.version += 1;
          candidate.updatedAt = now;
          restored.push(candidateId);
        }
      }
      return restored;
    });
  }

  async convertCandidates(
    candidateIds: string[],
  ): Promise<PrototypeMutationResult<PrototypeCase>> {
    const ids = [...new Set(candidateIds)];
    if (ids.length === 0 || ids.length > 20) {
      throw new PrototypeBackendError(
        "INVALID_INPUT",
        "Select 1 to 20 suggestions.",
      );
    }
    return this.mutate((state, now) => {
      const candidates = ids.map((candidateId) =>
        findCandidate(state, candidateId),
      );
      if (candidates.some((candidate) => candidate.status !== "VISIBLE")) {
        throw new PrototypeBackendError(
          "INVALID_TRANSITION",
          "Only visible suggestions can become tasks.",
        );
      }
      const providers = new Set(
        candidates.map((candidate) => candidate.provider),
      );
      const caseTypes = new Set(
        candidates.map((candidate) => candidate.caseTypeHint),
      );
      const risks = new Set(candidates.map((candidate) => candidate.risk));
      if (providers.size !== 1 || caseTypes.size !== 1 || risks.size !== 1) {
        throw new PrototypeBackendError(
          "MIXED_SELECTION",
          "Suggestions with different sources, task types or risk need separate review.",
        );
      }
      const provider = candidates[0].provider;
      const risk = highestRisk(candidates);
      const sequence = state.nextId++;
      const caseId = `case-converted-${sequence}`;
      const action: PrototypeAction = {
        actionId: `action-converted-${sequence}`,
        connector: provider,
        label: `${candidates.length} suggestions grouped into one plan`,
        target: `prototype://${provider}/candidate-bundle-${sequence}`,
        verb:
          provider === "smartthings"
            ? "PROPOSE_ROUTINE"
            : provider === "sms"
              ? "SCHEDULE_LOCAL_REMINDER"
              : "PREPARE",
        parameters: { candidateCount: candidates.length, fixture: true },
        risk,
        reversible: risk !== "HIGH",
        requiredScopes:
          provider === "google"
            ? ["calendar.events.owned"]
            : provider === "smartthings"
              ? ["devices:execute"]
              : [
                  "prototype.sms.selected.read",
                  "prototype.notifications.schedule",
                ],
        status: "PROPOSED",
        resultSummary: null,
      };
      const item: PrototypeCase = {
        caseId,
        caseType: candidates[0].caseTypeHint,
        goal:
          candidates.length === 1
            ? candidates[0].outcome
            : `${candidates[0].outcome} Plus ${candidates.length - 1} items grouped`,
        summary:
          "Duplicate sources were combined. External actions still await approval.",
        status: "DECISION_REQUIRED",
        risk,
        priority: 85,
        providers: [provider],
        whyNow: "You grouped these suggestions into one task.",
        nextAction: "Review the targets, details, permissions and risk.",
        evidence: candidates.map((candidate) => ({
          evidenceId: `evidence-${candidate.candidateId}`,
          provider,
          label: candidate.outcome,
          detail: candidate.evidenceSummary,
          revision: candidate.version,
        })),
        currentPlan: {
          version: 1,
          hash: fixtureHash(`${caseId}:1:${ids.join(",")}`),
          reason: "Selected suggestions were combined into one plan.",
          expectedOutcome:
            "Handle selected suggestions in one task without duplicates",
          risk,
          reversibility:
            risk === "HIGH"
              ? "High-risk examples require approval each time."
              : "You can stop or reset this example.",
          requiredScopes: action.requiredScopes,
          availableGrantModes: availableGrantModes(risk),
          actions: [action],
        },
        timeline: [
          {
            eventId: `event-converted-${state.nextId++}`,
            label: "Grouped into a task",
            body: `${candidates.length} suggestions kept their original sources.`,
            state: "CURRENT",
            occurredAt: now,
          },
        ],
        messages: [],
        planChange: null,
        partialFailure: null,
        policyIds: [],
        version: 1,
        createdAt: now,
        updatedAt: now,
      };
      for (const candidate of candidates) {
        candidate.status = "CONVERTED";
        candidate.version += 1;
        candidate.updatedAt = now;
      }
      state.cases.unshift(item);
      return item;
    });
  }

  async approveCase(
    caseId: string,
    input: PrototypeApprovalInput,
  ): Promise<PrototypeMutationResult<PrototypeCase>> {
    return this.mutate((state, now) => {
      const item = findCase(state, caseId);
      if (item.status !== "DECISION_REQUIRED" && item.status !== "PAUSED") {
        throw new PrototypeBackendError(
          "INVALID_TRANSITION",
          `${item.status} tasks cannot be approved.`,
        );
      }
      const plan = item.currentPlan;
      if (!plan) {
        throw new PrototypeBackendError(
          "INVALID_TRANSITION",
          "There is no current plan to approve.",
        );
      }
      if (plan.version !== input.planVersion || plan.hash !== input.planHash) {
        throw new PrototypeBackendError(
          "PLAN_CHANGED",
          "The plan changed. Review the current plan again.",
        );
      }
      if (!plan.availableGrantModes.includes(input.grantMode)) {
        throw new PrototypeBackendError(
          "UNSUPPORTED_GRANT",
          `${item.risk} risk does not allow ${input.grantMode} permission.`,
        );
      }
      for (const action of plan.actions) {
        if (["PROPOSED", "PENDING", "FAILED"].includes(action.status)) {
          action.status = "RUNNING";
          action.resultSummary = null;
        }
      }
      item.status = "RUNNING";
      item.nextAction =
        "The example is queued. Completion requires a verified result.";
      if (input.grantMode !== "ONCE") {
        const policyId = `policy-${state.nextId++}`;
        const policy: PrototypePolicy = {
          policyId,
          title: `${item.goal} Permissions`,
          description: "Applies only to this exact example action.",
          grantMode: input.grantMode,
          riskCeiling: item.risk === "LOW" ? "LOW" : "MEDIUM",
          scope: `${caseId}:${plan.hash}`,
          status: "ACTIVE",
          affectedCaseIds: [caseId],
          recentUse: now,
          revokedAt: null,
          version: 1,
        };
        state.policies.push(policy);
        item.policyIds.push(policyId);
      }
      appendTimeline(
        state,
        item,
        "Approved",
        `${input.grantMode} permission started the example. No external calls.`,
        "CURRENT",
        now,
      );
      refreshCase(item, now);
      return item;
    });
  }

  async deferCase(
    caseId: string,
  ): Promise<PrototypeMutationResult<PrototypeCase>> {
    return this.mutate((state, now) => {
      const item = findCase(state, caseId);
      if (item.status !== "DECISION_REQUIRED") {
        throw new PrototypeBackendError(
          "INVALID_TRANSITION",
          `${item.status} tasks cannot be deferred.`,
        );
      }
      item.status = "PAUSED";
      item.nextAction = "The plan is saved for your next decision.";
      appendTimeline(
        state,
        item,
        "Deferred",
        "Deferred without external changes.",
        "CURRENT",
        now,
      );
      refreshCase(item, now);
      return item;
    });
  }

  async stopCase(
    caseId: string,
  ): Promise<PrototypeMutationResult<PrototypeCase>> {
    return this.mutate((state, now) => {
      const item = findCase(state, caseId);
      if (terminalStatuses.has(item.status)) return item;
      item.status = "STOPPED";
      item.nextAction = "This task is stopped.";
      for (const action of item.currentPlan?.actions ?? []) {
        if (
          ["PROPOSED", "PENDING", "RUNNING", "VERIFYING"].includes(
            action.status,
          )
        ) {
          action.status = "CANCELLED";
        }
      }
      appendTimeline(
        state,
        item,
        "Stopped",
        "Remaining example actions cancelled.",
        "DONE",
        now,
      );
      refreshCase(item, now);
      return item;
    });
  }

  async retryCase(
    caseId: string,
  ): Promise<PrototypeMutationResult<PrototypeCase>> {
    return this.mutate((state, now) => {
      const item = findCase(state, caseId);
      if (item.status !== "FAILED") {
        throw new PrototypeBackendError(
          "INVALID_TRANSITION",
          `${item.status} tasks cannot be retried.`,
        );
      }
      const retryable = (item.currentPlan?.actions ?? []).filter((action) =>
        ["FAILED", "PENDING"].includes(action.status),
      );
      if (retryable.length === 0) {
        throw new PrototypeBackendError(
          "INVALID_TRANSITION",
          "No failed or waiting steps to retry.",
        );
      }
      for (const action of retryable) {
        action.status = "RUNNING";
        action.resultSummary = null;
      }
      item.status = "RUNNING";
      item.nextAction =
        "Keep completed steps and retry only failed or waiting steps.";
      appendTimeline(
        state,
        item,
        "Retry from the failed step",
        `${retryable.length} example steps restarted.`,
        "CURRENT",
        now,
      );
      refreshCase(item, now);
      return item;
    });
  }

  async advanceCaseExecution(
    caseId: string,
  ): Promise<PrototypeMutationResult<PrototypeCase>> {
    return this.mutate((state, now) => {
      const item = findCase(state, caseId);
      const actions = item.currentPlan?.actions;
      if (!actions) {
        throw new PrototypeBackendError(
          "INVALID_TRANSITION",
          "No current plan to continue.",
        );
      }
      if (item.status === "RUNNING") {
        const running = actions.filter((action) => action.status === "RUNNING");
        if (running.length === 0) {
          throw new PrototypeBackendError(
            "INVALID_TRANSITION",
            "No running action to verify.",
          );
        }
        for (const action of running) action.status = "VERIFYING";
        item.status = "VERIFYING";
        item.nextAction = "Checking the example result.";
        appendTimeline(
          state,
          item,
          "Verifying result",
          "An accepted request is not yet a completed result.",
          "CURRENT",
          now,
        );
      } else if (item.status === "VERIFYING") {
        const verifying = actions.filter(
          (action) => action.status === "VERIFYING",
        );
        if (verifying.length === 0) {
          throw new PrototypeBackendError(
            "INVALID_TRANSITION",
            "No result is ready to mark complete.",
          );
        }
        for (const action of verifying) {
          action.status = "SUCCEEDED";
          action.resultSummary =
            "The example result matched. No external calls were made.";
        }
        item.status = "COMPLETED";
        item.nextAction = "Recorded the example result.";
        item.partialFailure = null;
        appendTimeline(
          state,
          item,
          "Result verified",
          "Marked complete after verifying the result.",
          "DONE",
          now,
        );
      } else {
        throw new PrototypeBackendError(
          "INVALID_TRANSITION",
          `${item.status} tasks cannot advance.`,
        );
      }
      refreshCase(item, now);
      return item;
    });
  }

  async postCaseMessage(
    caseId: string,
    text: string,
  ): Promise<PrototypeMutationResult<PrototypeCase["messages"]>> {
    const safeText = requireText(text, "Task message");
    return this.mutate((state, now) => {
      const item = findCase(state, caseId);
      item.messages.push(
        {
          messageId: `message-${state.nextId++}`,
          author: "USER",
          text: safeText,
          createdAt: now,
        },
        {
          messageId: `message-${state.nextId++}`,
          author: "QUIETPILOT",
          text: "Changes apply only to this task. External changes require a new plan and approval.",
          createdAt: now,
        },
      );
      refreshCase(item, now);
      return item.messages;
    });
  }

  async revokePolicy(
    policyId: string,
  ): Promise<PrototypeMutationResult<PrototypePolicy>> {
    return this.mutate((state, now) => {
      const policy = findPolicy(state, policyId);
      if (policy.status === "REVOKED") return policy;
      policy.status = "REVOKED";
      policy.revokedAt = now;
      policy.version += 1;
      for (const caseId of policy.affectedCaseIds) {
        const item = state.cases.find(
          (candidate) => candidate.caseId === caseId,
        );
        if (!item || terminalStatuses.has(item.status)) continue;
        item.status = "PERMISSION_REVOKED";
        item.nextAction = "Review permissions again or stop the task.";
        for (const action of item.currentPlan?.actions ?? []) {
          if (["RUNNING", "VERIFYING"].includes(action.status)) {
            action.status = "PENDING";
          }
        }
        appendTimeline(
          state,
          item,
          "Permission revoked",
          "Stopped before the next example action.",
          "CURRENT",
          now,
        );
        refreshCase(item, now);
      }
      return policy;
    });
  }

  async reset(): Promise<PrototypeState> {
    const operation = this.writeQueue.then(async () => {
      const next = createInitialPrototypeState(this.now());
      await this.storage.removeItem(this.storageKey);
      this.state = next;
      this.loadPromise = Promise.resolve(next);
      return clone(next);
    });
    this.writeQueue = operation.then(
      () => undefined,
      () => undefined,
    );
    return operation;
  }

  private now(): string {
    return this.clock.now().toISOString();
  }

  private async loadState(): Promise<PrototypeState> {
    if (this.state) return this.state;
    if (!this.loadPromise) {
      this.loadPromise = this.storage.getItem(this.storageKey).then((raw) => {
        if (raw) {
          try {
            const parsed = JSON.parse(raw) as unknown;
            if (isPrototypeState(parsed)) {
              this.state = parsed;
              return parsed;
            }
          } catch {
            // Invalid development state falls back to a safe empty prototype.
          }
        }
        const initial = createInitialPrototypeState(this.now());
        this.state = initial;
        return initial;
      });
    }
    return this.loadPromise;
  }

  private mutate<T>(
    change: (state: PrototypeState, now: string) => T,
  ): Promise<PrototypeMutationResult<T>> {
    const operation = this.writeQueue.then(async () => {
      const current = await this.loadState();
      const draft = clone(current);
      const now = this.now();
      const value = change(draft, now);
      draft.schemaVersion = PROTOTYPE_SCHEMA_VERSION;
      draft.mode = PROTOTYPE_MODE;
      draft.externalSideEffects = false;
      draft.revision += 1;
      draft.updatedAt = now;
      await this.storage.setItem(this.storageKey, JSON.stringify(draft));
      this.state = draft;
      this.loadPromise = Promise.resolve(draft);
      return { value: clone(value), snapshot: clone(draft) };
    });
    this.writeQueue = operation.then(
      () => undefined,
      () => undefined,
    );
    return operation;
  }
}
