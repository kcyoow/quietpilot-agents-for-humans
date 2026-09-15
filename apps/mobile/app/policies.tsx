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
import { EmptyState } from "@/src/components/EmptyState";
import { usePrototype } from "@/src/prototype/PrototypeProvider";
import type { PrototypePolicy } from "@/src/prototype/types";
import { useAppTheme } from "@/src/theme/useAppTheme";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";

const safeguards = [
  {
    body: "Payments, deletion, sensitive sharing and device control require explicit approval.",
    icon: "shield-lock-outline" as const,
    title: "Approve important external changes",
  },
  {
    body: "Only decisions, failures and important changes need an alert.",
    icon: "bell-badge-outline" as const,
    title: "Only interrupt when needed",
  },
  {
    body: "Review again when the target, values, permissions, risk or sources change.",
    icon: "file-compare" as const,
    title: "Review changed plans",
  },
];

export default function PoliciesScreen() {
  const { colors } = useAppTheme();
  const { source } = useWorkspace();
  const { reset, revokePolicy, snapshot } = usePrototype();
  const policies = snapshot?.policies ?? [];
  const [pendingAction, setPendingAction] = useState<
    { kind: "reset" } | { kind: "revoke"; policy: PrototypePolicy } | null
  >(null);

  if (source === "LIVE") {
    return (
      <View style={[styles.screen, { backgroundColor: colors.background }]}>
        <BackHeader title="Permissions and automation" />
        <ScrollView contentContainerStyle={styles.content}>
          <View
            style={[
              styles.availability,
              { backgroundColor: colors.surface, borderColor: colors.border },
            ]}
          >
            <MaterialCommunityIcons
              color={colors.accent}
              name="shield-key-outline"
              size={26}
            />
            <Text style={[styles.title, { color: colors.text }]}>
              Automatic execution is not available
            </Text>
            <Text style={[styles.body, { color: colors.textMuted }]}>
              Each external action still needs your approval.
            </Text>
          </View>
          <Text style={[styles.sectionTitle, { color: colors.text }]}>
            Connected service permissions
          </Text>
          <Text style={[styles.body, { color: colors.textMuted }]}>
            Review access or disconnect a service in Connections.
          </Text>
          <Pressable
            accessibilityRole="button"
            onPress={() => router.push("/connections")}
            style={({ pressed }) => [
              styles.connectionButton,
              {
                backgroundColor: colors.accentSoft,
                borderColor: colors.accentBorder,
                opacity: pressed ? 0.72 : 1,
              },
            ]}
          >
            <Text
              style={[styles.connectionButtonText, { color: colors.accent }]}
            >
              Review connection permissions
            </Text>
          </Pressable>
        </ScrollView>
      </View>
    );
  }

  function confirmRevoke(policy: PrototypePolicy) {
    setPendingAction({ kind: "revoke", policy });
  }

  function confirmReset() {
    setPendingAction({ kind: "reset" });
  }

  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <BackHeader title="Permissions and automation" />
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={[styles.title, { color: colors.text }]}>
          Review permissions
        </Text>
        <Text style={[styles.body, { color: colors.textMuted }]}>
          Review and revoke example permissions.
        </Text>

        <Text style={[styles.sectionTitle, { color: colors.text }]}>
          Safeguards
        </Text>
        <View style={styles.list}>
          {safeguards.map((item) => (
            <View
              key={item.title}
              style={[
                styles.card,
                { backgroundColor: colors.surface, borderColor: colors.border },
              ]}
            >
              <MaterialCommunityIcons
                color={colors.accent}
                name={item.icon}
                size={22}
              />
              <View style={styles.copy}>
                <Text style={[styles.cardTitle, { color: colors.text }]}>
                  {item.title}
                </Text>
                <Text style={[styles.cardBody, { color: colors.textMuted }]}>
                  {item.body}
                </Text>
              </View>
            </View>
          ))}
        </View>

        <View style={styles.policyHeading}>
          <Text style={[styles.sectionTitle, { color: colors.text }]}>
            Example permissions
          </Text>
          <Text style={[styles.policyCount, { color: colors.textSubtle }]}>
            {policies.filter((item) => item.status === "ACTIVE").length} active
          </Text>
        </View>
        {policies.length === 0 ? (
          <EmptyState
            body="Conditional and recurring example permissions appear here."
            title="No example permissions"
          />
        ) : (
          <View style={styles.list}>
            {policies.map((policy) => (
              <View
                key={policy.policyId}
                style={[
                  styles.policyCard,
                  {
                    backgroundColor: colors.surface,
                    borderColor: colors.border,
                  },
                ]}
              >
                <View style={styles.policyTop}>
                  <View style={styles.copy}>
                    <Text style={[styles.cardTitle, { color: colors.text }]}>
                      {policy.title}
                    </Text>
                    <Text
                      style={[styles.cardBody, { color: colors.textMuted }]}
                    >
                      {policy.description}
                    </Text>
                  </View>
                  <View
                    style={[
                      styles.badge,
                      {
                        backgroundColor:
                          policy.status === "ACTIVE"
                            ? colors.successSoft
                            : colors.surfaceMuted,
                      },
                    ]}
                  >
                    <Text
                      style={[
                        styles.badgeText,
                        {
                          color:
                            policy.status === "ACTIVE"
                              ? colors.success
                              : colors.textMuted,
                        },
                      ]}
                    >
                      {policy.status === "ACTIVE" ? "Active" : "Revoked"}
                    </Text>
                  </View>
                </View>
                <View
                  style={[styles.policyMeta, { borderTopColor: colors.border }]}
                >
                  <Meta
                    label="Permission"
                    value={grantLabel(policy.grantMode)}
                  />
                  <Meta
                    label="Risk limit"
                    value={policy.riskCeiling === "LOW" ? "Low" : "Medium"}
                  />
                  <Meta
                    label="Tasks using this"
                    value={`${policy.affectedCaseIds.length} items`}
                  />
                </View>
                {policy.status === "ACTIVE" && (
                  <Pressable
                    accessibilityRole="button"
                    onPress={() => confirmRevoke(policy)}
                    style={({ pressed }) => [
                      styles.revoke,
                      {
                        borderColor: colors.danger,
                        opacity: pressed ? 0.7 : 1,
                      },
                    ]}
                  >
                    <MaterialCommunityIcons
                      color={colors.danger}
                      name="shield-off-outline"
                      size={17}
                    />
                    <Text style={[styles.revokeText, { color: colors.danger }]}>
                      Revoke permission
                    </Text>
                  </Pressable>
                )}
              </View>
            ))}
          </View>
        )}

        <Pressable
          accessibilityRole="button"
          onPress={confirmReset}
          style={({ pressed }) => [
            styles.resetButton,
            { borderColor: colors.border, opacity: pressed ? 0.7 : 1 },
          ]}
        >
          <Text style={[styles.resetButtonText, { color: colors.textMuted }]}>
            Reset example data
          </Text>
        </Pressable>
      </ScrollView>
      <Modal
        animationType="fade"
        onRequestClose={() => setPendingAction(null)}
        transparent
        visible={Boolean(pendingAction)}
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
              style={[styles.modalIcon, { backgroundColor: colors.dangerSoft }]}
            >
              <MaterialCommunityIcons
                color={colors.danger}
                name={
                  pendingAction?.kind === "reset"
                    ? "database-remove-outline"
                    : "shield-off-outline"
                }
                size={24}
              />
            </View>
            <Text style={[styles.modalTitle, { color: colors.text }]}>
              {pendingAction?.kind === "reset"
                ? "Reset example data"
                : "Revoke example permission"}
            </Text>
            <Text style={[styles.modalBody, { color: colors.textMuted }]}>
              {pendingAction?.kind === "reset"
                ? "Reset example connections, suggestions, tasks and history. You stay signed in."
                : "Example tasks using this permission will stop before their next action."}
            </Text>
            <View style={styles.modalButtons}>
              <Pressable
                accessibilityRole="button"
                onPress={() => setPendingAction(null)}
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
                  const action = pendingAction;
                  setPendingAction(null);
                  if (!action) return;
                  if (action.kind === "reset") {
                    await reset();
                    router.replace("/(tabs)");
                  } else {
                    await revokePolicy(action.policy.policyId);
                  }
                }}
                style={[styles.modalButton, { backgroundColor: colors.danger }]}
              >
                <Text style={[styles.modalButtonText, { color: "#fff" }]}>
                  {pendingAction?.kind === "reset"
                    ? "Reset"
                    : "Revoke permission"}
                </Text>
              </Pressable>
            </View>
          </View>
        </View>
      </Modal>
    </View>
  );
}

function Meta({ label, value }: { label: string; value: string }) {
  const { colors } = useAppTheme();
  return (
    <View style={styles.meta}>
      <Text style={[styles.metaLabel, { color: colors.textSubtle }]}>
        {label}
      </Text>
      <Text style={[styles.metaValue, { color: colors.text }]}>{value}</Text>
    </View>
  );
}

function grantLabel(mode: PrototypePolicy["grantMode"]) {
  return {
    CONDITIONAL: "Conditional",
    ONCE: "Once",
    STANDING: "Recurring",
  }[mode];
}

const styles = StyleSheet.create({
  availability: { borderRadius: 18, borderWidth: 1, gap: 12, padding: 20 },
  badge: { borderRadius: 999, paddingHorizontal: 8, paddingVertical: 6 },
  badgeText: { fontSize: 11, fontWeight: "600" },
  body: { fontSize: 14, lineHeight: 22, marginTop: 8 },
  card: {
    alignItems: "flex-start",
    borderRadius: 18,
    borderWidth: 1,
    flexDirection: "row",
    gap: 13,
    padding: 16,
  },
  cardBody: { fontSize: 14, lineHeight: 21, marginTop: 5 },
  cardTitle: { fontSize: 15, fontWeight: "600" },
  connectionButton: {
    alignItems: "center",
    borderRadius: 12,
    borderWidth: 1,
    justifyContent: "center",
    marginTop: 18,
    minHeight: 48,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  connectionButtonText: { fontSize: 14, fontWeight: "600" },
  content: { padding: 18, paddingBottom: 34 },
  copy: { flex: 1 },
  list: { gap: 10 },
  meta: { flex: 1, gap: 3 },
  metaLabel: { fontSize: 12, fontWeight: "400" },
  metaValue: { fontSize: 13, fontWeight: "600" },
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
    minHeight: 48,
  },
  modalButtons: { flexDirection: "row", gap: 9, marginTop: 20 },
  modalButtonText: { fontSize: 14, fontWeight: "600" },
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
    fontWeight: "600",
    marginTop: 14,
    textAlign: "center",
  },
  policyCard: { borderRadius: 18, borderWidth: 1, gap: 13, padding: 15 },
  policyCount: { fontSize: 11, fontWeight: "700" },
  policyHeading: {
    alignItems: "baseline",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 11,
    marginTop: 27,
  },
  policyMeta: {
    borderTopWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    paddingTop: 12,
  },
  policyTop: { alignItems: "flex-start", flexDirection: "row", gap: 10 },
  revoke: {
    alignItems: "center",
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    minHeight: 48,
  },
  revokeText: { fontSize: 13, fontWeight: "600" },
  resetButton: {
    alignItems: "center",
    borderRadius: 13,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 48,
    marginTop: 25,
  },
  resetButtonText: { fontSize: 13, fontWeight: "600" },
  screen: { flex: 1 },
  sectionTitle: {
    fontSize: 16,
    fontWeight: "600",
    marginBottom: 11,
    marginTop: 26,
  },
  title: { fontSize: 21, fontWeight: "600", letterSpacing: -0.4 },
});
