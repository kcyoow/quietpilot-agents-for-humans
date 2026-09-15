import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router } from "expo-router";
import { useState } from "react";
import {
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { BackHeader } from "@/src/components/BackHeader";
import { DevelopmentBanner } from "@/src/components/DevelopmentBanner";
import { usePrototype } from "@/src/prototype/PrototypeProvider";
import type { PrototypeScenario } from "@/src/prototype/types";
import { useAppTheme } from "@/src/theme/useAppTheme";

const scenarios: {
  body: string;
  count: string;
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  label: string;
  scenario: PrototypeScenario;
}[] = [
  {
    body: "Return to your account's live data.",
    count: "Live data",
    icon: "cloud-check-outline",
    label: "Live workspace",
    scenario: "LIVE",
  },
  {
    body: "One example suggestion.",
    count: "1 suggestion",
    icon: "numeric-1-box-outline",
    label: "Single suggestion",
    scenario: "ONE",
  },
  {
    body: "An example with no connections or tasks.",
    count: "0 suggestions",
    icon: "inbox-outline",
    label: "Empty state",
    scenario: "EMPTY",
  },
  {
    body: "Example mail, Calendar plans and recovery states.",
    count: "72 suggestions",
    icon: "google",
    label: "Google examples",
    scenario: "GOOGLE",
  },
  {
    body: "Example devices, routines and high-risk approvals.",
    count: "48 suggestions",
    icon: "home-automation",
    label: "SmartThings examples",
    scenario: "SMARTTHINGS",
  },
  {
    body: "Example SMS analysis, events and reminders.",
    count: "24 suggestions",
    icon: "message-text-clock-outline",
    label: "SMS examples",
    scenario: "SMS",
  },
  {
    body: "All example task types, groups and recovery states.",
    count: "144 suggestions",
    icon: "view-dashboard-variant-outline",
    label: "All examples",
    scenario: "FULL",
  },
];

export default function PrototypeScenariosScreen() {
  const { colors } = useAppTheme();
  const { loadScenario, snapshot } = usePrototype();
  const [pending, setPending] = useState<(typeof scenarios)[number] | null>(
    null,
  );

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <BackHeader title="Example scenarios" />
      <ScrollView contentContainerStyle={styles.content}>
        <DevelopmentBanner />
        <Text style={[styles.title, { color: colors.text }]}>
          Choose an example
        </Text>
        <Text style={[styles.body, { color: colors.textMuted }]}>
          Examples use simulated data and make no external changes.
        </Text>

        <View
          style={[
            styles.current,
            {
              backgroundColor: colors.surfaceMuted,
              borderColor: colors.border,
            },
          ]}
        >
          <Text style={[styles.currentLabel, { color: colors.textMuted }]}>
            Current
          </Text>
          <Text style={[styles.currentValue, { color: colors.text }]}>
            {scenarioLabel(snapshot?.loadedScenario ?? "LIVE")}
          </Text>
          <Text style={[styles.currentMeta, { color: colors.textSubtle }]}>
            {snapshot?.candidates.length ?? 0} suggestions ·{" "}
            {snapshot?.cases.length ?? 0} tasks
          </Text>
        </View>

        <View style={styles.list}>
          {scenarios.map((item) => {
            const active = snapshot?.loadedScenario === item.scenario;
            return (
              <Pressable
                accessibilityRole="button"
                key={item.scenario}
                onPress={() => setPending(item)}
                style={({ pressed }) => [
                  styles.card,
                  {
                    backgroundColor: active
                      ? colors.accentSoft
                      : colors.surface,
                    borderColor: active ? colors.accentBorder : colors.border,
                    opacity: pressed ? 0.74 : 1,
                  },
                ]}
              >
                <View
                  style={[
                    styles.icon,
                    { backgroundColor: colors.surfaceRaised },
                  ]}
                >
                  <MaterialCommunityIcons
                    color={active ? colors.accent : colors.textMuted}
                    name={item.icon}
                    size={22}
                  />
                </View>
                <View style={styles.copy}>
                  <View style={styles.titleRow}>
                    <Text style={[styles.cardTitle, { color: colors.text }]}>
                      {item.label}
                    </Text>
                    <Text style={[styles.count, { color: colors.textSubtle }]}>
                      {item.count}
                    </Text>
                  </View>
                  <Text style={[styles.cardBody, { color: colors.textMuted }]}>
                    {item.body}
                  </Text>
                </View>
                <MaterialCommunityIcons
                  color={colors.textSubtle}
                  name={active ? "check-circle" : "chevron-right"}
                  size={20}
                />
              </Pressable>
            );
          })}
        </View>

        <View style={[styles.note, { backgroundColor: colors.warningSoft }]}>
          <MaterialCommunityIcons
            color={colors.warning}
            name="information-outline"
            size={18}
          />
          <Text style={[styles.noteText, { color: colors.warning }]}>
            Choose Live workspace to return to your account.
          </Text>
        </View>
      </ScrollView>
      <Modal
        animationType="fade"
        onRequestClose={() => setPending(null)}
        transparent
        visible={Boolean(pending)}
      >
        <View style={styles.modalBackdrop}>
          <View
            style={[
              styles.modalCard,
              {
                backgroundColor: colors.surfaceRaised,
                borderColor: colors.border,
              },
            ]}
          >
            <View
              style={[
                styles.modalIcon,
                { backgroundColor: colors.warningSoft },
              ]}
            >
              <MaterialCommunityIcons
                color={colors.warning}
                name="database-refresh-outline"
                size={24}
              />
            </View>
            <Text style={[styles.modalTitle, { color: colors.text }]}>
              {pending?.label} Load
            </Text>
            <Text style={[styles.modalBody, { color: colors.textMuted }]}>
              Load this example? Your account and services stay unchanged.
            </Text>
            <View style={styles.modalButtons}>
              <Pressable
                accessibilityRole="button"
                onPress={() => setPending(null)}
                style={[styles.modalButton, { borderColor: colors.border }]}
              >
                <Text
                  style={[styles.modalButtonText, { color: colors.textMuted }]}
                >
                  Cancel
                </Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                onPress={async () => {
                  if (!pending) return;
                  const scenario = pending.scenario;
                  setPending(null);
                  await loadScenario(scenario);
                  router.replace("/(tabs)");
                }}
                style={[styles.modalButton, { backgroundColor: colors.accent }]}
              >
                <Text
                  style={[styles.modalButtonText, { color: colors.onAccent }]}
                >
                  Load
                </Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

function scenarioLabel(scenario: PrototypeScenario) {
  return {
    EMPTY: "Empty state",
    FULL: "All examples",
    GOOGLE: "Google examples",
    LIVE: "Live workspace",
    ONE: "Single suggestion",
    SMS: "SMS examples",
    SMARTTHINGS: "SmartThings examples",
  }[scenario];
}

const styles = StyleSheet.create({
  body: { fontSize: 14, lineHeight: 22, marginTop: 8 },
  card: {
    alignItems: "center",
    borderRadius: 18,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    padding: 15,
  },
  cardBody: { fontSize: 11, lineHeight: 17, marginTop: 5 },
  cardTitle: { fontSize: 14, fontWeight: "800" },
  content: { padding: 18, paddingBottom: 34 },
  copy: { flex: 1 },
  count: { fontSize: 10, fontWeight: "700" },
  current: { borderRadius: 15, borderWidth: 1, marginTop: 22, padding: 14 },
  currentLabel: { fontSize: 10, fontWeight: "800" },
  currentMeta: { fontSize: 11, marginTop: 4 },
  currentValue: { fontSize: 15, fontWeight: "800", marginTop: 4 },
  icon: {
    alignItems: "center",
    borderRadius: 13,
    height: 43,
    justifyContent: "center",
    width: 43,
  },
  list: { gap: 10, marginTop: 18 },
  modalBackdrop: {
    alignItems: "center",
    backgroundColor: "rgba(5, 12, 11, 0.52)",
    flex: 1,
    justifyContent: "center",
    padding: 24,
  },
  modalBody: {
    fontSize: 13,
    lineHeight: 20,
    marginTop: 8,
    textAlign: "center",
  },
  modalButton: {
    alignItems: "center",
    borderRadius: 13,
    borderWidth: 1,
    flex: 1,
    justifyContent: "center",
    minHeight: 47,
  },
  modalButtons: { flexDirection: "row", gap: 9, marginTop: 20 },
  modalButtonText: { fontSize: 12, fontWeight: "800" },
  modalCard: {
    borderRadius: 21,
    borderWidth: 1,
    maxWidth: 380,
    padding: 20,
    width: "100%",
  },
  modalIcon: {
    alignItems: "center",
    alignSelf: "center",
    borderRadius: 16,
    height: 50,
    justifyContent: "center",
    width: 50,
  },
  modalTitle: {
    fontSize: 18,
    fontWeight: "800",
    marginTop: 14,
    textAlign: "center",
  },
  note: {
    alignItems: "flex-start",
    borderRadius: 14,
    flexDirection: "row",
    gap: 8,
    marginTop: 18,
    padding: 13,
  },
  noteText: { flex: 1, fontSize: 11, lineHeight: 17 },
  screen: { flex: 1 },
  title: {
    fontSize: 25,
    fontWeight: "800",
    letterSpacing: -0.7,
    marginTop: 28,
  },
  titleRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
  },
});
