import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router } from "expo-router";
import {
  ActivityIndicator,
  Pressable,
  Text,
  TextInput,
  View,
} from "react-native";

import { EmptyState } from "@/src/components/EmptyState";
import type { GoogleDiscoveryPresentation } from "@/src/connections/googleDiscoveryPresentation";
import { useAppTheme } from "@/src/theme/useAppTheme";
import {
  actionQueueStances,
  type ActionQueueStance,
} from "@/src/workspace/actionQueue";

import { styles } from "./styles";

export function StanceFilter({
  active,
  count,
  label,
  onPress,
}: {
  active: boolean;
  count: number;
  label: string;
  onPress(): void;
}) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityLabel={count > 0 ? `${label}, ${count} items` : label}
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.filter,
        {
          backgroundColor: active ? colors.accent : "transparent",
          opacity: pressed ? 0.72 : 1,
        },
      ]}
    >
      <Text
        style={[
          styles.filterText,
          { color: active ? colors.onAccent : colors.textMuted },
        ]}
      >
        {label}
      </Text>
      {count > 0 && (
        <View
          style={[
            styles.filterCount,
            { backgroundColor: active ? colors.surface : colors.surfaceMuted },
          ]}
        >
          <Text
            style={[
              styles.filterCountText,
              { color: active ? colors.accent : colors.textMuted },
            ]}
          >
            {count}
          </Text>
        </View>
      )}
    </Pressable>
  );
}

export function QueueEmptyState({
  connectionKind,
  hasSearch,
  selectedStance,
}: {
  connectionKind: GoogleDiscoveryPresentation["kind"];
  hasSearch: boolean;
  selectedStance: ActionQueueStance;
}) {
  if (hasSearch) {
    return (
      <EmptyState
        body="Try another search or status."
        title="No matching tasks"
      />
    );
  }
  if (selectedStance !== "DECIDE") {
    return (
      <EmptyState
        body="Tasks appear here as their status changes."
        title={`${selectedStanceLabel(selectedStance)} Nothing here yet`}
      />
    );
  }
  if (connectionKind === "NO_CONNECTION") {
    return (
      <EmptyState
        actionLabel="Explore connections"
        body="Make a request or connect a service."
        onAction={() => router.push("/connections")}
        title="Nothing to review"
      />
    );
  }
  return (
    <EmptyState body="New tasks are grouped by source." title="No new tasks" />
  );
}

export function DirectRequestComposer({
  error,
  onChange,
  onSubmit,
  status,
  value,
}: {
  error: string | null;
  onChange(value: string): void;
  onSubmit(): void;
  status: "booting" | "ready" | "updating";
  value: string;
}) {
  const { colors } = useAppTheme();
  return (
    <View
      style={[
        styles.composer,
        { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      <TextInput
        accessibilityLabel="Make a direct request"
        multiline
        onChangeText={onChange}
        placeholder="What would you like help with?"
        placeholderTextColor={colors.textMuted}
        selectionColor={colors.accent}
        style={[
          styles.composerInput,
          { color: colors.text },
          error && { color: colors.danger },
        ]}
        value={value}
      />
      <Pressable
        accessibilityLabel="Send request"
        accessibilityRole="button"
        disabled={status !== "ready" || value.trim().length === 0}
        onPress={onSubmit}
        style={({ pressed }) => [
          styles.sendButton,
          {
            backgroundColor: colors.accent,
            opacity:
              pressed || status !== "ready" || value.trim().length === 0
                ? 0.46
                : 1,
          },
        ]}
      >
        {status === "updating" ? (
          <ActivityIndicator color={colors.onAccent} size="small" />
        ) : (
          <MaterialCommunityIcons
            color={colors.onAccent}
            name="arrow-up"
            size={21}
          />
        )}
      </Pressable>
    </View>
  );
}

function selectedStanceLabel(stance: ActionQueueStance): string {
  return actionQueueStances.find((item) => item.key === stance)?.label ?? "All";
}
