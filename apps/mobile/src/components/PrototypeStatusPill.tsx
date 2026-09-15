import { StyleSheet, Text, View } from "react-native";

import type { PrototypeCaseStatus } from "@/src/prototype/types";
import { useAppTheme } from "@/src/theme/useAppTheme";

const labels: Record<PrototypeCaseStatus, string> = {
  APPROVED: "Approved",
  COMPLETED: "Completed",
  DECISION_REQUIRED: "Decision needed",
  FAILED: "Failed",
  PAUSED: "Deferred",
  PERMISSION_REVOKED: "Permission revoked",
  PREPARING: "Preparing",
  QUEUED: "Queued",
  RUNNING: "Running",
  STOPPED: "Stopped",
  VERIFYING: "Verifying result",
};

export function PrototypeStatusPill({
  status,
}: {
  status: PrototypeCaseStatus;
}) {
  const { colors } = useAppTheme();
  const tone =
    status === "DECISION_REQUIRED"
      ? "warning"
      : status === "FAILED" || status === "PERMISSION_REVOKED"
        ? "danger"
        : status === "COMPLETED"
          ? "success"
          : status === "STOPPED" || status === "PAUSED"
            ? "neutral"
            : "accent";
  const foreground =
    tone === "warning"
      ? colors.warning
      : tone === "danger"
        ? colors.danger
        : tone === "success"
          ? colors.success
          : tone === "neutral"
            ? colors.textMuted
            : colors.accent;
  const background =
    tone === "warning"
      ? colors.warningSoft
      : tone === "danger"
        ? colors.dangerSoft
        : tone === "success"
          ? colors.successSoft
          : colors.surfaceMuted;
  return (
    <View style={[styles.pill, { backgroundColor: background }]}>
      <View style={[styles.dot, { backgroundColor: foreground }]} />
      <Text style={[styles.label, { color: foreground }]}>
        {labels[status]}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  dot: { borderRadius: 3, height: 6, width: 6 },
  label: { fontSize: 12, fontWeight: "600", lineHeight: 18 },
  pill: {
    alignItems: "center",
    alignSelf: "flex-start",
    borderRadius: 999,
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 9,
    paddingVertical: 6,
  },
});
