import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useAuth } from "@/src/auth/AuthProvider";
import { createGoogleConnectionsApi } from "@/src/connections/googleApi";
import type {
  PrototypeCandidate,
  PrototypeCandidateGroup,
} from "@/src/prototype/types";

export function useLiveGoogleSuggestions() {
  const { user } = useAuth();
  const api = useMemo(() => createGoogleConnectionsApi(), []);
  const [candidates, setCandidates] = useState<PrototypeCandidate[]>([]);
  const [groups, setGroups] = useState<PrototypeCandidateGroup[]>([]);
  const [error, setError] = useState<string | null>(null);
  const active = useRef(true);

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);

  const refresh = useCallback(async () => {
    if (!user || !api.configured) {
      if (active.current) {
        setCandidates([]);
        setGroups([]);
      }
      return;
    }
    try {
      const [records, groupRecords] = await Promise.all([
        api.listSuggestions(),
        api.listSuggestionGroups(),
      ]);
      const nextCandidates = records.map((record) => ({
        candidateId: record.candidate_id,
        provider: "google" as const,
        primaryGroupId: record.primary_group_id,
        caseTypeHint: record.source_type,
        outcome: record.outcome,
        opportunityType: record.opportunity_type,
        proposedActions: record.proposed_actions.map((action, index) => ({
          actionId: `${record.candidate_id}-proposal-${index + 1}`,
          connector: "quietpilot" as const,
          label:
            action.verb === "prepare_reminder"
              ? "Prepare reminder"
              : action.verb === "prepare_reply"
                ? "Draft reply"
                : "Prepare checklist",
          target: action.target_resource,
          verb: action.verb,
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
          risk: action.risk,
          reversible: action.reversible,
          requiredScopes: action.required_scopes,
          status: "PROPOSED" as const,
          resultSummary: null,
        })),
        summary: record.summary,
        evidenceSummary: `Gmail sources ${record.evidence_refs.length} items`,
        safetyState: null,
        safetySummary: null,
        eventAt: null,
        confidence: record.confidence,
        risk: record.risk,
        requiresApproval: false,
        tags: record.tags,
        status: record.status,
        version: record.version,
        createdAt: record.created_at,
        updatedAt: record.updated_at,
        whyNow: record.why_now,
      }));
      const nextGroups = groupRecords.map((record) => ({
        groupId: record.group_id,
        provider: "google" as const,
        label: record.label,
        reason: record.reason,
        icon: "email-outline",
        candidateIds: nextCandidates
          .filter((candidate) => candidate.primaryGroupId === record.group_id)
          .map((candidate) => candidate.candidateId),
      }));
      if (active.current) {
        setCandidates(nextCandidates);
        setGroups(nextGroups);
        setError(null);
      }
    } catch (caught) {
      if (active.current) {
        setError(
          caught instanceof Error
            ? caught.message
            : "Could not load new suggestions.",
        );
      }
    }
  }, [api, user]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void refresh();
    }, 0);
    return () => clearTimeout(timer);
  }, [refresh]);

  return { candidates, error, groups, refresh };
}
