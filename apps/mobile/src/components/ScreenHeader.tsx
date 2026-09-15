import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAppTheme } from "@/src/theme/useAppTheme";

export function ScreenHeader({
  connectionLabel = "Connections",
  onSearch,
  searchOpen = false,
  subtitle,
  title,
}: {
  connectionLabel?: string;
  onSearch?(): void;
  searchOpen?: boolean;
  subtitle?: string;
  title: string;
}) {
  const { colors } = useAppTheme();
  return (
    <SafeAreaView
      edges={["top"]}
      style={{ backgroundColor: colors.background }}
    >
      <View style={styles.root}>
        <View style={styles.brandRow}>
          <View style={styles.brand}>
            <View
              style={[styles.brandMark, { backgroundColor: colors.accentSoft }]}
            >
              <MaterialCommunityIcons
                color={colors.accent}
                name="radar"
                size={18}
              />
            </View>
            <Text style={[styles.brandName, { color: colors.text }]}>
              QuietPilot
            </Text>
          </View>
          <View style={styles.actions}>
            <HeaderButton
              accessibilityLabel="Manage connections"
              icon="link-variant"
              label={connectionLabel}
              onPress={() => router.push("/connections")}
            />
            <HeaderButton
              accessibilityLabel="Settings"
              icon="cog-outline"
              onPress={() => router.push("/settings")}
            />
          </View>
        </View>
        <View style={styles.headingRow}>
          <View style={styles.headingCopy}>
            <Text
              accessibilityRole="header"
              style={[styles.title, { color: colors.text }]}
            >
              {title}
            </Text>
            {subtitle && (
              <Text style={[styles.subtitle, { color: colors.textMuted }]}>
                {subtitle}
              </Text>
            )}
          </View>
          {onSearch && (
            <HeaderButton
              accessibilityLabel="Search and filters"
              expanded={searchOpen}
              icon={searchOpen ? "close" : "magnify"}
              label={searchOpen ? "Close" : "Search"}
              onPress={onSearch}
            />
          )}
        </View>
      </View>
    </SafeAreaView>
  );
}

function HeaderButton({
  accessibilityLabel,
  expanded,
  icon,
  label,
  onPress,
}: {
  accessibilityLabel: string;
  expanded?: boolean;
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  label?: string;
  onPress(): void;
}) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      accessibilityLabel={accessibilityLabel}
      accessibilityRole="button"
      accessibilityState={expanded === undefined ? undefined : { expanded }}
      accessibilityValue={label ? { text: label } : undefined}
      onPress={onPress}
      style={({ pressed }) => [
        styles.headerButton,
        label && styles.headerButtonLabeled,
        {
          backgroundColor: expanded ? colors.accentSoft : "transparent",
          opacity: pressed ? 0.72 : 1,
        },
      ]}
    >
      <MaterialCommunityIcons color={colors.textMuted} name={icon} size={20} />
      {label && (
        <Text style={[styles.headerButtonLabel, { color: colors.textMuted }]}>
          {label}
        </Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: "row", gap: 2 },
  brand: { alignItems: "center", flexDirection: "row", gap: 8 },
  brandMark: {
    alignItems: "center",
    borderRadius: 11,
    height: 28,
    justifyContent: "center",
    width: 28,
  },
  brandName: { fontSize: 13, fontWeight: "600", letterSpacing: -0.2 },
  brandRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
  headerButton: {
    alignItems: "center",
    borderRadius: 13,
    minHeight: 48,
    justifyContent: "center",
    minWidth: 48,
  },
  headerButtonLabel: { fontSize: 12, fontWeight: "500" },
  headerButtonLabeled: {
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 10,
    width: "auto",
  },
  headingCopy: { flex: 1 },
  headingRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    minHeight: 52,
  },
  root: { paddingBottom: 8, paddingHorizontal: 20, paddingTop: 2 },
  subtitle: { fontSize: 13, lineHeight: 19, marginTop: 4 },
  title: {
    fontSize: 26,
    fontWeight: "600",
    letterSpacing: -0.5,
  },
});
