import { MaterialCommunityIcons } from "@expo/vector-icons";
import { router, useFocusEffect } from "expo-router";
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Keyboard,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  TextInput,
  View,
} from "react-native";

import { ScreenHeader } from "@/src/components/ScreenHeader";
import { useAuth } from "@/src/auth/AuthProvider";
import {
  googleDiscoveryPresentation,
  type GoogleDiscoveryPresentation,
} from "@/src/connections/googleDiscoveryPresentation";
import { useGoogleConnection } from "@/src/connections/useGoogleConnection";
import { isMailConfigured } from "@/src/mail/mailApi";
import { MailFocusActions, MailFocusCard } from "@/src/mail/MailFocusCard";
import { useMailInterestState } from "@/src/mail/MailInterestProvider";
import { useAppTheme } from "@/src/theme/useAppTheme";
import {
  actionQueueSources,
  actionQueueStances,
  buildActionQueue,
  matchesActionQueueSearch,
  type ActionQueueSource,
  type ActionQueueStance,
} from "@/src/workspace/actionQueue";
import {
  DirectRequestComposer,
  QueueEmptyState,
  StanceFilter,
} from "@/src/workspace/screens/actionQueue/Controls";
import { SourceSection } from "@/src/workspace/screens/actionQueue/SourceSection";
import { styles } from "@/src/workspace/screens/actionQueue/styles";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";

export default function ActionQueueScreen() {
  const { user } = useAuth();
  const { source } = useWorkspace();
  return <ActionQueueContent key={`${user?.userId ?? "guest"}:${source}`} />;
}

function ActionQueueContent() {
  const { colors } = useAppTheme();
  const mail = useMailInterestState();
  const { createDirectCase, error, refresh, snapshot, source, status } =
    useWorkspace();
  const {
    checking: googleChecking,
    connection: googleConnection,
    error: googleError,
    refresh: refreshGoogle,
    rescan: rescanGoogle,
  } = useGoogleConnection();
  const [selectedStance, setSelectedStance] =
    useState<ActionQueueStance>("DECIDE");
  const [collapsedSources, setCollapsedSources] = useState<
    Set<ActionQueueSource>
  >(new Set());
  const [expandedSources, setExpandedSources] = useState<
    Set<ActionQueueSource>
  >(new Set());
  const [searchOpen, setSearchOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [prompt, setPrompt] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [localError, setLocalError] = useState<string | null>(null);
  const [refreshingDiscovery, setRefreshingDiscovery] = useState(false);
  const refreshMail = mail.refresh;
  const mailProfileVersion = mail.state?.profile.version;
  const mailScanId = mail.state?.scan.scan_id;
  const directRequest = useRef({
    active: false,
    pending: false,
    editVersion: 0,
  });

  useLayoutEffect(() => {
    const current = directRequest.current;
    current.active = true;
    return () => {
      current.active = false;
    };
  }, []);

  useFocusEffect(
    useCallback(() => {
      void refreshMail();
    }, [refreshMail]),
  );

  const showMailCandidates =
    source !== "LIVE" ||
    Boolean(
      mail.state &&
      isMailConfigured(mail.state.profile) &&
      mail.state.scan.profile_version === mail.state.profile.version &&
      mail.state.scan.status === "READY",
    );
  const queue = useMemo(
    () =>
      buildActionQueue(
        snapshot
          ? {
              ...snapshot,
              candidates: snapshot.candidates.filter(
                (candidate) =>
                  source !== "LIVE" ||
                  candidate.provider !== "google" ||
                  candidate.mailDerived === false ||
                  (showMailCandidates &&
                    candidate.mailProfileVersion === mailProfileVersion &&
                    candidate.mailScanId === mailScanId),
              ),
            }
          : null,
      ),
    [mailProfileVersion, mailScanId, showMailCandidates, snapshot, source],
  );
  const counts = useMemo(
    () =>
      Object.fromEntries(
        actionQueueStances.map(({ key }) => [
          key,
          queue.filter((item) => item.stance === key).length,
        ]),
      ) as Record<ActionQueueStance, number>,
    [queue],
  );
  const visibleItems = useMemo(
    () =>
      queue.filter(
        (item) =>
          item.stance === selectedStance &&
          matchesActionQueueSearch(item, search),
      ),
    [queue, search, selectedStance],
  );
  const googleCandidateCount = useMemo(
    () =>
      (snapshot?.candidates ?? []).filter(
        (candidate) =>
          candidate.provider === "google" &&
          candidate.status === "VISIBLE" &&
          candidate.proposedActions.length > 0,
      ).length,
    [snapshot?.candidates],
  );
  const googleDiscovery = useMemo(
    () =>
      googleDiscoveryPresentation({
        candidateCount: googleCandidateCount,
        connection: googleConnection,
        connectionChecking: googleChecking,
        connectionError: googleError,
        workspaceError: error,
        workspaceStatus: status,
      }),
    [
      error,
      googleCandidateCount,
      googleChecking,
      googleConnection,
      googleError,
      status,
    ],
  );
  const mailDiscovery =
    source === "LIVE" &&
    !mail.enabled &&
    shouldShowDiscoveryStatus(googleDiscovery)
      ? googleDiscovery
      : null;
  const sections = useMemo(
    () =>
      actionQueueSources
        .map((section) => ({
          ...section,
          items: visibleItems.filter((item) => item.source === section.key),
        }))
        .filter(
          (section) =>
            section.items.length > 0 ||
            (section.key === "MAIL" &&
              (mail.enabled || mailDiscovery !== null)),
        ),
    [mail.enabled, mailDiscovery, visibleItems],
  );
  const showQueueEmpty =
    visibleItems.length === 0 &&
    mailDiscovery === null &&
    (!mail.enabled || selectedStance !== "DECIDE" || showMailCandidates);
  const mailConnection = {
    connectionStatus: googleConnection.status,
    connectionVersion: googleConnection.version,
    checking: googleChecking,
    hasMailAccess: googleConnection.grantedScopes.includes(
      "https://www.googleapis.com/auth/gmail.readonly",
    ),
  };

  async function submitDirectRequest() {
    const current = directRequest.current;
    if (
      !current.active ||
      current.pending ||
      status !== "ready" ||
      !prompt.trim()
    )
      return;
    current.pending = true;
    const editVersion = current.editVersion;
    setSubmitting(true);
    setLocalError(null);
    try {
      const created = await createDirectCase(prompt);
      if (!current.active) return;
      if (current.editVersion === editVersion) setPrompt("");
      router.push({
        pathname: "/cases/[caseId]",
        params: { caseId: created.caseId },
      });
    } catch (caught) {
      if (current.active && current.editVersion === editVersion) {
        setLocalError(errorMessage(caught));
      }
    } finally {
      current.pending = false;
      if (current.active) setSubmitting(false);
    }
  }

  function editDirectRequest(value: string) {
    if (!directRequest.current.active) return;
    directRequest.current.editVersion += 1;
    setPrompt(value);
    setLocalError(null);
  }

  async function retryDiscovery() {
    setRefreshingDiscovery(true);
    setLocalError(null);
    try {
      if (
        googleDiscovery.kind === "REANALYSIS_REQUIRED" ||
        googleConnection.status === "ERROR"
      ) {
        await rescanGoogle();
      } else {
        await refreshGoogle();
      }
      await refresh();
    } catch (caught) {
      setLocalError(errorMessage(caught));
    } finally {
      setRefreshingDiscovery(false);
    }
  }

  function toggleSource(key: ActionQueueSource) {
    setCollapsedSources((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleExpandedSource(key: ActionQueueSource) {
    setExpandedSources((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  return (
    <KeyboardAvoidingView
      behavior={Platform.OS === "ios" ? "padding" : "height"}
      style={[styles.screen, { backgroundColor: colors.background }]}
    >
      <ScreenHeader
        connectionLabel={connectionLabel(googleConnection.status)}
        onSearch={() => {
          if (searchOpen) {
            Keyboard.dismiss();
            setSearch("");
          }
          setSearchOpen((value) => !value);
        }}
        searchOpen={searchOpen}
        title="To review"
      />
      <ScrollView
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        refreshControl={
          <RefreshControl
            onRefresh={() => {
              void refresh();
              void mail.refresh();
              void refreshGoogle();
            }}
            refreshing={status === "booting"}
            tintColor={colors.accent}
          />
        }
      >
        {searchOpen && (
          <View
            style={[
              styles.searchBox,
              { backgroundColor: colors.surface, borderColor: colors.border },
            ]}
          >
            <MaterialCommunityIcons
              color={colors.textSubtle}
              name="magnify"
              size={18}
            />
            <TextInput
              accessibilityLabel="Search tasks to review"
              autoFocus
              onChangeText={setSearch}
              placeholder="Search by title or source"
              placeholderTextColor={colors.textMuted}
              selectionColor={colors.accent}
              style={[styles.searchInput, { color: colors.text }]}
              value={search}
            />
            {search.length > 0 && (
              <Pressable
                accessibilityLabel="Clear search"
                accessibilityRole="button"
                style={styles.clearSearchButton}
                onPress={() => setSearch("")}
              >
                <MaterialCommunityIcons
                  color={colors.textSubtle}
                  name="close-circle"
                  size={18}
                />
              </Pressable>
            )}
          </View>
        )}

        <ScrollView
          contentContainerStyle={styles.filters}
          horizontal
          showsHorizontalScrollIndicator={false}
        >
          {actionQueueStances.map((item) => (
            <StanceFilter
              active={selectedStance === item.key}
              count={counts[item.key]}
              key={item.key}
              label={item.label}
              onPress={() => setSelectedStance(item.key)}
            />
          ))}
        </ScrollView>

        {(localError || (error && queue.length > 0)) && (
          <View
            style={[styles.errorRow, { backgroundColor: colors.dangerSoft }]}
          >
            <MaterialCommunityIcons
              color={colors.danger}
              name="alert-circle-outline"
              size={18}
            />
            <Text style={[styles.errorText, { color: colors.danger }]}>
              {localError ?? error}
            </Text>
          </View>
        )}

        {status === "booting" && !snapshot ? (
          <ActivityIndicator color={colors.accent} style={styles.loading} />
        ) : (
          <View style={styles.sections}>
            {sections.map((section) => (
              <SourceSection
                collapsed={collapsedSources.has(section.key)}
                expandedAll={expandedSources.has(section.key)}
                icon={section.icon}
                headerAccessory={
                  section.key === "MAIL" ? (
                    <MailFocusActions {...mailConnection} />
                  ) : undefined
                }
                summary={
                  section.key === "MAIL" ? (
                    <MailFocusCard {...mailConnection} />
                  ) : undefined
                }
                items={section.items}
                key={section.key}
                label={section.label}
                presentation={section.key === "MAIL" ? mailDiscovery : null}
                refreshingDiscovery={refreshingDiscovery}
                onToggle={() => toggleSource(section.key)}
                onToggleAll={() => toggleExpandedSource(section.key)}
                retryDiscovery={retryDiscovery}
                source={section.key}
              />
            ))}
            {showQueueEmpty && (
              <QueueEmptyState
                connectionKind={googleDiscovery.kind}
                hasSearch={search.trim().length > 0}
                selectedStance={selectedStance}
              />
            )}
          </View>
        )}
      </ScrollView>
      <View
        style={[
          styles.composerDock,
          { backgroundColor: colors.background, borderTopColor: colors.border },
        ]}
      >
        <DirectRequestComposer
          error={localError}
          onChange={editDirectRequest}
          onSubmit={submitDirectRequest}
          status={submitting ? "updating" : status}
          value={prompt}
        />
      </View>
    </KeyboardAvoidingView>
  );
}

function shouldShowDiscoveryStatus(
  presentation: GoogleDiscoveryPresentation,
): boolean {
  return !["HAS_WORK", "NO_CONNECTION", "NO_WORK"].includes(presentation.kind);
}

function connectionLabel(status: string): string {
  return (
    {
      CONNECTED: "1 connected",
      CONNECTING: "Connecting",
      DISCONNECTED: "Connections",
      ERROR: "Needs attention",
      REVOKING: "Disconnecting",
      SCANNING: "Checking",
    }[status] ?? "Connections"
  );
}

function errorMessage(caught: unknown): string {
  return caught instanceof Error
    ? caught.message
    : "Could not complete the request.";
}
