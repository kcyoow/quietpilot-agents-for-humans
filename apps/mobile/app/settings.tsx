import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Redirect, router } from "expo-router";
import { type ReactNode, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useAuth } from "@/src/auth/AuthProvider";
import { BackHeader } from "@/src/components/BackHeader";
import { AppearanceSettings } from "@/src/theme/AppearanceSettings";
import { useAppTheme } from "@/src/theme/useAppTheme";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";

export default function SettingsScreen() {
  const { colors } = useAppTheme();
  const { signOut, status, user } = useAuth();
  const { source } = useWorkspace();
  const [showSignOut, setShowSignOut] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (status === "ready" && !user) {
    return <Redirect href="/" />;
  }

  if (!user) {
    return (
      <View style={[styles.loading, { backgroundColor: colors.background }]}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  async function confirmSignOut() {
    setError(null);
    try {
      await signOut();
      setShowSignOut(false);
      router.replace("/");
    } catch (caught) {
      setShowSignOut(false);
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not sign out. Try again shortly.",
      );
    }
  }

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <BackHeader title="Settings" />
      <ScrollView contentContainerStyle={styles.content}>
        <View
          style={[
            styles.profile,
            { backgroundColor: colors.surface, borderColor: colors.border },
          ]}
        >
          <View style={[styles.avatar, { backgroundColor: colors.accentSoft }]}>
            <MaterialCommunityIcons
              color={colors.accent}
              name="account-outline"
              size={25}
            />
          </View>
          <View style={styles.profileCopy}>
            <Text style={[styles.profileName, { color: colors.text }]}>
              {user.displayName}
            </Text>
            <Text style={[styles.profileEmail, { color: colors.textMuted }]}>
              {user.email}
            </Text>
          </View>
          <View
            style={[styles.verified, { backgroundColor: colors.successSoft }]}
          >
            <MaterialCommunityIcons
              color={colors.success}
              name="check-decagram-outline"
              size={14}
            />
            <Text style={[styles.verifiedText, { color: colors.success }]}>
              Verified
            </Text>
          </View>
        </View>

        {error && (
          <View style={[styles.error, { backgroundColor: colors.dangerSoft }]}>
            <MaterialCommunityIcons
              color={colors.danger}
              name="alert-circle-outline"
              size={18}
            />
            <Text
              accessibilityLiveRegion="polite"
              style={[styles.errorText, { color: colors.danger }]}
            >
              {error}
            </Text>
          </View>
        )}

        <Section title="Appearance">
          <AppearanceSettings />
        </Section>

        <Section title="Connections and automation">
          <SettingsRow
            body="Alerts for decisions and tasks that need attention."
            icon="bell-outline"
            onPress={() => router.push("/notifications")}
            title="Notifications"
          />
          <SettingsRow
            body="Prepare plans from new mail and manage their rules."
            icon="repeat"
            onPress={() => router.push("/routines")}
            title="Preparation routines"
          />
          <SettingsRow
            body="Manage connected services and permissions."
            icon="link-variant"
            onPress={() => router.push("/connections")}
            title="Connections"
          />
          <SettingsRow
            body={
              source === "LIVE"
                ? "External actions require individual approval."
                : "Review permissions for example tasks."
            }
            icon="shield-key-outline"
            onPress={() => router.push("/policies")}
            title="Permissions and automation"
          />
        </Section>

        <Section title="Account security">
          <SettingsRow
            body="Update your password."
            icon="lock-reset"
            onPress={() => router.push("/password-reset")}
            title="Change password"
          />
        </Section>

        <Section title="Sign in">
          <Pressable
            accessibilityRole="button"
            onPress={() => setShowSignOut(true)}
            style={({ pressed }) => [
              styles.signOut,
              {
                backgroundColor: colors.surface,
                borderColor: colors.border,
                opacity: pressed ? 0.72 : 1,
              },
            ]}
          >
            <MaterialCommunityIcons
              color={colors.textMuted}
              name="logout-variant"
              size={21}
            />
            <View style={styles.rowCopy}>
              <Text style={[styles.rowTitle, { color: colors.text }]}>
                Sign out
              </Text>
              <Text style={[styles.rowBody, { color: colors.textMuted }]}>
                Sign out on this device. Connections and tasks are kept.
              </Text>
            </View>
          </Pressable>
        </Section>
      </ScrollView>

      <Modal
        animationType="fade"
        onRequestClose={() => setShowSignOut(false)}
        transparent
        visible={showSignOut}
      >
        <View style={styles.modalBackdrop}>
          <View
            style={[
              styles.modalCard,
              {
                backgroundColor: colors.surfaceRaised,
                borderColor: colors.border,
              },
            ]}
          >
            <View
              style={[styles.modalIcon, { backgroundColor: colors.accentSoft }]}
            >
              <MaterialCommunityIcons
                color={colors.accent}
                name="logout-variant"
                size={24}
              />
            </View>
            <Text style={[styles.modalTitle, { color: colors.text }]}>
              Sign out?
            </Text>
            <Text style={[styles.modalBody, { color: colors.textMuted }]}>
              Connections and active tasks will remain.
            </Text>
            <View style={styles.modalButtons}>
              <Pressable
                accessibilityRole="button"
                onPress={() => setShowSignOut(false)}
                style={[styles.modalButton, { borderColor: colors.border }]}
              >
                <Text
                  style={[styles.modalButtonText, { color: colors.textMuted }]}
                >
                  Cancel
                </Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                disabled={status === "loading"}
                onPress={() => void confirmSignOut()}
                style={[
                  styles.modalButton,
                  {
                    backgroundColor: colors.accent,
                    borderColor: colors.accent,
                    opacity: status === "loading" ? 0.72 : 1,
                  },
                ]}
              >
                {status === "loading" ? (
                  <ActivityIndicator color={colors.onAccent} />
                ) : (
                  <Text
                    style={[styles.modalButtonText, { color: colors.onAccent }]}
                  >
                    Sign out
                  </Text>
                )}
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

function Section({ children, title }: { children: ReactNode; title: string }) {
  const { colors } = useAppTheme();
  return (
    <View style={styles.section}>
      <Text style={[styles.sectionTitle, { color: colors.text }]}>{title}</Text>
      <View style={styles.list}>{children}</View>
    </View>
  );
}

function SettingsRow({
  body,
  icon,
  onPress,
  title,
}: {
  body: string;
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  onPress(): void;
  title: string;
}) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      accessibilityRole="button"
      onPress={onPress}
      style={({ pressed }) => [
        styles.row,
        {
          backgroundColor: colors.surface,
          borderColor: colors.border,
          opacity: pressed ? 0.72 : 1,
        },
      ]}
    >
      <View style={[styles.rowIcon, { backgroundColor: colors.accentSoft }]}>
        <MaterialCommunityIcons color={colors.accent} name={icon} size={21} />
      </View>
      <View style={styles.rowCopy}>
        <Text style={[styles.rowTitle, { color: colors.text }]}>{title}</Text>
        <Text style={[styles.rowBody, { color: colors.textMuted }]}>
          {body}
        </Text>
      </View>
      <MaterialCommunityIcons
        color={colors.textSubtle}
        name="chevron-right"
        size={22}
      />
    </Pressable>
  );
}

const styles = StyleSheet.create({
  avatar: {
    alignItems: "center",
    borderRadius: 16,
    height: 48,
    justifyContent: "center",
    width: 48,
  },
  content: { padding: 18, paddingBottom: 34 },
  error: {
    alignItems: "center",
    borderRadius: 13,
    flexDirection: "row",
    gap: 9,
    marginTop: 12,
    padding: 12,
  },
  errorText: { flex: 1, fontSize: 12, fontWeight: "700" },
  list: { gap: 9 },
  loading: { alignItems: "center", flex: 1, justifyContent: "center" },
  modalBackdrop: {
    alignItems: "center",
    backgroundColor: "rgba(5, 12, 11, 0.52)",
    flex: 1,
    justifyContent: "center",
    padding: 24,
  },
  modalBody: {
    fontSize: 13,
    lineHeight: 20,
    marginTop: 8,
    textAlign: "center",
  },
  modalButton: {
    alignItems: "center",
    borderRadius: 13,
    borderWidth: 1,
    flex: 1,
    justifyContent: "center",
    minHeight: 48,
  },
  modalButtons: { flexDirection: "row", gap: 9, marginTop: 20 },
  modalButtonText: { fontSize: 14, fontWeight: "600" },
  modalCard: {
    borderRadius: 21,
    borderWidth: 1,
    maxWidth: 380,
    padding: 20,
    width: "100%",
  },
  modalIcon: {
    alignItems: "center",
    alignSelf: "center",
    borderRadius: 16,
    height: 50,
    justifyContent: "center",
    width: 50,
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: "600",
    marginTop: 14,
    textAlign: "center",
  },
  profile: {
    alignItems: "center",
    borderRadius: 20,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    padding: 16,
  },
  profileCopy: { flex: 1 },
  profileEmail: { fontSize: 12, marginTop: 4 },
  profileName: { fontSize: 17, fontWeight: "600" },
  row: {
    alignItems: "center",
    borderRadius: 17,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    minHeight: 78,
    padding: 14,
  },
  rowBody: { fontSize: 13, lineHeight: 20, marginTop: 4 },
  rowCopy: { flex: 1 },
  rowIcon: {
    alignItems: "center",
    borderRadius: 13,
    height: 42,
    justifyContent: "center",
    width: 42,
  },
  rowTitle: { fontSize: 15, fontWeight: "600" },
  screen: { flex: 1 },
  section: { marginTop: 25 },
  sectionTitle: { fontSize: 15, fontWeight: "600", marginBottom: 10 },
  signOut: {
    alignItems: "center",
    borderRadius: 17,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    minHeight: 72,
    padding: 15,
  },
  verified: {
    alignItems: "center",
    borderRadius: 999,
    flexDirection: "row",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  verifiedText: { fontSize: 11, fontWeight: "600" },
});
