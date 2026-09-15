import AsyncStorage from "@react-native-async-storage/async-storage";
import { act, renderHook, waitFor } from "@testing-library/react-native";

import {
  MAX_MAIL_INTEREST_LENGTH,
  MAX_MAIL_INTERESTS,
  useMailInterests,
} from "@/src/mail/useMailInterests";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
}));

const mockedStorage = jest.mocked(AsyncStorage);
const ownerKey = (ownerId: string) => `quietpilot.mail-interests.v1:${ownerId}`;

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((accept, fail) => {
    resolve = accept;
    reject = fail;
  });
  return { promise, resolve, reject };
}

beforeEach(() => {
  mockedStorage.getItem.mockReset().mockResolvedValue(null);
  mockedStorage.setItem.mockReset().mockResolvedValue(undefined);
});

test("persists normalized interests, deduplicates, removes and restores per owner", async () => {
  const storage = new Map([[ownerKey("user-a"), JSON.stringify(["학교"])]]);
  mockedStorage.getItem.mockImplementation(
    async (key) => storage.get(key) ?? null,
  );
  mockedStorage.setItem.mockImplementation(async (key, value) => {
    storage.set(key, value);
  });
  const hook = await renderHook(() => useMailInterests("user-a"));
  await waitFor(() => expect(hook.result.current.ready).toBe(true));
  expect(hook.result.current.keywords).toEqual(["학교"]);

  await act(() => hook.result.current.save("  ＡＩ 프로젝트  "));
  await act(() => hook.result.current.save("AI 프로젝트"));
  expect(hook.result.current.keywords).toEqual(["학교", "AI 프로젝트"]);
  expect(mockedStorage.setItem).toHaveBeenCalledTimes(1);

  await act(() => hook.result.current.save("학교 소식, 광고는 제외"));
  await act(() => hook.result.current.remove(" 학교 "));
  expect(hook.result.current.keywords).toEqual([
    "AI 프로젝트",
    "학교 소식, 광고는 제외",
  ]);
  expect(mockedStorage.setItem).toHaveBeenLastCalledWith(
    ownerKey("user-a"),
    JSON.stringify(["AI 프로젝트", "학교 소식, 광고는 제외"]),
  );

  await hook.unmount();
  const restored = await renderHook(() => useMailInterests("user-a"));
  await waitFor(() => expect(restored.result.current.ready).toBe(true));
  expect(restored.result.current.keywords).toEqual([
    "AI 프로젝트",
    "학교 소식, 광고는 제외",
  ]);
});

test.each(["   ", "가".repeat(MAX_MAIL_INTEREST_LENGTH + 1)])(
  "rejects invalid interest %p without changing saved interests",
  async (keyword) => {
    mockedStorage.getItem.mockResolvedValue(JSON.stringify(["학교"]));
    const hook = await renderHook(() => useMailInterests("user-a"));
    await waitFor(() => expect(hook.result.current.ready).toBe(true));

    await act(() => hook.result.current.save(keyword));

    expect(hook.result.current.error).toBeTruthy();
    expect(hook.result.current.keywords).toEqual(["학교"]);
    expect(mockedStorage.setItem).not.toHaveBeenCalled();
  },
);

test("the limit never evicts an existing interest and still allows duplicates", async () => {
  const keywords = Array.from(
    { length: MAX_MAIL_INTERESTS },
    (_, i) => `관심 ${i}`,
  );
  mockedStorage.getItem.mockResolvedValue(JSON.stringify(keywords));
  const hook = await renderHook(() => useMailInterests("user-a"));
  await waitFor(() => expect(hook.result.current.ready).toBe(true));

  await act(() => hook.result.current.save("새 관심"));
  expect(hook.result.current.keywords).toEqual(keywords);
  expect(hook.result.current.error).toContain("8 keywords maximum");
  expect(mockedStorage.setItem).not.toHaveBeenCalled();

  await act(() => hook.result.current.save("관심 0"));
  expect(hook.result.current.error).toBeNull();
  expect(mockedStorage.setItem).not.toHaveBeenCalled();
});

test("ignores edits before hydration and while a write is in progress", async () => {
  const read = deferred<string | null>();
  mockedStorage.getItem.mockReturnValue(read.promise);
  const hook = await renderHook(() => useMailInterests("user-a"));

  await act(async () => {
    await hook.result.current.save("취업");
    await hook.result.current.remove("학교");
  });
  expect(hook.result.current.ready).toBe(false);
  expect(mockedStorage.setItem).not.toHaveBeenCalled();

  await act(() => read.resolve(JSON.stringify(["학교"])));
  const write = deferred<void>();
  mockedStorage.setItem.mockReturnValue(write.promise);
  let pending!: Promise<void>;
  await act(() => {
    pending = hook.result.current.save("취업");
  });
  expect(hook.result.current.saving).toBe(true);
  expect(hook.result.current.keywords).toEqual(["학교"]);
  await act(async () => {
    await hook.result.current.save("생활");
    await hook.result.current.remove("학교");
  });
  expect(mockedStorage.setItem).toHaveBeenCalledTimes(1);

  await act(async () => {
    write.resolve(undefined);
    await pending;
  });
  expect(hook.result.current.saving).toBe(false);
  expect(hook.result.current.keywords).toEqual(["학교", "취업"]);
});

test.each(["save", "remove"] as const)(
  "a failed %s does not show the change as saved",
  async (action) => {
    mockedStorage.getItem.mockResolvedValue(JSON.stringify(["학교"]));
    const hook = await renderHook(() => useMailInterests("user-a"));
    await waitFor(() => expect(hook.result.current.ready).toBe(true));
    mockedStorage.setItem.mockRejectedValue(new Error("storage unavailable"));

    await act(() =>
      hook.result.current[action](action === "save" ? "취업" : "학교"),
    );

    expect(hook.result.current.keywords).toEqual(["학교"]);
    expect(hook.result.current.error).toContain("Could not save keywords");
    expect(hook.result.current.saving).toBe(false);
  },
);

test.each(["read-error", "invalid-json", "invalid-shape"] as const)(
  "a hydration failure %s keeps editing disabled to protect existing data",
  async (failure) => {
    if (failure === "read-error") {
      mockedStorage.getItem.mockRejectedValue(new Error("storage unavailable"));
    } else {
      mockedStorage.getItem.mockResolvedValue(
        failure === "invalid-json" ? "{" : JSON.stringify({ keyword: "학교" }),
      );
    }
    const hook = await renderHook(() => useMailInterests("user-a"));
    await waitFor(() => expect(hook.result.current.error).toBeTruthy());

    await act(() => hook.result.current.save("취업"));

    expect(hook.result.current.ready).toBe(false);
    expect(hook.result.current.keywords).toEqual([]);
    expect(mockedStorage.setItem).not.toHaveBeenCalled();
  },
);

test("every render masks the former owner's keywords while the next owner loads", async () => {
  mockedStorage.getItem.mockResolvedValueOnce(JSON.stringify(["학교"]));
  const seen: { ownerId: string; keywords: string[] }[] = [];
  const hook = await renderHook(
    ({ ownerId }: { ownerId: string }) => {
      const interests = useMailInterests(ownerId);
      seen.push({ ownerId, keywords: interests.keywords });
      return interests;
    },
    { initialProps: { ownerId: "user-a" } },
  );
  await waitFor(() => expect(hook.result.current.ready).toBe(true));
  const oldSave = hook.result.current.save;
  const read = deferred<string | null>();
  mockedStorage.getItem.mockReturnValueOnce(read.promise);

  await hook.rerender({ ownerId: "user-b" });
  expect(hook.result.current.keywords).toEqual([]);
  expect(hook.result.current.ready).toBe(false);
  expect(seen.filter(({ ownerId }) => ownerId === "user-b")).toEqual(
    expect.arrayContaining([{ ownerId: "user-b", keywords: [] }]),
  );
  expect(
    seen.some(
      ({ ownerId, keywords }) =>
        ownerId === "user-b" && keywords.includes("학교"),
    ),
  ).toBe(false);
  await act(() => oldSave("프로젝트"));
  expect(mockedStorage.setItem).not.toHaveBeenCalled();

  await act(() => read.resolve(JSON.stringify(["생활"])));
  expect(hook.result.current.keywords).toEqual(["생활"]);
  expect(mockedStorage.getItem).toHaveBeenLastCalledWith(ownerKey("user-b"));
});

test("a previous owner's late hydration cannot replace the new owner's interests", async () => {
  const read = deferred<string | null>();
  mockedStorage.getItem
    .mockReturnValueOnce(read.promise)
    .mockResolvedValueOnce(JSON.stringify(["생활"]));
  const hook = await renderHook(
    ({ ownerId }: { ownerId: string }) => useMailInterests(ownerId),
    {
      initialProps: { ownerId: "user-a" },
    },
  );

  await hook.rerender({ ownerId: "user-b" });
  await waitFor(() => expect(hook.result.current.ready).toBe(true));
  await act(() => read.resolve(JSON.stringify(["학교"])));

  expect(hook.result.current.keywords).toEqual(["생활"]);
  expect(hook.result.current.error).toBeNull();
});

test.each(["success", "failure"] as const)(
  "an old owner's late write %s cannot change the next owner's saving state",
  async (outcome) => {
    mockedStorage.getItem
      .mockResolvedValueOnce(JSON.stringify(["학교"]))
      .mockResolvedValueOnce(JSON.stringify(["생활"]));
    const hook = await renderHook(
      ({ ownerId }: { ownerId: string }) => useMailInterests(ownerId),
      {
        initialProps: { ownerId: "user-a" },
      },
    );
    await waitFor(() => expect(hook.result.current.ready).toBe(true));
    const oldWrite = deferred<void>();
    const newWrite = deferred<void>();
    mockedStorage.setItem
      .mockReturnValueOnce(oldWrite.promise)
      .mockReturnValueOnce(newWrite.promise);
    let oldPending!: Promise<void>;
    let newPending!: Promise<void>;
    await act(() => {
      oldPending = hook.result.current.save("취업");
    });

    await hook.rerender({ ownerId: "user-b" });
    await waitFor(() => expect(hook.result.current.ready).toBe(true));
    await act(() => {
      newPending = hook.result.current.save("프로젝트");
    });
    await act(async () => {
      if (outcome === "success") oldWrite.resolve(undefined);
      else oldWrite.reject(new Error("old account write failed"));
      await oldPending;
    });

    expect(hook.result.current.keywords).toEqual(["생활"]);
    expect(hook.result.current.saving).toBe(true);
    expect(hook.result.current.error).toBeNull();
    expect(mockedStorage.setItem).toHaveBeenNthCalledWith(
      1,
      ownerKey("user-a"),
      JSON.stringify(["학교", "취업"]),
    );
    expect(mockedStorage.setItem).toHaveBeenNthCalledWith(
      2,
      ownerKey("user-b"),
      JSON.stringify(["생활", "프로젝트"]),
    );
    await act(async () => {
      newWrite.resolve(undefined);
      await newPending;
    });
    expect(hook.result.current.keywords).toEqual(["생활", "프로젝트"]);
    expect(hook.result.current.saving).toBe(false);
  },
);
