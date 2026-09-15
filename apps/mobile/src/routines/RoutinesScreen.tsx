import { useFocusEffect, useLocalSearchParams, Redirect } from "expo-router";
import { useCallback, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import {
  createProductApi,
  type LiveCaseDetail,
  type LiveRoutine,
} from "@/src/api/productApi";
import { useAuth } from "@/src/auth/AuthProvider";
import { BackHeader } from "@/src/components/BackHeader";
import { useAppTheme } from "@/src/theme/useAppTheme";
import { useWorkspace } from "@/src/workspace/WorkspaceProvider";

const labels = {
  PROPOSED: "Ready to enable",
  ACTIVE: "Active",
  PAUSED: "Paused",
  REVIEW_REQUIRED: "Connection needs attention",
};

export default function RoutinesScreen() {
  const { user, status: authStatus } = useAuth();
  const { source } = useWorkspace();
  const { colors } = useAppTheme();
  const { caseId } = useLocalSearchParams<{ caseId?: string }>();
  const api = useMemo(() => createProductApi(), []);
  const owner = user?.userId ?? null;
  const scope = useMemo(
    () => ({ owner, source, caseId, request: 0 }),
    [owner, source, caseId],
  );
  const current = useRef(scope);
  const [state, setState] = useState<{
    scope: typeof scope;
    routines: LiveRoutine[];
    seed: LiveCaseDetail | null;
    error: string | null;
    loading: boolean;
    busy: string | null;
  }>({
    scope,
    routines: [],
    seed: null,
    error: null,
    loading: true,
    busy: null,
  });
  useLayoutEffect(() => {
    current.current = scope;
  }, [scope]);
  const visible =
    state.scope === scope
      ? state
      : {
          scope,
          routines: [],
          seed: null,
          error: null,
          loading: true,
          busy: null,
        };
  const live = Boolean(owner) && source === "LIVE";

  const load = useCallback(async () => {
    if (!live) return;
    const request = ++scope.request;
    setState((previous) => ({
      ...(previous.scope === scope
        ? previous
        : { routines: [], seed: null, busy: null }),
      scope,
      loading: true,
      error: null,
    }));
    try {
      const [routines, seed] = await Promise.all([
        api.listRoutines(),
        caseId ? api.getCase(caseId) : Promise.resolve(null),
      ]);
      if (current.current === scope && request === scope.request)
        setState({
          scope,
          routines,
          seed,
          error: null,
          loading: false,
          busy: null,
        });
    } catch (error) {
      if (current.current === scope && request === scope.request)
        setState((previous) => ({
          ...previous,
          scope,
          loading: false,
          error: message(error),
        }));
    }
  }, [api, caseId, live, scope]);

  useFocusEffect(
    useCallback(() => {
      void load();
      return () => {
        scope.request += 1;
      };
    }, [load, scope]),
  );

  async function mutate(id: string, operation: () => Promise<LiveRoutine>) {
    if (!live || visible.busy) return;
    const request = ++scope.request;
    setState((previous) => ({ ...previous, scope, busy: id, error: null }));
    try {
      const routine = await operation();
      if (current.current === scope && request === scope.request)
        setState((previous) => ({
          ...previous,
          scope,
          busy: null,
          routines: [
            routine,
            ...previous.routines.filter(
              (item) => item.routine_id !== routine.routine_id,
            ),
          ],
        }));
    } catch (error) {
      if (current.current === scope && request === scope.request)
        setState((previous) => ({
          ...previous,
          scope,
          busy: null,
          error: message(error),
        }));
    }
  }

  if (authStatus === "ready" && !user) return <Redirect href="/" />;
  const seed = visible.seed;
  const alreadyProposed = Boolean(
    seed &&
    visible.routines.some((item) => item.source_case_id === seed.case_id),
  );
  return (
    <View style={[styles.screen, { backgroundColor: colors.background }]}>
      <BackHeader title="Preparation routines" />
      <ScrollView contentContainerStyle={styles.content}>
        <Text style={[styles.intro, { color: colors.text }]}>
          Prepare recurring tasks
        </Text>
        <Text style={[styles.body, { color: colors.textMuted }]}>
          Prepare plans for matching new mail.
        </Text>
        {!live ? (
          <Text style={[styles.body, { color: colors.textMuted }]}>
            Sign in to the live workspace to use preparation routines.
          </Text>
        ) : (
          <>
            {visible.error && (
              <Text
                accessibilityRole="alert"
                style={[styles.body, { color: colors.danger }]}
              >
                {visible.error}
              </Text>
            )}
            {visible.loading && (
              <ActivityIndicator
                accessibilityLabel="Loading preparation routines"
                color={colors.accent}
              />
            )}
            {seed && !alreadyProposed && (
              <View
                style={[
                  styles.card,
                  {
                    backgroundColor: colors.surface,
                    borderColor: colors.border,
                  },
                ]}
              >
                <Text style={[styles.title, { color: colors.text }]}>
                  {seed.goal}
                </Text>
                <Text style={[styles.body, { color: colors.textMuted }]}>
                  Review the sender and preparation rules from this completed
                  task.
                </Text>
                <Button
                  title="Create preparation routine"
                  disabled={Boolean(visible.busy) || visible.loading}
                  onPress={() =>
                    void mutate("propose", () =>
                      api.proposeRoutine(seed.case_id, seed.version),
                    )
                  }
                />
              </View>
            )}
            {!visible.loading &&
              visible.routines.length === 0 &&
              !seed &&
              !visible.error && (
                <Text style={[styles.body, { color: colors.textMuted }]}>
                  Create a routine from a completed, verified mail-to-Calendar
                  task.
                </Text>
              )}
            {visible.routines.map((routine) => (
              <View
                key={routine.routine_id}
                style={[
                  styles.card,
                  {
                    backgroundColor: colors.surface,
                    borderColor: colors.border,
                  },
                ]}
              >
                <Text
                  style={[
                    styles.status,
                    {
                      color:
                        routine.effective_status === "ACTIVE"
                          ? colors.success
                          : colors.textMuted,
                    },
                  ]}
                >
                  {labels[routine.effective_status]}
                </Text>
                <Text style={[styles.title, { color: colors.text }]}>
                  {`${routine.opportunity_type === "DEADLINE" ? "Deadline" : "Event"} mail from ${routine.sender_domain}`}
                </Text>
                <Text style={[styles.body, { color: colors.textMuted }]}>
                  {routine.mode === "PREPARE_ONLY"
                    ? "Each Calendar event needs your approval."
                    : routine.description}
                </Text>
                <Text style={[styles.body, { color: colors.text }]}>
                  Sender: {routine.sender_domain}
                  {"\n"}Task:{" "}
                  {routine.opportunity_type === "DEADLINE"
                    ? "Deadline preparation"
                    : "Event preparation"}
                </Text>
                {routine.review_reason && (
                  <Text style={[styles.body, { color: colors.warning }]}>
                    {routine.review_reason}
                  </Text>
                )}
                <Text style={[styles.body, { color: colors.textMuted }]}>
                  Pausing stops new preparation. Existing tasks stay in
                  progress.
                </Text>
                {routine.status === "ACTIVE" ? (
                  <Button
                    title="Pause"
                    disabled={Boolean(visible.busy) || visible.loading}
                    onPress={() =>
                      void mutate(routine.routine_id, () =>
                        api.pauseRoutine(routine.routine_id, routine.version),
                      )
                    }
                  />
                ) : (
                  <Button
                    title={
                      routine.status === "PAUSED"
                        ? "Resume"
                        : "Enable this routine"
                    }
                    disabled={
                      Boolean(visible.busy) ||
                      visible.loading ||
                      routine.effective_status === "REVIEW_REQUIRED"
                    }
                    onPress={() =>
                      void mutate(routine.routine_id, () =>
                        api.activateRoutine(
                          routine.routine_id,
                          routine.version,
                        ),
                      )
                    }
                  />
                )}
              </View>
            ))}
            <Button
              title="Refresh list"
              disabled={visible.loading || Boolean(visible.busy)}
              onPress={() => void load()}
            />
          </>
        )}
      </ScrollView>
    </View>
  );
}

function message(error: unknown) {
  return error instanceof Error
    ? error.message
    : "Could not load preparation routines. Try again.";
}
function Button({
  title,
  disabled,
  onPress,
}: {
  title: string;
  disabled: boolean;
  onPress(): void;
}) {
  const { colors } = useAppTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityState={{ disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.button,
        {
          backgroundColor: colors.accent,
          opacity: disabled || pressed ? 0.5 : 1,
        },
      ]}
    >
      <Text style={[styles.buttonText, { color: colors.onAccent }]}>
        {title}
      </Text>
    </Pressable>
  );
}
const styles = StyleSheet.create({
  screen: { flex: 1 },
  content: { padding: 20, gap: 16, paddingBottom: 48 },
  intro: { fontSize: 22, fontWeight: "700" },
  body: { fontSize: 15, lineHeight: 23 },
  title: { fontSize: 18, fontWeight: "700", lineHeight: 26 },
  status: { fontSize: 13, fontWeight: "600" },
  card: { borderWidth: 1, borderRadius: 16, padding: 18, gap: 12 },
  button: {
    minHeight: 48,
    borderRadius: 10,
    padding: 13,
    justifyContent: "center",
    alignItems: "center",
  },
  buttonText: { fontSize: 15, fontWeight: "600" },
});
