import * as SecureStore from "expo-secure-store";

import {
  prototypeAuthAdapter,
  prototypeSessionKey,
} from "@/src/auth/prototypeAuth";

const secureStore = jest.mocked(SecureStore);

describe("prototype auth adapter", () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("is explicitly prototype-only", () => {
    expect(prototypeAuthAdapter.mode).toBe("prototype");
    expect(prototypeAuthAdapter.label).toContain("example");
  });

  it("persists a local profile without the password", async () => {
    const user = await prototypeAuthAdapter.signUp({
      displayName: "철연",
      email: "Pilot@QuietPilot.local",
      password: "local-only",
    });

    expect(user).toMatchObject({
      displayName: "철연",
      email: "pilot@quietpilot.local",
      mode: "prototype",
    });
    const stored = secureStore.setItemAsync.mock.calls[0]?.[1] ?? "";
    expect(stored).not.toContain("local-only");
    expect(secureStore.setItemAsync).toHaveBeenCalledWith(
      prototypeSessionKey,
      expect.any(String),
    );
  });

  it("restores only a valid prototype profile", async () => {
    secureStore.getItemAsync.mockResolvedValueOnce(
      JSON.stringify({
        displayName: "QuietPilot 탐색자",
        email: "demo@quietpilot.local",
        mode: "prototype",
        userId: "prototype:demo@quietpilot.local",
      }),
    );
    await expect(prototypeAuthAdapter.restore()).resolves.toMatchObject({
      mode: "prototype",
    });

    secureStore.getItemAsync.mockResolvedValueOnce("not-json");
    await expect(prototypeAuthAdapter.restore()).resolves.toBeNull();
  });

  it("validates the local-only form and removes the session", async () => {
    await expect(
      prototypeAuthAdapter.signIn({ email: "bad", password: "1234" }),
    ).rejects.toThrow("email");
    await expect(
      prototypeAuthAdapter.signIn({
        email: "demo@quietpilot.local",
        password: "123",
      }),
    ).rejects.toThrow("8 characters");

    await prototypeAuthAdapter.signOut();
    expect(secureStore.deleteItemAsync).toHaveBeenCalledWith(
      prototypeSessionKey,
    );
  });
});
