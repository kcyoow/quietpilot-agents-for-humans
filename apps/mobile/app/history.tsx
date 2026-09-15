import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router, useFocusEffect } from "expo-router";
import { useCallback } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { BackHeader } from "@/src/components/BackHeader";
import { EmptyState } from "@/src/components/EmptyState";
import { PrototypeStatusPill } from "@/src/components/PrototypeStatusPill";
import { RiskPill } from "@/src/components/RiskPill";
import { useAppTheme } from "@/src/theme/useAppTheme";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";

export default function HistoryScreen() {
  const { colors } = useAppTheme();
  const { error, refresh, snapshot, source, status } = useWorkspace();

  useFocusEffect(
    useCallback(() => {
      void refresh();
    }, [refresh]),
  );

  const cases = (snapshot?.cases ?? [])
    .filter((item) => ["COMPLETED", "STOPPED"].includes(item.status))
    .sort((left, right) => right.updatedAt.localeCompare(left.updatedAt));
  const hasCases = cases.length > 0;
  const loading = status !== "ready" || (!snapshot && !error);
  const showError = Boolean(error) && status === "ready";

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <BackHeader title="History" />
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            onRefresh={refresh}
            refreshing={status === "booting"}
            tintColor={colors.accent}
          />
        }
      >
        <Text
          accessibilityRole="header"
          style={[styles.title, { color: colors.text }]}
        >
          Review past tasks
        </Text>
        <Text style={[styles.body, { color: colors.textMuted }]}>
          See the sources and results of completed or stopped tasks.
        </Text>

        {showError && (
          <View
            style={[styles.errorCard, { backgroundColor: colors.dangerSoft }]}
          >
            <View accessible accessibilityRole="alert">
              <Text style={[styles.errorTitle, { color: colors.danger }]}>
                {hasCases
                  ? "Could not refresh history"
                  : "Could not load history"}
              </Text>
              {hasCases && (
                <Text style={[styles.errorBody, { color: colors.textMuted }]}>
                  Showing the last loaded history.
                </Text>
              )}
              <Text style={[styles.errorBody, { color: colors.textMuted }]}>
                {error}
              </Text>
            </View>
            <Pressable
              accessibilityLabel="Reload history"
              accessibilityRole="button"
              onPress={() => void refresh()}
              style={({ pressed }) => [
                styles.retryButton,
                {
                  backgroundColor: colors.surface,
                  borderColor: colors.border,
                  opacity: pressed ? 0.7 : 1,
                },
              ]}
            >
              <MaterialCommunityIcons
                color={colors.accent}
                name="refresh"
                size={18}
              />
              <Text style={[styles.retryText, { color: colors.accent }]}>
                Reload
              </Text>
            </Pressable>
          </View>
        )}

        {!hasCases && loading ? (
          <View
            accessible
            accessibilityLabel="Loading history"
            accessibilityRole="progressbar"
            accessibilityState={{ busy: true }}
            style={styles.loading}
          >
            <ActivityIndicator color={colors.accent} />
            <Text style={[styles.loadingText, { color: colors.textMuted }]}>
              Loading history.
            </Text>
          </View>
        ) : !hasCases && !showError ? (
          <View style={styles.empty}>
            <EmptyState
              body={
                source === "LIVE"
                  ? "Completed and stopped tasks appear here."
                  : "Completed and stopped example tasks appear here."
              }
              title="No history yet"
            />
          </View>
        ) : hasCases ? (
          <View style={styles.list}>
            {cases.map((item) => (
              <Pressable
                accessibilityLabel={`${item.goal}, ${item.status === "COMPLETED" ? "Completed" : "Stopped"}`}
                accessibilityRole="button"
                key={item.caseId}
                onPress={() =>
                  router.push({
                    pathname: "/cases/[caseId]",
                    params: { caseId: item.caseId },
                  })
                }
                style={({ pressed }) => [
                  styles.card,
                  {
                    backgroundColor: colors.surface,
                    borderColor: colors.border,
                    opacity: pressed ? 0.74 : 1,
                  },
                ]}
              >
                <View style={styles.topline}>
                  <View style={styles.pills}>
                    <PrototypeStatusPill status={item.status} />
                    <RiskPill risk={item.risk} />
                  </View>
                  <MaterialCommunityIcons
                    color={colors.textMuted}
                    name="chevron-right"
                    size={20}
                  />
                </View>
                <Text
                  numberOfLines={2}
                  style={[styles.cardTitle, { color: colors.text }]}
                >
                  {item.goal}
                </Text>
                <Text
                  numberOfLines={3}
                  style={[styles.cardBody, { color: colors.textMuted }]}
                >
                  {item.summary}
                </Text>
              </Pressable>
            ))}
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { fontSize: 14, lineHeight: 21, marginTop: 8 },
  card: {
    borderRadius: 16,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 48,
    padding: 16,
  },
  cardBody: { fontSize: 14, lineHeight: 21, marginTop: 7 },
  cardTitle: { fontSize: 16, fontWeight: "600", lineHeight: 24, marginTop: 12 },
  content: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 36 },
  empty: { marginTop: 24 },
  errorCard: { borderRadius: 14, gap: 12, marginTop: 20, padding: 16 },
  errorTitle: { fontSize: 15, fontWeight: "600", lineHeight: 22 },
  errorBody: { fontSize: 13, lineHeight: 20, marginTop: 4 },
  list: { gap: 12, marginTop: 24 },
  loading: { alignItems: "center", gap: 12, paddingVertical: 40 },
  loadingText: { fontSize: 14, lineHeight: 21, textAlign: "center" },
  pills: { flex: 1, flexDirection: "row", flexWrap: "wrap", gap: 6 },
  retryButton: {
    alignItems: "center",
    alignSelf: "flex-start",
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 7,
    minHeight: 48,
    paddingHorizontal: 14,
  },
  retryText: { fontSize: 13, fontWeight: "600" },
  screen: { flex: 1 },
  title: {
    fontSize: 21,
    fontWeight: "600",
    letterSpacing: -0.4,
    lineHeight: 29,
  },
  topline: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    justifyContent: "space-between",
  },
});
