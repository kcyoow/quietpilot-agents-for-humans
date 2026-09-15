import { MaterialCommunityIcons } from "@expo/vector-icons";
import { StyleSheet, Text, View } from "react-native";

import { useAppTheme } from "@/src/theme/useAppTheme";

export function DevelopmentBanner() {
  const { colors } = useAppTheme();
  return (
    <View
      accessibilityLabel="Example data. No external changes."
      style={[
        styles.banner,
        {
          backgroundColor: colors.warningSoft,
          borderColor: colors.warningBorder,
        },
      ]}
    >
      <MaterialCommunityIcons
        color={colors.warning}
        name="flask-outline"
        size={16}
      />
      <Text style={[styles.text, { color: colors.warning }]}>
        EXAMPLE DATA · NO EXTERNAL CHANGES
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  banner: {
    alignItems: "center",
    alignSelf: "flex-start",
    borderRadius: 999,
    borderWidth: 1,
    flexDirection: "row",
    gap: 7,
    paddingHorizontal: 11,
    paddingVertical: 7,
  },
  text: { fontSize: 10, fontWeight: "800", letterSpacing: 0.35 },
});
