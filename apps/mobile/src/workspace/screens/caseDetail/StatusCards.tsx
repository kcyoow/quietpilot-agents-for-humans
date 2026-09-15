import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useState } from "react";
import { ActivityIndicator, Pressable, Text, View } from "react-native";

import { useAppTheme } from "@/src/theme/useAppTheme";
import type { WorkspaceCase } from "@/src/workspace/types";

import { SecondaryButton } from "./DetailPrimitives";
import { humanCopy } from "./presentation";
import {
  ActionResult,
  localPreparationKind,
  preparedContent,
} from "./PlanCard";
import { styles } from "./styles";

export function CaseRecoveryCard({
  item,
  onEditRequest,
  onRetry,
  updating,
}: {
  item: WorkspaceCase;
  onEditRequest?(): void;
  onRetry?(): void;
  updating: boolean;
}) {
  const { colors } = useAppTheme();
  const emptyPlan = !item.currentPlan || item.currentPlan.actions.length === 0;
  return (
    <View
      style={[
        styles.recoveryCard,
        {
          backgroundColor: colors.warningSoft,
          borderColor: colors.warningBorder,
        },
      ]}
    >
      <Text
        accessibilityRole="alert"
        accessibilityLiveRegion="polite"
        style={[styles.recoveryTitle, { color: colors.text }]}
      >
        {emptyPlan
          ? "No action prepared"
          : item.status === "FAILED"
            ? "Task could not finish"
            : "Plan needs review"}
      </Text>
      <Text style={[styles.recoveryBody, { color: colors.textMuted }]}>
        {emptyPlan
          ? onRetry
            ? "Prepare again from the same sources. External actions still need approval."
            : "No action is ready for approval. Review the sources or clarify your request."
          : onRetry
            ? "Check and resume the event you already approved."
            : "This plan cannot continue. Review its history and sources."}
      </Text>
      {emptyPlan && item.nextAction.trim() && (
        <Text selectable style={[styles.planOutcome, { color: colors.text }]}>
          {humanCopy(item.nextAction)}
        </Text>
      )}
      {item.dataSource === "LIVE" &&
        item.currentPlan?.actions.map((action) => (
          <ActionResult action={action} key={action.actionId} />
        ))}
      <View style={styles.buttonRow}>
        {onEditRequest && (
          <SecondaryButton
            disabled={updating}
            label="Clarify request"
            onPress={onEditRequest}
          />
        )}
        {onRetry && (
          <SecondaryButton
            disabled={updating}
            label={
              emptyPlan ? "Prepare again" : "Retry failed or waiting steps"
            }
            onPress={onRetry}
          />
        )}
      </View>
    </View>
  );
}

export function PlanChangeCard({ item }: { item: WorkspaceCase }) {
  const { colors } = useAppTheme();
  const change = item.planChange!;
  const [showVersions, setShowVersions] = useState(false);
  return (
    <View style={[styles.changeCard, { backgroundColor: colors.warningSoft }]}>
      <View style={styles.changeTitleRow}>
        <MaterialCommunityIcons
          color={colors.warning}
          name="file-compare"
          size={20}
        />
        <Text style={[styles.changeTitle, { color: colors.warning }]}>
          Previous approval is no longer valid
        </Text>
      </View>
      <Text style={[styles.changeReason, { color: colors.textMuted }]}>
        {humanCopy(change.reason)}
      </Text>
      <View style={styles.diffRow}>
        <View style={[styles.diff, { backgroundColor: colors.surface }]}>
          <Text style={[styles.diffLabel, { color: colors.textSubtle }]}>
            Before
          </Text>
          <Text style={[styles.diffText, { color: colors.textMuted }]}>
            {humanCopy(change.previousSummary)}
          </Text>
        </View>
        <MaterialCommunityIcons
          color={colors.warning}
          name="arrow-right"
          size={18}
        />
        <View style={[styles.diff, { backgroundColor: colors.surface }]}>
          <Text style={[styles.diffLabel, { color: colors.textSubtle }]}>
            Current plan
          </Text>
          <Text style={[styles.diffText, { color: colors.text }]}>
            {humanCopy(change.currentSummary)}
          </Text>
        </View>
      </View>
      <Pressable
        accessibilityLabel="View change details"
        accessibilityRole="button"
        accessibilityState={{ expanded: showVersions }}
        onPress={() => setShowVersions((current) => !current)}
        style={styles.inlineDisclosure}
      >
        <Text
          style={[styles.inlineDisclosureText, { color: colors.textMuted }]}
        >
          View change details
        </Text>
        <MaterialCommunityIcons
          color={colors.textMuted}
          name={showVersions ? "chevron-up" : "chevron-down"}
          size={18}
        />
      </Pressable>
      {showVersions && (
        <Text style={[styles.originalMeta, { color: colors.textMuted }]}>
          Previous version {change.previousVersion} → Current version{" "}
          {item.currentPlan?.version ?? "Not checked"}
        </Text>
      )}
    </View>
  );
}

export function PartialFailureCard({
  item,
  onRetry,
  updating,
}: {
  item: WorkspaceCase;
  onRetry?(): void;
  updating: boolean;
}) {
  const { colors } = useAppTheme();
  const failure = item.partialFailure!;
  return (
    <View style={[styles.failureCard, { backgroundColor: colors.dangerSoft }]}>
      <View style={styles.changeTitleRow}>
        <MaterialCommunityIcons
          color={colors.danger}
          name="alert-outline"
          size={20}
        />
        <Text style={[styles.changeTitle, { color: colors.danger }]}>
          Some steps are incomplete
        </Text>
      </View>
      <Text style={[styles.changeReason, { color: colors.textMuted }]}>
        {humanCopy(failure.explanation)}
      </Text>
      <View style={styles.failureCounts}>
        <FailureCount
          color={colors.success}
          label="Completed"
          value={failure.succeededActionIds.length}
        />
        <FailureCount
          color={colors.danger}
          label="Failed"
          value={failure.failedActionIds.length}
        />
        <FailureCount
          color={colors.textMuted}
          label="Not started"
          value={failure.pendingActionIds.length}
        />
      </View>
      {onRetry && (
        <Pressable
          accessibilityLabel="Retry failed or waiting steps"
          accessibilityRole="button"
          accessibilityState={{ disabled: updating }}
          disabled={updating}
          onPress={onRetry}
          style={({ pressed }) => [
            styles.retryButton,
            {
              backgroundColor: colors.danger,
              opacity: pressed || updating ? 0.65 : 1,
            },
          ]}
        >
          <MaterialCommunityIcons color="#fff" name="refresh" size={18} />
          <Text style={styles.retryText}>Retry failed or waiting steps</Text>
        </Pressable>
      )}
    </View>
  );
}

function FailureCount({
  color,
  label,
  value,
}: {
  color: string;
  label: string;
  value: number;
}) {
  return (
    <View style={styles.failureCount}>
      <Text style={[styles.failureValue, { color }]}>{value}</Text>
      <Text style={[styles.failureLabel, { color }]}>{label}</Text>
    </View>
  );
}

export function ExecutionCard({
  item,
  source,
  onRetry,
  updating = false,
}: {
  item: WorkspaceCase;
  source: "LIVE" | "SCENARIO";
  onRetry?(): void;
  updating?: boolean;
}) {
  const { colors } = useAppTheme();
  const verifying = item.status === "VERIFYING";
  const title = {
    APPROVED: "Request received",
    PREPARING: "Preparing your request",
    QUEUED: "Waiting to start",
    RUNNING: "Task in progress",
    VERIFYING: "Verifying the result",
  }[
    item.status as "APPROVED" | "PREPARING" | "QUEUED" | "RUNNING" | "VERIFYING"
  ];
  const description = verifying
    ? source === "LIVE"
      ? "Completion is shown after the external result is verified."
      : "Verifying an example. No external changes."
    : item.status === "APPROVED" || item.status === "QUEUED"
      ? "Your request is queued, not yet complete."
      : item.status === "PREPARING"
        ? "Review or revise the plan when it is ready."
        : "You can review the result when the task finishes.";
  return (
    <View
      style={[
        styles.executionCard,
        {
          backgroundColor: colors.accentSoft,
          borderColor: colors.accentBorder,
        },
      ]}
    >
      <ActivityIndicator color={colors.accent} size="small" />
      <View style={styles.nextCopy}>
        <Text style={[styles.executionTitle, { color: colors.text }]}>
          {title}
        </Text>
        <Text style={[styles.executionBody, { color: colors.textMuted }]}>
          {description}
        </Text>
        {item.nextAction.trim() && (
          <Text style={[styles.executionNext, { color: colors.textMuted }]}>
            Next step · {humanCopy(item.nextAction)}
          </Text>
        )}
        {source === "LIVE" &&
          item.currentPlan?.actions.map((action) => (
            <ActionResult action={action} key={action.actionId} />
          ))}
        {onRetry && (
          <>
            <Text style={[styles.executionBody, { color: colors.textMuted }]}>
              Check the approved event and resume without creating a duplicate.
            </Text>
            <SecondaryButton
              disabled={updating}
              label="Retry failed or waiting steps"
              onPress={onRetry}
            />
          </>
        )}
      </View>
    </View>
  );
}

export function CompletionCard({
  item,
  onRepeat,
}: {
  item: WorkspaceCase;
  onRepeat?(): void;
}) {
  const { colors } = useAppTheme();
  const actions = item.currentPlan?.actions ?? [];
  const noAction =
    item.dataSource === "LIVE" &&
    item.currentPlan !== null &&
    actions.length === 0;
  const localOnly =
    actions.length > 0 &&
    actions.every((action) => localPreparationKind(action));
  const localReady =
    localOnly && actions.every((action) => preparedContent(action) !== null);
  const localLabel =
    actions.length === 1 ? localPreparationKind(actions[0])?.label : "Content";
  const results =
    item.currentPlan?.actions.filter(
      (action) => action.status === "SUCCEEDED",
    ) ?? [];
  const confirmed = localOnly
    ? localReady
    : item.dataSource !== "LIVE" ||
      !item.currentPlan?.actions.some(
        (action) => action.verb === "calendar_event_create",
      ) ||
      Boolean(
        item.currentPlan?.actions.length &&
        item.currentPlan.actions.every(
          (action) => action.status === "SUCCEEDED" && action.verified === true,
        ),
      );
  return (
    <View
      style={[
        styles.completionCard,
        { backgroundColor: colors.successSoft, borderColor: colors.border },
      ]}
    >
      <View style={styles.changeTitleRow}>
        <MaterialCommunityIcons
          color={confirmed ? colors.success : colors.warning}
          name={confirmed ? "check-circle-outline" : "progress-clock"}
          size={22}
        />
        <Text style={[styles.changeTitle, { color: colors.text }]}>
          {noAction
            ? "No further action needed"
            : localOnly
              ? localReady
                ? `${localLabel} ready`
                : "Could not verify preparation"
              : confirmed
                ? "Task completed"
                : "Review the completion result"}
        </Text>
      </View>
      {results.length > 0 ? (
        results.map((action) => (
          <ActionResult key={action.actionId} action={action} />
        ))
      ) : (
        <Text style={[styles.executionBody, { color: colors.textMuted }]}>
          {noAction
            ? humanCopy(item.summary)
            : "See the result in activity history."}
        </Text>
      )}
      {!confirmed && !localOnly && (
        <Text style={[styles.executionBody, { color: colors.textMuted }]}>
          The completion record has no verified event. Check activity and Google
          Calendar.
        </Text>
      )}
      {onRepeat && confirmed && !localOnly && !noAction && (
        <SecondaryButton
          label="Create a preparation routine"
          onPress={onRepeat}
        />
      )}
    </View>
  );
}

export function StoppedResultsCard({ item }: { item: WorkspaceCase }) {
  const { colors } = useAppTheme();
  return (
    <View
      style={[styles.reconnectCard, { backgroundColor: colors.warningSoft }]}
    >
      <Text style={[styles.reconnectBody, { color: colors.textMuted }]}>
        Stopping or revoking access does not delete existing events. An
        in-flight request may already have been saved.
      </Text>
      {item.currentPlan?.actions.map((action) => (
        <ActionResult action={action} key={action.actionId} />
      ))}
    </View>
  );
}
