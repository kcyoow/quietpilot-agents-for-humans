import { fireEvent, render, waitFor } from "@testing-library/react-native";
import { StyleSheet } from "react-native";

import PasswordResetScreen from "@/app/password-reset";
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
  router: { back: jest.fn(), replace: jest.fn() },
  useLocalSearchParams: () => ({ email: "pilot@example.com" }),
}));

const mockedUseAuth = jest.mocked(useAuth);
const mockedRouter = jest.requireMock("expo-router").router as {
  replace: jest.Mock;
};

function authValue(overrides: Partial<ReturnType<typeof useAuth>> = {}) {
  return {
    adapter: { label: "Amazon Cognito", mode: "cognito" } as never,
    confirmPasswordReset: jest.fn(),
    confirmSignUp: jest.fn(),
    requestPasswordReset: jest.fn(),
    resendSignUpCode: jest.fn(),
    signIn: jest.fn(),
    signOut: jest.fn(),
    signUp: jest.fn(),
    startupError: null,
    status: "ready" as const,
    updatePassword: jest.fn(),
    user: null,
    ...overrides,
  };
}

test.each(["booting", "loading"] as const)(
  "does not request a reset code from the keyboard while auth is %s",
  async (status) => {
    const requestPasswordReset = jest.fn().mockResolvedValue({
      status: "confirmation-required",
      email: "pilot@example.com",
    });
    mockedUseAuth.mockReturnValue(authValue({ status, requestPasswordReset }));
    const screen = await render(<PasswordResetScreen />);
    await fireEvent(screen.getByLabelText("Email"), "submitEditing");
    expect(requestPasswordReset).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send code" })).toBeDisabled();
  },
);

test.each(["booting", "loading"] as const)(
  "does not change a password from the keyboard while auth is %s",
  async (status) => {
    const updatePassword = jest.fn().mockResolvedValue(undefined);
    mockedUseAuth.mockReturnValue(
      authValue({
        status,
        updatePassword,
        user: {
          displayName: "철연",
          email: "pilot@example.com",
          mode: "cognito",
          userId: "cognito-sub",
        },
      }),
    );
    const screen = await render(<PasswordResetScreen />);
    await fireEvent.changeText(
      screen.getByLabelText("Current password"),
      "Strong1_",
    );
    await fireEvent.changeText(
      screen.getByLabelText("New password"),
      "Changed1_",
    );
    await fireEvent.changeText(
      screen.getByLabelText("Confirm new password"),
      "Changed1_",
    );
    await fireEvent(
      screen.getByLabelText("Confirm new password"),
      "submitEditing",
    );
    expect(updatePassword).not.toHaveBeenCalled();
    expect(
      screen.getByRole("button", { name: "Change password" }),
    ).toBeDisabled();
  },
);

describe("password screen", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("changes the password of a signed-in user", async () => {
    const updatePassword = jest.fn().mockResolvedValue(undefined);
    mockedUseAuth.mockReturnValue(
      authValue({
        updatePassword,
        user: {
          displayName: "철연",
          email: "pilot@example.com",
          mode: "cognito",
          userId: "cognito-sub",
        },
      }),
    );

    const screen = await render(<PasswordResetScreen />);
    await fireEvent.changeText(
      screen.getByLabelText("Current password"),
      "Strong1_",
    );
    await fireEvent.changeText(
      screen.getByLabelText("New password"),
      "Changed1_",
    );
    await fireEvent.changeText(
      screen.getByLabelText("Confirm new password"),
      "Changed1_",
    );
    await fireEvent.press(
      screen.getByRole("button", { name: "Change password" }),
    );

    await waitFor(() => {
      expect(updatePassword).toHaveBeenCalledWith({
        currentPassword: "Strong1_",
        newPassword: "Changed1_",
      });
      expect(screen.getByText("Password changed.")).toBeTruthy();
    });
  });

  it("keeps the confirmation error until both new passwords match", async () => {
    const updatePassword = jest.fn();
    mockedUseAuth.mockReturnValue(
      authValue({
        updatePassword,
        user: {
          displayName: "철연",
          email: "pilot@example.com",
          mode: "cognito",
          userId: "cognito-sub",
        },
      }),
    );

    const screen = await render(<PasswordResetScreen />);
    await fireEvent.changeText(
      screen.getByLabelText("Current password"),
      "Strong1_",
    );
    await fireEvent.changeText(
      screen.getByLabelText("New password"),
      "Changed1_",
    );
    await fireEvent.changeText(
      screen.getByLabelText("Confirm new password"),
      "Different1_",
    );

    expect(screen.getByText("New passwords do not match.")).toBeTruthy();
    await fireEvent.press(
      screen.getByRole("button", { name: "Change password" }),
    );
    expect(updatePassword).not.toHaveBeenCalled();

    await fireEvent.changeText(
      screen.getByLabelText("Confirm new password"),
      "Changed1_",
    );
    expect(screen.queryByText("New passwords do not match.")).toBeNull();
  });

  it("recovers a signed-out account with an email code", async () => {
    const requestPasswordReset = jest.fn().mockResolvedValue({
      deliveryDestination: "p***@example.com",
      email: "pilot@example.com",
      status: "confirmation-required",
    });
    const confirmPasswordReset = jest.fn().mockResolvedValue(undefined);
    mockedUseAuth.mockReturnValue(
      authValue({ confirmPasswordReset, requestPasswordReset }),
    );

    const screen = await render(<PasswordResetScreen />);
    await fireEvent.press(screen.getByText("Send code"));
    await waitFor(() =>
      expect(screen.getByLabelText("Verification code")).toBeTruthy(),
    );

    expect(
      screen.getByText("Enter the six-digit code sent to p***@example.com."),
    ).toBeTruthy();
    expect(
      StyleSheet.flatten(
        screen.getByRole("button", { name: "Resend code" }).props.style,
      ).minHeight,
    ).toBeGreaterThanOrEqual(48);

    await fireEvent.changeText(
      screen.getByLabelText("Verification code"),
      "123456",
    );
    await fireEvent.changeText(
      screen.getByLabelText("New password"),
      "Changed1_",
    );
    await fireEvent.changeText(
      screen.getByLabelText("Confirm new password"),
      "Changed1_",
    );
    await fireEvent.press(screen.getByText("Save new password"));

    await waitFor(() => {
      expect(confirmPasswordReset).toHaveBeenCalledWith({
        confirmationCode: "123456",
        email: "pilot@example.com",
        newPassword: "Changed1_",
      });
      expect(
        screen.getByText("Password changed. Sign in with your new password."),
      ).toBeTruthy();
      expect(
        screen.getByRole("header", { name: "Password reset" }),
      ).toBeTruthy();
      expect(screen.getByText("Sign in with your new password.")).toBeTruthy();
      expect(
        screen.queryByText(/— enter the six-digit code sent to this address\./),
      ).toBeNull();
      expect(screen.queryByLabelText("Verification code")).toBeNull();
      expect(screen.queryByRole("button", { name: "Resend code" })).toBeNull();
    });
    await fireEvent.press(
      screen.getByRole("button", { name: "Back to sign in" }),
    );
    expect(mockedRouter.replace).toHaveBeenCalledWith("/");
  });

  it("shows completion instructions when recovery finishes without a code step", async () => {
    const requestPasswordReset = jest.fn().mockResolvedValue({
      email: "pilot@example.com",
      status: "done",
    });
    mockedUseAuth.mockReturnValue(authValue({ requestPasswordReset }));
    const screen = await render(<PasswordResetScreen />);
    await fireEvent.press(screen.getByRole("button", { name: "Send code" }));

    await waitFor(() => {
      expect(
        screen.getByRole("header", { name: "Password reset" }),
      ).toBeTruthy();
      expect(screen.getByText("Sign in with your new password.")).toBeTruthy();
      expect(
        screen.queryByText(/— enter the six-digit code sent to this address\./),
      ).toBeNull();
      expect(screen.queryByLabelText("Verification code")).toBeNull();
      expect(
        screen.getByRole("button", { name: "Back to sign in" }),
      ).toBeTruthy();
    });
  });
});
