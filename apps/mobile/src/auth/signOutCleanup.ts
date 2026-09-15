type Cleanup = () => Promise<void>;
const cleanups = new Map<symbol, { owner: string; cleanup: Cleanup }>();

export function registerBeforeSignOutCleanup(owner: string, cleanup: Cleanup) {
  const key = Symbol("before-signout");
  cleanups.set(key, { owner, cleanup });
  return () => {
    cleanups.delete(key);
  };
}

export async function runBeforeSignOutCleanup(owner: string) {
  for (const [key, entry] of [...cleanups]) {
    if (entry.owner === owner && cleanups.get(key) === entry) {
      await entry.cleanup();
    }
  }
}
