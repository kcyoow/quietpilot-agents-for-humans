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

import {
  cognitoAuthAdapter,
  type AuthActionResult,
  type AuthAdapter,
  type AuthUser,
  type ConfirmPasswordResetInput,
  type ConfirmSignUpInput,
  type PasswordResetRequestResult,
  type SignInInput,
  type SignUpInput,
  type UpdatePasswordInput,
} from "@/src/auth/cognitoAuth";
import { runBeforeSignOutCleanup } from "@/src/auth/signOutCleanup";

type AuthStatus = "booting" | "loading" | "ready";

type AuthContextValue = {
  adapter: AuthAdapter;
  confirmPasswordReset(input: ConfirmPasswordResetInput): Promise<void>;
  confirmSignUp(input: ConfirmSignUpInput): Promise<void>;
  requestPasswordReset(email: string): Promise<PasswordResetRequestResult>;
  resendSignUpCode(email: string): Promise<string | undefined>;
  signIn(input: SignInInput): Promise<AuthActionResult>;
  signOut(): Promise<void>;
  signUp(input: SignUpInput): Promise<AuthActionResult>;
  startupError: string | null;
  status: AuthStatus;
  user: AuthUser | null;
  updatePassword(input: UpdatePasswordInput): Promise<void>;
};

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const adapter = cognitoAuthAdapter;
  const [status, setStatus] = useState<AuthStatus>("booting");
  const [startupError, setStartupError] = useState<string | null>(null);
  const [user, setUser] = useState<AuthUser | null>(null);
  const currentUser = useRef<AuthUser | null>(null);
  const identityQueue = useRef<Promise<void>>(Promise.resolve());
  const identityRevision = useRef(0);
  const commitUser = useCallback((next: AuthUser | null) => {
    currentUser.current = next;
    setUser(next);
  }, []);
  const changeIdentity = useCallback(<T,>(work: () => Promise<T>) => {
    identityRevision.current += 1;
    const result = identityQueue.current.then(work, work);
    identityQueue.current = result.then(
      () => undefined,
      () => undefined,
    );
    return result;
  }, []);

  useEffect(() => {
    let active = true;
    const revision = identityRevision.current;
    adapter
      .restore()
      .then((restored) => {
        if (active && identityRevision.current === revision)
          commitUser(restored);
      })
      .catch((error: unknown) => {
        if (active && identityRevision.current === revision) {
          setStartupError(
            error instanceof Error
              ? error.message
              : "Could not load sign-in settings.",
          );
        }
      })
      .finally(() => {
        if (active && identityRevision.current === revision) setStatus("ready");
      });
    return () => {
      active = false;
    };
  }, [adapter, commitUser]);

  const value = useMemo<AuthContextValue>(
    () => ({
      adapter,
      async confirmPasswordReset(input) {
        setStatus("loading");
        try {
          await adapter.confirmPasswordReset(input);
        } finally {
          setStatus("ready");
        }
      },
      async confirmSignUp(input) {
        setStatus("loading");
        try {
          await adapter.confirmSignUp(input);
        } finally {
          setStatus("ready");
        }
      },
      async resendSignUpCode(email) {
        setStatus("loading");
        try {
          return await adapter.resendSignUpCode(email);
        } finally {
          setStatus("ready");
        }
      },
      async requestPasswordReset(email) {
        setStatus("loading");
        try {
          return await adapter.requestPasswordReset(email);
        } finally {
          setStatus("ready");
        }
      },
      async signIn(input) {
        return changeIdentity(async () => {
          setStatus("loading");
          try {
            const result = await adapter.signIn(input);
            if (result.status === "signed-in") commitUser(result.user);
            return result;
          } finally {
            setStatus("ready");
          }
        });
      },
      async signOut() {
        const owner = currentUser.current?.userId;
        return changeIdentity(async () => {
          if (currentUser.current?.userId !== owner)
            throw new Error("Your sign-in state changed.");
          setStatus("loading");
          try {
            if (owner) await runBeforeSignOutCleanup(owner);
            await adapter.signOut();
            commitUser(null);
          } finally {
            setStatus("ready");
          }
        });
      },
      async signUp(input) {
        return changeIdentity(async () => {
          setStatus("loading");
          try {
            const result = await adapter.signUp(input);
            if (result.status === "signed-in") commitUser(result.user);
            return result;
          } finally {
            setStatus("ready");
          }
        });
      },
      startupError,
      status,
      user,
      async updatePassword(input) {
        setStatus("loading");
        try {
          await adapter.updatePassword(input);
        } finally {
          setStatus("ready");
        }
      },
    }),
    [adapter, changeIdentity, commitUser, startupError, status, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
