import AsyncStorage from "@react-native-async-storage/async-storage";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export const MAX_MAIL_INTEREST_LENGTH = 120;
export const MAX_MAIL_INTERESTS = 8;

type Owner = { key: string };
type Session = {
  owner: Owner;
  keywords: string[];
  ready: boolean;
  saving: boolean;
};
type InterestState = Session & { error: string | null };

function normalizeKeyword(value: string) {
  return value.normalize("NFKC").trim();
}

function validationError(keyword: string) {
  if (!keyword) return "Enter an interest keyword.";
  if (keyword.length > MAX_MAIL_INTEREST_LENGTH) {
    return `Interest keywords allow up to ${MAX_MAIL_INTEREST_LENGTH} characters.`;
  }
  return null;
}

function readKeywords(value: string | null): string[] {
  if (value === null) return [];
  const parsed: unknown = JSON.parse(value);
  if (
    !Array.isArray(parsed) ||
    parsed.length > MAX_MAIL_INTERESTS ||
    parsed.some(
      (item) =>
        typeof item !== "string" || validationError(normalizeKeyword(item)),
    )
  ) {
    throw new Error("Invalid saved mail interests");
  }
  return [...new Set(parsed.map(normalizeKeyword))];
}

export function useMailInterests(ownerId: string) {
  const owner = useMemo(
    () => ({ key: `quietpilot.mail-interests.v1:${ownerId}` }),
    [ownerId],
  );
  const active = useRef<Session | null>(null);
  const [state, setState] = useState<InterestState>({
    owner,
    keywords: [],
    ready: false,
    saving: false,
    error: null,
  });

  const publish = useCallback(
    (session: Session, error: string | null = null) => {
      if (active.current === session) setState({ ...session, error });
    },
    [],
  );

  useEffect(() => {
    const session: Session = {
      owner,
      keywords: [],
      ready: false,
      saving: false,
    };
    active.current = session;
    publish(session);

    void AsyncStorage.getItem(owner.key)
      .then((value) => {
        if (active.current !== session) return;
        session.keywords = readKeywords(value);
        session.ready = true;
        publish(session);
      })
      .catch(() => {
        // Keep edits disabled so a failed read cannot overwrite saved interests.
        publish(session, "Could not load saved keywords. Reopen this screen.");
      });

    return () => {
      if (active.current === session) active.current = null;
    };
  }, [owner, publish]);

  async function persist(session: Session, keywords: string[]) {
    session.saving = true;
    publish(session);
    try {
      await AsyncStorage.setItem(session.owner.key, JSON.stringify(keywords));
      if (active.current !== session) return;
      session.keywords = keywords;
      session.saving = false;
      publish(session);
    } catch {
      if (active.current !== session) return;
      session.saving = false;
      publish(session, "Could not save keywords. Try again.");
    }
  }

  function editableSession() {
    const session = active.current;
    return session?.owner === owner && session.ready && !session.saving
      ? session
      : null;
  }

  async function save(value: string): Promise<void> {
    const session = editableSession();
    if (!session) return;
    const keyword = normalizeKeyword(value);
    const error = validationError(keyword);
    if (error) {
      publish(session, error);
      return;
    }
    if (session.keywords.includes(keyword)) {
      publish(session);
      return;
    }
    if (session.keywords.length >= MAX_MAIL_INTERESTS) {
      publish(
        session,
        `Interest keywords allow up to ${MAX_MAIL_INTERESTS} keywords maximum. Remove one to add another.`,
      );
      return;
    }
    await persist(session, [...session.keywords, keyword]);
  }

  async function remove(value: string): Promise<void> {
    const session = editableSession();
    if (!session) return;
    const keyword = normalizeKeyword(value);
    const error = validationError(keyword);
    if (error) {
      publish(session, error);
      return;
    }
    if (!session.keywords.includes(keyword)) {
      publish(session);
      return;
    }
    await persist(
      session,
      session.keywords.filter((saved) => saved !== keyword),
    );
  }

  // Mask the previous owner during render, before the next hydration effect.
  const current = state.owner === owner;
  return {
    keywords: current ? state.keywords : [],
    ready: current && state.ready,
    saving: current && state.saving,
    error: current ? state.error : null,
    save,
    remove,
  };
}
