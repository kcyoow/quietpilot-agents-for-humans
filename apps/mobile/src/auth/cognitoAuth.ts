import "@aws-amplify/react-native";

import { Amplify } from "aws-amplify";
import {
  confirmResetPassword as amplifyConfirmResetPassword,
  confirmSignUp as amplifyConfirmSignUp,
  fetchUserAttributes as amplifyFetchUserAttributes,
  getCurrentUser as amplifyGetCurrentUser,
  resendSignUpCode as amplifyResendSignUpCode,
  resetPassword as amplifyResetPassword,
  signIn as amplifySignIn,
  signOut as amplifySignOut,
  signUp as amplifySignUp,
  updatePassword as amplifyUpdatePassword,
} from "aws-amplify/auth";

import {
  meetsCognitoPasswordPolicy,
  PASSWORD_POLICY_HINT,
} from "@/src/auth/passwordPolicy";

export type AuthUser = {
  displayName: string;
  email: string;
  mode: "cognito";
  userId: string;
};

export type SignInInput = {
  email: string;
  password: string;
};

export type SignUpInput = SignInInput & {
  displayName: string;
};

export type AuthActionResult =
  | { status: "signed-in"; user: AuthUser }
  | {
      status: "confirmation-required";
      email: string;
      deliveryDestination?: string;
    }
  | { status: "password-reset-required"; email: string }
  | { status: "ready-to-sign-in"; email: string };

export type ConfirmSignUpInput = {
  confirmationCode: string;
  email: string;
};

export type ConfirmPasswordResetInput = {
  confirmationCode: string;
  email: string;
  newPassword: string;
};

export type PasswordResetRequestResult = {
  deliveryDestination?: string;
  email: string;
  status: "confirmation-required" | "done";
};

export type UpdatePasswordInput = {
  currentPassword: string;
  newPassword: string;
};

export interface AuthAdapter {
  readonly label: string;
  readonly mode: "cognito";
  confirmPasswordReset(input: ConfirmPasswordResetInput): Promise<void>;
  confirmSignUp(input: ConfirmSignUpInput): Promise<void>;
  requestPasswordReset(email: string): Promise<PasswordResetRequestResult>;
  resendSignUpCode(email: string): Promise<string | undefined>;
  restore(): Promise<AuthUser | null>;
  signIn(input: SignInInput): Promise<AuthActionResult>;
  signOut(): Promise<void>;
  signUp(input: SignUpInput): Promise<AuthActionResult>;
  updatePassword(input: UpdatePasswordInput): Promise<void>;
}

type CognitoCodeDeliveryDetails = {
  destination?: string;
};

type CognitoClient = {
  confirmResetPassword(input: {
    confirmationCode: string;
    newPassword: string;
    username: string;
  }): Promise<void>;
  confirmSignUp(input: {
    confirmationCode: string;
    username: string;
  }): Promise<unknown>;
  ensureConfigured(): void;
  fetchUserAttributes(): Promise<Partial<Record<string, string>>>;
  getCurrentUser(): Promise<{
    signInDetails?: { loginId?: string };
    userId: string;
    username: string;
  }>;
  resendSignUpCode(input: {
    username: string;
  }): Promise<CognitoCodeDeliveryDetails>;
  resetPassword(input: { username: string }): Promise<{
    isPasswordReset: boolean;
    nextStep: {
      codeDeliveryDetails?: CognitoCodeDeliveryDetails;
      resetPasswordStep: string;
    };
  }>;
  signIn(input: {
    options: { authFlowType: "USER_SRP_AUTH" };
    password: string;
    username: string;
  }): Promise<{
    isSignedIn: boolean;
    nextStep: { signInStep: string };
  }>;
  signOut(): Promise<void>;
  signUp(input: {
    options: {
      userAttributes: { email: string; name: string };
    };
    password: string;
    username: string;
  }): Promise<{
    isSignUpComplete: boolean;
    nextStep: {
      codeDeliveryDetails?: CognitoCodeDeliveryDetails;
      signUpStep: string;
    };
  }>;
  updatePassword(input: {
    newPassword: string;
    oldPassword: string;
  }): Promise<void>;
};

class UserFacingAuthError extends Error {}

let configuredPool: string | null = null;

function normalizeEmail(email: string) {
  const normalized = email.trim().toLowerCase();
  if (!/^\S+@\S+\.\S+$/.test(normalized)) {
    throw new UserFacingAuthError("Enter a valid email address.");
  }
  return normalized;
}

function requireValue(name: string, value: string | undefined) {
  const normalized = value?.trim();
  if (!normalized) {
    throw new UserFacingAuthError(
      `Sign-in is not configured. ${name} is required.`,
    );
  }
  return normalized;
}

export function configureAmplifyAuth() {
  const userPoolId = requireValue(
    "EXPO_PUBLIC_COGNITO_USER_POOL_ID",
    process.env.EXPO_PUBLIC_COGNITO_USER_POOL_ID,
  );
  const userPoolClientId = requireValue(
    "EXPO_PUBLIC_COGNITO_USER_POOL_CLIENT_ID",
    process.env.EXPO_PUBLIC_COGNITO_USER_POOL_CLIENT_ID,
  );
  const configurationKey = `${userPoolId}:${userPoolClientId}`;
  if (configuredPool === configurationKey) return;

  Amplify.configure({
    Auth: {
      Cognito: {
        loginWith: { email: true },
        passwordFormat: {
          minLength: 8,
          requireLowercase: true,
          requireNumbers: true,
          requireSpecialCharacters: true,
          requireUppercase: true,
        },
        signUpVerificationMethod: "code",
        userAttributes: {
          email: { required: true },
          name: { required: true },
        },
        userPoolClientId,
        userPoolId,
      },
    },
  });
  configuredPool = configurationKey;
}

function errorName(error: unknown) {
  if (error && typeof error === "object" && "name" in error) {
    return String(error.name);
  }
  return "";
}

function isSignedOutError(error: unknown) {
  return [
    "UserUnAuthenticatedException",
    "NotAuthorizedException",
    "NoAuthSessionFoundException",
  ].includes(errorName(error));
}

export function mapCognitoError(error: unknown): Error {
  if (error instanceof UserFacingAuthError) return error;

  switch (errorName(error)) {
    case "NotAuthorizedException":
    case "UserNotFoundException":
      return new UserFacingAuthError("Incorrect email or password.");
    case "UserNotConfirmedException":
      return new UserFacingAuthError("Verify your email first.");
    case "UsernameExistsException":
      return new UserFacingAuthError(
        "This email is already registered. Sign in or reset your password.",
      );
    case "CodeMismatchException":
      return new UserFacingAuthError("Incorrect verification code.");
    case "ExpiredCodeException":
      return new UserFacingAuthError("This code expired. Request a new one.");
    case "LimitExceededException":
    case "TooManyRequestsException":
      return new UserFacingAuthError("Too many requests. Try again shortly.");
    case "InvalidPasswordException":
      return new UserFacingAuthError(PASSWORD_POLICY_HINT);
    case "PasswordHistoryPolicyViolationException":
      return new UserFacingAuthError(
        "Choose a password you have not used recently.",
      );
    case "CodeDeliveryFailureException":
      return new UserFacingAuthError(
        "Could not send the verification email. Try again shortly.",
      );
    case "NetworkError":
      return new UserFacingAuthError("Check your connection and try again.");
    default:
      return new UserFacingAuthError(
        "Could not complete sign-in. Try again shortly.",
      );
  }
}

async function readCurrentUser(client: CognitoClient): Promise<AuthUser> {
  const [currentUser, attributes] = await Promise.all([
    client.getCurrentUser(),
    client.fetchUserAttributes(),
  ]);
  const email = (
    attributes.email ??
    currentUser.signInDetails?.loginId ??
    currentUser.username
  )
    .trim()
    .toLowerCase();
  const displayName = attributes.name?.trim() || email.split("@")[0] || email;
  return {
    displayName,
    email,
    mode: "cognito",
    userId: currentUser.userId,
  };
}

export function createCognitoAuthAdapter(client: CognitoClient): AuthAdapter {
  return {
    label: "Amazon Cognito",
    mode: "cognito",
    async confirmPasswordReset(input) {
      const email = normalizeEmail(input.email);
      const confirmationCode = input.confirmationCode.trim();
      if (!/^\d{6}$/.test(confirmationCode)) {
        throw new UserFacingAuthError(
          "Enter the six-digit code from your email.",
        );
      }
      if (!meetsCognitoPasswordPolicy(input.newPassword)) {
        throw new UserFacingAuthError(PASSWORD_POLICY_HINT);
      }
      try {
        client.ensureConfigured();
        await client.confirmResetPassword({
          confirmationCode,
          newPassword: input.newPassword,
          username: email,
        });
      } catch (error) {
        throw mapCognitoError(error);
      }
    },
    async confirmSignUp(input) {
      const email = normalizeEmail(input.email);
      const confirmationCode = input.confirmationCode.trim();
      if (!/^\d{6}$/.test(confirmationCode)) {
        throw new UserFacingAuthError(
          "Enter the six-digit code from your email.",
        );
      }
      try {
        client.ensureConfigured();
        await client.confirmSignUp({ username: email, confirmationCode });
      } catch (error) {
        throw mapCognitoError(error);
      }
    },
    async resendSignUpCode(emailInput) {
      const email = normalizeEmail(emailInput);
      try {
        client.ensureConfigured();
        const result = await client.resendSignUpCode({ username: email });
        return result.destination;
      } catch (error) {
        throw mapCognitoError(error);
      }
    },
    async requestPasswordReset(emailInput) {
      const email = normalizeEmail(emailInput);
      try {
        client.ensureConfigured();
        const result = await client.resetPassword({ username: email });
        if (
          result.isPasswordReset ||
          result.nextStep.resetPasswordStep === "DONE"
        ) {
          return { email, status: "done" };
        }
        if (
          result.nextStep.resetPasswordStep ===
          "CONFIRM_RESET_PASSWORD_WITH_CODE"
        ) {
          return {
            deliveryDestination:
              result.nextStep.codeDeliveryDetails?.destination,
            email,
            status: "confirmation-required",
          };
        }
        throw new UserFacingAuthError(
          "Could not confirm the password reset. Please try again.",
        );
      } catch (error) {
        throw mapCognitoError(error);
      }
    },
    async restore() {
      try {
        client.ensureConfigured();
        return await readCurrentUser(client);
      } catch (error) {
        if (isSignedOutError(error)) return null;
        throw mapCognitoError(error);
      }
    },
    async signIn(input) {
      const email = normalizeEmail(input.email);
      if (!input.password) {
        throw new UserFacingAuthError("Enter your password.");
      }
      try {
        client.ensureConfigured();
        const result = await client.signIn({
          username: email,
          password: input.password,
          options: { authFlowType: "USER_SRP_AUTH" },
        });
        if (result.isSignedIn || result.nextStep.signInStep === "DONE") {
          return { status: "signed-in", user: await readCurrentUser(client) };
        }
        if (result.nextStep.signInStep === "CONFIRM_SIGN_UP") {
          return { status: "confirmation-required", email };
        }
        if (result.nextStep.signInStep === "RESET_PASSWORD") {
          return { status: "password-reset-required", email };
        }
        throw new UserFacingAuthError(
          "This account needs an additional sign-in step. Contact support.",
        );
      } catch (error) {
        if (errorName(error) === "UserNotConfirmedException") {
          return { status: "confirmation-required", email };
        }
        throw mapCognitoError(error);
      }
    },
    async signOut() {
      try {
        client.ensureConfigured();
        await client.signOut();
      } catch (error) {
        throw mapCognitoError(error);
      }
    },
    async signUp(input) {
      const email = normalizeEmail(input.email);
      const displayName = input.displayName.trim();
      if (!displayName) {
        throw new UserFacingAuthError("Enter a display name.");
      }
      if (!meetsCognitoPasswordPolicy(input.password)) {
        throw new UserFacingAuthError(PASSWORD_POLICY_HINT);
      }
      try {
        client.ensureConfigured();
        const result = await client.signUp({
          username: email,
          password: input.password,
          options: { userAttributes: { email, name: displayName } },
        });
        if (
          !result.isSignUpComplete &&
          result.nextStep.signUpStep === "CONFIRM_SIGN_UP"
        ) {
          return {
            status: "confirmation-required",
            email,
            deliveryDestination:
              result.nextStep.codeDeliveryDetails?.destination,
          };
        }
        return { status: "ready-to-sign-in", email };
      } catch (error) {
        throw mapCognitoError(error);
      }
    },
    async updatePassword(input) {
      if (!input.currentPassword) {
        throw new UserFacingAuthError("Enter your current password.");
      }
      if (!meetsCognitoPasswordPolicy(input.newPassword)) {
        throw new UserFacingAuthError(PASSWORD_POLICY_HINT);
      }
      try {
        client.ensureConfigured();
        await client.updatePassword({
          oldPassword: input.currentPassword,
          newPassword: input.newPassword,
        });
      } catch (error) {
        if (errorName(error) === "NotAuthorizedException") {
          throw new UserFacingAuthError("Incorrect current password.");
        }
        throw mapCognitoError(error);
      }
    },
  };
}

const amplifyClient: CognitoClient = {
  confirmResetPassword: amplifyConfirmResetPassword,
  confirmSignUp: amplifyConfirmSignUp,
  ensureConfigured: configureAmplifyAuth,
  fetchUserAttributes: amplifyFetchUserAttributes,
  getCurrentUser: amplifyGetCurrentUser,
  resendSignUpCode: amplifyResendSignUpCode,
  resetPassword: amplifyResetPassword,
  signIn: amplifySignIn,
  signOut: () => amplifySignOut(),
  signUp: amplifySignUp,
  updatePassword: amplifyUpdatePassword,
};

export const cognitoAuthAdapter = createCognitoAuthAdapter(amplifyClient);
