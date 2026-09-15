import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Pressable, StyleSheet, Text, View } from "react-native";

import { themeColors, type AppPalette, type AppearanceMode } from "./tokens";
import { useAppTheme } from "./useAppTheme";

const palettes: { key: AppPalette; label: string }[] = [
  { key: "blue", label: "Blue" },
  { key: "coral", label: "Coral" },
];
const modes: { key: AppearanceMode; label: string }[] = [
  { key: "system", label: "System" },
  { key: "light", label: "Light" },
  { key: "dark", label: "Dark" },
];

export function AppearanceSettings() {
  const { colors, mode, palette, persistenceError, setMode, setPalette } =
    useAppTheme();

  return (
    <View
      style={[
        styles.root,
        { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      <Text style={[styles.label, { color: colors.text }]}>Color</Text>
      <View
        accessibilityRole="radiogroup"
        accessibilityLabel="Theme color"
        style={styles.options}
      >
        {palettes.map((item) => {
          const selected = palette === item.key;
          return (
            <Pressable
              key={item.key}
              accessibilityRole="radio"
              accessibilityLabel={`${item.label} theme`}
              accessibilityState={{ checked: selected }}
              onPress={() => setPalette(item.key)}
              style={({ pressed }) => [
                styles.option,
                {
                  backgroundColor: selected
                    ? colors.accentSoft
                    : colors.surface,
                  borderColor: selected ? colors.accent : colors.border,
                  opacity: pressed ? 0.72 : 1,
                },
              ]}
            >
              <View
                style={[
                  styles.swatch,
                  { backgroundColor: themeColors[item.key].light.accent },
                ]}
              />
              <Text
                style={[
                  styles.optionText,
                  { color: selected ? colors.accent : colors.text },
                ]}
              >
                {item.label}
              </Text>
              {selected && (
                <MaterialCommunityIcons
                  color={colors.accent}
                  name="check"
                  size={18}
                />
              )}
            </Pressable>
          );
        })}
      </View>

      <Text style={[styles.label, styles.modeLabel, { color: colors.text }]}>
        Appearance
      </Text>
      <View
        accessibilityRole="radiogroup"
        accessibilityLabel="Appearance mode"
        style={styles.options}
      >
        {modes.map((item) => {
          const selected = mode === item.key;
          return (
            <Pressable
              key={item.key}
              accessibilityRole="radio"
              accessibilityLabel={
                item.key === "system"
                  ? "Follow device settings"
                  : `${item.label} mode`
              }
              accessibilityState={{ checked: selected }}
              onPress={() => setMode(item.key)}
              style={({ pressed }) => [
                styles.option,
                styles.modeOption,
                {
                  backgroundColor: selected
                    ? colors.accentSoft
                    : colors.surface,
                  borderColor: selected ? colors.accent : colors.border,
                  opacity: pressed ? 0.72 : 1,
                },
              ]}
            >
              <MaterialCommunityIcons
                color={selected ? colors.accent : colors.textMuted}
                name={selected ? "radiobox-marked" : "radiobox-blank"}
                size={17}
              />
              <Text
                style={[
                  styles.optionText,
                  { color: selected ? colors.accent : colors.text },
                ]}
              >
                {item.label}
              </Text>
            </Pressable>
          );
        })}
      </View>
      <Text style={[styles.hint, { color: colors.textMuted }]}>
        {mode === "system"
          ? "Follow your device's light or dark mode."
          : "Use the selected appearance."}
      </Text>
      {persistenceError && (
        <Text
          accessibilityRole="alert"
          style={[styles.hint, { color: colors.danger }]}
        >
          {persistenceError}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { borderRadius: 14, borderWidth: 1, padding: 16 },
  label: { fontSize: 14, fontWeight: "600", marginBottom: 10 },
  modeLabel: { marginTop: 20 },
  options: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  option: {
    alignItems: "center",
    borderRadius: 9,
    borderWidth: 1,
    flexDirection: "row",
    flexBasis: 0,
    flexGrow: 1,
    gap: 8,
    justifyContent: "center",
    minHeight: 48,
    minWidth: 88,
    paddingHorizontal: 12,
    paddingVertical: 10,
  },
  modeOption: { gap: 6, paddingHorizontal: 10 },
  optionText: { fontSize: 14, fontWeight: "600", flexShrink: 1 },
  swatch: { borderRadius: 6, width: 18, height: 18 },
  hint: { fontSize: 13, lineHeight: 20, marginTop: 10 },
});
