import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router, useFocusEffect } from "expo-router";
import { useCallback, useRef, useState } from "react";
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Modal,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View,
} from "react-native";
import { SafeAreaView } from "react-native-safe-area-context";

import { useAuth } from "@/src/auth/AuthProvider";
import { BackHeader } from "@/src/components/BackHeader";
import { useGoogleConnection } from "@/src/connections/useGoogleConnection";
import { useAppTheme } from "@/src/theme/useAppTheme";
import { isMailConfigured, type MailResult } from "./mailApi";
import { useMailInterestState } from "./MailInterestProvider";
import { styles } from "./styles";

export default function MailScreen() {
  const { user } = useAuth();
  return <MailScreenContent key={user?.userId ?? "guest"} />;
}

function MailScreenContent() {
  const { colors } = useAppTheme();
  const mail = useMailInterestState();
  const google = useGoogleConnection();
  const [selected, setSelected] = useState<{
    scanId: string | null;
    version: number;
    ref: string;
  } | null>(null);
  const { state, results: storedResults } = mail;
  const refreshMail = mail.refresh;
  const refreshGoogle = google.refresh;
  const focusedOnce = useRef(false);
  const configured = state ? isMailConfigured(state.profile) : false;
  const preparationIncomplete =
    (state?.scan.status === "READY" || state?.scan.status === "PENDING") &&
    state.scan.error_code === "MAIL_ACTION_PREPARATION_INCOMPLETE";
  const connected =
    ["CONNECTED", "SCANNING", "ERROR"].includes(google.connection.status) ||
    (google.connection.status === "CONNECTING" &&
      google.connection.grantedScopes.includes(
        "https://www.googleapis.com/auth/gmail.readonly",
      ));
  const authorizationRequired =
    google.connection.error === "GOOGLE_AUTH_REQUIRED" ||
    state?.scan.error_code === "GOOGLE_AUTH_REQUIRED" ||
    state?.recommendations.error_code === "GOOGLE_AUTH_REQUIRED";
  const results =
    mail.enabled &&
    connected &&
    !authorizationRequired &&
    configured &&
    state &&
    state.scan.scan_id !== null &&
    storedResults &&
    state.scan.profile_version === state.profile.version &&
    (state.scan.status === "PENDING" ||
      state.scan.status === "READY" ||
      state.scan.status === "ERROR") &&
    storedResults.scan_id === state.scan.scan_id &&
    storedResults.profile_version === state.profile.version &&
    (storedResults.status === "PENDING" ||
      storedResults.status === "READY" ||
      storedResults.status === "ERROR")
      ? storedResults
      : null;
  const failed = state?.scan.status === "ERROR" || results?.status === "ERROR";
  const pending =
    !failed &&
    (state?.scan.status === "PENDING" || results?.status === "PENDING");
  const resultItems = pending
    ? (results?.items.slice(0, 30) ?? [])
    : (results?.items ?? []);
  const selectedMail =
    selected &&
    results?.scan_id === selected.scanId &&
    results.profile_version === selected.version
      ? (results.items.find((item) => item.evidence_ref === selected.ref) ??
        null)
      : null;

  useFocusEffect(
    useCallback(() => {
      void refreshMail();
      // The connection hook already reads on mount; later focus events need a fresh read.
      if (focusedOnce.current) void refreshGoogle().catch(() => undefined);
      focusedOnce.current = true;
    }, [refreshGoogle, refreshMail]),
  );

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      style={[styles.screen, { backgroundColor: colors.background }]}
    >
      <BackHeader title="Mail" showBack={router.canGoBack()} />
      <ScrollView
        key={`${state?.profile.version ?? 0}:${state?.scan.scan_id ?? "idle"}`}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            refreshing={mail.loading && state !== null}
            onRefresh={() => {
              void mail.refresh();
              void refreshGoogle().catch(() => undefined);
            }}
            tintColor={colors.accent}
          />
        }
      >
        {!mail.enabled ? (
          <View style={styles.emptyState}>
            <Text style={[styles.emptyTitle, { color: colors.text }]}>
              Mail matching your interests
            </Text>
            <Pressable
              accessibilityRole="button"
              onPress={() => router.replace("/")}
              style={styles.saveButton}
            >
              <Text style={[styles.saveText, { color: colors.accent }]}>
                Go to sign in
              </Text>
            </Pressable>
          </View>
        ) : !state ? (
          <View style={styles.emptyState}>
            {mail.loading && <ActivityIndicator color={colors.accent} />}
            <Text style={[styles.emptyBody, { color: colors.textMuted }]}>
              {mail.error ?? "Loading interests."}
            </Text>
            {!mail.loading && (
              <Pressable
                accessibilityRole="button"
                onPress={() => void mail.refresh()}
                style={styles.saveButton}
              >
                <Text style={[styles.saveText, { color: colors.accent }]}>
                  Check again
                </Text>
              </Pressable>
            )}
          </View>
        ) : (
          <>
            <View style={styles.intro}>
              <View style={styles.eyebrowRow}>
                <Text
                  accessibilityRole="header"
                  style={[styles.profileLabel, { color: colors.text }]}
                >
                  Interests
                </Text>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={
                    configured
                      ? "Review and edit interests"
                      : "Set mail interests"
                  }
                  onPress={() => router.push("/mail-interests")}
                  style={styles.interestButton}
                >
                  <Text
                    style={[
                      styles.interestButtonText,
                      { color: colors.accent },
                    ]}
                  >
                    {configured ? "Edit interests" : "Set interests"}
                  </Text>
                  <MaterialCommunityIcons
                    name="chevron-right"
                    size={18}
                    color={colors.accent}
                  />
                </Pressable>
              </View>
              {configured ? (
                <>
                  {state.profile.tags.length > 0 && (
                    <Text
                      numberOfLines={2}
                      style={[styles.profileTags, { color: colors.accent }]}
                    >
                      {state.profile.tags.map((tag) => "#" + tag).join("  ")}
                    </Text>
                  )}
                  {!!state.profile.description && (
                    <Text
                      numberOfLines={2}
                      style={[styles.description, { color: colors.textMuted }]}
                    >
                      {state.profile.description}
                    </Text>
                  )}
                </>
              ) : (
                <Text style={[styles.description, { color: colors.textMuted }]}>
                  Set your interests first.
                </Text>
              )}
            </View>

            {configured && (
              <>
                <View style={styles.resultHeading}>
                  <Text
                    accessibilityRole="header"
                    style={[styles.resultTitle, { color: colors.text }]}
                  >
                    Relevant mail
                  </Text>
                  <Text style={[styles.caption, { color: colors.textMuted }]}>
                    Inbox · Last 7 days
                  </Text>
                </View>
                {connected &&
                  !authorizationRequired &&
                  state.scan.status === "READY" &&
                  !preparationIncomplete && (
                    <Pressable
                      accessibilityRole="button"
                      accessibilityLabel="Rescan relevant mail"
                      disabled={mail.requesting || mail.saving || mail.loading}
                      onPress={() => void mail.scan().catch(() => undefined)}
                      style={{
                        alignSelf: "flex-end",
                        minHeight: 44,
                        justifyContent: "center",
                        paddingHorizontal: 8,
                      }}
                    >
                      <Text style={[styles.caption, { color: colors.accent }]}>
                        Rescan mail
                      </Text>
                    </Pressable>
                  )}
                {mail.error && (
                  <View>
                    <Text
                      accessibilityRole="alert"
                      style={[styles.errorText, { color: colors.danger }]}
                    >
                      {mail.error}
                    </Text>
                    {(results || state.scan.status === "PENDING") && (
                      <Pressable
                        accessibilityRole="button"
                        onPress={() => void mail.refresh()}
                        style={styles.saveButton}
                      >
                        <Text
                          style={[styles.saveText, { color: colors.accent }]}
                        >
                          {state.scan.status === "PENDING"
                            ? "Check again"
                            : "Reload"}
                        </Text>
                      </Pressable>
                    )}
                  </View>
                )}
                {connected && google.error && !authorizationRequired && (
                  <View>
                    <Text
                      accessibilityRole="alert"
                      style={[styles.errorText, { color: colors.danger }]}
                    >
                      {google.error}
                    </Text>
                    <Pressable
                      accessibilityRole="button"
                      disabled={google.checking}
                      onPress={() =>
                        void refreshGoogle().catch(() => undefined)
                      }
                      style={styles.saveButton}
                    >
                      <Text style={[styles.saveText, { color: colors.accent }]}>
                        Recheck connection
                      </Text>
                    </Pressable>
                  </View>
                )}
                {preparationIncomplete && (
                  <View style={{ paddingVertical: 12 }}>
                    <Text
                      accessibilityRole="alert"
                      style={[
                        styles.emptyBody,
                        { color: colors.warning, textAlign: "left" },
                      ]}
                    >
                      Mail found. Some task suggestions are still incomplete.
                    </Text>
                    <Pressable
                      accessibilityRole="button"
                      accessibilityState={{
                        disabled:
                          mail.requesting || mail.saving || mail.loading,
                      }}
                      disabled={mail.requesting || mail.saving || mail.loading}
                      onPress={() => {
                        if (state.scan.status === "PENDING")
                          void mail.refresh();
                        else void mail.scan().catch(() => undefined);
                      }}
                      style={styles.saveButton}
                    >
                      <Text style={[styles.saveText, { color: colors.accent }]}>
                        {state.scan.status === "PENDING"
                          ? "Refresh status"
                          : "Scan again"}
                      </Text>
                    </Pressable>
                  </View>
                )}
                {connected && !authorizationRequired && pending && (
                  <MailStatus
                    title="Finding relevant mail"
                    body={`${state.scan.processed_count} messages checked. Up to 30 results appear while scanning.`}
                    busy={!mail.error}
                    compact={resultItems.length > 0}
                    action="Retry request"
                    actionDisabled={
                      mail.requesting || mail.saving || mail.loading
                    }
                    onAction={() => void mail.scan().catch(() => undefined)}
                  />
                )}
                {connected && !authorizationRequired && failed && (
                  <MailStatus
                    title="Mail scan incomplete"
                    body={
                      resultItems.length > 0
                        ? "The scan stopped with an error. Showing verified partial results."
                        : "The scan failed. Please try again."
                    }
                    compact={resultItems.length > 0}
                    action="Scan again"
                    onAction={() => void mail.scan().catch(() => undefined)}
                  />
                )}
                {authorizationRequired ? (
                  <MailStatus
                    title="Sign in to Google again"
                    body="Reconnect to scan mail. Your interests are kept."
                    action="Reconnect Google"
                    onAction={() => router.push("/connections")}
                  />
                ) : !connected && google.checking ? (
                  <MailStatus
                    title="Checking mail connection"
                    body="Your interests and mail are kept."
                    busy
                  />
                ) : !connected && google.error ? (
                  <MailStatus
                    title="Could not verify mail connection"
                    body={google.error}
                    action="Recheck connection"
                    onAction={() => void refreshGoogle().catch(() => undefined)}
                  />
                ) : !connected ? (
                  <MailStatus
                    title="Connect mail to continue"
                    body="Reconnect to find relevant mail. Your interests are kept."
                    action="Connect mail"
                    onAction={() => router.push("/connections")}
                  />
                ) : state.scan.status === "NOT_STARTED" ? (
                  <MailStatus
                    title="Find mail that matters to you"
                    body="Find mail matching your tags and description."
                    action="Find relevant mail"
                    onAction={() => void mail.scan().catch(() => undefined)}
                  />
                ) : !results ? (
                  pending || failed ? null : (
                    <MailStatus
                      title={
                        mail.error ? "Could not load results" : "Loading mail"
                      }
                      body="Loading results for your current interests."
                      busy={!mail.error}
                      action={mail.error ? "Reload" : undefined}
                      onAction={() => void mail.refresh()}
                    />
                  )
                ) : resultItems.length === 0 ? (
                  pending || failed ? null : (
                    <MailStatus
                      title="No matching mail"
                      body="Your last 7 days of inbox mail were checked. Try adjusting your interests."
                      action="Edit interests"
                      onAction={() => router.push("/mail-interests")}
                    />
                  )
                ) : (
                  <>
                    <Text
                      style={[
                        styles.caption,
                        { color: colors.textMuted, marginVertical: 14 },
                      ]}
                    >
                      {failed
                        ? `${resultItems.length} partial results · Scan incomplete`
                        : pending
                          ? `${resultItems.length} early results · Order may change during the scan.`
                          : `${state.scan.matched_count} messages · ${mailTime(state.scan.completed_at)}`}
                    </Text>
                    <View
                      style={[
                        styles.mailList,
                        {
                          backgroundColor: colors.surface,
                          borderColor: colors.border,
                        },
                      ]}
                    >
                      {resultItems.map((item, index) => (
                        <Pressable
                          key={item.evidence_ref}
                          testID={`mail-result-${index}`}
                          accessibilityRole="button"
                          accessibilityLabel={`${displayMailTitle(item.title)}, view mail details`}
                          accessibilityHint={
                            item.importance === "HIGH"
                              ? "Priority mail."
                              : undefined
                          }
                          onPress={() =>
                            setSelected({
                              scanId: results.scan_id,
                              version: results.profile_version,
                              ref: item.evidence_ref,
                            })
                          }
                          style={({ pressed }) => [
                            styles.mailRow,
                            {
                              opacity: pressed ? 0.7 : 1,
                              borderTopColor: colors.border,
                              borderTopWidth: index ? 1 : 0,
                            },
                          ]}
                        >
                          <View style={styles.senderRow}>
                            <Text
                              numberOfLines={1}
                              style={[
                                styles.sender,
                                { color: colors.textMuted },
                              ]}
                            >
                              {item.sender_domain || "Mail"}
                            </Text>
                            <Text
                              style={[
                                styles.caption,
                                { color: colors.textSubtle },
                              ]}
                            >
                              {mailTime(item.received_at)}
                            </Text>
                          </View>
                          {item.importance === "HIGH" && <ImportanceBadge />}
                          <Text
                            numberOfLines={2}
                            style={[styles.mailTitle, { color: colors.text }]}
                          >
                            {displayMailTitle(item.title)}
                          </Text>
                          <Text
                            numberOfLines={2}
                            style={[
                              styles.mailPreview,
                              { color: colors.textMuted },
                            ]}
                          >
                            {item.summary || item.reason}
                          </Text>
                          <View style={styles.messageFooter}>
                            <Text
                              style={[
                                styles.topicText,
                                { color: colors.accent },
                              ]}
                            >
                              {item.matched_tags
                                .map((tag) => `#${tag}`)
                                .join(" · ") ||
                                (item.importance === "HIGH"
                                  ? "Priority mail"
                                  : "Matches your interests")}
                            </Text>
                            <MaterialCommunityIcons
                              name="chevron-right"
                              size={20}
                              color={colors.textSubtle}
                            />
                          </View>
                        </Pressable>
                      ))}
                    </View>
                    {!pending &&
                      results.status !== "PENDING" &&
                      results.next_cursor && (
                        <Pressable
                          accessibilityRole="button"
                          disabled={mail.loadingMore}
                          onPress={() => void mail.loadMore()}
                          style={styles.saveButton}
                        >
                          {mail.loadingMore ? (
                            <ActivityIndicator color={colors.accent} />
                          ) : (
                            <Text
                              style={[
                                styles.saveText,
                                { color: colors.accent },
                              ]}
                            >
                              Load more mail
                            </Text>
                          )}
                        </Pressable>
                      )}
                  </>
                )}
              </>
            )}
          </>
        )}
      </ScrollView>
      <MailDetail message={selectedMail} onClose={() => setSelected(null)} />
    </KeyboardAvoidingView>
  );
}

function MailStatus({
  title,
  body,
  busy,
  action,
  actionDisabled = false,
  compact = false,
  onAction,
}: {
  title: string;
  body: string;
  busy?: boolean;
  action?: string;
  actionDisabled?: boolean;
  compact?: boolean;
  onAction?(): void;
}) {
  const { colors } = useAppTheme();
  return (
    <View
      style={[
        styles.emptyState,
        compact && {
          paddingTop: 16,
          paddingBottom: 8,
          alignItems: "flex-start",
        },
      ]}
    >
      {busy && (
        <ActivityIndicator color={colors.accent} style={{ marginBottom: 16 }} />
      )}
      <Text
        accessibilityRole="header"
        style={[styles.emptyTitle, { color: colors.text }]}
      >
        {title}
      </Text>
      <Text style={[styles.emptyBody, { color: colors.textMuted }]}>
        {body}
      </Text>
      {action && (
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ disabled: actionDisabled }}
          disabled={actionDisabled}
          onPress={onAction}
          style={[styles.saveButton, { opacity: actionDisabled ? 0.5 : 1 }]}
        >
          <Text style={[styles.saveText, { color: colors.accent }]}>
            {action}
          </Text>
        </Pressable>
      )}
    </View>
  );
}

function MailDetail({
  message,
  onClose,
}: {
  message: MailResult | null;
  onClose(): void;
}) {
  const { colors } = useAppTheme();
  return (
    <Modal
      animationType="slide"
      onRequestClose={onClose}
      transparent
      visible={message !== null}
    >
      <View style={styles.modalBackdrop}>
        <Pressable
          accessibilityLabel="Close mail details"
          accessibilityRole="button"
          onPress={onClose}
          style={styles.modalDismiss}
        />
        <SafeAreaView
          accessibilityViewIsModal
          edges={["bottom"]}
          style={[styles.detailSheet, { backgroundColor: colors.surface }]}
        >
          <View style={styles.detailHeader}>
            <View style={styles.detailHeading}>
              <Text style={[styles.eyebrow, { color: colors.accent }]}>
                {message?.importance === "HIGH" &&
                message.matched_tags.length === 0
                  ? "Priority mail"
                  : "Relevant mail"}
              </Text>
              {message?.importance === "HIGH" && <ImportanceBadge />}
            </View>
            <Pressable
              accessibilityLabel="Close mail"
              accessibilityRole="button"
              onPress={onClose}
              style={styles.iconButton}
            >
              <MaterialCommunityIcons
                name="close"
                size={23}
                color={colors.text}
              />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.detailContent}>
            <Text
              accessibilityRole="header"
              style={[styles.detailTitle, { color: colors.text }]}
            >
              {displayMailTitle(message?.title)}
            </Text>
            <View style={[styles.detailSender, { borderColor: colors.border }]}>
              <Text style={[styles.sender, { color: colors.textMuted }]}>
                {message?.sender_domain || "Mail"} ·{" "}
                {mailTime(message?.received_at ?? null)}
              </Text>
            </View>
            <Text
              style={[
                styles.sectionLabel,
                { color: colors.accent, marginBottom: 10 },
              ]}
            >
              Mail summary
            </Text>
            <Text style={[styles.detailBody, { color: colors.text }]}>
              {message?.summary || displayMailTitle(message?.title)}
            </Text>
            <Text
              style={[
                styles.sectionLabel,
                { color: colors.accent, marginTop: 24, marginBottom: 10 },
              ]}
            >
              Why this matched
            </Text>
            <Text style={[styles.detailBody, { color: colors.textMuted }]}>
              {message?.reason}
            </Text>
          </ScrollView>
        </SafeAreaView>
      </View>
    </Modal>
  );
}

function ImportanceBadge() {
  const { colors } = useAppTheme();
  return (
    <Text
      style={[
        styles.importanceBadge,
        { backgroundColor: colors.accentSoft, color: colors.accent },
      ]}
    >
      Priority
    </Text>
  );
}

function mailTime(value: string | null) {
  if (!value) return "Time unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "Time unavailable"
    : date.toLocaleDateString("en-US", { month: "long", day: "numeric" });
}

function displayMailTitle(value: string | undefined) {
  return value === "제목 없는 Gmail 메일" ? "Untitled Gmail message" : value;
}
