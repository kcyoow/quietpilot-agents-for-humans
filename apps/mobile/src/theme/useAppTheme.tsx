import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  createContext,
  type PropsWithChildren,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useColorScheme } from "react-native";

import {
  themeColors,
  type AppColorScheme,
  type AppColors,
  type AppPalette,
  type AppearanceMode,
} from "@/src/theme/tokens";

type Preferences = { palette: AppPalette; mode: AppearanceMode };
type AppTheme = Preferences & {
  colors: AppColors;
  scheme: AppColorScheme;
  persistenceError: string | null;
  setPalette(palette: AppPalette): void;
  setMode(mode: AppearanceMode): void;
};

export const THEME_STORAGE_KEY = "quietpilot.appearance.v1";
const defaults: Preferences = { palette: "blue", mode: "system" };
const AppThemeContext = createContext<AppTheme | null>(null);

export function AppThemeProvider({ children }: PropsWithChildren) {
  const systemScheme = useColorScheme() === "dark" ? "dark" : "light";
  const [preferences, setPreferences] = useState<Preferences>(defaults);
  const [persistenceError, setPersistenceError] = useState<string | null>(null);
  const current = useRef(preferences);
  const revision = useRef(0);
  const active = useRef(false);
  const writes = useRef(Promise.resolve());

  useEffect(() => {
    active.current = true;
    void AsyncStorage.getItem(THEME_STORAGE_KEY)
      .then((stored) => {
        if (!active.current || revision.current !== 0) return;
        const next = readPreferences(stored);
        current.current = next;
        setPreferences(next);
      })
      .catch(() => {
        if (active.current && revision.current === 0) {
          setPersistenceError(
            "Could not load appearance settings. Select them again.",
          );
        }
      });
    return () => {
      active.current = false;
    };
  }, []);

  const update = useCallback((change: Partial<Preferences>) => {
    const next = { ...current.current, ...change };
    const savedRevision = ++revision.current;
    current.current = next;
    setPreferences(next);
    setPersistenceError(null);
    // Serialize quick palette/brightness changes so the last choice stays saved.
    writes.current = writes.current
      .catch(() => undefined)
      .then(() => AsyncStorage.setItem(THEME_STORAGE_KEY, JSON.stringify(next)))
      .catch(() => {
        if (active.current && revision.current === savedRevision) {
          setPersistenceError(
            "Applied to this screen, but not saved. Select again to retry.",
          );
        }
      });
  }, []);

  const scheme =
    preferences.mode === "system" ? systemScheme : preferences.mode;
  const value = useMemo<AppTheme>(
    () => ({
      ...preferences,
      scheme,
      colors: themeColors[preferences.palette][scheme],
      persistenceError,
      setPalette: (palette) => update({ palette }),
      setMode: (mode) => update({ mode }),
    }),
    [persistenceError, preferences, scheme, update],
  );

  return (
    <AppThemeContext.Provider value={value}>
      {children}
    </AppThemeContext.Provider>
  );
}

export function useAppTheme(): AppTheme {
  const context = useContext(AppThemeContext);
  const systemScheme = useColorScheme() === "dark" ? "dark" : "light";
  return (
    context ?? {
      ...defaults,
      colors: themeColors.blue[systemScheme],
      scheme: systemScheme,
      persistenceError: null,
      setPalette: () => undefined,
      setMode: () => undefined,
    }
  );
}

function readPreferences(stored: string | null): Preferences {
  try {
    const value: unknown = stored ? JSON.parse(stored) : null;
    if (!value || typeof value !== "object") return defaults;
    const { palette, mode } = value as Partial<Preferences>;
    return {
      palette: palette === "coral" ? "coral" : "blue",
      mode: mode === "light" || mode === "dark" ? mode : "system",
    };
  } catch {
    return defaults;
  }
}
