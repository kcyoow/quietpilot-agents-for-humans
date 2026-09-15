import { act, renderHook, waitFor } from "@testing-library/react-native";
import type { PropsWithChildren } from "react";

import { AuthProvider, useAuth } from "./AuthProvider";
import { cognitoAuthAdapter } from "./cognitoAuth";
import { registerBeforeSignOutCleanup } from "./signOutCleanup";

jest.mock("./cognitoAuth", () => ({
  cognitoAuthAdapter: {
    restore: jest.fn(),
    signIn: jest.fn(),
    signUp: jest.fn(),
    signOut: jest.fn(),
  },
}));
const adapter = jest.mocked(cognitoAuthAdapter);
const owner = {
  userId: "owner",
  email: "owner@example.test",
  displayName: "사용자",
  mode: "cognito" as const,
};
const nextOwner = {
  userId: "next-owner",
  email: "next@example.test",
  displayName: "다음 사용자",
  mode: "cognito" as const,
};
const wrapper = ({ children }: PropsWithChildren) => (
  <AuthProvider>{children}</AuthProvider>
);
beforeEach(() => {
  jest.clearAllMocks();
  adapter.restore.mockResolvedValue(owner);
  adapter.signOut.mockResolvedValue(undefined);
  adapter.signIn.mockResolvedValue({ status: "signed-in", user: nextOwner });
});

test("without cleanup hooks signout keeps existing behavior", async () => {
  const hook = await renderHook(useAuth, { wrapper });
  await waitFor(() => expect(hook.result.current.user?.userId).toBe("owner"));
  await act(async () => {
    await hook.result.current.signOut();
  });
  expect(adapter.signOut).toHaveBeenCalledTimes(1);
  expect(hook.result.current.user).toBeNull();
});

test("cleanup runs before credentials are signed out and failure preserves the signed-in owner", async () => {
  const cleanup = jest.fn().mockRejectedValue(new Error("알림 해제 실패"));
  const remove = registerBeforeSignOutCleanup("owner", cleanup);
  try {
    const hook = await renderHook(useAuth, { wrapper });
    await waitFor(() => expect(hook.result.current.user?.userId).toBe("owner"));
    await act(async () => {
      await expect(hook.result.current.signOut()).rejects.toThrow(
        "알림 해제 실패",
      );
    });
    expect(adapter.signOut).not.toHaveBeenCalled();
    expect(hook.result.current.user?.userId).toBe("owner");
    expect(hook.result.current.status).toBe("ready");
  } finally {
    remove();
  }
});

test("another owner's cleanup never runs", async () => {
  const cleanup = jest.fn().mockResolvedValue(undefined);
  const remove = registerBeforeSignOutCleanup("different-owner", cleanup);
  try {
    const hook = await renderHook(useAuth, { wrapper });
    await waitFor(() => expect(hook.result.current.user?.userId).toBe("owner"));
    await act(async () => {
      await hook.result.current.signOut();
    });
    expect(cleanup).not.toHaveBeenCalled();
  } finally {
    remove();
  }
});

test("new sign-in waits for prior cleanup so it cannot lose the new account's token", async () => {
  let finish!: () => void;
  const cleanup = jest.fn(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  const remove = registerBeforeSignOutCleanup("owner", cleanup);
  try {
    const hook = await renderHook(useAuth, { wrapper });
    await waitFor(() => expect(hook.result.current.user?.userId).toBe("owner"));
    let signout!: Promise<void>;
    let signin!: ReturnType<typeof adapter.signIn>;
    await act(async () => {
      signout = hook.result.current.signOut();
      signin = hook.result.current.signIn({
        email: "next@example.test",
        password: "SyntheticPassword1!",
      });
      await Promise.resolve();
    });
    expect(cleanup).toHaveBeenCalledTimes(1);
    expect(adapter.signIn).not.toHaveBeenCalled();
    await act(async () => {
      finish();
      await signout;
      await signin;
    });
    expect(adapter.signOut.mock.invocationCallOrder[0]).toBeLessThan(
      adapter.signIn.mock.invocationCallOrder[0],
    );
    expect(hook.result.current.user?.userId).toBe("next-owner");
  } finally {
    remove();
  }
});

test("late startup restore cannot replace a newer signed-in owner", async () => {
  let restore!: (value: typeof owner) => void;
  adapter.restore.mockReturnValueOnce(
    new Promise((resolve) => {
      restore = resolve;
    }),
  );
  const hook = await renderHook(useAuth, { wrapper });
  await act(async () => {
    await hook.result.current.signIn({
      email: "next@example.test",
      password: "SyntheticPassword1!",
    });
  });
  await act(async () => {
    restore(owner);
    await Promise.resolve();
  });
  expect(hook.result.current.user?.userId).toBe("next-owner");
});
