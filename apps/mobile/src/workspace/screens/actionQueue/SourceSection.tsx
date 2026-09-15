import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router } from "expo-router";
import type { ReactNode } from "react";
import { ActivityIndicator, Pressable, Text, View } from "react-native";

import type { GoogleDiscoveryPresentation } from "@/src/connections/googleDiscoveryPresentation";
import { useAppTheme } from "@/src/theme/useAppTheme";
import type {
  ActionQueueItem,
  ActionQueueSource,
  ActionQueueTone,
} from "@/src/workspace/actionQueue";

import { styles } from "./styles";

export function SourceSection({
  collapsed,
  expandedAll,
  headerAccessory,
  summary,
  icon,
  items,
  label,
  onToggle,
  onToggleAll,
  presentation,
  refreshingDiscovery,
  retryDiscovery,
}: {
  collapsed: boolean;
  expandedAll: boolean;
  headerAccessory?: ReactNode;
  summary?: ReactNode;
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  items: ActionQueueItem[];
  label: string;
  onToggle(): void;
  onToggleAll(): void;
  presentation: GoogleDiscoveryPresentation | null;
  refreshingDiscovery: boolean;
  retryDiscovery(): void;
  source: ActionQueueSource;
}) {
  const { colors } = useAppTheme();
  const visibleItems = expandedAll ? items : items.slice(0, 3);
  const discoveryStatus = presentation
    ? sourceDiscoveryStatus(presentation, colors)
    : null;
  const countLabel = presentation
    ? items.length > 0
      ? `Earlier results ${items.length} items`
      : null
    : `${items.length} items`;
  const accessibilityLabel = presentation
    ? [
        label,
        discoveryStatus?.label,
        countLabel,
        collapsed ? "Expand" : "Collapse",
      ]
        .filter(Boolean)
        .join(", ")
    : `${label} ${items.length} items ${collapsed ? "Expand" : "Collapse"}`;
  return (
    <View>
      <View style={styles.sourceHeadingRow}>
        <Pressable
          accessibilityLabel={accessibilityLabel}
          accessibilityRole="button"
          accessibilityState={{ expanded: !collapsed }}
          onPress={onToggle}
          style={({ pressed }) => [
            styles.sourceHeader,
            styles.sourceHeaderMain,
            {
              opacity: pressed ? 0.72 : 1,
            },
          ]}
        >
          <MaterialCommunityIcons
            color={colors.textMuted}
            name={icon}
            size={19}
          />
          <Text style={[styles.sourceLabel, { color: colors.text }]}>
            {label}
          </Text>
          {countLabel && (
            <Text style={[styles.sourceCount, { color: colors.textMuted }]}>
              {presentation ? `Earlier results ${items.length}` : items.length}
            </Text>
          )}
          {discoveryStatus && (
            <View
              style={[
                styles.sourceStatusBadge,
                { backgroundColor: discoveryStatus.background },
              ]}
            >
              <Text
                style={[
                  styles.sourceStatusBadgeText,
                  { color: discoveryStatus.text },
                ]}
              >
                {discoveryStatus.label}
              </Text>
            </View>
          )}
          <MaterialCommunityIcons
            color={colors.textMuted}
            name={collapsed ? "chevron-down" : "chevron-up"}
            size={20}
            style={!discoveryStatus ? styles.sourceChevron : undefined}
          />
        </Pressable>
        {headerAccessory}
      </View>
      {!collapsed && summary}
      {!collapsed && (items.length > 0 || presentation) && (
        <View
          style={[
            styles.sourceRows,
            { backgroundColor: colors.surface, borderColor: colors.border },
          ]}
        >
          {presentation && (
            <SourceDiscoveryStatus
              presentation={presentation}
              refreshing={refreshingDiscovery}
              retry={retryDiscovery}
            />
          )}
          {visibleItems.map((item, index) => (
            <ActionRow
              item={item}
              key={item.id}
              showDivider={index < visibleItems.length - 1 || items.length > 3}
            />
          ))}
          {items.length > 3 && (
            <Pressable
              accessibilityRole="button"
              onPress={onToggleAll}
              style={({ pressed }) => [
                styles.moreButton,
                { opacity: pressed ? 0.68 : 1 },
              ]}
            >
              <Text style={[styles.moreText, { color: colors.accent }]}>
                {expandedAll
                  ? "Collapse"
                  : `${items.length - visibleItems.length} more`}
              </Text>
              <MaterialCommunityIcons
                color={colors.accent}
                name={expandedAll ? "chevron-up" : "chevron-down"}
                size={18}
              />
            </Pressable>
          )}
        </View>
      )}
    </View>
  );
}

function ActionRow({
  item,
  showDivider,
}: {
  item: ActionQueueItem;
  showDivider: boolean;
}) {
  const { colors } = useAppTheme();
  const tone = toneColors(item.tone, colors);
  return (
    <Pressable
      accessibilityLabel={`${item.title}, ${item.stanceLabel}`}
      accessibilityRole="button"
      onPress={() => openQueueItem(item)}
      style={({ pressed }) => [
        styles.actionRow,
        {
          borderBottomColor: showDivider ? colors.border : "transparent",
          opacity: pressed ? 0.68 : 1,
        },
      ]}
    >
      <View style={styles.actionCopy}>
        <Text
          numberOfLines={2}
          style={[styles.actionTitle, { color: colors.text }]}
        >
          {item.title}
        </Text>
        <Text
          numberOfLines={1}
          style={[styles.actionBody, { color: colors.textMuted }]}
        >
          {item.body}
        </Text>
        <View style={styles.actionMeta}>
          <View style={[styles.priorityDot, { backgroundColor: tone.text }]} />
          <Text style={[styles.stanceText, { color: tone.text }]}>
            {item.stanceLabel}
          </Text>
          {item.relatedCount > 1 && (
            <Text style={[styles.relatedCount, { color: colors.textMuted }]}>
              Source {item.relatedCount} items
            </Text>
          )}
          <Text style={[styles.actionTime, { color: colors.textMuted }]}>
            {item.timeLabel}
          </Text>
        </View>
      </View>
      <MaterialCommunityIcons
        color={colors.textSubtle}
        name="chevron-right"
        size={21}
      />
    </Pressable>
  );
}

function SourceDiscoveryStatus({
  presentation,
  refreshing,
  retry,
}: {
  presentation: GoogleDiscoveryPresentation;
  refreshing: boolean;
  retry(): void;
}) {
  const { colors } = useAppTheme();
  const warning = presentation.tone === "warning";
  const progress = presentation.progress;
  return (
    <View
      style={[
        styles.sourceStatusRow,
        {
          backgroundColor: warning ? colors.warningSoft : colors.surface,
          borderBottomColor: colors.border,
        },
      ]}
    >
      <MaterialCommunityIcons
        color={warning ? colors.warning : colors.accent}
        name={warning ? "email-alert-outline" : "email-sync-outline"}
        size={18}
      />
      <View style={styles.sourceStatusCopy}>
        <Text style={[styles.sourceStatusBody, { color: colors.textMuted }]}>
          {presentation.body}
        </Text>
        {progress !== null && (
          <View
            accessibilityLabel="Gmail scan progress"
            accessibilityRole="progressbar"
            accessibilityValue={{ max: 100, min: 0, now: progress }}
            style={[
              styles.sourceProgressTrack,
              { backgroundColor: colors.surfaceMuted },
            ]}
          >
            <View
              style={[
                styles.sourceProgressFill,
                {
                  backgroundColor: warning ? colors.warning : colors.accent,
                  width: `${progress}%`,
                },
              ]}
            />
          </View>
        )}
      </View>
      {presentation.retryable && (
        <Pressable
          accessibilityRole="button"
          disabled={refreshing}
          onPress={retry}
          style={({ pressed }) => [
            styles.retryButton,
            {
              borderColor: warning ? colors.warningBorder : colors.accentBorder,
              opacity: pressed || refreshing ? 0.58 : 1,
            },
          ]}
        >
          {refreshing ? (
            <ActivityIndicator
              color={warning ? colors.warning : colors.accent}
              size="small"
            />
          ) : (
            <Text
              style={[
                styles.retryText,
                { color: warning ? colors.warning : colors.accent },
              ]}
            >
              Check again
            </Text>
          )}
        </Pressable>
      )}
    </View>
  );
}

function openQueueItem(item: ActionQueueItem) {
  if (item.caseId) {
    router.push({
      pathname: "/cases/[caseId]",
      params: { caseId: item.caseId },
    });
    return;
  }
  if (item.groupId) {
    router.push({
      pathname: "/suggestions/[groupId]",
      params: {
        groupId: item.groupId,
        ...(item.kind === "CANDIDATE" && item.id.startsWith("candidate:")
          ? { suggestionId: item.id.slice("candidate:".length) }
          : {}),
      },
    });
  }
}

function sourceDiscoveryStatus(
  presentation: GoogleDiscoveryPresentation,
  colors: ReturnType<typeof useAppTheme>["colors"],
): {
  background: string;
  label: string;
  text: string;
} {
  const progress = presentation.progress;
  const labels: Record<GoogleDiscoveryPresentation["kind"], string> = {
    CHECKING_CONNECTION: "Checking connections",
    DISCONNECTING: "Disconnecting",
    ERROR: presentation.badge,
    HAS_WORK: "Checked",
    INCOMPATIBLE_SERVER: "Scan error",
    LINKING: "Connecting",
    LOADING_RESULTS: "Organizing results",
    NO_CONNECTION: "Connection required",
    NO_WORK: "Checked",
    REANALYSIS_REQUIRED: "Refresh needed",
    SCANNING:
      progress !== null && progress > 0
        ? `Checking · ${progress}%`
        : "Checking",
  };
  const warning = presentation.tone === "warning";
  return {
    background: warning ? colors.warningSoft : colors.surface,
    label: labels[presentation.kind],
    text: warning ? colors.warning : colors.accent,
  };
}

function toneColors(
  tone: ActionQueueTone,
  colors: ReturnType<typeof useAppTheme>["colors"],
) {
  return {
    accent: { background: colors.accentSoft, text: colors.accent },
    danger: { background: colors.dangerSoft, text: colors.danger },
    muted: { background: colors.surfaceMuted, text: colors.textMuted },
    warning: { background: colors.warningSoft, text: colors.warning },
  }[tone];
}
