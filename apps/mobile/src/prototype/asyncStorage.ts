import AsyncStorage from "@react-native-async-storage/async-storage";

import { PrototypeBackend } from "@/src/prototype/backend";
import type { PrototypeClock } from "@/src/prototype/types";

export function createAsyncStoragePrototypeBackend(options?: {
  clock?: PrototypeClock;
  storageKey?: string;
}): PrototypeBackend {
  return new PrototypeBackend({
    clock: options?.clock,
    storage: AsyncStorage,
    storageKey: options?.storageKey,
  });
}
