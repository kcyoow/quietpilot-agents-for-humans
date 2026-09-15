import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useState, type ReactNode } from "react";
import { Pressable, Text, View } from "react-native";

import type {
  PrototypeEvidence,
  PrototypeTimelineItem,
} from "@/src/prototype/types";
import { useAppTheme } from "@/src/theme/useAppTheme";

import { humanCopy, humanEvidenceDetail } from "./presentation";
import { styles } from "./styles";

export function DisclosureSection({
  children,
  count,
  expanded,
  icon,
  onPress,
  summary,
  title,
}: {
  children: ReactNode;
  count: number;
  expanded: boolean;
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  onPress(): void;
  summary?: ReactNode;
  title: string;
}) {
  const { colors } = useAppTheme();
  return (
    <View
      style={[
        styles.disclosure,
        { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      <Pressable
        accessibilityLabel={`${title} ${expanded ? "Collapse" : "Expand"}`}
        accessibilityRole="button"
        accessibilityState={{ expanded }}
        onPress={onPress}
        style={({ pressed }) => [
          styles.disclosureHeader,
          { opacity: pressed ? 0.68 : 1 },
        ]}
      >
        <View
          style={[
            styles.disclosureIcon,
            { backgroundColor: colors.accentSoft },
          ]}
        >
          <MaterialCommunityIcons color={colors.accent} name={icon} size={18} />
        </View>
        <Text style={[styles.disclosureTitle, { color: colors.text }]}>
          {title}
        </Text>
        <Text style={[styles.disclosureCount, { color: colors.textSubtle }]}>
          {count}
        </Text>
        <MaterialCommunityIcons
          color={colors.textSubtle}
          name={expanded ? "chevron-up" : "chevron-down"}
          size={21}
        />
      </Pressable>
      {!expanded && summary}
      {expanded && (
        <View
          style={[styles.disclosureBody, { borderTopColor: colors.border }]}
        >
          {children}
        </View>
      )}
    </View>
  );
}

export function EvidenceItem({ evidence }: { evidence: PrototypeEvidence }) {
  const { colors } = useAppTheme();
  const [showOriginal, setShowOriginal] = useState(false);
  return (
    <View style={[styles.evidenceItem, { borderColor: colors.border }]}>
      <Text style={[styles.evidenceTitle, { color: colors.text }]}>
        {evidence.label}
      </Text>
      <Text style={[styles.evidenceText, { color: colors.textMuted }]}>
        {humanEvidenceDetail(evidence.detail)}
      </Text>
      <Pressable
        accessibilityLabel={`${evidence.label} Original source ${showOriginal ? "Collapse" : "Expand"}`}
        accessibilityRole="button"
        accessibilityState={{ expanded: showOriginal }}
        onPress={() => setShowOriginal((current) => !current)}
        style={styles.inlineDisclosure}
      >
        <Text style={[styles.inlineDisclosureText, { color: colors.accent }]}>
          {showOriginal ? "Hide source" : "View source"}
        </Text>
        <MaterialCommunityIcons
          color={colors.accent}
          name={showOriginal ? "chevron-up" : "chevron-down"}
          size={18}
        />
      </Pressable>
      {showOriginal && (
        <View
          style={[styles.originalBox, { backgroundColor: colors.surfaceMuted }]}
        >
          <Text
            selectable
            style={[styles.originalText, { color: colors.text }]}
          >
            {evidence.detail}
          </Text>
          <Text style={[styles.originalMeta, { color: colors.textMuted }]}>
            Saved source · Version {evidence.revision}
          </Text>
        </View>
      )}
    </View>
  );
}

export function SecondaryButton({
  danger = false,
  disabled = false,
  label,
  onPress,
}: {
  danger?: boolean;
  disabled?: boolean;
  label: string;
  onPress(): void;
}) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.secondaryButton,
        {
          borderColor: danger ? colors.danger : colors.border,
          opacity: pressed || disabled ? 0.7 : 1,
        },
      ]}
    >
      <Text
        style={[
          styles.secondaryText,
          { color: danger ? colors.danger : colors.textMuted },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

export function TimelineItem({
  isLast,
  item,
}: {
  isLast: boolean;
  item: PrototypeTimelineItem;
}) {
  const { colors } = useAppTheme();
  const activeColor =
    item.state === "DONE"
      ? colors.success
      : item.state === "FAILED"
        ? colors.danger
        : item.state === "CURRENT"
          ? colors.accent
          : colors.textSubtle;
  return (
    <View style={styles.timelineRow}>
      <View style={styles.timelineRail}>
        <View style={[styles.timelineDot, { backgroundColor: activeColor }]} />
        {!isLast && (
          <View
            style={[styles.timelineLine, { backgroundColor: colors.border }]}
          />
        )}
      </View>
      <View style={styles.timelineCopy}>
        <Text style={[styles.timelineTitle, { color: colors.text }]}>
          {item.label}
        </Text>
        <Text style={[styles.timelineBody, { color: colors.textMuted }]}>
          {humanCopy(item.body)}
        </Text>
        <Text style={[styles.timelineTime, { color: colors.textSubtle }]}>
          {new Date(item.occurredAt).toLocaleString("en-US", {
            hour: "2-digit",
            minute: "2-digit",
          })}
        </Text>
      </View>
    </View>
  );
}
