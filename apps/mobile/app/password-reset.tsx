import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router, useLocalSearchParams } from "expo-router";
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

import { useAuth } from "@/src/auth/AuthProvider";
import {
  meetsCognitoPasswordPolicy,
  PASSWORD_POLICY_HINT,
} from "@/src/auth/passwordPolicy";
import { BackHeader } from "@/src/components/BackHeader";
import { useAppTheme } from "@/src/theme/useAppTheme";

export default function PasswordResetScreen() {
  const { colors } = useAppTheme();
  const params = useLocalSearchParams<{ email?: string }>();
  const {
    confirmPasswordReset,
    requestPasswordReset,
    status,
    updatePassword,
    user,
  } = useAuth();
  const busy = status !== "ready";
  const signedIn = Boolean(user);
  const [email, setEmail] = useState(user?.email ?? params.email ?? "");
  const [submittedEmail, setSubmittedEmail] = useState<string | null>(null);
  const [destination, setDestination] = useState<string | null>(null);
  const [confirmationCode, setConfirmationCode] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [passwordWasChecked, setPasswordWasChecked] = useState(false);
  const [confirmationWasChecked, setConfirmationWasChecked] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);

  const passwordError =
    passwordWasChecked && !meetsCognitoPasswordPolicy(newPassword)
      ? PASSWORD_POLICY_HINT
      : null;
  const confirmationError =
    confirmationWasChecked && newPassword !== confirmation
      ? "New passwords do not match."
      : null;

  async function requestCode() {
    if (busy) return;
    setError(null);
    setSuccess(null);
    setFinished(false);
    try {
      const result = await requestPasswordReset(email);
      setSubmittedEmail(result.email);
      if (result.status === "done") {
        setFinished(true);
        setSuccess("Password reset. Please sign in.");
        return;
      }
      setDestination(result.deliveryDestination ?? null);
      setSuccess("Verification code sent.");
    } catch (caught) {
      setError(messageFrom(caught, "Could not send the verification code."));
    }
  }

  async function submitNewPassword() {
    if (busy) return;
    setError(null);
    setSuccess(null);
    setPasswordWasChecked(true);
    setConfirmationWasChecked(true);
    if (
      !meetsCognitoPasswordPolicy(newPassword) ||
      newPassword !== confirmation
    ) {
      return;
    }
    try {
      if (signedIn) {
        await updatePassword({ currentPassword, newPassword });
        setCurrentPassword("");
        setNewPassword("");
        setConfirmation("");
        setPasswordWasChecked(false);
        setConfirmationWasChecked(false);
        setSuccess("Password changed.");
        return;
      }
      if (!submittedEmail) return;
      await confirmPasswordReset({
        confirmationCode,
        email: submittedEmail,
        newPassword,
      });
      setConfirmationCode("");
      setNewPassword("");
      setConfirmation("");
      setFinished(true);
      setSuccess("Password changed. Sign in with your new password.");
    } catch (caught) {
      setError(messageFrom(caught, "Could not change the password."));
    }
  }

  const resetFinished = !signedIn && finished;

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <BackHeader title={signedIn ? "Change password" : "Reset password"} />
      <KeyboardAvoidingView
        behavior={Platform.OS === "ios" ? "padding" : "height"}
        style={styles.flex}
      >
        <ScrollView
          contentContainerStyle={styles.content}
          keyboardDismissMode="on-drag"
          keyboardShouldPersistTaps="handled"
        >
          <View style={styles.intro}>
            <View style={styles.introHeading}>
              <View
                style={[styles.icon, { backgroundColor: colors.accentSoft }]}
              >
                <MaterialCommunityIcons
                  color={colors.accent}
                  name={resetFinished ? "check-circle-outline" : "lock-reset"}
                  size={22}
                />
              </View>
              <Text
                accessibilityRole="header"
                style={[styles.title, { color: colors.text }]}
              >
                {resetFinished
                  ? "Password reset"
                  : signedIn
                    ? "Change your password"
                    : submittedEmail
                      ? "Set a new password"
                      : "Reset your password"}
              </Text>
            </View>
            <Text style={[styles.body, { color: colors.textMuted }]}>
              {resetFinished
                ? "Sign in with your new password."
                : signedIn
                  ? "Enter your current password and choose a new one."
                  : submittedEmail
                    ? `Enter the six-digit code sent to ${destination ?? submittedEmail}.`
                    : "We will send a reset code to your email."}
            </Text>
          </View>

          <View
            style={[
              styles.card,
              { backgroundColor: colors.surface, borderColor: colors.border },
            ]}
          >
            {signedIn ? (
              <>
                <View
                  style={[
                    styles.account,
                    {
                      backgroundColor: colors.surfaceMuted,
                      borderColor: colors.border,
                    },
                  ]}
                >
                  <MaterialCommunityIcons
                    color={colors.textMuted}
                    name="email-outline"
                    size={18}
                  />
                  <Text
                    style={[styles.accountEmail, { color: colors.textMuted }]}
                  >
                    {user?.email}
                  </Text>
                </View>
                <PasswordField
                  autoComplete="current-password"
                  label="Current password"
                  onChangeText={setCurrentPassword}
                  textContentType="password"
                  value={currentPassword}
                />
                <NewPasswordFields
                  confirmation={confirmation}
                  confirmationError={confirmationError}
                  newPassword={newPassword}
                  passwordError={passwordError}
                  setConfirmation={(value) => {
                    setConfirmation(value);
                    setConfirmationWasChecked(true);
                  }}
                  setNewPassword={(value) => {
                    setNewPassword(value);
                    setPasswordWasChecked(true);
                  }}
                  submit={() => void submitNewPassword()}
                />
              </>
            ) : resetFinished ? null : !submittedEmail ? (
              <Field
                autoCapitalize="none"
                autoComplete="email"
                keyboardType="email-address"
                label="Email"
                onChangeText={setEmail}
                onSubmitEditing={() => void requestCode()}
                placeholder="name@example.com"
                textContentType="emailAddress"
                value={email}
              />
            ) : (
              <>
                <Field
                  autoComplete="one-time-code"
                  keyboardType="number-pad"
                  label="Verification code"
                  maxLength={6}
                  onChangeText={(value) =>
                    setConfirmationCode(value.replace(/\D/g, "").slice(0, 6))
                  }
                  placeholder="6-digit code"
                  textContentType="oneTimeCode"
                  value={confirmationCode}
                />
                <NewPasswordFields
                  confirmation={confirmation}
                  confirmationError={confirmationError}
                  newPassword={newPassword}
                  passwordError={passwordError}
                  setConfirmation={(value) => {
                    setConfirmation(value);
                    setConfirmationWasChecked(true);
                  }}
                  setNewPassword={(value) => {
                    setNewPassword(value);
                    setPasswordWasChecked(true);
                  }}
                  submit={() => void submitNewPassword()}
                />
              </>
            )}

            {success && (
              <Notice
                color={colors.accent}
                icon="check-circle-outline"
                text={success}
              />
            )}
            {error && (
              <Notice
                color={colors.danger}
                danger
                icon="alert-circle-outline"
                text={error}
              />
            )}

            {resetFinished ? (
              <PrimaryButton
                label="Back to sign in"
                loading={false}
                onPress={() => router.replace("/")}
              />
            ) : (
              <PrimaryButton
                label={
                  signedIn
                    ? "Change password"
                    : submittedEmail
                      ? "Save new password"
                      : "Send code"
                }
                loading={busy}
                onPress={
                  signedIn || submittedEmail
                    ? () => void submitNewPassword()
                    : () => void requestCode()
                }
              />
            )}

            {!signedIn && submittedEmail && !resetFinished && (
              <Pressable
                accessibilityRole="button"
                disabled={busy}
                onPress={() => void requestCode()}
                style={({ pressed }) => [
                  styles.textButton,
                  { opacity: pressed || busy ? 0.68 : 1 },
                ]}
              >
                <Text
                  style={[styles.textButtonLabel, { color: colors.accent }]}
                >
                  Resend code
                </Text>
              </Pressable>
            )}
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function NewPasswordFields({
  confirmation,
  confirmationError,
  newPassword,
  passwordError,
  setConfirmation,
  setNewPassword,
  submit,
}: {
  confirmation: string;
  confirmationError: string | null;
  newPassword: string;
  passwordError: string | null;
  setConfirmation(value: string): void;
  setNewPassword(value: string): void;
  submit(): void;
}) {
  return (
    <>
      <PasswordField
        accessibilityHint={PASSWORD_POLICY_HINT}
        autoComplete="new-password"
        errorMessage={passwordError}
        label="New password"
        onChangeText={setNewPassword}
        placeholder="8+ characters · uppercase, lowercase, number and symbol"
        textContentType="newPassword"
        value={newPassword}
      />
      <PasswordField
        autoComplete="new-password"
        errorMessage={confirmationError}
        label="Confirm new password"
        onChangeText={setConfirmation}
        onSubmitEditing={submit}
        placeholder="Enter new password again"
        textContentType="newPassword"
        value={confirmation}
      />
    </>
  );
}

function Field({
  label,
  ...props
}: ComponentProps<typeof TextInput> & { label: string }) {
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
          style={styles.visibilityButton}
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
          style={[styles.inlineError, { color: colors.danger }]}
        >
          {errorMessage}
        </Text>
      )}
    </View>
  );
}

function Notice({
  color,
  danger = false,
  icon,
  text,
}: {
  color: string;
  danger?: boolean;
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  text: string;
}) {
  const { colors } = useAppTheme();
  return (
    <View
      style={[
        styles.notice,
        { backgroundColor: danger ? colors.dangerSoft : colors.accentSoft },
      ]}
    >
      <MaterialCommunityIcons color={color} name={icon} size={17} />
      <Text
        accessibilityLiveRegion="polite"
        style={[styles.noticeText, { color }]}
      >
        {text}
      </Text>
    </View>
  );
}

function PrimaryButton({
  label,
  loading,
  onPress,
}: {
  label: string;
  loading: boolean;
  onPress(): void;
}) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      accessibilityLabel={label}
      accessibilityRole="button"
      disabled={loading}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        {
          backgroundColor: colors.accent,
          opacity: pressed || loading ? 0.74 : 1,
        },
      ]}
    >
      {loading ? (
        <ActivityIndicator color={colors.onAccent} />
      ) : (
        <>
          <Text style={[styles.buttonText, { color: colors.onAccent }]}>
            {label}
          </Text>
          <MaterialCommunityIcons
            color={colors.onAccent}
            name="arrow-right"
            size={20}
          />
        </>
      )}
    </Pressable>
  );
}

function messageFrom(caught: unknown, fallback: string) {
  return caught instanceof Error ? caught.message : fallback;
}

const styles = StyleSheet.create({
  account: {
    alignItems: "center",
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    padding: 11,
  },
  accountEmail: { flex: 1, fontSize: 13, fontWeight: "400", lineHeight: 19 },
  body: { fontSize: 14, fontWeight: "400", lineHeight: 21, marginTop: 10 },
  button: {
    alignItems: "center",
    borderRadius: 12,
    flexDirection: "row",
    gap: 9,
    justifyContent: "center",
    minHeight: 52,
    marginTop: 2,
  },
  buttonText: { fontSize: 14, fontWeight: "600" },
  card: { borderRadius: 16, borderWidth: 1, gap: 14, padding: 16 },
  content: {
    flexGrow: 1,
    paddingHorizontal: 20,
    paddingTop: 8,
    paddingBottom: 32,
  },
  field: { gap: 7 },
  fieldLabel: { fontSize: 13, fontWeight: "500" },
  flex: { flex: 1 },
  icon: {
    alignItems: "center",
    borderRadius: 10,
    height: 36,
    justifyContent: "center",
    width: 36,
  },
  inlineError: { fontSize: 12, fontWeight: "400", lineHeight: 18 },
  input: {
    borderRadius: 13,
    borderWidth: 1,
    fontSize: 14,
    minHeight: 49,
    paddingHorizontal: 14,
  },
  intro: { marginBottom: 16 },
  introHeading: { alignItems: "center", flexDirection: "row", gap: 10 },
  notice: {
    alignItems: "center",
    borderRadius: 12,
    flexDirection: "row",
    gap: 8,
    padding: 11,
  },
  noticeText: { flex: 1, fontSize: 13, fontWeight: "400", lineHeight: 19 },
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
  screen: { flex: 1 },
  textButton: {
    alignItems: "center",
    justifyContent: "center",
    minHeight: 48,
    minWidth: 48,
    paddingHorizontal: 8,
    paddingVertical: 8,
  },
  textButtonLabel: { fontSize: 13, fontWeight: "600" },
  title: {
    flex: 1,
    fontSize: 21,
    fontWeight: "600",
    letterSpacing: -0.4,
    lineHeight: 29,
  },
  visibilityButton: {
    alignItems: "center",
    height: 48,
    justifyContent: "center",
    width: 48,
  },
});
