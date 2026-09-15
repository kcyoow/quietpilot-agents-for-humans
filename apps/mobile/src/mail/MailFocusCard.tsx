import { router } from "expo-router";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useAppTheme } from "@/src/theme/useAppTheme";
import { isMailConfigured } from "./mailApi";
import { useMailInterestState } from "./MailInterestProvider";

type MailConnectionProps = {
  connectionStatus: string;
  connectionVersion?: number;
  checking: boolean;
  hasMailAccess?: boolean;
};

function isConnected({
  connectionStatus,
  hasMailAccess = false,
}: MailConnectionProps) {
  return (
    ["CONNECTED", "SCANNING", "ERROR"].includes(connectionStatus) ||
    (connectionStatus === "CONNECTING" && hasMailAccess)
  );
}

export function MailFocusActions(props: MailConnectionProps) {
  const { colors } = useAppTheme();
  const mail = useMailInterestState();
  const configured = mail.state ? isMailConfigured(mail.state.profile) : false;
  const canEdit = mail.enabled && Boolean(mail.state);
  if (!canEdit) {
    return !isConnected(props) ? (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Connect mail"
        disabled={props.checking}
        onPress={() => router.push("/connections")}
        style={styles.smallButton}
      >
        <Text style={[styles.buttonText, { color: colors.accent }]}>
          Mail connection
        </Text>
      </Pressable>
    ) : null;
  }
  return (
    <View style={styles.actions}>
      {configured && (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Browse mail by interest"
          onPress={() => router.push("/mail")}
          style={styles.smallButton}
        >
          <Text style={[styles.buttonText, { color: colors.textMuted }]}>
            View mail
          </Text>
        </Pressable>
      )}
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={
          configured ? "Review mail interests" : "Set mail interests"
        }
        onPress={() => router.push("/mail-interests")}
        style={styles.smallButton}
      >
        <Text style={[styles.buttonText, { color: colors.accent }]}>
          {configured ? "Edit interests" : "Set interests"}
        </Text>
      </Pressable>
    </View>
  );
}

export function MailFocusCard(props: MailConnectionProps) {
  const { colors } = useAppTheme();
  const mail = useMailInterestState();
  if (!mail.enabled) return null;
  const authorizationRequired =
    mail.state?.recommendations.error_code === "GOOGLE_AUTH_REQUIRED" ||
    mail.state?.scan.error_code === "GOOGLE_AUTH_REQUIRED";
  const needsConnection = !isConnected(props) || authorizationRequired;
  const firstConnection =
    props.connectionStatus === "DISCONNECTED" &&
    (props.connectionVersion ?? 0) === 0 &&
    !props.hasMailAccess &&
    !authorizationRequired;
  const savedInterests = mail.state
    ? isMailConfigured(mail.state.profile)
    : false;
  const preparationIncomplete =
    (mail.state?.scan.status === "READY" ||
      mail.state?.scan.status === "PENDING") &&
    mail.state.scan.error_code === "MAIL_ACTION_PREPARATION_INCOMPLETE";
  const summary = !mail.state
    ? (mail.error ?? "Loading interests.")
    : needsConnection
      ? firstConnection
        ? "Connect Google to find relevant mail."
        : savedInterests
          ? "Reconnect Google. Your saved interests will be kept."
          : "Reconnect Google to resume mail checks."
      : mail.error
        ? "Could not refresh mail status. Please try again."
        : preparationIncomplete
          ? "Mail found. Some task suggestions are still incomplete."
          : mail.state.scan.status === "PENDING"
            ? "Finding relevant mail"
            : mail.state.scan.status === "ERROR"
              ? "Open Mail to check the scan."
              : null;
  if (!summary) return null;
  return (
    <View style={styles.statusRow}>
      {mail.loading && !mail.state && (
        <ActivityIndicator color={colors.accent} size="small" />
      )}
      <Text style={[styles.body, { color: colors.textMuted }]}>{summary}</Text>
      {needsConnection && mail.state ? (
        <Pressable
          accessibilityRole="button"
          onPress={() => router.push("/connections")}
          style={styles.smallButton}
        >
          <Text style={[styles.buttonText, { color: colors.accent }]}>
            {firstConnection ? "Connect Google" : "Reconnect"}
          </Text>
        </Pressable>
      ) : (!mail.state || mail.error) && !mail.loading ? (
        <Pressable
          accessibilityRole="button"
          onPress={() => void mail.refresh()}
          style={styles.smallButton}
        >
          <Text style={[styles.buttonText, { color: colors.accent }]}>
            Check again
          </Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  actions: { flexDirection: "row", alignItems: "center", gap: 6 },
  statusRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingBottom: 8,
  },
  body: { flex: 1, fontSize: 14, lineHeight: 21 },
  smallButton: {
    minHeight: 48,
    paddingHorizontal: 4,
    justifyContent: "center",
  },
  buttonText: { fontSize: 14, fontWeight: "600", lineHeight: 21 },
});
