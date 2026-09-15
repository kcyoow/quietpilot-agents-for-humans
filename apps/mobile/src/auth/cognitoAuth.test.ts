import {
  createCognitoAuthAdapter,
  mapCognitoError,
} from "@/src/auth/cognitoAuth";

jest.mock("@aws-amplify/react-native", () => ({}));
jest.mock("aws-amplify", () => ({ Amplify: { configure: jest.fn() } }));
jest.mock("aws-amplify/auth", () => ({
  confirmResetPassword: jest.fn(),
  confirmSignUp: jest.fn(),
  fetchUserAttributes: jest.fn(),
  getCurrentUser: jest.fn(),
  resendSignUpCode: jest.fn(),
  resetPassword: jest.fn(),
  signIn: jest.fn(),
  signOut: jest.fn(),
  signUp: jest.fn(),
  updatePassword: jest.fn(),
}));

type Client = Parameters<typeof createCognitoAuthAdapter>[0];

function serviceError(name: string) {
  return Object.assign(new Error(name), { name });
}

function makeClient(): jest.Mocked<Client> {
  return {
    confirmResetPassword: jest.fn(),
    confirmSignUp: jest.fn(),
    ensureConfigured: jest.fn(),
    fetchUserAttributes: jest.fn(),
    getCurrentUser: jest.fn(),
    resendSignUpCode: jest.fn(),
    resetPassword: jest.fn(),
    signIn: jest.fn(),
    signOut: jest.fn(),
    signUp: jest.fn(),
    updatePassword: jest.fn(),
  };
}

describe("Cognito auth adapter", () => {
  it("creates an email user and requests confirmation without retaining the password", async () => {
    const client = makeClient();
    client.signUp.mockResolvedValue({
      isSignUpComplete: false,
      nextStep: {
        signUpStep: "CONFIRM_SIGN_UP",
        codeDeliveryDetails: { destination: "p***@example.com" },
      },
    });
    const adapter = createCognitoAuthAdapter(client);

    await expect(
      adapter.signUp({
        displayName: "철연",
        email: " Pilot@Example.com ",
        password: "Strong1_",
      }),
    ).resolves.toEqual({
      status: "confirmation-required",
      email: "pilot@example.com",
      deliveryDestination: "p***@example.com",
    });
    expect(client.signUp).toHaveBeenCalledWith({
      username: "pilot@example.com",
      password: "Strong1_",
      options: {
        userAttributes: { email: "pilot@example.com", name: "철연" },
      },
    });
  });

  it("confirms and resends only normalized six-digit email codes", async () => {
    const client = makeClient();
    client.resendSignUpCode.mockResolvedValue({
      destination: "p***@example.com",
    });
    const adapter = createCognitoAuthAdapter(client);

    await expect(
      adapter.confirmSignUp({
        email: "Pilot@Example.com",
        confirmationCode: "12345",
      }),
    ).rejects.toThrow("six-digit");
    await adapter.confirmSignUp({
      email: "Pilot@Example.com",
      confirmationCode: " 123456 ",
    });
    await expect(adapter.resendSignUpCode("Pilot@Example.com")).resolves.toBe(
      "p***@example.com",
    );

    expect(client.confirmSignUp).toHaveBeenCalledWith({
      username: "pilot@example.com",
      confirmationCode: "123456",
    });
    expect(client.resendSignUpCode).toHaveBeenCalledWith({
      username: "pilot@example.com",
    });
  });

  it("uses SRP and returns the verified Cognito identity", async () => {
    const client = makeClient();
    client.signIn.mockResolvedValue({
      isSignedIn: true,
      nextStep: { signInStep: "DONE" },
    });
    client.getCurrentUser.mockResolvedValue({
      userId: "cognito-sub",
      username: "generated-username",
      signInDetails: { loginId: "pilot@example.com" },
    });
    client.fetchUserAttributes.mockResolvedValue({
      email: "pilot@example.com",
      name: "철연",
    });
    const adapter = createCognitoAuthAdapter(client);

    await expect(
      adapter.signIn({ email: "PILOT@example.com", password: "Strong1_" }),
    ).resolves.toEqual({
      status: "signed-in",
      user: {
        displayName: "철연",
        email: "pilot@example.com",
        mode: "cognito",
        userId: "cognito-sub",
      },
    });
    expect(client.signIn).toHaveBeenCalledWith({
      username: "pilot@example.com",
      password: "Strong1_",
      options: { authFlowType: "USER_SRP_AUTH" },
    });
  });

  it("routes an unconfirmed sign-in back to email confirmation", async () => {
    const client = makeClient();
    client.signIn.mockRejectedValue(serviceError("UserNotConfirmedException"));
    const adapter = createCognitoAuthAdapter(client);

    await expect(
      adapter.signIn({ email: "pilot@example.com", password: "Strong1_" }),
    ).resolves.toEqual({
      status: "confirmation-required",
      email: "pilot@example.com",
    });
  });

  it("routes a required password reset to the recovery screen", async () => {
    const client = makeClient();
    client.signIn.mockResolvedValue({
      isSignedIn: false,
      nextStep: { signInStep: "RESET_PASSWORD" },
    });
    const adapter = createCognitoAuthAdapter(client);

    await expect(
      adapter.signIn({ email: "Pilot@example.com", password: "Strong1_" }),
    ).resolves.toEqual({
      status: "password-reset-required",
      email: "pilot@example.com",
    });
  });

  it("requests and confirms password recovery without retaining secrets", async () => {
    const client = makeClient();
    client.resetPassword.mockResolvedValue({
      isPasswordReset: false,
      nextStep: {
        resetPasswordStep: "CONFIRM_RESET_PASSWORD_WITH_CODE",
        codeDeliveryDetails: { destination: "p***@example.com" },
      },
    });
    const adapter = createCognitoAuthAdapter(client);

    await expect(
      adapter.requestPasswordReset(" Pilot@example.com "),
    ).resolves.toEqual({
      status: "confirmation-required",
      email: "pilot@example.com",
      deliveryDestination: "p***@example.com",
    });
    await adapter.confirmPasswordReset({
      confirmationCode: " 123456 ",
      email: "Pilot@example.com",
      newPassword: "Changed1_",
    });

    expect(client.resetPassword).toHaveBeenCalledWith({
      username: "pilot@example.com",
    });
    expect(client.confirmResetPassword).toHaveBeenCalledWith({
      confirmationCode: "123456",
      newPassword: "Changed1_",
      username: "pilot@example.com",
    });
  });

  it("changes a signed-in password and gives a specific old-password error", async () => {
    const client = makeClient();
    const adapter = createCognitoAuthAdapter(client);

    await adapter.updatePassword({
      currentPassword: "Strong1_",
      newPassword: "Changed1_",
    });
    expect(client.updatePassword).toHaveBeenCalledWith({
      oldPassword: "Strong1_",
      newPassword: "Changed1_",
    });

    client.updatePassword.mockRejectedValue(
      serviceError("NotAuthorizedException"),
    );
    await expect(
      adapter.updatePassword({
        currentPassword: "Wrong1_",
        newPassword: "Changed1_",
      }),
    ).rejects.toThrow("Incorrect current password.");
  });

  it("restores only an authenticated Cognito session", async () => {
    const client = makeClient();
    client.getCurrentUser.mockRejectedValue(
      serviceError("UserUnAuthenticatedException"),
    );
    client.fetchUserAttributes.mockResolvedValue({});
    const adapter = createCognitoAuthAdapter(client);

    await expect(adapter.restore()).resolves.toBeNull();
  });

  it("maps account enumeration and confirmation failures to safe Korean messages", () => {
    expect(mapCognitoError(serviceError("UserNotFoundException")).message).toBe(
      "Incorrect email or password.",
    );
    expect(mapCognitoError(serviceError("CodeMismatchException")).message).toBe(
      "Incorrect verification code.",
    );
  });
});
