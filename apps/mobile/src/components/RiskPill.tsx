import { StyleSheet, Text, View } from "react-native";

import type { PrototypeRisk } from "@/src/prototype/types";
import { useAppTheme } from "@/src/theme/useAppTheme";

const labels: Record<PrototypeRisk, string> = {
  HIGH: "High risk",
  LOW: "Low risk",
  MEDIUM: "Medium risk",
};

export function RiskPill({ risk }: { risk: PrototypeRisk }) {
  const { colors } = useAppTheme();
  const foreground =
    risk === "HIGH"
      ? colors.danger
      : risk === "MEDIUM"
        ? colors.warning
        : colors.success;
  const background =
    risk === "HIGH"
      ? colors.dangerSoft
      : risk === "MEDIUM"
        ? colors.warningSoft
        : colors.successSoft;
  return (
    <View style={[styles.pill, { backgroundColor: background }]}>
      <Text style={[styles.label, { color: foreground }]}>{labels[risk]}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  label: { fontSize: 12, fontWeight: "600", lineHeight: 18 },
  pill: {
    alignSelf: "flex-start",
    borderRadius: 999,
    paddingHorizontal: 9,
    paddingVertical: 6,
  },
});
