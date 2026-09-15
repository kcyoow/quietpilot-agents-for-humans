import { fireEvent, render, waitFor } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

import LoginScreen from "@/app/index";
import { useAuth } from "@/src/auth/AuthProvider";

jest.mock("@/src/auth/AuthProvider", () => ({ useAuth: jest.fn() }));
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
  router: { push: jest.fn() },
}));

const mockedUseAuth = jest.mocked(useAuth);
const mockedRouter = jest.requireMock("expo-router").router as {
  push: jest.Mock;
};

test.each(["booting", "loading"] as const)(
  "does not submit sign-in from the keyboard while auth is %s",
  async (status) => {
    const signIn = jest.fn().mockResolvedValue({ status: "ready-to-sign-in" });
    mockedUseAuth.mockReturnValue({
      status,
      user: null,
      startupError: null,
      signIn,
    } as never);
    const screen = await render(<LoginScreen />);
    await fireEvent.changeText(
      screen.getByLabelText("Email"),
      "pilot@example.com",
    );
    await fireEvent.changeText(screen.getByLabelText("Password"), "Strong1_");
    await fireEvent(screen.getByLabelText("Password"), "submitEditing");
    expect(signIn).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  },
);

describe("Cognito login screen", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("moves sign-up through email confirmation and back to sign-in", async () => {
    const signUp = jest.fn().mockResolvedValue({
      status: "confirmation-required",
      email: "pilot@example.com",
      deliveryDestination: "p***@example.com",
    });
    const confirmSignUp = jest.fn().mockResolvedValue(undefined);
    const resendSignUpCode = jest.fn().mockResolvedValue("p***@example.com");
    mockedUseAuth.mockReturnValue({
      adapter: { label: "Amazon Cognito", mode: "cognito" } as never,
      confirmPasswordReset: jest.fn(),
      confirmSignUp,
      requestPasswordReset: jest.fn(),
      resendSignUpCode,
      signIn: jest.fn(),
      signOut: jest.fn(),
      signUp,
      startupError: null,
      status: "ready",
      user: null,
      updatePassword: jest.fn(),
    });

    const screen = await render(<LoginScreen />);
    for (const label of ["Sign in", "Sign up"]) {
      expect(
        StyleSheet.flatten(screen.getByRole("tab", { name: label }).props.style)
          .minHeight,
      ).toBeGreaterThanOrEqual(48);
    }
    await fireEvent.press(screen.getByText("Sign up"));
    await fireEvent.changeText(screen.getByLabelText("Display name"), "철연");
    await fireEvent.changeText(
      screen.getByLabelText("Email"),
      "pilot@example.com",
    );
    await fireEvent.changeText(screen.getByLabelText("Password"), "Strong1_");
    await fireEvent.changeText(
      screen.getByLabelText("Confirm password"),
      "Strong1_",
    );
    await fireEvent.press(screen.getByText("Create account"));

    await waitFor(() => {
      expect(screen.getByText("Check your email")).toBeTruthy();
    });
    expect(signUp).toHaveBeenCalledWith({
      displayName: "철연",
      email: "pilot@example.com",
      password: "Strong1_",
    });

    expect(
      screen.getByText(
        "Enter the six-digit verification code sent to p***@example.com.",
      ),
    ).toBeTruthy();
    for (const label of ["Resend code", "Back to sign in"]) {
      expect(
        StyleSheet.flatten(
          screen.getByRole("button", { name: label }).props.style,
        ).minHeight,
      ).toBeGreaterThanOrEqual(48);
    }
    await fireEvent.press(screen.getByRole("button", { name: "Resend code" }));
    await waitFor(() => {
      expect(resendSignUpCode).toHaveBeenCalledWith("pilot@example.com");
      expect(screen.getByText("A new code has been sent.")).toBeTruthy();
    });

    await fireEvent.changeText(
      screen.getByLabelText("Verification code"),
      "12a3456",
    );
    expect(screen.getByDisplayValue("123456")).toBeTruthy();
    await fireEvent.press(screen.getByText("Verify email"));

    await waitFor(() => {
      expect(confirmSignUp).toHaveBeenCalledWith({
        email: "pilot@example.com",
        confirmationCode: "123456",
      });
      expect(
        screen.getByText("Email verified. You can now sign in."),
      ).toBeTruthy();
    });
    expect(screen.getAllByText("Sign in").length).toBeGreaterThan(0);
    expect(screen.getByLabelText("Password").props.value).toBe("");
  });

  it("opens password recovery with the entered email", async () => {
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
      user: null,
    });

    const screen = await render(<LoginScreen />);
    await fireEvent.changeText(
      screen.getByLabelText("Email"),
      "pilot@example.com",
    );
    const recovery = screen.getByRole("button", {
      name: "Forgot password?",
    });
    expect(
      StyleSheet.flatten(recovery.props.style).minHeight,
    ).toBeGreaterThanOrEqual(48);
    await fireEvent.press(recovery);

    expect(mockedRouter.push).toHaveBeenCalledWith({
      pathname: "/password-reset",
      params: { email: "pilot@example.com" },
    });
  });
});
