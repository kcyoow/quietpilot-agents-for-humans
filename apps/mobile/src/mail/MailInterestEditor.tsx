import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  Text,
  TextInput,
  View,
} from "react-native";

import { editorStyles as styles } from "@/src/mail/editorStyles";
import { useAppTheme } from "@/src/theme/useAppTheme";

export type MailInterestEditorProps = {
  profile: { tags: string[]; description: string; version: number };
  recommendations: {
    status: "NOT_STARTED" | "PENDING" | "READY" | "ERROR";
    tags: { tag: string; evidence_refs: string[] }[];
    title_count: number;
    error_code: string | null;
  };
  saving: boolean;
  error: string | null;
  onSave(input: {
    tags: string[];
    description: string;
    expected_version: number;
  }): Promise<void>;
  onRecommend(): Promise<void>;
  onReconnect?(): void;
  onCancel?(): void;
};

const MAX_TAGS = 8;
const MAX_TAG_LENGTH = 32;
const MAX_DESCRIPTION_LENGTH = 1000;

function draftFrom(profile: MailInterestEditorProps["profile"]) {
  return {
    tags: [...profile.tags],
    description: profile.description,
    version: profile.version,
    wasConfigured:
      profile.tags.length > 0 || Boolean(profile.description.trim()),
  };
}

function tagKey(tag: string) {
  return tag.normalize("NFKC").toLocaleLowerCase("ko");
}

function combineTags(current: string[], added: string[]) {
  const tags = [...current];
  for (const value of added) {
    const tag = value.normalize("NFKC");
    if (!tag || /[\s#]/u.test(tag)) {
      throw new Error("Separate tags with spaces, such as #school #careers.");
    }
    if (Array.from(tag).length > MAX_TAG_LENGTH) {
      throw new Error("Each tag can have up to 32 characters.");
    }
    if (!tags.some((saved) => tagKey(saved) === tagKey(tag))) tags.push(tag);
  }
  if (tags.length > MAX_TAGS) {
    throw new Error("Choose up to 8 tags. Remove one to add another.");
  }
  return tags;
}

function manualTags(value: string) {
  if (!value.trim()) return [];
  return value
    .trim()
    .split(/\s+/u)
    .map((token) => (token.startsWith("#") ? token.slice(1) : token));
}

export function MailInterestEditor({
  profile,
  recommendations,
  saving,
  error,
  onSave,
  onRecommend,
  onReconnect,
  onCancel,
}: MailInterestEditorProps) {
  const { colors } = useAppTheme();
  const [draft, setDraft] = useState(() => draftFrom(profile));
  const [tagInput, setTagInput] = useState("");
  const [validationMessage, setValidationMessage] = useState<string | null>(
    null,
  );
  const [saveError, setSaveError] = useState<string | null>(null);
  const [recommendError, setRecommendError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [requesting, setRequesting] = useState(false);
  const submitPending = useRef(false);
  const recommendPending = useRef(false);
  const busy = saving || submitting;
  const recommendationPending =
    recommendations.status === "PENDING" || requesting;
  const authorizationRequired =
    recommendations.error_code === "GOOGLE_AUTH_REQUIRED";
  const conflict = profile.version !== draft.version;
  const descriptionLength = Array.from(draft.description).length;
  const descriptionTooLong = descriptionLength > MAX_DESCRIPTION_LENGTH;
  const configured =
    draft.tags.length > 0 ||
    Boolean(draft.description.trim()) ||
    Boolean(tagInput.trim());
  const visibleError =
    saveError ||
    validationMessage ||
    (!recommendError && recommendations.status !== "ERROR" ? error : null);

  function addManualTags() {
    if (busy || !tagInput.trim()) return;
    try {
      const tags = combineTags(draft.tags, manualTags(tagInput));
      setDraft((current) => ({ ...current, tags }));
      setTagInput("");
      setValidationMessage(null);
    } catch (cause) {
      setValidationMessage((cause as Error).message);
    }
  }

  function toggleRecommendation(tag: string) {
    if (busy) return;
    const selected = draft.tags.some((saved) => tagKey(saved) === tagKey(tag));
    try {
      const tags = selected
        ? draft.tags.filter((saved) => tagKey(saved) !== tagKey(tag))
        : combineTags(draft.tags, [tag]);
      setDraft((current) => ({ ...current, tags }));
      setValidationMessage(null);
    } catch (cause) {
      setValidationMessage((cause as Error).message);
    }
  }

  async function saveDraft() {
    if (busy || submitPending.current || descriptionTooLong) return;
    let tags: string[];
    try {
      tags = combineTags(draft.tags, manualTags(tagInput));
    } catch (cause) {
      setValidationMessage((cause as Error).message);
      return;
    }
    submitPending.current = true;
    setSubmitting(true);
    setValidationMessage(null);
    setSaveError(null);
    try {
      await onSave({
        tags,
        description: draft.description,
        expected_version: draft.version,
      });
    } catch {
      setSaveError("Could not save interests. Your edits are kept.");
    } finally {
      submitPending.current = false;
      setSubmitting(false);
    }
  }

  async function recommend() {
    if (busy || requesting || recommendPending.current) return;
    recommendPending.current = true;
    setRequesting(true);
    setRecommendError(null);
    try {
      await onRecommend();
    } catch {
      setRecommendError("Could not load suggested tags. Try again.");
    } finally {
      recommendPending.current = false;
      setRequesting(false);
    }
  }

  return (
    <View style={styles.editor}>
      <Text style={[styles.body, { color: colors.textMuted }]}>
        Choose topics or describe the updates you want.
      </Text>

      {conflict && (
        <View
          style={[
            styles.notice,
            {
              backgroundColor: colors.warningSoft,
              borderColor: colors.warningBorder,
            },
          ]}
        >
          <Text
            accessibilityRole="alert"
            style={[styles.body, { color: colors.text }]}
          >
            New settings are available. Your edits are kept.
          </Text>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: busy }}
            disabled={busy}
            onPress={() => {
              setDraft(draftFrom(profile));
              setTagInput("");
              setValidationMessage(null);
              setSaveError(null);
            }}
            style={styles.textButton}
          >
            <Text style={[styles.buttonText, { color: colors.accent }]}>
              Load latest settings
            </Text>
          </Pressable>
          <Text style={[styles.caption, { color: colors.textMuted }]}>
            This will replace your edits with the latest settings.
          </Text>
        </View>
      )}

      <View
        style={[
          styles.recommendationSection,
          { backgroundColor: colors.accentSoft },
        ]}
      >
        <View style={styles.sectionHeading}>
          <View style={styles.headingLabel}>
            <Text
              accessibilityRole="header"
              style={[styles.sectionTitle, { color: colors.text }]}
            >
              Suggested interests
            </Text>
          </View>
          <Pressable
            accessibilityRole="button"
            accessibilityState={{
              disabled: busy || requesting,
              busy: requesting,
            }}
            disabled={busy || requesting}
            onPress={() =>
              authorizationRequired && onReconnect
                ? onReconnect()
                : void recommend()
            }
            style={({ pressed }) => [
              styles.recommendButton,
              { opacity: busy || requesting || pressed ? 0.5 : 1 },
            ]}
          >
            <Text style={[styles.buttonText, { color: colors.accent }]}>
              {authorizationRequired && onReconnect
                ? "Reconnect Google"
                : requesting
                  ? "Finding suggestions"
                  : recommendations.status === "PENDING"
                    ? "Retry request"
                    : recommendations.status === "ERROR" || recommendError
                      ? "Retry suggestions"
                      : recommendations.status === "READY"
                        ? "Refresh suggestions"
                        : "Suggest interests"}
            </Text>
          </Pressable>
        </View>
        {recommendationPending ? (
          <View style={styles.recommendationState}>
            <ActivityIndicator color={colors.accent} size="small" />
            <Text
              accessibilityLiveRegion="polite"
              style={[styles.body, { color: colors.textMuted }]}
            >
              Finding suggestions. You can also enter your own interests.
            </Text>
          </View>
        ) : authorizationRequired ? (
          <Text
            accessibilityRole="alert"
            style={[styles.body, { color: colors.textMuted }]}
          >
            Reconnect Google for suggestions, or enter interests yourself.
          </Text>
        ) : recommendations.status === "ERROR" || recommendError ? (
          <Text
            accessibilityRole="alert"
            style={[styles.body, { color: colors.textMuted }]}
          >
            Suggestions unavailable. You can enter interests below.
          </Text>
        ) : recommendations.status === "READY" ? (
          <>
            <Text style={[styles.caption, { color: colors.textMuted }]}>
              {`Based on ${recommendations.title_count} mail titles.`}
            </Text>
            {recommendations.tags.length > 0 ? (
              <View style={styles.chips}>
                {recommendations.tags.map(({ tag }) => {
                  const selected = draft.tags.some(
                    (saved) => tagKey(saved) === tagKey(tag),
                  );
                  return (
                    <Pressable
                      accessibilityLabel={tag + " suggested tag"}
                      accessibilityRole="checkbox"
                      accessibilityState={{ checked: selected, disabled: busy }}
                      disabled={busy}
                      key={tag}
                      onPress={() => toggleRecommendation(tag)}
                      style={({ pressed }) => [
                        styles.recommendationChip,
                        {
                          backgroundColor: selected
                            ? colors.accent
                            : colors.surface,
                          borderColor: selected
                            ? colors.accent
                            : colors.accentBorder,
                          opacity: busy || pressed ? 0.5 : 1,
                        },
                      ]}
                    >
                      <MaterialCommunityIcons
                        name={selected ? "check" : "plus"}
                        size={16}
                        color={selected ? colors.onAccent : colors.accent}
                      />
                      <Text
                        style={[
                          styles.chipText,
                          { color: selected ? colors.onAccent : colors.accent },
                        ]}
                      >
                        {"#" + tag}
                      </Text>
                    </Pressable>
                  );
                })}
              </View>
            ) : (
              <Text style={[styles.body, { color: colors.textMuted }]}>
                No suggestions yet. Add your own interests below.
              </Text>
            )}
          </>
        ) : (
          <Text style={[styles.body, { color: colors.textMuted }]}>
            Suggest topics from recent mail titles.
          </Text>
        )}
        <Text style={[styles.caption, { color: colors.textMuted }]}>
          Uses up to 32 titles from the last 7 days. Only selected topics are
          added.
        </Text>
      </View>

      <View style={styles.section}>
        <View style={styles.sectionHeading}>
          <Text
            accessibilityRole="header"
            style={[styles.sectionTitle, { color: colors.text }]}
          >
            Your tags
          </Text>
          <Text style={[styles.caption, { color: colors.textMuted }]}>
            {draft.tags.length + "/8 tags"}
          </Text>
        </View>
        {draft.tags.length > 0 && (
          <View style={styles.chips}>
            {draft.tags.map((tag) => (
              <Pressable
                accessibilityLabel={"Remove interest tag " + tag}
                accessibilityRole="button"
                accessibilityState={{ disabled: busy }}
                disabled={busy}
                key={tag}
                onPress={() => {
                  setDraft((current) => ({
                    ...current,
                    tags: current.tags.filter((saved) => saved !== tag),
                  }));
                  setValidationMessage(null);
                }}
                style={({ pressed }) => [
                  styles.selectedChip,
                  {
                    backgroundColor: colors.surfaceMuted,
                    opacity: busy || pressed ? 0.5 : 1,
                  },
                ]}
              >
                <Text style={[styles.chipText, { color: colors.text }]}>
                  {"#" + tag}
                </Text>
                <MaterialCommunityIcons
                  name="close"
                  size={16}
                  color={colors.textMuted}
                />
              </Pressable>
            ))}
          </View>
        )}
        <View style={styles.tagInputRow}>
          <TextInput
            accessibilityLabel="Enter interest tags"
            autoCapitalize="none"
            autoCorrect={false}
            editable={!busy}
            onChangeText={(value) => {
              setTagInput(value);
              setValidationMessage(null);
            }}
            onSubmitEditing={addManualTags}
            placeholder="Add topics, such as school or careers"
            placeholderTextColor={colors.textSubtle}
            returnKeyType="done"
            selectionColor={colors.accent}
            style={[
              styles.tagInput,
              {
                color: colors.text,
                borderColor: colors.border,
                backgroundColor: colors.surface,
              },
            ]}
            value={tagInput}
          />
          <Pressable
            accessibilityLabel="Add tags"
            accessibilityRole="button"
            accessibilityState={{ disabled: busy || !tagInput.trim() }}
            disabled={busy || !tagInput.trim()}
            onPress={addManualTags}
            style={({ pressed }) => [
              styles.addButton,
              {
                backgroundColor: colors.accentSoft,
                opacity: busy || !tagInput.trim() || pressed ? 0.5 : 1,
              },
            ]}
          >
            <MaterialCommunityIcons
              name="plus"
              size={23}
              color={colors.accent}
            />
          </Pressable>
        </View>
      </View>

      <View style={styles.section}>
        <View style={styles.sectionHeading}>
          <Text
            accessibilityRole="header"
            style={[styles.sectionTitle, { color: colors.text }]}
          >
            Describe your interests
          </Text>
          <Text
            style={[
              styles.characterCount,
              { color: descriptionTooLong ? colors.danger : colors.textMuted },
            ]}
          >
            {descriptionLength + "/1,000 characters"}
          </Text>
        </View>
        <TextInput
          accessibilityLabel="Describe mail interests"
          editable={!busy}
          multiline
          onChangeText={(description) =>
            setDraft((current) => ({ ...current, description }))
          }
          placeholder="Keep school notices and scholarship news. Exclude ads."
          placeholderTextColor={colors.textSubtle}
          selectionColor={colors.accent}
          style={[
            styles.descriptionInput,
            {
              color: colors.text,
              borderColor: descriptionTooLong ? colors.danger : colors.border,
              backgroundColor: colors.surface,
            },
          ]}
          textAlignVertical="top"
          value={draft.description}
        />
        {descriptionTooLong && (
          <Text
            accessibilityRole="alert"
            style={[styles.body, { color: colors.danger }]}
          >
            Use up to 1,000 characters.
          </Text>
        )}
      </View>

      {!configured && draft.wasConfigured && (
        <Text style={[styles.body, { color: colors.textMuted }]}>
          Clearing your interests stops mail filtering.
        </Text>
      )}
      {visibleError && (
        <Text
          accessibilityRole="alert"
          style={[styles.body, { color: colors.danger }]}
        >
          {visibleError}
        </Text>
      )}
      <View style={styles.actions}>
        {onCancel && (
          <Pressable
            accessibilityRole="button"
            accessibilityState={{ disabled: busy }}
            disabled={busy}
            onPress={onCancel}
            style={styles.cancelButton}
          >
            <Text style={[styles.buttonText, { color: colors.textMuted }]}>
              Cancel
            </Text>
          </Pressable>
        )}
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ disabled: busy || descriptionTooLong, busy }}
          disabled={busy || descriptionTooLong}
          onPress={() => void saveDraft()}
          style={({ pressed }) => [
            styles.primaryButton,
            {
              backgroundColor: colors.accent,
              opacity: busy || descriptionTooLong || pressed ? 0.5 : 1,
            },
          ]}
        >
          {busy && <ActivityIndicator color={colors.onAccent} size="small" />}
          <Text style={[styles.buttonText, { color: colors.onAccent }]}>
            {busy
              ? "Saving"
              : configured && !draft.wasConfigured
                ? "Start with these interests"
                : "Save"}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}
