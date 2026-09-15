import AsyncStorage from "@react-native-async-storage/async-storage";
import { useEffect, useMemo, useState } from "react";

import { useAuth } from "@/src/auth/AuthProvider";

export type SuggestionFilter =
  "all" | "approval" | "google" | "smartthings" | "sms" | "medium-high";

const allowed = new Set<SuggestionFilter>([
  "all",
  "approval",
  "google",
  "medium-high",
  "smartthings",
  "sms",
]);

export function useSuggestionPreferences() {
  const { user } = useAuth();
  const key = useMemo(
    () => `quietpilot.suggestion-filter.v1:${user?.userId ?? "guest"}`,
    [user?.userId],
  );
  const [filter, setFilterState] = useState<SuggestionFilter>("all");

  useEffect(() => {
    let active = true;
    void AsyncStorage.getItem(key)
      .then((value) => {
        if (!active) return;
        setFilterState(
          value && allowed.has(value as SuggestionFilter)
            ? (value as SuggestionFilter)
            : "all",
        );
      })
      .catch(() => {
        if (active) setFilterState("all");
      });
    return () => {
      active = false;
    };
  }, [key]);

  function setFilter(value: SuggestionFilter) {
    setFilterState(value);
    void AsyncStorage.setItem(key, value).catch(() => undefined);
  }

  return { filter, setFilter };
}
