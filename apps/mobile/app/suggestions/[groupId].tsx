import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { BackHeader } from "@/src/components/BackHeader";
import { EmptyState } from "@/src/components/EmptyState";
import { useAppTheme } from "@/src/theme/useAppTheme";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";
import type { WorkspaceCandidate } from "@/src/workspace/types";

export default function SuggestionGroupScreen() {
  const { colors } = useAppTheme();
  const { groupId, suggestionId } = useLocalSearchParams<{
    groupId: string;
    suggestionId?: string | string[];
  }>();
  const { convertCandidates, error, hideCandidate, snapshot, status } =
    useWorkspace();
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [localError, setLocalError] = useState<string | null>(null);
  const scrollView = useRef<ScrollView>(null);
  const requestedSuggestionId =
    typeof suggestionId === "string" ? suggestionId : null;
  const group = snapshot?.candidateGroups.find(
    (item) => item.groupId === groupId,
  );
  const candidates = useMemo(() => {
    const ids = new Set(group?.candidateIds ?? []);
    const items = (snapshot?.candidates ?? []).filter(
      (candidate) =>
        ids.has(candidate.candidateId) &&
        candidate.status === "VISIBLE" &&
        candidate.proposedActions.length > 0,
    );
    const selected = items.find(
      (candidate) => candidate.candidateId === requestedSuggestionId,
    );
    return selected
      ? [selected, ...items.filter((candidate) => candidate !== selected)]
      : items;
  }, [group?.candidateIds, requestedSuggestionId, snapshot?.candidates]);
  const selectedSuggestionId = candidates.some(
    (candidate) => candidate.candidateId === requestedSuggestionId,
  )
    ? requestedSuggestionId
    : null;
  const busy = status !== "ready" || busyId !== null;
  const visibleError = localError ?? error;

  useEffect(() => {
    if (selectedSuggestionId)
      scrollView.current?.scrollTo({ y: 0, animated: false });
  }, [selectedSuggestionId]);

  async function prepare(candidate: WorkspaceCandidate) {
    if (busy) return;
    setBusyId(candidate.candidateId);
    setMessage(null);
    setLocalError(null);
    try {
      const item = await convertCandidates([candidate.candidateId]);
      router.replace({
        pathname: "/cases/[caseId]",
        params: { caseId: item.caseId },
      });
    } catch (caught) {
      setLocalError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  }

  async function dismiss(candidate: WorkspaceCandidate) {
    if (busy) return;
    setBusyId(candidate.candidateId);
    setMessage(null);
    setLocalError(null);
    try {
      await hideCandidate(candidate.candidateId);
      setMessage("Suggestion hidden.");
    } catch (caught) {
      setLocalError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <BackHeader title={group?.label ?? "Suggestions"} />
      <ScrollView ref={scrollView} contentContainerStyle={styles.content}>
        {visibleError && (
          <Text
            accessibilityRole="alert"
            accessibilityLiveRegion="polite"
            style={[styles.message, { color: colors.danger }]}
          >
            {visibleError}
          </Text>
        )}
        {status === "booting" && !group ? (
          <ActivityIndicator
            accessible
            accessibilityLabel="Loading suggestions"
            accessibilityRole="progressbar"
            color={colors.accent}
            style={styles.loading}
          />
        ) : !group ? (
          <EmptyState
            body="Return to the list and try again."
            title={
              visibleError
                ? "Could not load suggestions"
                : "Suggestion not found"
            }
          />
        ) : (
          <>
            <Text style={[styles.intro, { color: colors.textMuted }]}>
              {group.reason}
            </Text>
            {requestedSuggestionId && !selectedSuggestionId && (
              <Text style={[styles.message, { color: colors.textMuted }]}>
                This suggestion is no longer in the list.
              </Text>
            )}
            {message && (
              <Text
                accessibilityLiveRegion="polite"
                style={[styles.message, { color: colors.accent }]}
              >
                {message}
              </Text>
            )}

            {candidates.length === 0 ? (
              <EmptyState
                body="Return to the list for other suggestions."
                title={
                  visibleError
                    ? "Could not load suggestions"
                    : "No suggestions left"
                }
              />
            ) : (
              candidates.map((candidate) => {
                const selected = candidate.candidateId === selectedSuggestionId;
                return (
                  <View
                    key={candidate.candidateId}
                    style={[
                      styles.card,
                      {
                        backgroundColor: colors.surface,
                        borderColor: selected ? colors.accent : colors.border,
                      },
                    ]}
                  >
                    {selected && (
                      <Text
                        style={[styles.selectedLabel, { color: colors.accent }]}
                      >
                        Selected suggestion
                      </Text>
                    )}
                    <Text
                      accessibilityRole="header"
                      accessibilityLabel={
                        selected
                          ? `Selected suggestion: ${candidate.outcome}`
                          : candidate.outcome
                      }
                      style={[styles.title, { color: colors.text }]}
                    >
                      {candidate.outcome}
                    </Text>
                    <Text style={[styles.why, { color: colors.textMuted }]}>
                      {candidate.whyNow}
                    </Text>
                    <View
                      style={[
                        styles.actions,
                        { backgroundColor: colors.surfaceMuted },
                      ]}
                    >
                      {candidate.proposedActions.map((action) => (
                        <View key={action.actionId} style={styles.actionRow}>
                          <MaterialCommunityIcons
                            color={colors.accent}
                            name="file-document-edit-outline"
                            size={18}
                          />
                          <Text
                            style={[styles.actionText, { color: colors.text }]}
                          >
                            {action.label}
                          </Text>
                        </View>
                      ))}
                    </View>
                    <View style={styles.buttons}>
                      <Pressable
                        accessibilityLabel={`${candidate.outcome}, prepare plan`}
                        accessibilityRole="button"
                        accessibilityState={{ disabled: busy }}
                        disabled={busy}
                        onPress={() => prepare(candidate)}
                        style={({ pressed }) => [
                          styles.prepare,
                          {
                            backgroundColor: colors.accent,
                            opacity: pressed || busy ? 0.65 : 1,
                          },
                        ]}
                      >
                        {busyId === candidate.candidateId ? (
                          <ActivityIndicator
                            color={colors.onAccent}
                            size="small"
                          />
                        ) : (
                          <Text
                            style={[
                              styles.prepareText,
                              { color: colors.onAccent },
                            ]}
                          >
                            Prepare plan
                          </Text>
                        )}
                      </Pressable>
                      <Pressable
                        accessibilityLabel={`${candidate.outcome}, dismiss`}
                        accessibilityRole="button"
                        accessibilityState={{ disabled: busy }}
                        disabled={busy}
                        onPress={() => dismiss(candidate)}
                        style={({ pressed }) => [
                          styles.dismiss,
                          {
                            borderColor: colors.border,
                            opacity: pressed || busy ? 0.65 : 1,
                          },
                        ]}
                      >
                        <Text
                          style={[
                            styles.dismissText,
                            { color: colors.textMuted },
                          ]}
                        >
                          Dismiss
                        </Text>
                      </Pressable>
                    </View>
                  </View>
                );
              })
            )}
          </>
        )}
      </ScrollView>
    </View>
  );
}

function errorMessage(caught: unknown) {
  return caught instanceof Error
    ? caught.message
    : "Could not update the suggestion.";
}

const styles = StyleSheet.create({
  actionRow: { alignItems: "flex-start", flexDirection: "row", gap: 8 },
  actionText: { flex: 1, fontSize: 14, fontWeight: "400", lineHeight: 21 },
  actions: { borderRadius: 12, gap: 8, marginTop: 14, padding: 12 },
  buttons: { flexDirection: "row", flexWrap: "wrap", gap: 9, marginTop: 14 },
  card: { borderRadius: 16, borderWidth: 1, marginBottom: 12, padding: 16 },
  content: { paddingBottom: 32, paddingHorizontal: 20 },
  dismiss: {
    alignItems: "center",
    borderRadius: 12,
    borderWidth: 1,
    flex: 0.7,
    justifyContent: "center",
    minHeight: 48,
    minWidth: 104,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  dismissText: { fontSize: 14, fontWeight: "600", lineHeight: 20 },
  intro: { fontSize: 14, fontWeight: "400", lineHeight: 21, marginBottom: 16 },
  loading: { marginVertical: 32 },
  message: {
    fontSize: 14,
    fontWeight: "400",
    lineHeight: 21,
    marginBottom: 12,
  },
  prepare: {
    alignItems: "center",
    borderRadius: 12,
    flex: 1,
    justifyContent: "center",
    minHeight: 48,
    minWidth: 120,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  prepareText: { fontSize: 14, fontWeight: "600", lineHeight: 20 },
  screen: { flex: 1 },
  selectedLabel: {
    fontSize: 12,
    fontWeight: "600",
    lineHeight: 18,
    marginBottom: 6,
  },
  title: { fontSize: 18, fontWeight: "600", lineHeight: 26 },
  why: { fontSize: 14, fontWeight: "400", lineHeight: 21, marginTop: 6 },
});
