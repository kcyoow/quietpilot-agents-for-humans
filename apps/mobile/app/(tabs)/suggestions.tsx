import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router, useFocusEffect } from "expo-router";
import { useCallback, useMemo } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { EmptyState } from "@/src/components/EmptyState";
import { ScreenHeader } from "@/src/components/ScreenHeader";
import { useAppTheme } from "@/src/theme/useAppTheme";
import {
  actionQueueSources,
  buildActionQueue,
  type ActionQueueItem,
} from "@/src/workspace/actionQueue";
import { humanCopy } from "@/src/workspace/screens/caseDetail/presentation";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";

export default function ProgressScreen() {
  const { colors } = useAppTheme();
  const { error, refresh, snapshot, status } = useWorkspace();

  useFocusEffect(
    useCallback(() => {
      void refresh();
    }, [refresh]),
  );

  const items = useMemo(
    () =>
      buildActionQueue(snapshot).filter(
        (item) =>
          item.kind === "CASE" &&
          (item.stance === "PREPARING" || item.stance === "WAITING"),
      ),
    [snapshot],
  );
  const sections = useMemo(
    () =>
      actionQueueSources
        .map((section) => ({
          ...section,
          items: items.filter((item) => item.source === section.key),
        }))
        .filter((section) => section.items.length > 0),
    [items],
  );
  const hasItems = items.length > 0;
  const loading = status !== "ready" || (!snapshot && !error);
  const showError = Boolean(error) && status === "ready";

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <ScreenHeader
        subtitle={
          hasItems && !loading && !error
            ? `Check ${items.length} tasks.`
            : undefined
        }
        title="In progress"
      />
      <ScrollView
        contentContainerStyle={styles.content}
        refreshControl={
          <RefreshControl
            onRefresh={refresh}
            refreshing={hasItems && status === "booting"}
            colors={[colors.accent]}
            progressBackgroundColor={colors.surface}
            tintColor={colors.accent}
          />
        }
      >
        <View style={styles.topline}>
          <Text style={[styles.intro, { color: colors.textMuted }]}>
            Tasks being prepared or waiting for a decision.
          </Text>
          <Pressable
            accessibilityLabel="View history"
            accessibilityRole="button"
            onPress={() => router.push("/history")}
            style={({ pressed }) => [
              styles.historyButton,
              { opacity: pressed ? 0.68 : 1 },
            ]}
          >
            <MaterialCommunityIcons
              color={colors.textMuted}
              name="history"
              size={18}
            />
            <Text style={[styles.historyText, { color: colors.textMuted }]}>
              View history
            </Text>
          </Pressable>
        </View>

        {hasItems && loading && (
          <View
            accessible
            accessibilityLabel="Updating progress"
            accessibilityRole="progressbar"
            accessibilityState={{ busy: true }}
            style={styles.refreshing}
          >
            {status !== "booting" && (
              <ActivityIndicator color={colors.accent} size="small" />
            )}
            <Text style={[styles.refreshingText, { color: colors.textMuted }]}>
              Refreshing status.
            </Text>
          </View>
        )}

        {showError && (
          <View
            style={[styles.errorCard, { backgroundColor: colors.dangerSoft }]}
          >
            <View accessible accessibilityRole="alert">
              <Text style={[styles.errorTitle, { color: colors.danger }]}>
                {hasItems
                  ? "Could not refresh status"
                  : "Could not load progress"}
              </Text>
              {hasItems && (
                <Text style={[styles.errorBody, { color: colors.textMuted }]}>
                  Showing the last saved view.
                </Text>
              )}
              <Text style={[styles.errorBody, { color: colors.textMuted }]}>
                {error}
              </Text>
            </View>
            <Pressable
              accessibilityLabel="Reload progress"
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

        {!hasItems && loading ? (
          <View
            accessible
            accessibilityLabel="Loading active tasks"
            accessibilityRole="progressbar"
            accessibilityState={{ busy: true }}
            style={styles.loading}
          >
            <ActivityIndicator color={colors.accent} />
            <Text style={[styles.loadingText, { color: colors.textMuted }]}>
              Loading progress.
            </Text>
          </View>
        ) : !hasItems && !showError ? (
          <EmptyState
            body="Tasks appear here when preparation starts. Finished tasks are in History."
            title="No active tasks"
          />
        ) : hasItems ? (
          <View style={styles.sections}>
            {sections.map((section) => (
              <ProgressSection
                icon={section.icon}
                items={section.items}
                key={section.key}
                label={section.label}
              />
            ))}
          </View>
        ) : null}
      </ScrollView>
    </View>
  );
}

function ProgressSection({
  icon,
  items,
  label,
}: {
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  items: ActionQueueItem[];
  label: string;
}) {
  const { colors } = useAppTheme();
  return (
    <View>
      <View style={styles.sectionHeader}>
        <MaterialCommunityIcons
          color={colors.textMuted}
          name={icon}
          size={19}
        />
        <Text
          accessibilityRole="header"
          style={[styles.sectionTitle, { color: colors.text }]}
        >
          {label}
        </Text>
        <Text style={[styles.sectionCount, { color: colors.textMuted }]}>
          {items.length}
        </Text>
      </View>
      <View
        style={[
          styles.rows,
          { backgroundColor: colors.surface, borderColor: colors.border },
        ]}
      >
        {items.map((item, index) => {
          const tone =
            item.tone === "warning"
              ? colors.warning
              : item.tone === "muted"
                ? colors.textMuted
                : colors.accent;
          return (
            <Pressable
              accessibilityLabel={`${item.title}, ${item.stanceLabel}`}
              accessibilityHint="View the plan and next steps."
              accessibilityRole="button"
              key={item.id}
              onPress={() =>
                router.push({
                  pathname: "/cases/[caseId]",
                  params: { caseId: item.caseId! },
                })
              }
              style={({ pressed }) => [
                styles.row,
                {
                  borderBottomColor:
                    index < items.length - 1 ? colors.border : "transparent",
                  opacity: pressed ? 0.68 : 1,
                },
              ]}
            >
              <View style={styles.rowCopy}>
                <View style={styles.rowMeta}>
                  <View
                    style={[
                      styles.statusBadge,
                      { backgroundColor: colors.surfaceMuted },
                    ]}
                  >
                    <View
                      style={[styles.statusDot, { backgroundColor: tone }]}
                    />
                    <Text style={[styles.statusText, { color: tone }]}>
                      {item.stanceLabel}
                    </Text>
                  </View>
                  <Text style={[styles.rowTime, { color: colors.textMuted }]}>
                    {item.timeLabel} updated
                  </Text>
                </View>
                <Text style={[styles.rowTitle, { color: colors.text }]}>
                  {item.title}
                </Text>
                <View style={styles.nextStep}>
                  <Text style={[styles.nextLabel, { color: colors.textMuted }]}>
                    Next step
                  </Text>
                  <Text style={[styles.rowBody, { color: colors.text }]}>
                    {humanCopy(item.body)}
                  </Text>
                </View>
                <View style={styles.detailLink}>
                  <Text style={[styles.detailText, { color: colors.accent }]}>
                    View details
                  </Text>
                  <MaterialCommunityIcons
                    color={colors.accent}
                    name="arrow-right"
                    size={17}
                  />
                </View>
              </View>
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  content: { paddingBottom: 32, paddingHorizontal: 20 },
  detailLink: {
    alignItems: "center",
    flexDirection: "row",
    gap: 6,
    marginTop: 14,
  },
  detailText: { fontSize: 14, fontWeight: "600", lineHeight: 21 },
  errorCard: { borderRadius: 14, gap: 12, marginBottom: 16, padding: 16 },
  errorTitle: { fontSize: 15, fontWeight: "600", lineHeight: 22 },
  errorBody: { fontSize: 13, lineHeight: 20, marginTop: 4 },
  historyButton: {
    alignItems: "center",
    borderRadius: 12,
    flexDirection: "row",
    gap: 6,
    minHeight: 48,
    minWidth: 48,
    paddingHorizontal: 12,
  },
  historyText: { fontSize: 14, fontWeight: "600", lineHeight: 21 },
  intro: { flex: 1, flexBasis: 170, fontSize: 14, lineHeight: 22 },
  loading: { alignItems: "center", gap: 12, paddingVertical: 40 },
  loadingText: { fontSize: 14, lineHeight: 21, textAlign: "center" },
  nextLabel: { fontSize: 12, fontWeight: "600", lineHeight: 18 },
  nextStep: { gap: 4, marginTop: 12 },
  refreshing: {
    alignItems: "center",
    flexDirection: "row",
    gap: 9,
    paddingVertical: 12,
  },
  refreshingText: { flex: 1, fontSize: 13, lineHeight: 20 },
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
  row: {
    alignItems: "center",
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 12,
    minHeight: 104,
    paddingHorizontal: 18,
    paddingVertical: 18,
  },
  rowBody: { fontSize: 14, lineHeight: 22 },
  rowCopy: { flex: 1, minWidth: 0 },
  rowMeta: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 7,
    marginBottom: 12,
  },
  rowTime: { fontSize: 12, lineHeight: 18, marginLeft: "auto" },
  rowTitle: {
    fontSize: 18,
    fontWeight: "600",
    letterSpacing: -0.25,
    lineHeight: 27,
  },
  rows: {
    borderRadius: 16,
    borderWidth: StyleSheet.hairlineWidth,
    overflow: "hidden",
  },
  screen: { flex: 1 },
  sectionCount: { fontSize: 13, fontWeight: "500" },
  sectionHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    minHeight: 48,
    paddingHorizontal: 3,
    paddingVertical: 6,
  },
  sectionTitle: {
    flexShrink: 1,
    fontSize: 15,
    fontWeight: "600",
    lineHeight: 23,
  },
  sections: { gap: 16 },
  statusDot: { borderRadius: 3, height: 6, width: 6 },
  statusBadge: {
    alignItems: "center",
    borderRadius: 8,
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 8,
    paddingVertical: 5,
    maxWidth: "100%",
  },
  statusText: {
    flexShrink: 1,
    fontSize: 12,
    fontWeight: "600",
    lineHeight: 18,
  },
  topline: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginBottom: 12,
  },
});
