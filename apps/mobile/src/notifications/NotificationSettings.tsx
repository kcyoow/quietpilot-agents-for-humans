import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { BackHeader } from "@/src/components/BackHeader";
import { useAppTheme } from "@/src/theme/useAppTheme";
import { useNotifications } from "./NotificationProvider";

export function NotificationSettings() {
  const state = useNotifications();
  const { colors } = useAppTheme();
  const unavailable = state.status === "unavailable";
  return (
    <View
      style={[
        styles.card,
        { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      <Text style={[styles.title, { color: colors.text }]}>
        Only the alerts you need
      </Text>
      <Text style={[styles.body, { color: colors.textMuted }]}>
        Get alerts for decisions and problems. Suggestions and successful tasks
        stay quiet.
      </Text>
      <Text
        accessibilityLiveRegion="polite"
        style={[styles.status, { color: colors.text }]}
      >
        {state.busy
          ? "Checking notifications"
          : state.enabled
            ? "Notifications on"
            : state.status === "denied"
              ? "Device permission off"
              : unavailable
                ? "Notifications unavailable"
                : state.status === "error"
                  ? "Check notification status"
                  : "Notifications off"}
      </Text>
      {state.message && (
        <Text style={[styles.body, { color: colors.textMuted }]}>
          {state.message}
        </Text>
      )}
      {state.busy && <ActivityIndicator color={colors.accent} />}
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: state.busy || unavailable }}
        disabled={state.busy || unavailable}
        onPress={() => {
          void state.enable();
        }}
        style={[
          styles.button,
          {
            backgroundColor: colors.accent,
            opacity: state.busy || unavailable ? 0.45 : 1,
          },
        ]}
      >
        <Text style={[styles.buttonText, { color: colors.onAccent }]}>
          Enable notifications
        </Text>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ disabled: state.busy }}
        disabled={state.busy}
        onPress={() => {
          void state.disable();
        }}
        style={[styles.button, { borderWidth: 1, borderColor: colors.border }]}
      >
        <Text style={[styles.buttonText, { color: colors.text }]}>
          Disable notifications
        </Text>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        disabled={state.busy}
        onPress={() => {
          void state.refresh();
        }}
        style={styles.refresh}
      >
        <Text style={{ color: colors.accent }}>Refresh status</Text>
      </Pressable>
    </View>
  );
}

export function NotificationSettingsScreen() {
  const { colors } = useAppTheme();
  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <BackHeader title="Notifications" />
      <ScrollView contentContainerStyle={styles.content}>
        <NotificationSettings />
      </ScrollView>
    </View>
  );
}
export default NotificationSettingsScreen;

const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: { padding: 20 },
  card: { borderWidth: 1, borderRadius: 18, padding: 20, gap: 14 },
  title: { fontSize: 20, fontWeight: "700" },
  body: { fontSize: 14, lineHeight: 22 },
  status: { fontSize: 16, fontWeight: "600", marginTop: 10 },
  button: {
    minHeight: 48,
    borderRadius: 12,
    alignItems: "center",
    justifyContent: "center",
  },
  buttonText: { fontSize: 15, fontWeight: "600" },
  refresh: { alignItems: "center", padding: 12 },
});
