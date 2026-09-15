import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

const SESSION_KEY = "quietpilot.prototype-session.v1";

export type AuthUser = {
  displayName: string;
  email: string;
  mode: "prototype";
  userId: string;
};

export type SignInInput = {
  email: string;
  password: string;
};

export type SignUpInput = SignInInput & {
  displayName: string;
};

export interface AuthAdapter {
  readonly label: string;
  readonly mode: "prototype";
  restore(): Promise<AuthUser | null>;
  signIn(input: SignInInput): Promise<AuthUser>;
  signOut(): Promise<void>;
  signUp(input: SignUpInput): Promise<AuthUser>;
}

function normalizeEmail(email: string) {
  return email.trim().toLowerCase();
}

function validateCredentials(email: string, password: string) {
  const normalized = normalizeEmail(email);
  if (!/^\S+@\S+\.\S+$/.test(normalized)) {
    throw new Error("Enter a valid email address.");
  }
  if (password.length < 8) {
    throw new Error("Use at least 8 characters for your password.");
  }
  return normalized;
}

function parseStoredUser(value: string | null): AuthUser | null {
  if (!value) return null;
  try {
    const parsed = JSON.parse(value) as Partial<AuthUser>;
    if (
      parsed.mode !== "prototype" ||
      typeof parsed.userId !== "string" ||
      typeof parsed.email !== "string" ||
      typeof parsed.displayName !== "string"
    ) {
      return null;
    }
    return parsed as AuthUser;
  } catch {
    return null;
  }
}

async function persistUser(user: AuthUser) {
  await setSessionValue(JSON.stringify(user));
  return user;
}

async function getSessionValue() {
  if (Platform.OS === "web") {
    return typeof localStorage === "undefined"
      ? null
      : localStorage.getItem(SESSION_KEY);
  }
  return SecureStore.getItemAsync(SESSION_KEY);
}

async function setSessionValue(value: string) {
  if (Platform.OS === "web") {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(SESSION_KEY, value);
    }
    return;
  }
  await SecureStore.setItemAsync(SESSION_KEY, value);
}

async function deleteSessionValue() {
  if (Platform.OS === "web") {
    if (typeof localStorage !== "undefined") {
      localStorage.removeItem(SESSION_KEY);
    }
    return;
  }
  await SecureStore.deleteItemAsync(SESSION_KEY);
}

function prototypeUser(email: string, displayName?: string): AuthUser {
  const localName = email.split("@")[0] || "Explorer";
  return {
    displayName: displayName?.trim() || localName,
    email,
    mode: "prototype",
    userId: `prototype:${email}`,
  };
}

export const prototypeAuthAdapter: AuthAdapter = {
  label: "Local example session",
  mode: "prototype",
  async restore() {
    return parseStoredUser(await getSessionValue());
  },
  async signIn(input) {
    const email = validateCredentials(input.email, input.password);
    return persistUser(prototypeUser(email));
  },
  async signOut() {
    await deleteSessionValue();
  },
  async signUp(input) {
    const email = validateCredentials(input.email, input.password);
    if (!input.displayName.trim()) {
      throw new Error("Enter a display name.");
    }
    return persistUser(prototypeUser(email, input.displayName));
  },
};

export const prototypeSessionKey = SESSION_KEY;
