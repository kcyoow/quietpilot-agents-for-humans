import { MaterialCommunityIcons } from "@expo/vector-icons";
import { Linking, Pressable, Text, View } from "react-native";
import { useState } from "react";

import type { PrototypeAction } from "@/src/prototype/types";
import { useAppTheme } from "@/src/theme/useAppTheme";
import type { WorkspaceCase } from "@/src/workspace/types";

import { humanCopy } from "./presentation";
import { CalendarSchedule } from "./ApprovalCard";
import { styles } from "./styles";

const LOCAL_ARTIFACTS = {
  prepare_reply: {
    type: "REPLY_DRAFT",
    label: "Reply draft",
    note: "Draft only. No email sent.",
  },
  prepare_task: {
    type: "CHECKLIST",
    label: "Checklist",
    note: "Prepared for review. No external changes.",
  },
  prepare_reminder: {
    type: "REMINDER",
    label: "Reminder draft",
    note: "Draft only. No reminder or event created.",
  },
} as const;

export function localPreparationKind(action: PrototypeAction) {
  return action.connector === "quietpilot"
    ? LOCAL_ARTIFACTS[action.verb as keyof typeof LOCAL_ARTIFACTS]
    : undefined;
}

export function preparedContent(action: PrototypeAction): string | null {
  const kind = localPreparationKind(action);
  const content = action.parameters.content;
  return kind &&
    action.status === "SUCCEEDED" &&
    action.parameters.artifact_type === kind.type &&
    typeof content === "string" &&
    content.trim()
    ? content
    : null;
}

export function PlanCard({ item }: { item: WorkspaceCase }) {
  const { colors } = useAppTheme();
  const plan = item.currentPlan!;
  return (
    <View style={styles.planContent}>
      <View style={styles.planHeader}>
        <View style={styles.planCopy}>
          <Text style={[styles.planOutcome, { color: colors.text }]}>
            {humanCopy(plan.expectedOutcome)}
          </Text>
          <Text style={[styles.planReason, { color: colors.textMuted }]}>
            {humanCopy(plan.reason)}
          </Text>
        </View>
      </View>
      {plan.actions.map((action, index) => (
        <ActionCard action={action} index={index} key={action.actionId} />
      ))}
      <View style={[styles.planMeta, { borderTopColor: colors.border }]}>
        <MetaLine
          icon="undo-variant"
          label="Undo"
          value={humanCopy(plan.reversibility)}
        />
        <MetaLine
          icon="connection"
          label="Required connections"
          value={
            plan.requiredScopes.length > 0
              ? "Only within your connected permissions"
              : "No additional connection"
          }
        />
      </View>
    </View>
  );
}

function ActionCard({
  action,
  index,
}: {
  action: PrototypeAction;
  index: number;
}) {
  const { colors } = useAppTheme();
  return (
    <View style={[styles.actionCard, { borderColor: colors.border }]}>
      <View style={styles.actionTop}>
        <View
          style={[styles.actionIndex, { backgroundColor: colors.accentSoft }]}
        >
          <Text style={[styles.actionIndexText, { color: colors.accent }]}>
            {index + 1}
          </Text>
        </View>
        <View style={styles.actionCopy}>
          <Text style={[styles.actionTitle, { color: colors.text }]}>
            {humanCopy(action.label)}
          </Text>
          <Text style={[styles.actionTarget, { color: colors.textMuted }]}>
            {actionDescription(action)}
          </Text>
          <ActionStatus action={action} />
        </View>
      </View>
      {action.verb === "calendar_event_create" && (
        <CalendarSchedule action={action} />
      )}
      {humanParameters(action).length > 0 && (
        <View style={styles.parameterRows}>
          {humanParameters(action).map(([key, value]) => (
            <View key={key} style={styles.parameterRow}>
              <Text style={[styles.parameterKey, { color: colors.textSubtle }]}>
                {parameterLabel(key)}
              </Text>
              <Text style={[styles.parameterValue, { color: colors.text }]}>
                {parameterValue(key, value)}
              </Text>
            </View>
          ))}
        </View>
      )}
      <ActionResult action={action} />
    </View>
  );
}

function actionDescription(action: PrototypeAction) {
  return (
    {
      prepare_reminder: "Prepare the reminder text and timing.",
      prepare_reply: "Prepare a draft without sending it.",
      prepare_task: "Prepare tasks and a checklist.",
      calendar_event_create:
        "Create one personal event in your primary Google Calendar.",
    }[action.verb] ?? "Review all changes before execution."
  );
}

function humanParameters(
  action: PrototypeAction,
): [string, string | number | boolean][] {
  const visible = new Set([
    "due",
    "eventAt",
    "remindBeforeMinutes",
    "start",
    "title",
  ]);
  if (action.verb === "calendar_event_create") {
    visible.add("summary");
    visible.add("description");
  }
  return Object.entries(action.parameters).filter(
    (entry): entry is [string, string | number | boolean] =>
      visible.has(entry[0]) &&
      !(action.verb === "calendar_event_create" && entry[0] === "start") &&
      (typeof entry[1] === "string" ||
        typeof entry[1] === "number" ||
        typeof entry[1] === "boolean"),
  );
}

function parameterLabel(key: string) {
  return (
    {
      due: "Deadline",
      eventAt: "Reminder time",
      remindBeforeMinutes: "Reminder",
      start: "Time",
      summary: "Event title",
      description: "Description",
      title: "Content",
    }[key] ?? "Details"
  );
}

function parameterValue(key: string, value: string | number | boolean): string {
  if (key === "eventAt" && typeof value === "string") {
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleString("en-US", {
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        month: "long",
      });
    }
  }
  if (key === "remindBeforeMinutes" && typeof value === "number") {
    return value % 60 === 0
      ? `${value / 60} hours before`
      : `${value} minutes before`;
  }
  return String(value);
}

function ActionStatus({ action }: { action: PrototypeAction }) {
  const { colors } = useAppTheme();
  const status = action.status;
  const failed = status === "FAILED";
  const local = localPreparationKind(action);
  const localReady = local && preparedContent(action) !== null;
  const unconfirmed =
    status === "SUCCEEDED" &&
    (local
      ? !localReady
      : action.verified === false ||
        (action.verb === "calendar_event_create" && action.verified !== true));
  const succeeded = status === "SUCCEEDED" && !unconfirmed;
  const color = failed
    ? colors.danger
    : succeeded
      ? colors.success
      : colors.accent;
  return (
    <Text style={[styles.actionStatus, { color }]}>
      {local && status === "SUCCEEDED"
        ? localReady
          ? "Prepared"
          : "Review preparation"
        : unconfirmed
          ? "Result needs review"
          : {
              CANCELLED: "Cancel",
              FAILED: "Failed",
              PENDING: "Queued",
              PROPOSED: "Not started",
              RUNNING: "In progress",
              SUCCEEDED: "Result verified",
              VERIFYING: "Verifying result",
            }[status]}
    </Text>
  );
}

export function ActionResult({ action }: { action: PrototypeAction }) {
  const { colors } = useAppTheme();
  const [linkError, setLinkError] = useState(false);
  const local = localPreparationKind(action);
  const content = preparedContent(action);
  const calendar =
    action.connector === "google" && action.verb === "calendar_event_create";
  const url = calendar ? calendarResultUrl(action.htmlUrl) : null;
  const failure = action.errorCode
    ? ({
        GOOGLE_AUTH_REQUIRED: "Check your Google Calendar connection.",
        GOOGLE_ACCOUNT_CHANGED:
          "The Google account changed since approval. Check the connection.",
        CALENDAR_PERMISSION_CHANGED:
          "Calendar permissions changed. Execution stopped.",
        CALENDAR_ACCOUNT_CHANGED:
          "The Google account changed. Execution stopped.",
        CALENDAR_RESULT_UNCONFIRMED:
          "The event may exist, but its result is not yet verified.",
        CALENDAR_RESULT_MISMATCH:
          "Check whether the saved event matches your approval.",
      }[action.errorCode] ??
      "Could not verify the result. Check activity and connections.")
    : null;
  return (
    <>
      {local && content && (
        <View style={[styles.actionCard, { borderColor: colors.border }]}>
          <Text style={[styles.actionTitle, { color: colors.text }]}>
            {typeof action.parameters.title === "string" &&
            action.parameters.title.trim()
              ? action.parameters.title
              : local.label}
          </Text>
          <Text
            selectable
            accessibilityLabel={`${local.label} Content`}
            style={[styles.parameterValue, { color: colors.text }]}
          >
            {content}
          </Text>
          <Text style={[styles.actionTarget, { color: colors.textMuted }]}>
            Press and hold to select or copy.
          </Text>
          <Text style={[styles.actionTarget, { color: colors.textMuted }]}>
            {local.note}
          </Text>
        </View>
      )}
      {local && action.status === "SUCCEEDED" && !content && (
        <Text style={[styles.resultText, { color: colors.warning }]}>
          Could not load the prepared content. Please refresh.
        </Text>
      )}
      {action.resultSummary && (
        <Text
          style={[
            styles.resultText,
            {
              color:
                action.verified === true || content
                  ? colors.success
                  : colors.textMuted,
            },
          ]}
        >
          {humanCopy(action.resultSummary)}
        </Text>
      )}
      {failure && (
        <Text style={[styles.resultText, { color: colors.danger }]}>
          {failure}
        </Text>
      )}
      {calendar && action.resultRef && action.verified !== true && (
        <Text style={[styles.resultText, { color: colors.textMuted }]}>
          The event may exist. Completion requires verification.
        </Text>
      )}
      {url && (
        <Pressable
          accessibilityRole="link"
          accessibilityLabel="Open event in Google Calendar"
          onPress={async () => {
            setLinkError(false);
            try {
              await Linking.openURL(url);
            } catch {
              setLinkError(true);
            }
          }}
        >
          <Text style={[styles.resultText, { color: colors.accent }]}>
            Open event in Google Calendar
          </Text>
        </Pressable>
      )}
      {linkError && (
        <Text
          accessibilityRole="alert"
          style={[styles.resultText, { color: colors.danger }]}
        >
          Could not open the event link. Check Google Calendar.
        </Text>
      )}
    </>
  );
}

function calendarResultUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" &&
      ["calendar.google.com", "www.google.com"].includes(url.hostname) &&
      url.pathname.startsWith("/calendar") &&
      !url.username &&
      !url.password
      ? value
      : null;
  } catch {
    return null;
  }
}

function MetaLine({
  icon,
  label,
  value,
}: {
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  label: string;
  value: string;
}) {
  const { colors } = useAppTheme();
  return (
    <View style={styles.metaLine}>
      <MaterialCommunityIcons color={colors.textSubtle} name={icon} size={17} />
      <View style={styles.nextCopy}>
        <Text style={[styles.metaLabel, { color: colors.textSubtle }]}>
          {label}
        </Text>
        <Text style={[styles.metaValue, { color: colors.textMuted }]}>
          {value}
        </Text>
      </View>
    </View>
  );
}
