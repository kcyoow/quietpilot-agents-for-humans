import AsyncStorage from "@react-native-async-storage/async-storage";
import { act, renderHook, waitFor } from "@testing-library/react-native";

import { useAuth } from "@/src/auth/AuthProvider";
import { useSuggestionPreferences } from "@/src/workspace/useSuggestionPreferences";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
}));
jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));

const mockedStorage = jest.mocked(AsyncStorage);
const mockedUseAuth = jest.mocked(useAuth);

beforeEach(() => {
  jest.clearAllMocks();
  mockedUseAuth.mockReturnValue({
    user: { userId: "cognito-subject" },
  } as never);
  mockedStorage.setItem.mockResolvedValue(undefined);
});

test("restores and persists the selected filter per authenticated user", async () => {
  mockedStorage.getItem.mockResolvedValue("medium-high");

  const hook = await renderHook(() => useSuggestionPreferences());
  await waitFor(() => expect(hook.result.current.filter).toBe("medium-high"));

  await act(() => hook.result.current.setFilter("google"));

  expect(hook.result.current.filter).toBe("google");
  expect(mockedStorage.getItem).toHaveBeenCalledWith(
    "quietpilot.suggestion-filter.v1:cognito-subject",
  );
  expect(mockedStorage.setItem).toHaveBeenCalledWith(
    "quietpilot.suggestion-filter.v1:cognito-subject",
    "google",
  );
});

test("falls back to all for stale or unreadable saved values", async () => {
  mockedStorage.getItem.mockResolvedValue("not-a-filter");
  const stale = await renderHook(() => useSuggestionPreferences());
  await waitFor(() => expect(stale.result.current.filter).toBe("all"));
  await stale.unmount();

  mockedStorage.getItem.mockRejectedValue(new Error("storage unavailable"));
  const unavailable = await renderHook(() => useSuggestionPreferences());
  await waitFor(() => expect(unavailable.result.current.filter).toBe("all"));
});
