import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react-native";
import { Appearance } from "react-native";

import { AppearanceSettings } from "./AppearanceSettings";
import { themeColors } from "./tokens";
import {
  AppThemeProvider,
  THEME_STORAGE_KEY,
  useAppTheme,
} from "./useAppTheme";

// The RN test preset fixes this hook to light; exercise its real subscription.
jest.unmock("react-native/Libraries/Utilities/useColorScheme");

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
}));

const storage = jest.mocked(AsyncStorage);
const setNativeScheme = jest
  .spyOn(Appearance, "setColorScheme")
  .mockImplementation(() => undefined);
const deviceScheme = jest.spyOn(Appearance, "getColorScheme");
const deviceListeners = new Set<
  Parameters<typeof Appearance.addChangeListener>[0]
>();
jest.spyOn(Appearance, "addChangeListener").mockImplementation((listener) => {
  deviceListeners.add(listener);
  return {
    remove: () => {
      deviceListeners.delete(listener);
    },
  };
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  storage.getItem.mockReset().mockResolvedValue(null);
  storage.setItem.mockReset().mockResolvedValue(undefined);
  setNativeScheme.mockClear();
  deviceScheme.mockReturnValue("light");
});

test("restores the chosen palette and brightness without rewriting storage", async () => {
  storage.getItem.mockResolvedValue(
    JSON.stringify({ palette: "coral", mode: "dark" }),
  );
  const hook = await renderHook(useAppTheme, { wrapper: AppThemeProvider });
  await waitFor(() => expect(hook.result.current.palette).toBe("coral"));
  expect(hook.result.current.scheme).toBe("dark");
  expect(hook.result.current.colors).toBe(themeColors.coral.dark);
  expect(setNativeScheme).not.toHaveBeenCalled();
  expect(storage.setItem).not.toHaveBeenCalled();
});

test("keeps a user's selection when an older saved preference arrives late", async () => {
  const read = deferred<string | null>();
  storage.getItem.mockReturnValue(read.promise);
  const hook = await renderHook(useAppTheme, { wrapper: AppThemeProvider });
  await act(() => {
    hook.result.current.setPalette("coral");
    hook.result.current.setMode("dark");
  });
  await act(() =>
    read.resolve(JSON.stringify({ palette: "blue", mode: "light" })),
  );
  expect(hook.result.current.palette).toBe("coral");
  expect(hook.result.current.mode).toBe("dark");
  await waitFor(() =>
    expect(storage.setItem).toHaveBeenLastCalledWith(
      THEME_STORAGE_KEY,
      JSON.stringify({ palette: "coral", mode: "dark" }),
    ),
  );
});

test("serializes rapid changes so the last choice is the one restored later", async () => {
  const firstWrite = deferred<void>();
  const values = new Map<string, string>();
  storage.getItem.mockImplementation(async (key) => values.get(key) ?? null);
  storage.setItem
    .mockImplementationOnce(async (key, value) => {
      await firstWrite.promise;
      values.set(key, value);
    })
    .mockImplementation(async (key, value) => {
      values.set(key, value);
    });
  const hook = await renderHook(useAppTheme, { wrapper: AppThemeProvider });
  await act(() => hook.result.current.setPalette("coral"));
  await act(() => hook.result.current.setMode("dark"));
  expect(storage.setItem).toHaveBeenCalledTimes(1);
  await act(() => firstWrite.resolve());
  await waitFor(() =>
    expect(values.get(THEME_STORAGE_KEY)).toBe(
      JSON.stringify({ palette: "coral", mode: "dark" }),
    ),
  );
  await hook.unmount();
  const restored = await renderHook(useAppTheme, { wrapper: AppThemeProvider });
  await waitFor(() =>
    expect(restored.result.current.colors).toBe(themeColors.coral.dark),
  );
});

test.each([
  "not-json",
  JSON.stringify({ palette: "unknown", mode: "unknown" }),
])(
  "uses usable defaults for invalid stored preferences: %s",
  async (stored) => {
    storage.getItem.mockResolvedValue(stored);
    const hook = await renderHook(useAppTheme, { wrapper: AppThemeProvider });
    await waitFor(() =>
      expect(storage.getItem).toHaveBeenCalledWith(THEME_STORAGE_KEY),
    );
    expect(hook.result.current.palette).toBe("blue");
    expect(hook.result.current.mode).toBe("system");
  },
);

test("reports a save failure without reverting the visible theme and allows retry", async () => {
  storage.setItem.mockRejectedValueOnce(new Error("storage unavailable"));
  const hook = await renderHook(useAppTheme, { wrapper: AppThemeProvider });
  await act(() => hook.result.current.setMode("dark"));
  await waitFor(() =>
    expect(hook.result.current.persistenceError).toContain("not saved"),
  );
  expect(hook.result.current.colors).toBe(themeColors.blue.dark);
  await act(() => hook.result.current.setMode("dark"));
  await waitFor(() => expect(storage.setItem).toHaveBeenCalledTimes(2));
  expect(hook.result.current.persistenceError).toBeNull();
});

test("offers accessible palette and brightness choices and can return to the device setting", async () => {
  await render(
    <AppThemeProvider>
      <AppearanceSettings />
    </AppThemeProvider>,
  );
  await fireEvent.press(screen.getByRole("radio", { name: "Coral theme" }));
  await fireEvent.press(screen.getByRole("radio", { name: "Dark mode" }));
  expect(screen.getByRole("radio", { name: "Coral theme" })).toBeChecked();
  expect(screen.getByRole("radio", { name: "Dark mode" })).toBeChecked();
  await fireEvent.press(
    screen.getByRole("radio", { name: "Follow device settings" }),
  );
  expect(
    screen.getByRole("radio", { name: "Follow device settings" }),
  ).toBeChecked();
  expect(setNativeScheme).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(storage.setItem).toHaveBeenLastCalledWith(
      THEME_STORAGE_KEY,
      JSON.stringify({ palette: "coral", mode: "system" }),
    ),
  );
});

test("returns to the real device theme after a manual choice and follows later changes", async () => {
  const hook = await renderHook(useAppTheme, { wrapper: AppThemeProvider });
  await act(() => hook.result.current.setMode("dark"));
  expect(hook.result.current.scheme).toBe("dark");
  await act(() => hook.result.current.setMode("system"));
  expect(hook.result.current.scheme).toBe("light");
  await act(() => {
    deviceScheme.mockReturnValue("dark");
    for (const listener of deviceListeners) listener({ colorScheme: "dark" });
  });
  expect(hook.result.current.scheme).toBe("dark");
  expect(setNativeScheme).not.toHaveBeenCalled();
});
