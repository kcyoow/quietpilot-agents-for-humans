import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router, useFocusEffect, useLocalSearchParams } from "expo-router";
import { useCallback, useLayoutEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from "react-native";

import { BackHeader } from "@/src/components/BackHeader";
import { EmptyState } from "@/src/components/EmptyState";
import { PrototypeStatusPill } from "@/src/components/PrototypeStatusPill";
import { RiskPill } from "@/src/components/RiskPill";
import {
  canRetryLiveCalendar,
  isOnceCalendarPlan,
} from "@/src/workspace/adapters";
import type { PrototypeGrantMode } from "@/src/prototype/types";
import { useAppTheme } from "@/src/theme/useAppTheme";
import {
  ApprovalCard,
  calendarScheduleLines,
} from "@/src/workspace/screens/caseDetail/ApprovalCard";
import {
  DisclosureSection,
  EvidenceItem,
  SecondaryButton,
  TimelineItem,
} from "@/src/workspace/screens/caseDetail/DetailPrimitives";
import {
  ActionResult,
  PlanCard,
  preparedContent,
} from "@/src/workspace/screens/caseDetail/PlanCard";
import {
  caseTypeLabel,
  humanCopy,
} from "@/src/workspace/screens/caseDetail/presentation";
import {
  CaseRecoveryCard,
  CompletionCard,
  ExecutionCard,
  PartialFailureCard,
  PlanChangeCard,
  StoppedResultsCard,
} from "@/src/workspace/screens/caseDetail/StatusCards";
import { styles } from "@/src/workspace/screens/caseDetail/styles";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";

type DetailSection = "evidence" | "plan" | "timeline" | "chat";

export default function CaseDetailScreen() {
  const { colors } = useAppTheme();
  const { caseId } = useLocalSearchParams<{ caseId: string }>();
  const {
    approveCase,
    deferCase,
    error,
    loadCase,
    postCaseMessage,
    retryCase,
    snapshot,
    status,
    stopCase,
    source,
  } = useWorkspace();
  const item = snapshot?.cases.find((candidate) => candidate.caseId === caseId);
  const [grantMode, setGrantMode] = useState<PrototypeGrantMode>("ONCE");
  const [message, setMessage] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const [expandedSections, setExpandedSections] = useState<Set<DetailSection>>(
    new Set(),
  );
  const [loadingCase, setLoadingCase] = useState(true);
  const messageState = useRef({
    editVersion: 0,
    requestVersion: 0,
  });
  useLayoutEffect(() => {
    const currentMessage = messageState.current;
    currentMessage.requestVersion += 1;
    return () => {
      currentMessage.requestVersion += 1;
    };
  }, [caseId, source]);
  const scrollView = useRef<ScrollView>(null);
  const revealComposer = useRef(false);
  const revealPlan = useRef(false);
  const planPosition = useRef(0);
  const plan = item?.currentPlan;
  const awaitingDecision =
    item?.status === "DECISION_REQUIRED" ||
    item?.status === "PAUSED" ||
    (source === "LIVE" &&
      item?.status === "PERMISSION_REVOKED" &&
      isOnceCalendarPlan(plan ?? null));
  const reapproveCalendar =
    source === "LIVE" &&
    isOnceCalendarPlan(plan ?? null) &&
    Boolean(
      item && ["DECISION_REQUIRED", "PERMISSION_REVOKED"].includes(item.status),
    );
  const planReadyForApproval = Boolean(
    plan &&
    plan.actions.length > 0 &&
    plan.availableGrantModes.length > 0 &&
    Number.isInteger(plan.version) &&
    plan.version > 0 &&
    /^[0-9a-f]{64}$/i.test(plan.hash) &&
    plan.actions.every(
      (action) =>
        action.status === "PROPOSED" ||
        action.status === "PENDING" ||
        (reapproveCalendar &&
          ["VERIFYING", "FAILED", "SUCCEEDED"].includes(action.status)),
    ),
  );
  const approvalReady =
    awaitingDecision && planReadyForApproval && !item?.partialFailure;
  const liveCalendarApproval =
    source === "LIVE" &&
    isOnceCalendarPlan(plan ?? null) &&
    calendarScheduleLines(plan!.actions[0]).length > 0;
  const needsRecovery = Boolean(
    item &&
    !loadingCase &&
    (item.status === "FAILED" ||
      (awaitingDecision &&
        !approvalReady &&
        item.status !== "PERMISSION_REVOKED")),
  );
  const canPrepareAgain =
    source === "LIVE" &&
    item?.status === "DECISION_REQUIRED" &&
    Boolean(plan && plan.actions.length === 0 && !item.partialFailure);
  const canRetry =
    source === "LIVE"
      ? canPrepareAgain || Boolean(item && canRetryLiveCalendar(item))
      : item?.status === "FAILED" &&
        Boolean(
          plan?.actions.some(
            (action) =>
              action.status === "FAILED" || action.status === "PENDING",
          ),
        );
  const canRefineLocal =
    source === "LIVE" &&
    item?.status === "COMPLETED" &&
    Boolean(
      plan &&
      plan.requiredScopes.length === 0 &&
      plan.availableGrantModes.length === 0 &&
      ((plan.localPreparationStatus === "NO_ACTION" &&
        plan.actions.length === 0) ||
        (plan.localPreparationStatus === "READY" &&
          plan.actions.length === 1 &&
          plan.actions[0].requiredScopes.length === 0 &&
          preparedContent(plan.actions[0]) !== null)),
    );
  const messagingLocked =
    source === "LIVE" &&
    (Boolean(
      item &&
      [
        "APPROVED",
        "QUEUED",
        "RUNNING",
        "VERIFYING",
        "COMPLETED",
        "STOPPED",
        "PERMISSION_REVOKED",
      ].includes(item.status) &&
      !canRefineLocal,
    ) ||
      Boolean(
        isOnceCalendarPlan(plan ?? null) &&
        plan?.actions.some((action) =>
          ["VERIFYING", "FAILED", "SUCCEEDED", "RUNNING"].includes(
            action.status,
          ),
        ),
      ));
  const messagingDisabled =
    loadingCase || status !== "ready" || !message.trim() || messagingLocked;
  const visibleError = localError ?? error;
  const selectedGrantMode = item?.currentPlan?.availableGrantModes.includes(
    grantMode,
  )
    ? grantMode
    : (item?.currentPlan?.availableGrantModes[0] ?? "ONCE");

  const shouldPoll =
    source === "LIVE" &&
    Boolean(
      item &&
      ["PREPARING", "APPROVED", "QUEUED", "RUNNING", "VERIFYING"].includes(
        item.status,
      ),
    );

  useFocusEffect(
    useCallback(() => {
      let focused = true;
      let checking = false;
      let active =
        AppState.currentState !== "background" &&
        AppState.currentState !== "inactive";
      let pollAllowed = true;
      let timer: ReturnType<typeof setTimeout> | undefined;
      if (!caseId) {
        setLoadingCase(false);
        return;
      }
      setLoadingCase(true);
      async function check() {
        if (!focused || !active || checking) return;
        checking = true;
        clearTimeout(timer);
        try {
          const latest = await loadCase(caseId);
          if (focused && latest) setLocalError(null);
        } catch (caught) {
          if (
            caught &&
            typeof caught === "object" &&
            "code" in caught &&
            caught.code === "AUTH_REQUIRED"
          )
            pollAllowed = false;
        } finally {
          checking = false;
          if (focused) {
            setLoadingCase(false);
            if (active && shouldPoll && pollAllowed)
              timer = setTimeout(() => {
                void check();
              }, 2_500);
          }
        }
      }
      void check();
      const subscription = AppState.addEventListener("change", (next) => {
        active = next === "active";
        clearTimeout(timer);
        if (active) {
          pollAllowed = true;
          void check();
        }
      });
      return () => {
        focused = false;
        clearTimeout(timer);
        subscription.remove();
      };
    }, [caseId, loadCase, shouldPoll]),
  );

  async function approve() {
    if (
      !item?.currentPlan ||
      !approvalReady ||
      loadingCase ||
      status !== "ready" ||
      (source !== "SCENARIO" && !liveCalendarApproval)
    )
      return;
    setLocalError(null);
    try {
      await approveCase(item.caseId, {
        grantMode: selectedGrantMode,
        planHash: item.currentPlan.hash,
        planVersion: item.currentPlan.version,
      });
    } catch (caught) {
      setLocalError(errorMessage(caught));
    }
  }

  function editMessage(value: string) {
    // A -> B -> A is still a later edit, even though the text matches again.
    messageState.current.editVersion += 1;
    setMessage(value);
  }

  async function sendMessage() {
    if (!item || messagingDisabled) return;
    const sentMessage = message;
    const requestVersion = ++messageState.current.requestVersion;
    const editVersion = messageState.current.editVersion;
    const isCurrentMessage = () =>
      messageState.current.requestVersion === requestVersion &&
      messageState.current.editVersion === editVersion;
    setLocalError(null);
    try {
      await postCaseMessage(item.caseId, sentMessage);
      if (isCurrentMessage()) setMessage("");
    } catch (caught) {
      if (isCurrentMessage()) setLocalError(errorMessage(caught));
    }
  }

  function editRequest() {
    if (expandedSections.has("chat")) {
      scrollView.current?.scrollToEnd({ animated: true });
      return;
    }
    revealComposer.current = true;
    setExpandedSections((current) => new Set(current).add("chat"));
  }

  function reviewPlan() {
    if (expandedSections.has("plan")) {
      scrollView.current?.scrollTo({ y: planPosition.current, animated: true });
      return;
    }
    revealPlan.current = true;
    setExpandedSections((current) => new Set(current).add("plan"));
  }

  async function retryFailedActions() {
    if (!item || !canRetry || loadingCase || status !== "ready") return;
    setLocalError(null);
    try {
      await retryCase(item.caseId);
    } catch (caught) {
      setLocalError(errorMessage(caught));
    }
  }

  async function stopCurrentCase() {
    if (
      !item ||
      loadingCase ||
      status !== "ready" ||
      ["COMPLETED", "STOPPED"].includes(item.status)
    )
      return;
    setLocalError(null);
    try {
      await stopCase(item.caseId);
    } catch (caught) {
      setLocalError(errorMessage(caught));
    }
  }

  function toggleSection(section: DetailSection) {
    setExpandedSections((current) => {
      const next = new Set(current);
      if (next.has(section)) next.delete(section);
      else next.add(section);
      return next;
    });
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      style={[styles.screen, { backgroundColor: colors.background }]}
    >
      <BackHeader title="Task details" />
      <ScrollView
        ref={scrollView}
        contentContainerStyle={styles.content}
        keyboardDismissMode="on-drag"
        keyboardShouldPersistTaps="handled"
        onContentSizeChange={() => {
          if (revealPlan.current) {
            revealPlan.current = false;
            scrollView.current?.scrollTo({
              y: planPosition.current,
              animated: true,
            });
          }
          if (!revealComposer.current) return;
          revealComposer.current = false;
          scrollView.current?.scrollToEnd({ animated: true });
        }}
      >
        {visibleError && (
          <View
            style={[styles.errorCard, { backgroundColor: colors.dangerSoft }]}
          >
            <MaterialCommunityIcons
              color={colors.danger}
              name="alert-circle-outline"
              size={19}
            />
            <Text
              accessibilityRole="alert"
              accessibilityLiveRegion="polite"
              style={[styles.errorText, { color: colors.danger }]}
            >
              {visibleError}
            </Text>
          </View>
        )}
        {loadingCase && !item ? (
          <ActivityIndicator
            accessible
            accessibilityLabel="Loading task"
            accessibilityRole="progressbar"
            color={colors.accent}
            style={styles.loading}
          />
        ) : !item ? (
          <EmptyState
            body="Return to the list and try again."
            title={visibleError ? "Could not load task" : "Task not found"}
          />
        ) : (
          <>
            <View
              style={[
                styles.overview,
                { backgroundColor: colors.surface, borderColor: colors.border },
              ]}
            >
              <View style={styles.pills}>
                <PrototypeStatusPill status={item.status} />
                <Text style={[styles.caseType, { color: colors.textMuted }]}>
                  {caseTypeLabel(item.caseType)}
                </Text>
              </View>
              <Text
                accessibilityRole="header"
                style={[styles.title, { color: colors.text }]}
              >
                {item.goal}
              </Text>
              <Text
                textBreakStrategy="balanced"
                style={[styles.summary, { color: colors.textMuted }]}
              >
                {humanCopy(item.summary)}
              </Text>
              {source === "SCENARIO" && (
                <Text
                  style={[styles.scenarioNote, { color: colors.textMuted }]}
                >
                  Example only. No external changes.
                </Text>
              )}
            </View>

            {loadingCase && (
              <View style={styles.refreshing}>
                <ActivityIndicator color={colors.accent} size="small" />
                <Text
                  style={[styles.refreshingText, { color: colors.textMuted }]}
                >
                  Checking for updates.
                </Text>
              </View>
            )}

            {needsRecovery ? (
              <CaseRecoveryCard
                item={item}
                onEditRequest={messagingLocked ? undefined : editRequest}
                onRetry={
                  canRetry && !item.partialFailure
                    ? retryFailedActions
                    : undefined
                }
                updating={loadingCase || status !== "ready"}
              />
            ) : [
                "PREPARING",
                "RUNNING",
                "VERIFYING",
                "APPROVED",
                "QUEUED",
              ].includes(item.status) ? (
              <ExecutionCard
                item={item}
                source={source}
                onRetry={canRetry ? retryFailedActions : undefined}
                updating={loadingCase || status !== "ready"}
              />
            ) : item.status === "COMPLETED" ? (
              <CompletionCard
                item={item}
                onRepeat={
                  item.dataSource === "LIVE" &&
                  item.caseType !== "DIRECT_DELEGATION" &&
                  item.currentPlan?.actions.length === 1 &&
                  item.currentPlan.actions[0].verb ===
                    "calendar_event_create" &&
                  item.currentPlan.actions[0].verified === true
                    ? () =>
                        router.push({
                          pathname: "/routines",
                          params: { caseId: item.caseId },
                        })
                    : undefined
                }
              />
            ) : (
              <View
                style={[
                  styles.nextCard,
                  {
                    backgroundColor:
                      item.status === "DECISION_REQUIRED"
                        ? colors.warningSoft
                        : colors.accentSoft,
                    borderColor:
                      item.status === "DECISION_REQUIRED"
                        ? colors.warningBorder
                        : colors.accentBorder,
                  },
                ]}
              >
                <MaterialCommunityIcons
                  color={
                    item.status === "DECISION_REQUIRED"
                      ? colors.warning
                      : colors.accent
                  }
                  name="arrow-right-circle-outline"
                  size={20}
                />
                <View style={styles.nextCopy}>
                  <Text style={[styles.nextLabel, { color: colors.textMuted }]}>
                    {item.status === "STOPPED" ? "Stopped task" : "Review now"}
                  </Text>
                  <Text style={[styles.nextValue, { color: colors.text }]}>
                    {loadingCase && !plan
                      ? "Loading the plan."
                      : source === "LIVE" && approvalReady
                        ? "Review the plan or request a change."
                        : humanCopy(item.nextAction)}
                  </Text>
                </View>
              </View>
            )}

            {source === "LIVE" &&
              ["STOPPED", "PERMISSION_REVOKED"].includes(item.status) &&
              isOnceCalendarPlan(plan ?? null) && (
                <StoppedResultsCard item={item} />
              )}

            {item.planChange && <PlanChangeCard item={item} />}

            {item.partialFailure && (
              <PartialFailureCard
                item={item}
                onRetry={canRetry ? retryFailedActions : undefined}
                updating={loadingCase || status !== "ready"}
              />
            )}

            <View style={styles.reasonSection}>
              <Text
                accessibilityRole="header"
                style={[styles.sectionLabel, { color: colors.text }]}
              >
                {item.caseType === "DIRECT_DELEGATION"
                  ? "Why this was prepared"
                  : "Why this was suggested"}
              </Text>
              <Text style={[styles.whyNow, { color: colors.textMuted }]}>
                {humanCopy(item.whyNow)}
              </Text>
            </View>

            <DisclosureSection
              count={item.evidence.length}
              expanded={expandedSections.has("evidence")}
              icon="file-document-outline"
              onPress={() => toggleSection("evidence")}
              title="Sources"
            >
              {item.evidence.length === 0 && (
                <Text
                  style={[styles.evidenceText, { color: colors.textMuted }]}
                >
                  No saved sources.
                </Text>
              )}
              {item.evidence.map((evidence) => (
                <EvidenceItem evidence={evidence} key={evidence.evidenceId} />
              ))}
            </DisclosureSection>

            {item.currentPlan && (
              <View
                onLayout={(event) => {
                  planPosition.current = event.nativeEvent.layout.y;
                }}
              >
                <DisclosureSection
                  count={item.currentPlan.actions.length}
                  expanded={expandedSections.has("plan")}
                  icon="clipboard-text-outline"
                  onPress={() => toggleSection("plan")}
                  title="Prepared plan"
                  summary={
                    <View style={styles.planPreview}>
                      <Text
                        style={[styles.planOutcome, { color: colors.text }]}
                      >
                        {humanCopy(item.currentPlan.expectedOutcome)}
                      </Text>
                      {item.currentPlan.actions.slice(0, 3).map((action) => (
                        <Text
                          key={action.actionId}
                          style={[
                            styles.previewAction,
                            { color: colors.textMuted },
                          ]}
                        >
                          • {humanCopy(action.label)}
                        </Text>
                      ))}
                      {item.currentPlan.actions.length > 3 && (
                        <Text
                          style={[
                            styles.previewAction,
                            { color: colors.textMuted },
                          ]}
                        >
                          Plus {item.currentPlan.actions.length - 3} more · Show
                          all
                        </Text>
                      )}
                    </View>
                  }
                >
                  <RiskPill risk={item.risk} />
                  <PlanCard item={item} />
                </DisclosureSection>
              </View>
            )}

            {approvalReady &&
              reapproveCalendar &&
              plan?.actions.some(
                (action) =>
                  action.resultRef ||
                  action.errorCode ||
                  action.status === "SUCCEEDED",
              ) &&
              item.status !== "PERMISSION_REVOKED" && (
                <View
                  style={[
                    styles.reconnectCard,
                    { backgroundColor: colors.warningSoft },
                  ]}
                >
                  <Text
                    style={[styles.reconnectBody, { color: colors.textMuted }]}
                  >
                    Check the event you already approved. An existing event will
                    not be duplicated.
                  </Text>
                  {plan.actions.map((action) => (
                    <ActionResult action={action} key={action.actionId} />
                  ))}
                </View>
              )}

            {approvalReady && (
              <ApprovalCard
                grantMode={selectedGrantMode}
                item={item}
                onApprove={approve}
                onDefer={
                  item.status === "DECISION_REQUIRED"
                    ? async () => {
                        if (loadingCase || status !== "ready") return;
                        setLocalError(null);
                        try {
                          await deferCase(item.caseId);
                        } catch (caught) {
                          setLocalError(errorMessage(caught));
                        }
                      }
                    : undefined
                }
                onGrantMode={setGrantMode}
                onReviewPlan={reviewPlan}
                approvalEnabled={source === "SCENARIO" || liveCalendarApproval}
                updating={loadingCase || status !== "ready"}
              />
            )}

            {item.status === "PERMISSION_REVOKED" && (
              <View
                style={[
                  styles.reconnectCard,
                  { backgroundColor: colors.warningSoft },
                ]}
              >
                <Text
                  style={[styles.reconnectTitle, { color: colors.warning }]}
                >
                  Connection or permission required
                </Text>
                <Text
                  style={[styles.reconnectBody, { color: colors.textMuted }]}
                >
                  Check your Calendar connection and approve the plan again.
                  Previous results remain in the history below.
                </Text>
                <View style={styles.buttonRow}>
                  <SecondaryButton
                    label="Check connection"
                    onPress={() => router.push("/connections")}
                  />
                  <SecondaryButton
                    danger
                    disabled={loadingCase || status !== "ready"}
                    label="Stop task"
                    onPress={stopCurrentCase}
                  />
                </View>
              </View>
            )}

            {item.timeline.length > 0 && (
              <DisclosureSection
                count={item.timeline.length}
                expanded={expandedSections.has("timeline")}
                icon="timeline-clock-outline"
                onPress={() => toggleSection("timeline")}
                title="Activity"
              >
                {item.timeline.map((timelineItem, index) => (
                  <TimelineItem
                    isLast={index === item.timeline.length - 1}
                    item={timelineItem}
                    key={timelineItem.eventId}
                  />
                ))}
              </DisclosureSection>
            )}

            <DisclosureSection
              count={item.messages.length}
              expanded={expandedSections.has("chat")}
              icon="message-processing-outline"
              onPress={() => toggleSection("chat")}
              title="Request a change"
            >
              <Text style={[styles.chatNote, { color: colors.textSubtle }]}>
                {messagingLocked
                  ? "Running or finished tasks cannot be edited. Check the latest result."
                  : "Changes apply only to this task and are shown before execution."}
              </Text>
              {item.messages.map((chatMessage) => (
                <View
                  key={chatMessage.messageId}
                  style={[
                    styles.bubble,
                    {
                      alignSelf:
                        chatMessage.author === "USER"
                          ? "flex-end"
                          : "flex-start",
                      backgroundColor:
                        chatMessage.author === "USER"
                          ? colors.accentSoft
                          : colors.surfaceMuted,
                    },
                  ]}
                >
                  <Text style={[styles.bubbleText, { color: colors.text }]}>
                    {chatMessage.text}
                  </Text>
                </View>
              ))}
              <View style={styles.chatComposer}>
                <TextInput
                  accessibilityLabel="Task message"
                  editable={!messagingLocked}
                  multiline
                  onChangeText={editMessage}
                  placeholder={
                    canRefineLocal
                      ? "For example: Make it shorter"
                      : needsRecovery
                        ? "Describe what to change"
                        : "For example: Move it to 9 AM tomorrow"
                  }
                  placeholderTextColor={colors.textSubtle}
                  selectionColor={colors.accent}
                  style={[
                    styles.chatInput,
                    {
                      backgroundColor: colors.background,
                      borderColor: colors.border,
                      color: colors.text,
                    },
                  ]}
                  value={message}
                />
                <Pressable
                  accessibilityLabel="Send task message"
                  accessibilityRole="button"
                  accessibilityState={{ disabled: messagingDisabled }}
                  disabled={messagingDisabled}
                  onPress={sendMessage}
                  style={({ pressed }) => [
                    styles.sendButton,
                    {
                      backgroundColor: colors.accent,
                      opacity: pressed || messagingDisabled ? 0.62 : 1,
                    },
                  ]}
                >
                  <MaterialCommunityIcons
                    color={colors.onAccent}
                    name="arrow-up"
                    size={19}
                  />
                </Pressable>
              </View>
            </DisclosureSection>

            {!["COMPLETED", "STOPPED", "PERMISSION_REVOKED"].includes(
              item.status,
            ) &&
              (needsRecovery || !awaitingDecision) && (
                <Pressable
                  accessibilityLabel={
                    needsRecovery ? "Stop task" : "Stop remaining work"
                  }
                  accessibilityRole="button"
                  accessibilityState={{
                    disabled: loadingCase || status !== "ready",
                  }}
                  disabled={loadingCase || status !== "ready"}
                  onPress={stopCurrentCase}
                  style={({ pressed }) => [
                    styles.stopButton,
                    {
                      borderColor: colors.danger,
                      opacity:
                        pressed || loadingCase || status !== "ready" ? 0.7 : 1,
                    },
                  ]}
                >
                  <Text style={[styles.stopText, { color: colors.danger }]}>
                    {needsRecovery ? "Stop task" : "Stop remaining work"}
                  </Text>
                </Pressable>
              )}
          </>
        )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function errorMessage(caught: unknown) {
  return caught instanceof Error
    ? caught.message
    : "Could not complete the request.";
}
