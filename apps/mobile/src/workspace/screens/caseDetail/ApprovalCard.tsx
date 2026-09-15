import { MaterialCommunityIcons } from "@expo/vector-icons";
import { ActivityIndicator, Pressable, Text, View } from "react-native";
import type {
  PrototypeAction,
  PrototypeGrantMode,
} from "@/src/prototype/types";
import { useAppTheme } from "@/src/theme/useAppTheme";
import type { WorkspaceCase } from "@/src/workspace/types";
import { SecondaryButton } from "./DetailPrimitives";
import { styles } from "./styles";

export function ApprovalCard({
  grantMode,
  item,
  onApprove,
  onDefer,
  onGrantMode,
  onReviewPlan,
  approvalEnabled,
  updating,
}: {
  grantMode: PrototypeGrantMode;
  item: WorkspaceCase;
  onApprove(): void;
  onDefer?(): void;
  onGrantMode(mode: PrototypeGrantMode): void;
  onReviewPlan(): void;
  approvalEnabled: boolean;
  updating: boolean;
}) {
  const { colors } = useAppTheme();
  const modes = item.currentPlan!.availableGrantModes;
  const calendar = item.currentPlan!.actions.find(
    (action) =>
      action.connector === "google" && action.verb === "calendar_event_create",
  );
  return (
    <View
      style={[
        styles.approvalCard,
        {
          backgroundColor: approvalEnabled
            ? colors.warningSoft
            : colors.surface,
          borderColor: approvalEnabled ? colors.warningBorder : colors.border,
        },
      ]}
    >
      <Text style={[styles.approvalTitle, { color: colors.text }]}>
        {approvalEnabled ? "Approve this plan?" : "Review the prepared plan"}
      </Text>
      {calendar && (
        <View style={{ gap: 6, marginVertical: 12 }}>
          <Text style={[styles.grantTitle, { color: colors.text }]}>
            {String(calendar.parameters.summary ?? "")}
          </Text>
          <CalendarSchedule action={calendar} />
          {typeof calendar.parameters.description === "string" && (
            <Text style={[styles.grantBody, { color: colors.textMuted }]}>
              {calendar.parameters.description}
            </Text>
          )}
          <Text style={[styles.grantBody, { color: colors.textMuted }]}>
            Create one personal event in your primary calendar. You can delete
            it in Google Calendar.
          </Text>
        </View>
      )}
      <Text style={[styles.approvalBody, { color: colors.textMuted }]}>
        {approvalEnabled
          ? "Review the changes and choose what to allow."
          : "Review the plan. External execution is unavailable for this task."}
      </Text>
      {!approvalEnabled && (
        <Pressable
          accessibilityLabel="Review plan"
          accessibilityRole="button"
          accessibilityHint="Expand the plan without executing it."
          onPress={onReviewPlan}
          style={({ pressed }) => [
            styles.approveButton,
            { backgroundColor: colors.accent, opacity: pressed ? 0.7 : 1 },
          ]}
        >
          <Text style={[styles.approveText, { color: colors.onAccent }]}>
            Review plan
          </Text>
          <MaterialCommunityIcons
            color={colors.onAccent}
            name="arrow-right"
            size={19}
          />
        </Pressable>
      )}
      {approvalEnabled && (
        <>
          <View accessibilityRole="radiogroup" style={styles.grants}>
            {modes.map((mode) => (
              <Pressable
                accessibilityLabel={grantLabel(mode)}
                accessibilityRole="radio"
                accessibilityState={{
                  checked: grantMode === mode,
                  disabled: updating,
                }}
                disabled={updating}
                key={mode}
                onPress={() => onGrantMode(mode)}
                style={[
                  styles.grant,
                  {
                    backgroundColor:
                      grantMode === mode ? colors.surface : "transparent",
                    borderColor:
                      grantMode === mode ? colors.warningBorder : colors.border,
                  },
                ]}
              >
                <MaterialCommunityIcons
                  color={
                    grantMode === mode ? colors.warning : colors.textSubtle
                  }
                  name={
                    grantMode === mode ? "radiobox-marked" : "radiobox-blank"
                  }
                  size={18}
                />
                <View style={styles.grantCopy}>
                  <Text style={[styles.grantTitle, { color: colors.text }]}>
                    {grantLabel(mode)}
                  </Text>
                  <Text style={[styles.grantBody, { color: colors.textMuted }]}>
                    {grantDescription(mode)}
                  </Text>
                </View>
              </Pressable>
            ))}
          </View>
          <Pressable
            accessibilityLabel={`${grantLabel(grantMode)} to continue`}
            accessibilityRole="button"
            accessibilityState={{ disabled: updating }}
            disabled={updating}
            onPress={onApprove}
            style={({ pressed }) => [
              styles.approveButton,
              {
                backgroundColor: colors.accent,
                opacity: pressed || updating ? 0.55 : 1,
              },
            ]}
          >
            {updating ? (
              <ActivityIndicator color={colors.onAccent} size="small" />
            ) : (
              <>
                <MaterialCommunityIcons
                  color={colors.onAccent}
                  name="shield-check-outline"
                  size={19}
                />
                <Text style={[styles.approveText, { color: colors.onAccent }]}>
                  {grantLabel(grantMode)} to continue
                </Text>
              </>
            )}
          </Pressable>
        </>
      )}
      {onDefer && (
        <View style={styles.buttonRow}>
          <SecondaryButton
            disabled={updating}
            label="Not now"
            onPress={onDefer}
          />
        </View>
      )}
    </View>
  );
}

export function CalendarSchedule({ action }: { action: PrototypeAction }) {
  const { colors } = useAppTheme();
  return (
    <>
      {calendarScheduleLines(action).map((line) => (
        <Text
          key={line}
          style={[styles.grantBody, { color: colors.textMuted }]}
        >
          {line}
        </Text>
      ))}
    </>
  );
}

export function calendarScheduleLines(action: PrototypeAction): string[] {
  const { start, end } = action.parameters;
  if (typeof start !== "string" || typeof end !== "string") return [];
  const allDay = /^\d{4}-\d{2}-\d{2}$/;
  const starts = new Date(start);
  const ends = new Date(end);
  if (
    !Number.isFinite(starts.getTime()) ||
    !Number.isFinite(ends.getTime()) ||
    ends <= starts
  )
    return [];
  if (allDay.test(start) && allDay.test(end)) {
    if (
      starts.toISOString().slice(0, 10) !== start ||
      ends.toISOString().slice(0, 10) !== end
    )
      return [];
    const lastDay = new Date(ends.getTime() - 86_400_000);
    const format = new Intl.DateTimeFormat("en-US", {
      timeZone: "UTC",
      year: "numeric",
      month: "long",
      day: "numeric",
    });
    return [
      `Date: ${format.format(starts)}${lastDay.getTime() === starts.getTime() ? "" : ` ~ ${format.format(lastDay)}`} (all day)`,
    ];
  }
  if (
    allDay.test(start) ||
    allDay.test(end) ||
    !/(Z|[+-]\d{2}:\d{2})$/.test(start) ||
    !/(Z|[+-]\d{2}:\d{2})$/.test(end)
  )
    return [];
  const format = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return [
    `Start: ${format.format(starts)} (Korea time)`,
    `End: ${format.format(ends)} (Korea time)`,
  ];
}

function grantLabel(mode: PrototypeGrantMode) {
  return {
    CONDITIONAL: "Conditional permission",
    ONCE: "Allow once",
    STANDING: "Recurring permission",
  }[mode];
}

function grantDescription(mode: PrototypeGrantMode) {
  return {
    CONDITIONAL: "Only under these target, timing and risk conditions",
    ONCE: "Only this exact plan, once",
    STANDING: "For repeated low-impact tasks within the shown scope",
  }[mode];
}
