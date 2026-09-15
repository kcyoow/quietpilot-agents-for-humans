import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router } from "expo-router";
import * as WebBrowser from "expo-web-browser";
import { useState } from "react";
import {
  ActivityIndicator,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";

import { BackHeader } from "@/src/components/BackHeader";
import { useGoogleConnection } from "@/src/connections/useGoogleConnection";
import {
  formatConnectionTime,
  syncModeLabel,
  watchHealthLabel,
} from "@/src/connections/googleConnectionView";
import { usePrototype } from "@/src/prototype/PrototypeProvider";
import {
  PROTOTYPE_PROVIDER_META,
  PROTOTYPE_PROVIDER_ORDER,
} from "@/src/prototype/providerMeta";
import type {
  PrototypeConnection,
  PrototypeProvider,
} from "@/src/prototype/types";
import { useAppTheme } from "@/src/theme/useAppTheme";

export default function ConnectionsScreen() {
  const { colors } = useAppTheme();
  const { error } = usePrototype();
  const google = useGoogleConnection();
  const initialChecking =
    google.checking && google.connection.status === "DISCONNECTED";
  const [expanded, setExpanded] = useState(false);
  const upcomingProviders = PROTOTYPE_PROVIDER_ORDER.filter(
    (provider) => provider !== "google",
  );
  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <BackHeader title="Connections" />
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={[styles.title, { color: colors.text }]}>
          Connect the services you need
        </Text>
        <Text style={[styles.body, { color: colors.textMuted }]}>
          You can also make direct requests without a connection.
        </Text>

        {(google.error || error) && (
          <View
            accessible
            accessibilityRole="alert"
            style={[styles.errorCard, { backgroundColor: colors.dangerSoft }]}
          >
            <MaterialCommunityIcons
              color={colors.danger}
              name="alert-circle-outline"
              size={18}
            />
            <Text style={[styles.errorText, { color: colors.danger }]}>
              {google.error || error}
            </Text>
          </View>
        )}

        <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>
          {initialChecking
            ? "Checking connections"
            : google.connection.status === "CONNECTED"
              ? "Connected services · 1"
              : "Available services"}
        </Text>
        <ConnectionCard
          checking={initialChecking}
          connection={google.connection}
          expanded={expanded}
          onConnect={async () => {
            try {
              const connection = await google.connect();
              if (
                ["CONNECTED", "SCANNING"].includes(connection.status) ||
                (connection.status === "CONNECTING" &&
                  connection.grantedScopes.includes(
                    "https://www.googleapis.com/auth/gmail.readonly",
                  ))
              ) {
                router.replace({
                  pathname: "/(tabs)",
                  params: { mailSetup: "1" },
                });
              }
              return connection;
            } catch {
              return google.connection;
            }
          }}
          onDisconnect={() =>
            google.disconnect().catch(() => google.connection)
          }
          onToggle={() => setExpanded((current) => !current)}
          updating={google.updating}
        />
        {google.updating &&
          google.connection.status !== "REVOKING" &&
          (Platform.OS === "android" ? (
            <Text style={[styles.body, { color: colors.textMuted }]}>
              Close the sign-in window to return to the app.
            </Text>
          ) : (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Close sign-in"
              onPress={() => WebBrowser.dismissAuthSession()}
              style={{
                minHeight: 44,
                justifyContent: "center",
                alignItems: "flex-end",
              }}
            >
              <Text style={{ color: colors.accent }}>Close sign-in</Text>
            </Pressable>
          ))}

        {google.connection.status === "CONNECTED" &&
          google.connection.unavailable.some(
            (item) => item.itemId === "calendar-connect-live",
          ) && (
            <View
              style={[
                styles.card,
                { backgroundColor: colors.surface, borderColor: colors.border },
              ]}
            >
              <Text style={[styles.title, { color: colors.text }]}>
                Google Calendar
              </Text>
              <Text style={[styles.body, { color: colors.textMuted }]}>
                {google.connection.accessible.some(
                  (item) => item.capability === "calendar.events.owned",
                )
                  ? "Connected. Review and approve each event found in your mail."
                  : "Add appointments and deadlines from mail. Use the same Google account as Gmail."}
              </Text>
              {
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Connect Google Calendar"
                  disabled={google.updating || google.checking}
                  onPress={() =>
                    void google.connect("calendar").catch(() => undefined)
                  }
                  style={({ pressed }) => ({
                    paddingVertical: 16,
                    opacity: pressed || google.updating ? 0.6 : 1,
                  })}
                >
                  <Text style={{ color: colors.accent, fontWeight: "700" }}>
                    {google.connection.accessible.some(
                      (item) => item.capability === "calendar.events.owned",
                    )
                      ? "Reconnect Calendar"
                      : "Connect Google Calendar"}
                  </Text>
                </Pressable>
              }
            </View>
          )}

        <Text style={[styles.sectionTitle, { color: colors.textMuted }]}>
          Coming soon
        </Text>
        <View style={styles.list}>
          {upcomingProviders.map((provider) => (
            <UpcomingConnection key={provider} provider={provider} />
          ))}
        </View>
      </ScrollView>
    </View>
  );
}

function ConnectionCard({
  checking,
  connection,
  expanded,
  onConnect,
  onDisconnect,
  onToggle,
  updating,
}: {
  checking: boolean;
  connection: PrototypeConnection;
  expanded: boolean;
  onConnect(): Promise<PrototypeConnection>;
  onDisconnect(): Promise<PrototypeConnection>;
  onToggle(): void;
  updating: boolean;
}) {
  const { colors } = useAppTheme();
  const isConnected = connection.status === "CONNECTED";
  const isScanning = connection.status === "SCANNING";
  const isRevoking = connection.status === "REVOKING";
  const isBusy = isScanning || isRevoking;
  const progress = Math.max(0, Math.min(100, connection.scanProgress));
  const providerMeta = PROTOTYPE_PROVIDER_META[connection.provider];
  const statusLabel = checking ? "Checking" : connectionStatusLabel(connection);
  const actionLabel = isConnected
    ? "Disconnect Google"
    : connection.status === "CONNECTING"
      ? "Resume Google connection"
      : isScanning || connection.status === "ERROR"
        ? "Reconnect Google"
        : "Connect Google";
  return (
    <View
      style={[
        styles.card,
        { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      <Pressable
        accessibilityLabel="Google connection details"
        accessibilityRole="button"
        accessibilityState={{ expanded }}
        accessibilityValue={{ text: statusLabel }}
        onPress={onToggle}
        style={({ pressed }) => [
          styles.cardHeader,
          { opacity: pressed ? 0.72 : 1 },
        ]}
      >
        <View style={[styles.icon, { backgroundColor: colors.surfaceMuted }]}>
          <MaterialCommunityIcons
            color={colors.accent}
            name={
              providerMeta.icon as keyof typeof MaterialCommunityIcons.glyphMap
            }
            size={22}
          />
        </View>
        <View style={styles.copy}>
          <View style={styles.titleRow}>
            <Text style={[styles.cardTitle, { color: colors.text }]}>
              {connection.label}
            </Text>
            <View
              style={[
                styles.badge,
                {
                  backgroundColor: isConnected
                    ? colors.successSoft
                    : isBusy
                      ? colors.accentSoft
                      : colors.surfaceMuted,
                },
              ]}
            >
              <Text
                style={[
                  styles.badgeText,
                  {
                    color: isConnected
                      ? colors.success
                      : isBusy
                        ? colors.accent
                        : colors.textMuted,
                  },
                ]}
              >
                {statusLabel}
              </Text>
            </View>
          </View>
          <Text style={[styles.cardBody, { color: colors.textMuted }]}>
            {providerMeta.description}
          </Text>
        </View>
        <MaterialCommunityIcons
          color={colors.textSubtle}
          name={expanded ? "chevron-up" : "chevron-down"}
          size={20}
        />
      </Pressable>

      {checking && (
        <View
          accessible
          accessibilityLabel="Checking Google connection"
          accessibilityRole="progressbar"
          style={styles.revoking}
        >
          <ActivityIndicator color={colors.accent} size="small" />
          <Text style={[styles.cardBody, { color: colors.textMuted }]}>
            Checking connection status.
          </Text>
        </View>
      )}

      {isScanning && (
        <View
          accessible
          accessibilityLabel="Gmail sync progress"
          accessibilityRole="progressbar"
          accessibilityValue={{ min: 0, max: 100, now: progress }}
          style={[
            styles.progressTrack,
            { backgroundColor: colors.surfaceMuted },
          ]}
        >
          <View
            style={[
              styles.progressFill,
              { backgroundColor: colors.accent, width: `${progress}%` },
            ]}
          />
        </View>
      )}
      {isRevoking && (
        <View
          accessible
          accessibilityLabel="Disconnecting Google"
          accessibilityRole="progressbar"
          style={styles.revoking}
        >
          <ActivityIndicator color={colors.accent} size="small" />
          <Text style={[styles.cardBody, { color: colors.textMuted }]}>
            Removing the connection and its permissions.
          </Text>
        </View>
      )}

      {expanded && (
        <View style={[styles.details, { borderTopColor: colors.border }]}>
          <ScopeRow
            body={providerMeta.readScope}
            icon="eye-outline"
            title="Read access"
          />
          <ScopeRow
            body={providerMeta.executionScope}
            icon="gesture-tap-button"
            title="Action access"
          />

          {isConnected && (
            <>
              <View
                style={[
                  styles.maintenance,
                  {
                    backgroundColor: colors.surfaceMuted,
                    borderColor: colors.border,
                  },
                ]}
              >
                <Text style={[styles.maintenanceTitle, { color: colors.text }]}>
                  Automatic sync
                </Text>
                <MaintenanceRow
                  label="New mail checks"
                  value={watchHealthLabel(connection)}
                />
                <MaintenanceRow
                  label="Next renewal"
                  value={
                    connection.nextRenewalDueAt
                      ? `By ${formatConnectionTime(connection.nextRenewalDueAt)}`
                      : "Checking"
                  }
                />
                <MaintenanceRow
                  label="Sync access expires"
                  value={formatConnectionTime(connection.watchExpiresAt)}
                />
                <MaintenanceRow label="Recovery check" value="Every 6 hours" />
                <MaintenanceRow
                  label="Last synced"
                  value={
                    connection.lastCheckedAt
                      ? `${syncModeLabel(connection.lastSyncMode) ?? "Check status"} · ${formatConnectionTime(connection.lastCheckedAt)}`
                      : "Checking"
                  }
                />
              </View>
              <Text style={[styles.inventoryTitle, { color: colors.text }]}>
                Available
              </Text>
              {connection.accessible.map((item) => (
                <InventoryRow accessible item={item} key={item.itemId} />
              ))}
              <Text style={[styles.inventoryTitle, { color: colors.text }]}>
                Unavailable
              </Text>
              {connection.unavailable.map((item) => (
                <InventoryRow item={item} key={item.itemId} />
              ))}
            </>
          )}
        </View>
      )}

      {!checking && isConnected && expanded && (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Reconnect Google"
          disabled={updating}
          onPress={onConnect}
          style={({ pressed }) => [
            styles.connectButton,
            {
              backgroundColor: colors.accentSoft,
              borderColor: colors.accentBorder,
              opacity: pressed || updating ? 0.58 : 1,
            },
          ]}
        >
          <MaterialCommunityIcons
            name="refresh"
            size={18}
            color={colors.accent}
          />
          <Text style={[styles.connectText, { color: colors.accent }]}>
            Reconnect Google
          </Text>
        </Pressable>
      )}
      {!checking && !isRevoking && (!isConnected || expanded) && (
        <Pressable
          accessibilityLabel={actionLabel}
          accessibilityRole="button"
          accessibilityState={{ disabled: updating }}
          disabled={updating}
          onPress={isConnected ? onDisconnect : onConnect}
          style={({ pressed }) => [
            styles.connectButton,
            {
              backgroundColor: isConnected ? colors.surface : colors.accentSoft,
              borderColor: isConnected ? colors.border : colors.accentBorder,
              opacity: pressed || updating ? 0.58 : 1,
            },
          ]}
        >
          {updating ? (
            <ActivityIndicator color={colors.accent} size="small" />
          ) : (
            <MaterialCommunityIcons
              color={isConnected ? colors.textMuted : colors.accent}
              name={isConnected ? "link-variant-off" : "link-variant-plus"}
              size={18}
            />
          )}
          <Text
            style={[
              styles.connectText,
              { color: isConnected ? colors.textMuted : colors.accent },
            ]}
          >
            {actionLabel}
          </Text>
        </Pressable>
      )}
    </View>
  );
}

function UpcomingConnection({ provider }: { provider: PrototypeProvider }) {
  const { colors } = useAppTheme();
  const providerMeta = PROTOTYPE_PROVIDER_META[provider];
  return (
    <View
      accessible
      accessibilityLabel={`${providerMeta.label}, coming soon`}
      style={[
        styles.upcomingRow,
        { backgroundColor: colors.surface, borderColor: colors.border },
      ]}
    >
      <View style={[styles.icon, { backgroundColor: colors.surfaceMuted }]}>
        <MaterialCommunityIcons
          color={colors.textSubtle}
          name={
            providerMeta.icon as keyof typeof MaterialCommunityIcons.glyphMap
          }
          size={22}
        />
      </View>
      <View style={styles.copy}>
        <Text style={[styles.cardTitle, { color: colors.textMuted }]}>
          {providerMeta.label}
        </Text>
        <Text style={[styles.cardBody, { color: colors.textMuted }]}>
          {providerMeta.description}
        </Text>
      </View>
      <View style={[styles.badge, { backgroundColor: colors.surfaceMuted }]}>
        <Text style={[styles.badgeText, { color: colors.textMuted }]}>
          Coming soon
        </Text>
      </View>
    </View>
  );
}

function MaintenanceRow({ label, value }: { label: string; value: string }) {
  const { colors } = useAppTheme();
  return (
    <View style={styles.maintenanceRow}>
      <Text style={[styles.maintenanceLabel, { color: colors.textMuted }]}>
        {label}
      </Text>
      <Text style={[styles.maintenanceValue, { color: colors.text }]}>
        {value}
      </Text>
    </View>
  );
}

function ScopeRow({
  body,
  icon,
  title,
}: {
  body: string;
  icon: keyof typeof MaterialCommunityIcons.glyphMap;
  title: string;
}) {
  const { colors } = useAppTheme();
  return (
    <View style={styles.scopeRow}>
      <MaterialCommunityIcons color={colors.accent} name={icon} size={18} />
      <View style={styles.scopeCopy}>
        <Text style={[styles.scopeTitle, { color: colors.text }]}>{title}</Text>
        <Text style={[styles.scopeBody, { color: colors.textMuted }]}>
          {body}
        </Text>
      </View>
    </View>
  );
}

function InventoryRow({
  accessible = false,
  item,
}: {
  accessible?: boolean;
  item: PrototypeConnection["accessible"][number];
}) {
  const { colors } = useAppTheme();
  return (
    <View style={styles.inventoryRow}>
      <MaterialCommunityIcons
        color={accessible ? colors.success : colors.textSubtle}
        name={accessible ? "check-circle-outline" : "minus-circle-outline"}
        size={17}
      />
      <View style={styles.inventoryCopy}>
        <Text style={[styles.inventoryLabel, { color: colors.text }]}>
          {item.label}
        </Text>
        <Text style={[styles.inventoryDetail, { color: colors.textMuted }]}>
          {item.detail}
        </Text>
      </View>
    </View>
  );
}

function connectionStatusLabel(connection: PrototypeConnection) {
  return {
    CONNECTED: "Connected",
    CONNECTING: "Awaiting consent",
    DISCONNECTED: "Not connected",
    ERROR: "Needs attention",
    REVOKING: "Disconnecting",
    SCANNING: `Gmail sync ${connection.scanProgress}%`,
  }[connection.status];
}

const styles = StyleSheet.create({
  badge: { borderRadius: 8, paddingHorizontal: 8, paddingVertical: 4 },
  badgeText: { fontSize: 11, fontWeight: "600" },
  body: { fontSize: 14, fontWeight: "400", lineHeight: 21, marginTop: 8 },
  card: { borderRadius: 16, borderWidth: 1, gap: 14, padding: 16 },
  cardBody: { fontSize: 13, fontWeight: "400", lineHeight: 19, marginTop: 4 },
  cardHeader: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    minHeight: 52,
  },
  cardTitle: { fontSize: 16, fontWeight: "600" },
  connectButton: {
    alignItems: "center",
    borderRadius: 12,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    justifyContent: "center",
    minHeight: 48,
  },
  connectText: { fontSize: 14, fontWeight: "600" },
  content: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 36 },
  copy: { flex: 1 },
  details: {
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: 14,
    paddingTop: 16,
  },
  errorCard: {
    alignItems: "flex-start",
    borderRadius: 12,
    flexDirection: "row",
    gap: 9,
    marginTop: 16,
    padding: 14,
  },
  errorText: { flex: 1, fontSize: 13, fontWeight: "400", lineHeight: 19 },
  icon: {
    alignItems: "center",
    borderRadius: 12,
    height: 44,
    justifyContent: "center",
    width: 44,
  },
  inventoryCopy: { flex: 1 },
  inventoryDetail: {
    fontSize: 12,
    fontWeight: "400",
    lineHeight: 18,
    marginTop: 3,
  },
  inventoryLabel: { fontSize: 13, fontWeight: "600" },
  inventoryRow: { alignItems: "flex-start", flexDirection: "row", gap: 9 },
  inventoryTitle: { fontSize: 13, fontWeight: "600", marginTop: 6 },
  list: { gap: 10 },
  maintenance: {
    borderRadius: 12,
    borderWidth: 1,
    gap: 10,
    padding: 14,
  },
  maintenanceLabel: { fontSize: 12, fontWeight: "400", lineHeight: 18 },
  maintenanceRow: {
    alignItems: "flex-start",
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between",
  },
  maintenanceTitle: { fontSize: 13, fontWeight: "600", marginBottom: 2 },
  maintenanceValue: {
    flexShrink: 1,
    fontSize: 12,
    fontWeight: "400",
    lineHeight: 18,
    textAlign: "right",
  },
  progressFill: { borderRadius: 3, height: 4 },
  progressTrack: { borderRadius: 3, height: 4, overflow: "hidden" },
  revoking: { alignItems: "center", flexDirection: "row", gap: 9 },
  scopeBody: { fontSize: 13, fontWeight: "400", lineHeight: 19, marginTop: 3 },
  scopeCopy: { flex: 1 },
  scopeRow: { alignItems: "flex-start", flexDirection: "row", gap: 10 },
  scopeTitle: { fontSize: 13, fontWeight: "600" },
  screen: { flex: 1 },
  sectionTitle: {
    fontSize: 13,
    fontWeight: "600",
    marginBottom: 12,
    marginTop: 28,
  },
  title: {
    fontSize: 21,
    fontWeight: "600",
    letterSpacing: -0.4,
    lineHeight: 29,
    marginTop: 8,
  },
  titleRow: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
  },
  upcomingRow: {
    alignItems: "center",
    borderRadius: 16,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    minHeight: 80,
    padding: 16,
  },
});
