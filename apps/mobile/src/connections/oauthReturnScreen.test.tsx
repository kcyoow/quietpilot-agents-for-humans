import { act, render } from "@testing-library/react-native";
import { router, useLocalSearchParams } from "expo-router";
import OAuthReturnScreen from "@/app/oauth-return";
import { useAuth } from "@/src/auth/AuthProvider";
import { completeGoogleOAuthOnce } from "@/src/connections/googleApi";

jest.mock("expo-router", () => ({
  router: { replace: jest.fn() },
  useLocalSearchParams: jest.fn(),
}));
jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("@/src/connections/googleApi", () => ({
  completeGoogleOAuthOnce: jest.fn(),
  createGoogleConnectionsApi: () => ({ complete: jest.fn() }),
}));
jest.mock("@/src/theme/useAppTheme", () => {
  const { appColors } = jest.requireActual("@/src/theme/tokens");
  return { useAppTheme: () => ({ colors: appColors.light }) };
});
const mockedParams = jest.mocked(useLocalSearchParams);
const mockedComplete = jest.mocked(completeGoogleOAuthOnce);
const mockedReplace = jest.mocked(router.replace);
const mockedAuth = jest.mocked(useAuth);
const firstCode = "a".repeat(43);
const nextCode = "b".repeat(43);

function auth(userId: string | null, status = "ready") {
  mockedAuth.mockReturnValue({
    status,
    user: userId
      ? {
          userId,
          mode: "cognito",
          email: "audit@example.invalid",
          displayName: "사용자",
        }
      : null,
  } as never);
}
function deferred() {
  let resolve!: (value: never) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<never>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve: () => resolve({} as never), reject };
}
beforeEach(() => {
  jest.clearAllMocks();
  mockedComplete.mockReset();
  mockedParams.mockReset();
  auth("user-a");
  mockedParams.mockReturnValue({ provider: "google", code: firstCode });
  mockedComplete.mockResolvedValue({} as never);
});

test("completes once for the authenticated owner and navigates only once on a duplicate render", async () => {
  const screen = await render(<OAuthReturnScreen />);
  expect(mockedComplete).toHaveBeenCalledWith(
    expect.any(Object),
    firstCode,
    "user-a",
  );
  expect(mockedReplace).toHaveBeenCalledTimes(1);
  await screen.rerender(<OAuthReturnScreen />);
  expect(mockedComplete).toHaveBeenCalledTimes(1);
  expect(mockedReplace).toHaveBeenCalledTimes(1);
  expect(mockedReplace).toHaveBeenCalledWith({
    pathname: "/(tabs)",
    params: { mailSetup: "1" },
  });
});

test("rejects a malformed return without completing it", async () => {
  mockedParams.mockReturnValue({ provider: "google", code: "short" });
  const screen = await render(<OAuthReturnScreen />);
  expect(screen.getByText("Connection incomplete")).toBeTruthy();
  expect(mockedComplete).not.toHaveBeenCalled();
});

test("waits for restored authentication before completing the callback", async () => {
  auth(null, "booting");
  const screen = await render(<OAuthReturnScreen />);
  expect(mockedComplete).not.toHaveBeenCalled();
  expect(screen.getByText("Checking your account")).toBeTruthy();
  auth("user-a");
  await screen.rerender(<OAuthReturnScreen />);
  expect(mockedComplete).toHaveBeenCalledWith(
    expect.any(Object),
    firstCode,
    "user-a",
  );
  expect(mockedReplace).toHaveBeenCalledTimes(1);
});

test("does not complete a callback when authentication is ready but signed out", async () => {
  auth(null);
  const screen = await render(<OAuthReturnScreen />);
  expect(screen.getByText("Sign in, then connect again.")).toBeTruthy();
  expect(mockedComplete).not.toHaveBeenCalled();
  expect(mockedReplace).not.toHaveBeenCalled();
});

test.each(["success", "failure"] as const)(
  "ignores the original account's late %s without reusing its code for the next account",
  async (outcome) => {
    const response = deferred();
    mockedComplete.mockReturnValueOnce(response.promise);
    const screen = await render(<OAuthReturnScreen />);
    auth("user-b");
    await screen.rerender(<OAuthReturnScreen />);
    await act(async () => {
      if (outcome === "success") response.resolve();
      else response.reject(new Error("old account error"));
    });
    expect(mockedComplete).toHaveBeenCalledTimes(1);
    expect(mockedReplace).not.toHaveBeenCalled();
    expect(screen.queryByText("old account error")).toBeNull();
    expect(
      screen.getByText(/Your account changed\. Start again from Connections\./),
    ).toBeTruthy();
  },
);

test("binds an already present account while waiting for auth readiness", async () => {
  auth("user-a", "loading");
  const screen = await render(<OAuthReturnScreen />);
  auth("user-b");
  await screen.rerender(<OAuthReturnScreen />);
  expect(mockedComplete).not.toHaveBeenCalled();
  expect(mockedReplace).not.toHaveBeenCalled();
});

test("does not revive the same code after sign-out and re-entry into the same account", async () => {
  const response = deferred();
  mockedComplete.mockReturnValueOnce(response.promise);
  const screen = await render(<OAuthReturnScreen />);
  auth(null);
  await screen.rerender(<OAuthReturnScreen />);
  auth("user-a");
  await screen.rerender(<OAuthReturnScreen />);
  await act(async () => {
    response.resolve();
  });
  expect(mockedComplete).toHaveBeenCalledTimes(1);
  expect(mockedReplace).not.toHaveBeenCalled();
});

test("a new code starts cleanly after the previous code failed", async () => {
  const next = deferred();
  mockedComplete
    .mockRejectedValueOnce(new Error("previous callback error"))
    .mockReturnValueOnce(next.promise);
  const screen = await render(<OAuthReturnScreen />);
  expect(screen.getByText("previous callback error")).toBeTruthy();
  mockedParams.mockReturnValue({ provider: "google", code: nextCode });
  await screen.rerender(<OAuthReturnScreen />);
  expect(screen.queryByText("previous callback error")).toBeNull();
  expect(screen.getByText("Checking Google connection")).toBeTruthy();
  await act(async () => {
    next.resolve();
  });
  expect(mockedComplete).toHaveBeenLastCalledWith(
    expect.any(Object),
    nextCode,
    "user-a",
  );
  expect(mockedReplace).toHaveBeenCalledTimes(1);
});

test("a new code can bind the next account without reusing the old callback", async () => {
  const first = deferred();
  const next = deferred();
  mockedComplete
    .mockReturnValueOnce(first.promise)
    .mockReturnValueOnce(next.promise);
  const screen = await render(<OAuthReturnScreen />);
  auth("user-b");
  mockedParams.mockReturnValue({ provider: "google", code: nextCode });
  await screen.rerender(<OAuthReturnScreen />);
  await act(async () => {
    first.resolve();
  });
  expect(mockedReplace).not.toHaveBeenCalled();
  await act(async () => {
    next.resolve();
  });
  expect(mockedComplete).toHaveBeenLastCalledWith(
    expect.any(Object),
    nextCode,
    "user-b",
  );
  expect(mockedReplace).toHaveBeenCalledTimes(1);
});

test("auth readiness changes do not retry a failed completion automatically", async () => {
  mockedComplete.mockRejectedValueOnce(new Error("callback failed"));
  const screen = await render(<OAuthReturnScreen />);
  auth("user-a", "loading");
  await screen.rerender(<OAuthReturnScreen />);
  auth("user-a");
  await screen.rerender(<OAuthReturnScreen />);
  expect(mockedComplete).toHaveBeenCalledTimes(1);
  expect(mockedReplace).not.toHaveBeenCalled();
});

test("navigation waits for auth readiness while reusing the same pending completion", async () => {
  const response = deferred();
  mockedComplete.mockReturnValueOnce(response.promise);
  const screen = await render(<OAuthReturnScreen />);
  auth("user-a", "loading");
  await screen.rerender(<OAuthReturnScreen />);
  await act(async () => {
    response.resolve();
  });
  expect(mockedReplace).not.toHaveBeenCalled();
  auth("user-a");
  await screen.rerender(<OAuthReturnScreen />);
  expect(mockedComplete).toHaveBeenCalledTimes(1);
  expect(mockedReplace).toHaveBeenCalledTimes(1);
});
