import { router, useFocusEffect } from "expo-router";
import { useCallback, useEffect, useRef } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAuth } from "@/src/auth/AuthProvider";
import { BackHeader } from "@/src/components/BackHeader";
import { useGoogleConnection } from "@/src/connections/useGoogleConnection";
import { useAppTheme } from "@/src/theme/useAppTheme";
import { MailInterestEditor } from "./MailInterestEditor";
import { useMailInterestState } from "./MailInterestProvider";

export default function MailInterestsScreen() {
  const { user } = useAuth();
  return <MailInterestsContent key={user?.userId ?? "guest"} />;
}

function MailInterestsContent() {
  const { colors } = useAppTheme();
  const mail = useMailInterestState();
  const google = useGoogleConnection();
  const active = useRef(true);
  const refreshMail = mail.refresh;
  const refreshGoogle = google.refresh;
  const focusedOnce = useRef(false);

  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
    };
  }, []);

  useFocusEffect(
    useCallback(() => {
      void refreshMail();
      // The connection hook owns the initial read; refresh it when this screen returns.
      if (focusedOnce.current) void refreshGoogle().catch(() => undefined);
      focusedOnce.current = true;
    }, [refreshGoogle, refreshMail]),
  );

  useEffect(() => {
    if (
      mail.enabled &&
      !google.checking &&
      !google.error &&
      google.connection.status === "CONNECTED" &&
      google.connection.error !== "GOOGLE_AUTH_REQUIRED" &&
      mail.state?.recommendations.status === "NOT_STARTED" &&
      !mail.requesting &&
      !mail.error
    ) {
      void mail.recommend().catch(() => undefined);
    }
  }, [
    google.checking,
    google.error,
    google.connection.status,
    google.connection.error,
    mail,
  ]);

  function close() {
    if (router.canGoBack()) router.back();
    else router.replace("/(tabs)");
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      style={[styles.screen, { backgroundColor: colors.background }]}
    >
      <BackHeader title="Mail interests" showBack={router.canGoBack()} />
      <SafeAreaView edges={["bottom"]} style={styles.screen}>
        <ScrollView
          contentContainerStyle={styles.content}
          keyboardShouldPersistTaps="handled"
          keyboardDismissMode="on-drag"
        >
          {mail.enabled && google.error && (
            <View style={styles.status}>
              <Text
                accessibilityRole="alert"
                style={[styles.body, { color: colors.danger }]}
              >
                {google.error}
              </Text>
              <Pressable
                accessibilityRole="button"
                disabled={google.checking}
                onPress={() => void refreshGoogle().catch(() => undefined)}
                style={styles.button}
              >
                <Text style={[styles.buttonText, { color: colors.accent }]}>
                  Recheck connection
                </Text>
              </Pressable>
            </View>
          )}
          {!mail.enabled ? (
            <View style={styles.status}>
              <Text style={[styles.body, { color: colors.textMuted }]}>
                Sign in to save your interests.
              </Text>
              <Pressable
                accessibilityRole="button"
                onPress={() => router.replace("/")}
                style={styles.button}
              >
                <Text style={[styles.buttonText, { color: colors.accent }]}>
                  Go to sign in
                </Text>
              </Pressable>
            </View>
          ) : !mail.state ? (
            <View style={styles.status}>
              {mail.loading && <ActivityIndicator color={colors.accent} />}
              <Text style={[styles.body, { color: colors.textMuted }]}>
                {mail.error ?? "Loading interests."}
              </Text>
              {!mail.loading && (
                <Pressable
                  accessibilityRole="button"
                  onPress={() => void mail.refresh()}
                  style={styles.button}
                >
                  <Text style={[styles.buttonText, { color: colors.accent }]}>
                    Check again
                  </Text>
                </Pressable>
              )}
            </View>
          ) : (
            <MailInterestEditor
              profile={mail.state.profile}
              recommendations={mail.state.recommendations}
              saving={mail.saving}
              error={mail.error}
              onRecommend={mail.recommend}
              onReconnect={() => router.push("/connections")}
              onCancel={close}
              onSave={async (input) => {
                await mail.save(input);
                if (active.current) close();
              }}
            />
          )}
        </ScrollView>
      </SafeAreaView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: {
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 20,
    width: "100%",
    maxWidth: 680,
    alignSelf: "center",
  },
  status: { paddingVertical: 32, alignItems: "center", gap: 12 },
  body: { fontSize: 14, lineHeight: 21, textAlign: "center" },
  button: { minHeight: 48, paddingHorizontal: 12, justifyContent: "center" },
  buttonText: { fontSize: 14, lineHeight: 21, fontWeight: "600" },
});
