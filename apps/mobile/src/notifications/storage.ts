import "react-native-get-random-values";
import * as SecureStore from "expo-secure-store";

import { preferenceKey } from "./helpers";

const DEVICE_KEY = "quietpilot.notifications.device";
let devicePromise: Promise<string> | null = null;

export const notificationStorage = {
  deviceId(): Promise<string> {
    devicePromise ??= (async () => {
      const saved = await SecureStore.getItemAsync(DEVICE_KEY);
      if (saved && /^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$/.test(saved))
        return saved;
      const bytes = crypto.getRandomValues(new Uint8Array(16));
      const created = `qp-device-${Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("")}`;
      await SecureStore.setItemAsync(DEVICE_KEY, created);
      return created;
    })().catch((error: unknown) => {
      devicePromise = null;
      throw error;
    });
    return devicePromise;
  },
  async enabled(owner: string) {
    const saved = await SecureStore.getItemAsync(preferenceKey(owner));
    if (!saved) return false;
    try {
      const value: unknown = JSON.parse(saved);
      return Boolean(
        value &&
        typeof value === "object" &&
        "owner" in value &&
        value.owner === owner &&
        "enabled" in value &&
        value.enabled === true,
      );
    } catch {
      return false;
    }
  },
  async setEnabled(owner: string, enabled: boolean) {
    await SecureStore.setItemAsync(
      preferenceKey(owner),
      JSON.stringify({ owner, enabled }),
    );
  },
};
