import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

import {
  MailInterestEditor,
  type MailInterestEditorProps,
} from "@/src/mail/MailInterestEditor";

jest.mock("@/src/theme/useAppTheme", () => {
  const { appColors } =
    jest.requireActual<typeof import("@/src/theme/tokens")>(
      "@/src/theme/tokens",
    );
  return { useAppTheme: () => ({ colors: appColors.light }) };
});

function props(
  overrides: Partial<MailInterestEditorProps> = {},
): MailInterestEditorProps {
  return {
    profile: { tags: [], description: "", version: 0 },
    recommendations: {
      status: "NOT_STARTED",
      tags: [],
      title_count: 0,
      error_code: null,
    },
    saving: false,
    error: null,
    onSave: jest.fn().mockResolvedValue(undefined),
    onRecommend: jest.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

test("adds multiple manual hashtags without restricting punctuation or emoji", async () => {
  const input = props();
  const screen = await render(<MailInterestEditor {...input} />);
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "#학교 #취업 #C++ #연구·개발 #🤖",
  );
  await fireEvent.press(screen.getByLabelText("Add tags"));
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "학교",
  );
  await fireEvent.press(screen.getByLabelText("Add tags"));
  await fireEvent.press(
    screen.getByRole("button", { name: "Start with these interests" }),
  );

  expect(input.onSave).toHaveBeenCalledWith({
    tags: ["학교", "취업", "C++", "연구·개발", "🤖"],
    description: "",
    expected_version: 0,
  });
  expect(input.onRecommend).not.toHaveBeenCalled();
});

test("one word is enough and an unadded hashtag draft is included on save", async () => {
  const input = props();
  const screen = await render(<MailInterestEditor {...input} />);
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "학교",
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Start with these interests" }),
  );

  expect(input.onSave).toHaveBeenCalledWith({
    tags: ["학교"],
    description: "",
    expected_version: 0,
  });
});

test("AI tags disclose the actual title count and only become interests when selected", async () => {
  const input = props({
    recommendations: {
      status: "READY",
      tags: [
        { tag: "학교", evidence_refs: ["title-1"] },
        { tag: "도서관", evidence_refs: ["title-2"] },
      ],
      title_count: 17,
      error_code: null,
    },
  });
  const screen = await render(<MailInterestEditor {...input} />);
  expect(screen.getByText("Based on 17 mail titles.")).toBeTruthy();
  expect(
    screen.getByRole("checkbox", { name: "학교 suggested tag" }).props
      .accessibilityState.checked,
  ).toBe(false);
  expect(screen.queryByLabelText("Remove interest tag 학교")).toBeNull();

  await fireEvent.press(
    screen.getByRole("checkbox", { name: "학교 suggested tag" }),
  );
  await fireEvent.press(
    screen.getByRole("checkbox", { name: "도서관 suggested tag" }),
  );
  await fireEvent.press(
    screen.getByRole("checkbox", { name: "학교 suggested tag" }),
  );
  expect(
    screen.getByRole("checkbox", { name: "학교 suggested tag" }).props
      .accessibilityState.checked,
  ).toBe(false);
  await fireEvent.press(
    screen.getByRole("button", { name: "Start with these interests" }),
  );

  expect(input.onSave).toHaveBeenCalledWith({
    tags: ["도서관"],
    description: "",
    expected_version: 0,
  });
});

test("description-only interests keep wording, exclusions, whitespace and line breaks", async () => {
  const input = props();
  const description =
    "  학교 공지와 장학금 소식이 궁금해요.\n광고는 제외해 주세요.  ";
  const screen = await render(<MailInterestEditor {...input} />);
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    description,
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Start with these interests" }),
  );

  expect(input.onSave).toHaveBeenCalledWith({
    tags: [],
    description,
    expected_version: 0,
  });
});

test("removing all interests is saveable and explains that mail filtering stops", async () => {
  const input = props({
    profile: { tags: ["학교"], description: "학교 공지", version: 4 },
  });
  const screen = await render(<MailInterestEditor {...input} />);
  await fireEvent.press(screen.getByLabelText("Remove interest tag 학교"));
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    "",
  );

  expect(
    screen.getByText("Clearing your interests stops mail filtering."),
  ).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));

  expect(input.onSave).toHaveBeenCalledWith({
    tags: [],
    description: "",
    expected_version: 4,
  });
});

test("pending recommendations do not block manual tags or description-only saving", async () => {
  const input = props({
    recommendations: {
      status: "PENDING",
      tags: [{ tag: "지난추천", evidence_refs: ["old-title"] }],
      title_count: 0,
      error_code: null,
    },
  });
  const screen = await render(<MailInterestEditor {...input} />);
  expect(
    screen.getByRole("button", { name: "Retry request" }).props
      .accessibilityState.disabled,
  ).toBe(false);
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.getByLabelText("Enter interest tags").props.editable).toBe(
    true,
  );
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    "학교",
  );

  await fireEvent.press(
    screen.getByRole("button", { name: "Start with these interests" }),
  );
  expect(input.onSave).toHaveBeenCalledWith({
    tags: [],
    description: "학교",
    expected_version: 0,
  });
  expect(input.onRecommend).not.toHaveBeenCalled();
});

test("pending recommendation resend is guarded in flight and preserves the manual draft", async () => {
  let complete!: () => void;
  const input = props({
    recommendations: {
      status: "PENDING",
      tags: [],
      title_count: 0,
      error_code: null,
    },
    onRecommend: jest.fn(
      () =>
        new Promise<void>((resolve) => {
          complete = resolve;
        }),
    ),
  });
  const screen = await render(<MailInterestEditor {...input} />);
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "#취업",
  );
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    "내가 작성한 관심사",
  );
  const retry = screen.getByRole("button", { name: "Retry request" });
  await fireEvent.press(retry);
  await fireEvent.press(retry);
  expect(input.onRecommend).toHaveBeenCalledTimes(1);
  expect(
    screen.getByRole("button", { name: "Finding suggestions" }).props
      .accessibilityState.disabled,
  ).toBe(true);
  expect(screen.getByLabelText("Enter interest tags").props.value).toBe(
    "#취업",
  );
  expect(screen.getByLabelText("Describe mail interests").props.value).toBe(
    "내가 작성한 관심사",
  );
  await act(() => complete());
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Retry request" }).props
        .accessibilityState.disabled,
    ).toBe(false),
  );
  await screen.rerender(<MailInterestEditor {...input} saving />);
  await fireEvent.press(screen.getByRole("button", { name: "Retry request" }));
  expect(
    screen.getByRole("button", { name: "Retry request" }).props
      .accessibilityState.disabled,
  ).toBe(true);
  expect(input.onRecommend).toHaveBeenCalledTimes(1);
  expect(input.onSave).not.toHaveBeenCalled();
});

test("pending recommendations requiring Google authorization reconnect instead of resending", async () => {
  const input = props({
    recommendations: {
      status: "PENDING",
      tags: [],
      title_count: 0,
      error_code: "GOOGLE_AUTH_REQUIRED",
    },
    onReconnect: jest.fn(),
  });
  const screen = await render(<MailInterestEditor {...input} />);
  await fireEvent.press(
    screen.getByRole("button", { name: "Reconnect Google" }),
  );
  expect(input.onReconnect).toHaveBeenCalledTimes(1);
  expect(input.onRecommend).not.toHaveBeenCalled();
});

test.each(["ERROR", "READY"] as const)(
  "%s without tags shows no canned recommendations and offers a retry",
  async (status) => {
    const input = props({
      recommendations: {
        status,
        tags: [],
        title_count: status === "READY" ? 0 : 12,
        error_code: status === "ERROR" ? "RECOMMENDATION_FAILED" : null,
      },
    });
    const screen = await render(<MailInterestEditor {...input} />);
    expect(screen.queryByRole("checkbox")).toBeNull();
    if (status === "ERROR") {
      expect(
        screen.getByText(
          /Suggestions unavailable\. You can enter interests below\./,
        ),
      ).toBeTruthy();
    } else {
      expect(screen.getByText("Based on 0 mail titles.")).toBeTruthy();
      expect(
        screen.getByText(/No suggestions yet\. Add your own interests below\./),
      ).toBeTruthy();
    }
    await fireEvent.press(
      screen.getByRole("button", {
        name: status === "ERROR" ? "Retry suggestions" : "Refresh suggestions",
      }),
    );
    expect(input.onRecommend).toHaveBeenCalledTimes(1);
  },
);

test("a version refresh preserves the draft and baseline until explicitly reloaded", async () => {
  const input = props({
    profile: { tags: ["학교"], description: "원래 설명", version: 2 },
  });
  const screen = await render(<MailInterestEditor {...input} />);
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    "작성 중인 설명",
  );
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "#취업",
  );
  const latest = {
    tags: ["생활"],
    description: "새로 저장된 설명",
    version: 3,
  };
  await screen.rerender(<MailInterestEditor {...input} profile={latest} />);

  expect(screen.getByLabelText("Describe mail interests").props.value).toBe(
    "작성 중인 설명",
  );
  expect(screen.getByLabelText("Enter interest tags").props.value).toBe(
    "#취업",
  );
  expect(screen.getByLabelText("Remove interest tag 학교")).toBeTruthy();
  expect(
    screen.getByText(/New settings are available\. Your edits are kept\./),
  ).toBeTruthy();
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  expect(input.onSave).toHaveBeenLastCalledWith({
    tags: ["학교", "취업"],
    description: "작성 중인 설명",
    expected_version: 2,
  });

  await fireEvent.press(
    screen.getByRole("button", { name: "Load latest settings" }),
  );
  expect(screen.getByLabelText("Describe mail interests").props.value).toBe(
    "새로 저장된 설명",
  );
  expect(screen.getByLabelText("Enter interest tags").props.value).toBe("");
  expect(screen.queryByLabelText("Remove interest tag 학교")).toBeNull();
  expect(screen.getByLabelText("Remove interest tag 생활")).toBeTruthy();
  expect(
    screen.queryByText(/New settings are available\. Your edits are kept\./),
  ).toBeNull();
  await fireEvent.press(screen.getByRole("button", { name: "Save" }));
  expect(input.onSave).toHaveBeenLastCalledWith({
    tags: ["생활"],
    description: "새로 저장된 설명",
    expected_version: 3,
  });
});

test("recommendation refresh at the same profile version does not replace a draft", async () => {
  const input = props();
  const screen = await render(<MailInterestEditor {...input} />);
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    "내가 쓴 설명",
  );
  await screen.rerender(
    <MailInterestEditor
      {...input}
      profile={{ ...input.profile }}
      recommendations={{
        status: "READY",
        tags: [{ tag: "학교", evidence_refs: ["title-1"] }],
        title_count: 1,
        error_code: null,
      }}
    />,
  );

  expect(screen.getByLabelText("Describe mail interests").props.value).toBe(
    "내가 쓴 설명",
  );
  expect(
    screen.queryByText(/New settings are available\. Your edits are kept\./),
  ).toBeNull();
  expect(screen.queryByLabelText("Remove interest tag 학교")).toBeNull();
});

test("tag limits count Unicode characters and reject overlong tags without truncation", async () => {
  const input = props();
  const screen = await render(<MailInterestEditor {...input} />);
  const valid = "🤖".repeat(32);
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "#" + valid,
  );
  await fireEvent.press(screen.getByLabelText("Add tags"));
  expect(screen.getByLabelText("Remove interest tag " + valid)).toBeTruthy();
  const overlong = "🤖".repeat(33);
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "#" + overlong,
  );
  await fireEvent.press(screen.getByLabelText("Add tags"));

  expect(
    screen.getByText("Each tag can have up to 32 characters."),
  ).toBeTruthy();
  expect(screen.getByLabelText("Enter interest tags").props.value).toBe(
    "#" + overlong,
  );
  expect(screen.queryByLabelText("Remove interest tag " + overlong)).toBeNull();
  expect(input.onSave).not.toHaveBeenCalled();
});

test("tag count overflow never evicts existing interests and embedded # is not silently removed", async () => {
  const tags = Array.from({ length: 8 }, (_, index) => "태그" + index);
  const input = props({ profile: { tags, description: "", version: 1 } });
  const screen = await render(<MailInterestEditor {...input} />);
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "#추가",
  );
  await fireEvent.press(screen.getByLabelText("Add tags"));
  expect(
    screen.getByText(/Choose up to 8 tags\. Remove one to add another\./),
  ).toBeTruthy();
  for (const tag of tags)
    expect(screen.getByLabelText("Remove interest tag " + tag)).toBeTruthy();
  await fireEvent.changeText(
    screen.getByLabelText("Enter interest tags"),
    "C#",
  );
  await fireEvent.press(screen.getByLabelText("Add tags"));
  expect(
    screen.getByText("Separate tags with spaces, such as #school #careers."),
  ).toBeTruthy();
  expect(screen.getByLabelText("Enter interest tags").props.value).toBe("C#");
  expect(screen.queryByLabelText("Remove interest tag C")).toBeNull();
});

test("overlong descriptions stay intact but cannot be saved", async () => {
  const input = props();
  const screen = await render(<MailInterestEditor {...input} />);
  const description = "가".repeat(1001);
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    description,
  );

  expect(screen.getByLabelText("Describe mail interests").props.value).toBe(
    description,
  );
  expect(screen.getByText("Use up to 1,000 characters.")).toBeTruthy();
  const save = screen.getByRole("button", {
    name: "Start with these interests",
  });
  expect(save.props.accessibilityState.disabled).toBe(true);
  await fireEvent.press(save);
  expect(input.onSave).not.toHaveBeenCalled();
});

test("saving disables editing, removal, recommendation, reload and cancellation", async () => {
  const input = props({
    profile: { tags: ["학교"], description: "학교", version: 1 },
    recommendations: {
      status: "READY",
      tags: [{ tag: "취업", evidence_refs: ["title-1"] }],
      title_count: 1,
      error_code: null,
    },
    onCancel: jest.fn(),
  });
  const screen = await render(<MailInterestEditor {...input} />);
  await screen.rerender(
    <MailInterestEditor
      {...input}
      profile={{ ...input.profile, version: 2 }}
      saving
    />,
  );
  expect(screen.getByLabelText("Enter interest tags").props.editable).toBe(
    false,
  );
  expect(screen.getByLabelText("Describe mail interests").props.editable).toBe(
    false,
  );
  for (const name of [
    "Remove interest tag 학교",
    "Add tags",
    "Load latest settings",
    "Refresh suggestions",
    "Cancel",
    "Saving",
  ]) {
    expect(
      screen.getByRole("button", { name }).props.accessibilityState.disabled,
    ).toBe(true);
  }
  expect(
    screen.getByRole("checkbox", { name: "취업 suggested tag" }).props
      .accessibilityState.disabled,
  ).toBe(true);
});

test("a failed save preserves the draft and displays a truthful failure", async () => {
  const input = props({
    onSave: jest.fn().mockRejectedValue(new Error("save failed")),
  });
  const screen = await render(<MailInterestEditor {...input} />);
  await fireEvent.changeText(
    screen.getByLabelText("Describe mail interests"),
    "학교 소식",
  );
  await fireEvent.press(
    screen.getByRole("button", { name: "Start with these interests" }),
  );
  await waitFor(() =>
    expect(
      screen.getByText(/Could not save interests\. Your edits are kept\./),
    ).toBeTruthy(),
  );

  expect(screen.getByLabelText("Describe mail interests").props.value).toBe(
    "학교 소식",
  );
  expect(
    screen.getByRole("button", { name: "Start with these interests" }).props
      .accessibilityState.disabled,
  ).toBe(false);
});
