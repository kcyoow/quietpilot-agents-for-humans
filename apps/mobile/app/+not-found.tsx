import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router, Stack } from "expo-router";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { useAppTheme } from "@/src/theme/useAppTheme";

export default function NotFoundScreen() {
  const { colors } = useAppTheme();
  return (
    <>
      <Stack.Screen options={{ title: "Page not found" }} />
      <View style={[styles.container, { backgroundColor: colors.background }]}>
        <View
          style={[
            styles.card,
            { backgroundColor: colors.surface, borderColor: colors.border },
          ]}
        >
          <MaterialCommunityIcons
            color={colors.accent}
            name="map-search-outline"
            size={28}
          />
          <Text style={[styles.title, { color: colors.text }]}>
            Page not found
          </Text>
          <Text style={[styles.body, { color: colors.textMuted }]}>
            Return home to continue.
          </Text>
          <Pressable
            accessibilityRole="button"
            onPress={() => router.replace("/")}
            style={({ pressed }) => [
              styles.link,
              { backgroundColor: colors.accent, opacity: pressed ? 0.72 : 1 },
            ]}
          >
            <Text style={[styles.linkText, { color: colors.onAccent }]}>
              Go home
            </Text>
          </Pressable>
        </View>
      </View>
    </>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 20,
  },
  card: {
    alignItems: "center",
    borderRadius: 18,
    borderWidth: 1,
    gap: 14,
    maxWidth: 420,
    padding: 24,
    width: "100%",
  },
  title: {
    fontSize: 20,
    fontWeight: "600",
    textAlign: "center",
  },
  body: { fontSize: 14, lineHeight: 21, textAlign: "center" },
  link: {
    alignItems: "center",
    borderRadius: 12,
    justifyContent: "center",
    marginTop: 4,
    minHeight: 48,
    paddingHorizontal: 16,
    paddingVertical: 12,
    width: "100%",
  },
  linkText: {
    fontSize: 14,
    fontWeight: "600",
  },
});
