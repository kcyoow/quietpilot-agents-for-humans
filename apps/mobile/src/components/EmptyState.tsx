import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useAppTheme } from "@/src/theme/useAppTheme";

export function EmptyState({
  actionLabel,
  body,
  onAction,
  title,
}: {
  actionLabel?: string;
  body: string;
  onAction?(): void;
  title: string;
}) {
  const { colors } = useAppTheme();
  return (
    <View
      style={[
        styles.root,
        { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      <MaterialCommunityIcons color={colors.textSubtle} name="tray" size={28} />
      <Text style={[styles.title, { color: colors.text }]}>{title}</Text>
      <Text style={[styles.body, { color: colors.textMuted }]}>{body}</Text>
      {actionLabel && onAction && (
        <Pressable
          accessibilityRole="button"
          onPress={onAction}
          style={({ pressed }) => [
            styles.action,
            {
              backgroundColor: colors.accentSoft,
              borderColor: colors.accentBorder,
              opacity: pressed ? 0.72 : 1,
            },
          ]}
        >
          <MaterialCommunityIcons
            color={colors.accent}
            name="link-variant"
            size={17}
          />
          <Text style={[styles.actionText, { color: colors.accent }]}>
            {actionLabel}
          </Text>
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  action: {
    alignItems: "center",
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 7,
    marginTop: 17,
    minHeight: 48,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  actionText: { fontSize: 13, fontWeight: "600" },
  body: { fontSize: 14, lineHeight: 21, marginTop: 7, textAlign: "center" },
  root: { alignItems: "center", borderRadius: 18, borderWidth: 1, padding: 28 },
  title: { fontSize: 16, fontWeight: "600", marginTop: 12 },
});
