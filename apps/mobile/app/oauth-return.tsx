import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router, useLocalSearchParams } from "expo-router";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { useAuth } from "@/src/auth/AuthProvider";
import {
  completeGoogleOAuthOnce,
  createGoogleConnectionsApi,
  type GoogleConnectionRecord,
} from "@/src/connections/googleApi";
import { useAppTheme } from "@/src/theme/useAppTheme";

const RETURN_CODE_PATTERN = /^[A-Za-z0-9_-]{32,256}$/;

type CallbackAttempt = Readonly<{
  code: string | undefined;
  provider: string | undefined;
  userId: string | null;
  ownerId: string | null;
  cancelled: boolean;
}>;
type CallbackCompletion = {
  attempt: CallbackAttempt;
  promise: Promise<GoogleConnectionRecord>;
  navigated: boolean;
};

export default function OAuthReturnScreen() {
  const { colors } = useAppTheme();
  const { user, status: authStatus } = useAuth();
  const userId = user?.userId ?? null;
  const params = useLocalSearchParams<{
    code?: string | string[];
    provider?: string | string[];
  }>();
  const api = useMemo(() => createGoogleConnectionsApi(), []);
  const code = singleValue(params.code);
  const provider = singleValue(params.provider);
  const invalidReturn =
    provider !== "google" || !code || !RETURN_CODE_PATTERN.test(code);
  const [attempt, setAttempt] = useState<CallbackAttempt>(() => ({
    code,
    provider,
    userId,
    ownerId: userId,
    cancelled: false,
  }));
  const [failure, setFailure] = useState<{
    attempt: CallbackAttempt;
    message: string;
  } | null>(null);
  const completion = useRef<CallbackCompletion | null>(null);
  const activeRender = useRef<{
    attempt: CallbackAttempt;
    ready: boolean;
  } | null>(null);

  // Adjust ownership only when observed inputs change, before committing this render.
  if (attempt.code !== code || attempt.provider !== provider) {
    setAttempt({ code, provider, userId, ownerId: userId, cancelled: false });
  } else if (attempt.userId !== userId) {
    setAttempt({
      ...attempt,
      userId,
      ownerId: attempt.ownerId ?? userId,
      cancelled: attempt.cancelled || attempt.ownerId !== null,
    });
  }

  useLayoutEffect(() => {
    const current = { attempt, ready: authStatus === "ready" };
    activeRender.current = current;
    return () => {
      if (activeRender.current === current) activeRender.current = null;
    };
  }, [attempt, authStatus]);

  const visibleError = invalidReturn
    ? "Could not verify the Google connection response."
    : attempt.cancelled
      ? "Your account changed. Start again from Connections."
      : authStatus === "ready" && !userId
        ? "Sign in, then connect again."
        : failure?.attempt === attempt
          ? failure.message
          : null;
  const waitingForAuth = authStatus !== "ready";

  useEffect(() => {
    let active = true;
    const isCurrent = () => {
      const current = activeRender.current;
      return (
        active &&
        current?.attempt === attempt &&
        current.ready &&
        !attempt.cancelled &&
        attempt.ownerId === userId
      );
    };
    if (
      invalidReturn ||
      authStatus !== "ready" ||
      !userId ||
      attempt.cancelled ||
      attempt.ownerId !== userId
    ) {
      return () => {
        active = false;
      };
    }
    if (completion.current?.attempt !== attempt) {
      completion.current = {
        attempt,
        promise: completeGoogleOAuthOnce(api, code, userId),
        navigated: false,
      };
    }
    const pending = completion.current;
    void pending.promise
      .then(() => {
        if (!isCurrent() || pending.navigated) return;
        pending.navigated = true;
        router.replace({ pathname: "/(tabs)", params: { mailSetup: "1" } });
      })
      .catch((caught: unknown) => {
        if (!isCurrent()) return;
        setFailure({
          attempt,
          message:
            caught instanceof Error
              ? caught.message
              : "Could not verify Google. Please reconnect.",
        });
      });
    return () => {
      active = false;
    };
  }, [api, attempt, authStatus, code, invalidReturn, userId]);

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <View
        style={[
          styles.card,
          { backgroundColor: colors.surface, borderColor: colors.border },
        ]}
      >
        {visibleError ? (
          <>
            <View style={[styles.icon, { backgroundColor: colors.dangerSoft }]}>
              <MaterialCommunityIcons
                color={colors.danger}
                name="alert-circle-outline"
                size={28}
              />
            </View>
            <Text style={[styles.title, { color: colors.text }]}>
              Connection incomplete
            </Text>
            <Text style={[styles.copy, { color: colors.textMuted }]}>
              {visibleError}
            </Text>
            <Pressable
              accessibilityRole="button"
              onPress={() => router.replace("/connections")}
              style={[styles.button, { backgroundColor: colors.accent }]}
            >
              <Text style={[styles.buttonText, { color: colors.onAccent }]}>
                Back to Connections
              </Text>
            </Pressable>
          </>
        ) : (
          <>
            <ActivityIndicator color={colors.accent} size="large" />
            <Text style={[styles.title, { color: colors.text }]}>
              {waitingForAuth
                ? "Checking your account"
                : "Checking Google connection"}
            </Text>
            <Text style={[styles.copy, { color: colors.textMuted }]}>
              {waitingForAuth
                ? "Verifying your account to finish connecting."
                : "Once connected, choose the mail topics you care about."}
            </Text>
          </>
        )}
      </View>
    </View>
  );
}

function singleValue(value: string | string[] | undefined) {
  return typeof value === "string" ? value : undefined;
}

const styles = StyleSheet.create({
  screen: {
    alignItems: "center",
    flex: 1,
    justifyContent: "center",
    padding: 24,
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
  icon: {
    alignItems: "center",
    borderRadius: 24,
    height: 48,
    justifyContent: "center",
    width: 48,
  },
  title: { fontSize: 20, fontWeight: "600", textAlign: "center" },
  copy: { fontSize: 14, lineHeight: 21, textAlign: "center" },
  button: {
    alignItems: "center",
    borderRadius: 14,
    justifyContent: "center",
    marginTop: 4,
    minHeight: 48,
    paddingHorizontal: 18,
    paddingVertical: 13,
    width: "100%",
  },
  buttonText: { fontSize: 14, fontWeight: "600" },
});
