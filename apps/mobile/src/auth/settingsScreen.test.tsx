import { fireEvent, render, waitFor } from "@testing-library/react-native";

import SettingsScreen from "@/app/settings";
import { useAuth } from "@/src/auth/AuthProvider";

jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));
jest.mock("@/src/workspace/WorkspaceProvider", () => ({
  useWorkspace: () => ({ source: "LIVE" }),
}));
jest.mock("@/src/theme/useAppTheme", () => ({
  useAppTheme: () => ({
    colors: {
      accent: "#4466ee",
      accentSoft: "#eef1ff",
      background: "#ffffff",
      border: "#dddddd",
      danger: "#cc3344",
      dangerSoft: "#fff0f2",
      onAccent: "#ffffff",
      success: "#228855",
      successSoft: "#eaf8ef",
      surface: "#ffffff",
      surfaceMuted: "#f4f4f4",
      surfaceRaised: "#ffffff",
      text: "#111111",
      textMuted: "#666666",
      textSubtle: "#888888",
    },
  }),
}));
jest.mock("expo-router", () => ({
  __esModule: true,
  Redirect: () => null,
  router: {
    back: jest.fn(),
    push: jest.fn(),
    replace: jest.fn(),
  },
}));

const mockedUseAuth = jest.mocked(useAuth);
const mockedRouter = jest.requireMock("expo-router").router as {
  back: jest.Mock;
  push: jest.Mock;
  replace: jest.Mock;
};

describe("settings screen", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("shows the Cognito identity and routes secondary account controls", async () => {
    mockedUseAuth.mockReturnValue({
      adapter: { label: "Amazon Cognito", mode: "cognito" } as never,
      confirmPasswordReset: jest.fn(),
      confirmSignUp: jest.fn(),
      requestPasswordReset: jest.fn(),
      resendSignUpCode: jest.fn(),
      signIn: jest.fn(),
      signOut: jest.fn(),
      signUp: jest.fn(),
      startupError: null,
      status: "ready",
      updatePassword: jest.fn(),
      user: {
        displayName: "철연",
        email: "pilot@example.com",
        mode: "cognito",
        userId: "cognito-sub",
      },
    });

    const screen = await render(<SettingsScreen />);
    expect(screen.getByText("철연")).toBeTruthy();
    expect(screen.getByText("pilot@example.com")).toBeTruthy();
    expect(
      screen.getByText("External actions require individual approval."),
    ).toBeTruthy();

    await fireEvent.press(screen.getByText("Connections"));
    await fireEvent.press(screen.getByText("Permissions and automation"));
    await fireEvent.press(screen.getByText("Change password"));

    expect(mockedRouter.push).toHaveBeenNthCalledWith(1, "/connections");
    expect(mockedRouter.push).toHaveBeenNthCalledWith(2, "/policies");
    expect(mockedRouter.push).toHaveBeenNthCalledWith(3, "/password-reset");
  });

  it("confirms real sign-out before returning to login", async () => {
    const signOut = jest.fn().mockResolvedValue(undefined);
    mockedUseAuth.mockReturnValue({
      adapter: { label: "Amazon Cognito", mode: "cognito" } as never,
      confirmPasswordReset: jest.fn(),
      confirmSignUp: jest.fn(),
      requestPasswordReset: jest.fn(),
      resendSignUpCode: jest.fn(),
      signIn: jest.fn(),
      signOut,
      signUp: jest.fn(),
      startupError: null,
      status: "ready",
      updatePassword: jest.fn(),
      user: {
        displayName: "철연",
        email: "pilot@example.com",
        mode: "cognito",
        userId: "cognito-sub",
      },
    });

    const screen = await render(<SettingsScreen />);
    await fireEvent.press(screen.getAllByText("Sign out")[0]);
    expect(screen.getByText("Sign out?")).toBeTruthy();
    await fireEvent.press(screen.getAllByText("Sign out")[1]);

    await waitFor(() => {
      expect(signOut).toHaveBeenCalledTimes(1);
      expect(mockedRouter.replace).toHaveBeenCalledWith("/");
    });
  });
});
