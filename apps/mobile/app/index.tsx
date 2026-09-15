import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Redirect, router } from "expo-router";
import { type ComponentProps, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAuth } from "@/src/auth/AuthProvider";
import {
  meetsCognitoPasswordPolicy,
  PASSWORD_POLICY_HINT,
} from "@/src/auth/passwordPolicy";
import { useAppTheme } from "@/src/theme/useAppTheme";

type AuthMode = "sign-in" | "sign-up";

export default function LoginScreen() {
  const { colors } = useAppTheme();
  const {
    confirmSignUp,
    resendSignUpCode,
    signIn,
    signUp,
    startupError,
    status,
    user,
  } = useAuth();
  const busy = status !== "ready";
  const [mode, setMode] = useState<AuthMode>("sign-in");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordWasChecked, setPasswordWasChecked] = useState(false);
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [passwordConfirmationWasChecked, setPasswordConfirmationWasChecked] =
    useState(false);
  const [confirmationCode, setConfirmationCode] = useState("");
  const [confirmationDestination, setConfirmationDestination] = useState<
    string | null
  >(null);
  const [confirmationEmail, setConfirmationEmail] = useState<string | null>(
    null,
  );
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const passwordConfirmationError =
    mode === "sign-up" &&
    passwordConfirmationWasChecked &&
    password !== passwordConfirmation
      ? "Passwords do not match."
      : null;
  const passwordPolicyError =
    mode === "sign-up" &&
    passwordWasChecked &&
    !meetsCognitoPasswordPolicy(password)
      ? PASSWORD_POLICY_HINT
      : null;

  if (status === "ready" && user) {
    return <Redirect href="/(tabs)" />;
  }

  async function submit() {
    if (busy) return;
    setError(null);
    setSuccess(null);
    try {
      if (confirmationEmail) {
        await confirmSignUp({
          confirmationCode,
          email: confirmationEmail,
        });
        setConfirmationCode("");
        setConfirmationDestination(null);
        setConfirmationEmail(null);
        setMode("sign-in");
        setPassword("");
        setPasswordConfirmation("");
        setSuccess("Email verified. You can now sign in.");
        return;
      }

      if (mode === "sign-up") {
        setPasswordWasChecked(true);
        setPasswordConfirmationWasChecked(true);
        if (
          !meetsCognitoPasswordPolicy(password) ||
          password !== passwordConfirmation
        ) {
          return;
        }
        const result = await signUp({ displayName, email, password });
        if (result.status === "confirmation-required") {
          setEmail(result.email);
          setConfirmationEmail(result.email);
          setConfirmationDestination(result.deliveryDestination ?? null);
          setPassword("");
          setPasswordConfirmation("");
          setSuccess("Verification code sent.");
        } else if (result.status === "ready-to-sign-in") {
          setEmail(result.email);
          setMode("sign-in");
          setPassword("");
          setSuccess("Account created. Please sign in.");
        }
      } else {
        const result = await signIn({ email, password });
        if (result.status === "confirmation-required") {
          setEmail(result.email);
          setConfirmationEmail(result.email);
          setConfirmationDestination(null);
          setPassword("");
          setSuccess("Verify your email to continue.");
        } else if (result.status === "password-reset-required") {
          setPassword("");
          router.push({
            pathname: "/password-reset",
            params: { email: result.email },
          });
        }
      }
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not complete the request. Try again shortly.",
      );
    }
  }

  function selectMode(nextMode: AuthMode) {
    setMode(nextMode);
    setError(null);
    setSuccess(null);
    setConfirmationCode("");
    setConfirmationDestination(null);
    setConfirmationEmail(null);
    setPasswordWasChecked(false);
    setPasswordConfirmation("");
    setPasswordConfirmationWasChecked(false);
  }

  async function resendCode() {
    if (busy || !confirmationEmail) return;
    setError(null);
    setSuccess(null);
    try {
      const destination = await resendSignUpCode(confirmationEmail);
      setConfirmationDestination(destination ?? null);
      setSuccess("A new code has been sent.");
    } catch (caught) {
      setError(
        caught instanceof Error ? caught.message : "Could not resend the code.",
      );
    }
  }

  const visibleError = error ?? startupError;

  return (
    <SafeAreaView
      style={[styles.safeArea, { backgroundColor: colors.background }]}
    >
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={styles.flex}
      >
        <ScrollView
          contentContainerStyle={styles.content}
          keyboardDismissMode="on-drag"
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.hero}>
            <View style={styles.brand}>
              <View
                style={[styles.mark, { backgroundColor: colors.accentSoft }]}
              >
                <MaterialCommunityIcons
                  color={colors.accent}
                  name="radar"
                  size={24}
                />
              </View>
              <Text style={[styles.eyebrow, { color: colors.accent }]}>
                QUIETPILOT
              </Text>
            </View>
            <Text
              accessibilityRole="header"
              textBreakStrategy="balanced"
              style={[styles.title, { color: colors.text }]}
            >
              Catch what needs your attention
            </Text>
            <Text
              textBreakStrategy="balanced"
              style={[styles.body, { color: colors.textMuted }]}
            >
              Turn mail and requests into clear next steps. You approve external
              actions.
            </Text>
          </View>

          <View
            style={[
              styles.formCard,
              { backgroundColor: colors.surface, borderColor: colors.border },
            ]}
          >
            {confirmationEmail ? (
              <>
                <View style={styles.confirmationHeader}>
                  <Text
                    style={[styles.confirmationTitle, { color: colors.text }]}
                  >
                    Check your email
                  </Text>
                  <Text
                    style={[
                      styles.confirmationBody,
                      { color: colors.textMuted },
                    ]}
                  >
                    Enter the six-digit verification code sent to{" "}
                    {confirmationDestination ?? confirmationEmail}.
                  </Text>
                </View>
                <Field
                  autoComplete="one-time-code"
                  keyboardType="number-pad"
                  label="Verification code"
                  maxLength={6}
                  onChangeText={(value) =>
                    setConfirmationCode(value.replace(/\D/g, "").slice(0, 6))
                  }
                  onSubmitEditing={() => void submit()}
                  placeholder="6-digit code"
                  textContentType="oneTimeCode"
                  value={confirmationCode}
                />
              </>
            ) : (
              <>
                <View
                  accessibilityRole="tablist"
                  style={[
                    styles.segment,
                    { backgroundColor: colors.surfaceMuted },
                  ]}
                >
                  <ModeButton
                    active={mode === "sign-in"}
                    label="Sign in"
                    onPress={() => selectMode("sign-in")}
                  />
                  <ModeButton
                    active={mode === "sign-up"}
                    label="Sign up"
                    onPress={() => selectMode("sign-up")}
                  />
                </View>

                {mode === "sign-up" && (
                  <Field
                    autoCapitalize="words"
                    autoComplete="name"
                    label="Display name"
                    onChangeText={setDisplayName}
                    placeholder="Your name"
                    textContentType="name"
                    value={displayName}
                  />
                )}
                <Field
                  autoCapitalize="none"
                  autoComplete="email"
                  keyboardType="email-address"
                  label="Email"
                  onChangeText={setEmail}
                  placeholder="name@example.com"
                  textContentType="emailAddress"
                  value={email}
                />
                <PasswordField
                  autoComplete={
                    mode === "sign-up" ? "new-password" : "current-password"
                  }
                  accessibilityHint={
                    mode === "sign-up" ? PASSWORD_POLICY_HINT : undefined
                  }
                  errorMessage={passwordPolicyError}
                  label="Password"
                  onChangeText={(value) => {
                    setPassword(value);
                    if (mode === "sign-up") setPasswordWasChecked(true);
                  }}
                  onSubmitEditing={
                    mode === "sign-in" ? () => void submit() : undefined
                  }
                  placeholder={
                    mode === "sign-up"
                      ? "8+ characters · uppercase, lowercase, number and symbol"
                      : "Password"
                  }
                  textContentType={
                    mode === "sign-up" ? "newPassword" : "password"
                  }
                  value={password}
                />
                {mode === "sign-in" && (
                  <Pressable
                    accessibilityRole="button"
                    onPress={() =>
                      router.push({
                        pathname: "/password-reset",
                        params: email.trim() ? { email: email.trim() } : {},
                      })
                    }
                    style={({ pressed }) => [
                      styles.forgotPassword,
                      { opacity: pressed ? 0.68 : 1 },
                    ]}
                  >
                    <Text style={[styles.textButton, { color: colors.accent }]}>
                      Forgot password?
                    </Text>
                  </Pressable>
                )}
                {mode === "sign-up" && (
                  <PasswordField
                    autoComplete="new-password"
                    errorMessage={passwordConfirmationError}
                    label="Confirm password"
                    onChangeText={(value) => {
                      setPasswordConfirmation(value);
                      setPasswordConfirmationWasChecked(true);
                    }}
                    onSubmitEditing={() => void submit()}
                    placeholder="Enter password again"
                    textContentType="newPassword"
                    value={passwordConfirmation}
                  />
                )}
              </>
            )}

            {success && (
              <View
                style={[styles.notice, { backgroundColor: colors.accentSoft }]}
              >
                <MaterialCommunityIcons
                  color={colors.accent}
                  name="check-circle-outline"
                  size={17}
                />
                <Text
                  accessibilityLiveRegion="polite"
                  style={[styles.noticeText, { color: colors.accent }]}
                >
                  {success}
                </Text>
              </View>
            )}

            {visibleError && (
              <View
                style={[styles.error, { backgroundColor: colors.dangerSoft }]}
              >
                <MaterialCommunityIcons
                  color={colors.danger}
                  name="alert-circle-outline"
                  size={17}
                />
                <Text
                  accessibilityLiveRegion="polite"
                  style={[styles.errorText, { color: colors.danger }]}
                >
                  {visibleError}
                </Text>
              </View>
            )}

            <Pressable
              accessibilityLabel={
                confirmationEmail
                  ? "Verify email"
                  : mode === "sign-up"
                    ? "Create account"
                    : "Sign in"
              }
              accessibilityRole="button"
              disabled={busy}
              onPress={submit}
              style={({ pressed }) => [
                styles.button,
                {
                  backgroundColor: colors.accent,
                  opacity: pressed || busy ? 0.74 : 1,
                },
              ]}
            >
              {busy ? (
                <ActivityIndicator color={colors.onAccent} />
              ) : (
                <>
                  <Text style={[styles.buttonText, { color: colors.onAccent }]}>
                    {confirmationEmail
                      ? "Verify email"
                      : mode === "sign-up"
                        ? "Create account"
                        : "Sign in"}
                  </Text>
                  <MaterialCommunityIcons
                    color={colors.onAccent}
                    name="arrow-right"
                    size={20}
                  />
                </>
              )}
            </Pressable>

            {confirmationEmail && (
              <View style={styles.confirmationActions}>
                <Pressable
                  accessibilityRole="button"
                  disabled={busy}
                  onPress={() => void resendCode()}
                  style={({ pressed }) => [
                    styles.secondaryButton,
                    { opacity: pressed || busy ? 0.68 : 1 },
                  ]}
                >
                  <Text style={[styles.textButton, { color: colors.accent }]}>
                    Resend code
                  </Text>
                </Pressable>
                <Pressable
                  accessibilityRole="button"
                  disabled={busy}
                  onPress={() => selectMode("sign-in")}
                  style={({ pressed }) => [
                    styles.secondaryButton,
                    { opacity: pressed || busy ? 0.68 : 1 },
                  ]}
                >
                  <Text
                    style={[styles.textButton, { color: colors.textMuted }]}
                  >
                    Back to sign in
                  </Text>
                </Pressable>
              </View>
            )}

            <View style={styles.servicePromise}>
              <MaterialCommunityIcons
                color={colors.textSubtle}
                name="shield-check-outline"
                size={17}
              />
              <Text style={[styles.footnote, { color: colors.textMuted }]}>
                Choose your connections and permissions after signing in.
              </Text>
            </View>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function ModeButton({
  active,
  label,
  onPress,
}: {
  active: boolean;
  label: string;
  onPress(): void;
}) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={[
        styles.modeButton,
        active && { backgroundColor: colors.surfaceRaised },
      ]}
    >
      <Text
        style={[
          styles.modeLabel,
          { color: active ? colors.text : colors.textMuted },
        ]}
      >
        {label}
      </Text>
    </Pressable>
  );
}

function Field({
  label,
  ...props
}: ComponentProps<typeof TextInput> & {
  label: string;
}) {
  const { colors } = useAppTheme();
  return (
    <View style={styles.field}>
      <Text style={[styles.fieldLabel, { color: colors.textMuted }]}>
        {label}
      </Text>
      <TextInput
        accessibilityLabel={label}
        placeholderTextColor={colors.textSubtle}
        selectionColor={colors.accent}
        style={[
          styles.input,
          {
            backgroundColor: colors.background,
            borderColor: colors.border,
            color: colors.text,
          },
        ]}
        {...props}
      />
    </View>
  );
}

function PasswordField({
  errorMessage,
  label,
  ...props
}: ComponentProps<typeof TextInput> & {
  errorMessage?: string | null;
  label: string;
}) {
  const { colors } = useAppTheme();
  const [visible, setVisible] = useState(false);
  return (
    <View style={styles.field}>
      <Text style={[styles.fieldLabel, { color: colors.textMuted }]}>
        {label}
      </Text>
      <View
        style={[
          styles.passwordShell,
          {
            backgroundColor: colors.background,
            borderColor: errorMessage ? colors.danger : colors.border,
          },
        ]}
      >
        <TextInput
          accessibilityLabel={label}
          autoCapitalize="none"
          placeholderTextColor={colors.textSubtle}
          secureTextEntry={!visible}
          selectionColor={colors.accent}
          style={[styles.passwordInput, { color: colors.text }]}
          {...props}
        />
        <Pressable
          accessibilityLabel={visible ? `Hide ${label}` : `Show ${label}`}
          accessibilityRole="button"
          hitSlop={8}
          onPress={() => setVisible((current) => !current)}
          style={({ pressed }) => [
            styles.visibilityButton,
            { opacity: pressed ? 0.58 : 1 },
          ]}
        >
          <MaterialCommunityIcons
            color={colors.textMuted}
            name={visible ? "eye-off-outline" : "eye-outline"}
            size={20}
          />
        </Pressable>
      </View>
      {errorMessage && (
        <Text
          accessibilityLiveRegion="polite"
          style={[styles.inlineErrorText, { color: colors.danger }]}
        >
          {errorMessage}
        </Text>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  body: { fontSize: 14, fontWeight: "400", lineHeight: 21, marginTop: 8 },
  brand: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    marginBottom: 12,
  },
  button: {
    alignItems: "center",
    borderRadius: 12,
    flexDirection: "row",
    gap: 9,
    justifyContent: "center",
    minHeight: 52,
    marginTop: 4,
  },
  buttonText: { fontSize: 15, fontWeight: "600" },
  confirmationActions: { alignItems: "stretch", gap: 0 },
  confirmationBody: { fontSize: 13, lineHeight: 20 },
  confirmationHeader: { gap: 7 },
  confirmationTitle: { fontSize: 18, fontWeight: "600", lineHeight: 25 },
  content: {
    flexGrow: 1,
    paddingHorizontal: 20,
    paddingTop: 12,
    paddingBottom: 32,
  },
  error: {
    alignItems: "center",
    borderRadius: 12,
    flexDirection: "row",
    gap: 8,
    padding: 11,
  },
  errorText: { flex: 1, fontSize: 13, fontWeight: "400", lineHeight: 19 },
  eyebrow: { fontSize: 12, fontWeight: "600", letterSpacing: 1.5 },
  field: { gap: 7 },
  fieldLabel: { fontSize: 13, fontWeight: "500" },
  flex: { flex: 1 },
  footnote: { flex: 1, fontSize: 12, fontWeight: "400", lineHeight: 18 },
  forgotPassword: {
    alignSelf: "flex-end",
    justifyContent: "center",
    minHeight: 48,
    minWidth: 48,
    paddingHorizontal: 8,
    paddingVertical: 8,
  },
  formCard: { borderRadius: 16, borderWidth: 1, gap: 14, padding: 16 },
  hero: { marginBottom: 20, marginTop: 4 },
  input: {
    borderRadius: 13,
    borderWidth: 1,
    fontSize: 14,
    minHeight: 49,
    paddingHorizontal: 14,
  },
  inlineErrorText: { fontSize: 12, fontWeight: "400", lineHeight: 18 },
  passwordInput: {
    flex: 1,
    fontSize: 14,
    minHeight: 49,
    paddingHorizontal: 14,
    paddingVertical: 0,
  },
  passwordShell: {
    alignItems: "center",
    borderRadius: 13,
    borderWidth: 1,
    flexDirection: "row",
    minHeight: 49,
  },
  servicePromise: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 2,
    paddingTop: 2,
  },
  mark: {
    alignItems: "center",
    borderRadius: 12,
    height: 40,
    justifyContent: "center",
    width: 40,
  },
  modeButton: {
    alignItems: "center",
    borderRadius: 11,
    flex: 1,
    justifyContent: "center",
    minHeight: 48,
    minWidth: 48,
  },
  modeLabel: { fontSize: 14, fontWeight: "600" },
  notice: {
    alignItems: "center",
    borderRadius: 12,
    flexDirection: "row",
    gap: 8,
    padding: 11,
  },
  noticeText: { flex: 1, fontSize: 13, fontWeight: "400", lineHeight: 19 },
  safeArea: { flex: 1 },
  secondaryButton: {
    alignItems: "center",
    justifyContent: "center",
    minHeight: 48,
    minWidth: 48,
    paddingHorizontal: 8,
    paddingVertical: 8,
  },
  segment: { borderRadius: 13, flexDirection: "row", padding: 3 },
  title: {
    fontSize: 24,
    fontWeight: "600",
    letterSpacing: -0.5,
    lineHeight: 32,
  },
  textButton: { fontSize: 13, fontWeight: "600" },
  visibilityButton: {
    alignItems: "center",
    height: 48,
    justifyContent: "center",
    width: 48,
  },
});
