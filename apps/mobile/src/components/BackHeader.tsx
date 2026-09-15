import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAppTheme } from "@/src/theme/useAppTheme";

export function BackHeader({
  showBack = true,
  title,
}: {
  showBack?: boolean;
  title: string;
}) {
  const { colors } = useAppTheme();
  return (
    <SafeAreaView
      edges={["top"]}
      style={{ backgroundColor: colors.background }}
    >
      <View style={styles.root}>
        {showBack ? (
          <Pressable
            accessibilityLabel="Back"
            accessibilityRole="button"
            onPress={() => router.back()}
            style={({ pressed }) => [
              styles.back,
              {
                backgroundColor: "transparent",
                opacity: pressed ? 0.72 : 1,
              },
            ]}
          >
            <MaterialCommunityIcons
              color={colors.text}
              name="arrow-left"
              size={21}
            />
          </Pressable>
        ) : (
          <View style={styles.spacer} />
        )}
        <Text
          accessibilityRole="header"
          style={[styles.title, { color: colors.text }]}
        >
          {title}
        </Text>
        <View style={styles.spacer} />
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  back: {
    alignItems: "center",
    borderRadius: 13,
    minHeight: 48,
    justifyContent: "center",
    width: 48,
  },
  root: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    paddingBottom: 8,
    paddingHorizontal: 20,
    paddingTop: 4,
  },
  spacer: { width: 48 },
  title: {
    flex: 1,
    fontSize: 18,
    fontWeight: "600",
    marginHorizontal: 8,
    textAlign: "center",
  },
});
