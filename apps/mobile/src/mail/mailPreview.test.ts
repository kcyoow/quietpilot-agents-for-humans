import { findPreviewMail, MAIL_EXAMPLE_KEYWORDS } from "@/src/mail/mailPreview";

test("the school keyword includes school information without an action gate", () => {
  const messages = findPreviewMail("학교");

  expect(messages.map((message) => message.id)).toEqual([
    "example-school-courses",
    "example-school-scholarships",
    "example-school-library",
  ]);
  expect(
    messages.find((message) => message.id === "example-school-library"),
  ).toMatchObject({
    title: "도서관 조용한 열람 공간 안내",
    topics: expect.arrayContaining(["학교", "도서관"]),
  });
  expect(
    messages.every((message) => message.body.split("\n\n").length >= 2),
  ).toBe(true);
});

test.each(["", "   ", "\n\t", "전혀없는키워드", "학교 소식 중 광고는 제외"])(
  "an empty or unmatched query %p does not expose every preview message",
  (query) => {
    expect(findPreviewMail(query)).toEqual([]);
  },
);

test("matches the complete query lexically after NFKC and case normalization", () => {
  expect(findPreviewMail("  학교  ")).toEqual(findPreviewMail("학교"));
  expect(findPreviewMail("ａｉ")).toEqual(findPreviewMail("AI"));
  expect(findPreviewMail("ai").map((message) => message.id)).toEqual([
    "example-project-notes",
  ]);
  expect(findPreviewMail("조용한 열람").map((message) => message.id)).toEqual([
    "example-school-library",
  ]);
  expect(
    findPreviewMail("예시대학교 학사팀").map((message) => message.id),
  ).toEqual(["example-school-courses"]);
});

test("example keywords have fictional messages and stable ordering", () => {
  expect(MAIL_EXAMPLE_KEYWORDS).toEqual(["학교", "취업", "프로젝트", "생활"]);
  for (const keyword of MAIL_EXAMPLE_KEYWORDS) {
    const messages = findPreviewMail(keyword);
    expect(messages.length).toBeGreaterThan(0);
    expect(messages.every((message) => message.sender.startsWith("예시"))).toBe(
      true,
    );
    expect(findPreviewMail(keyword)).toEqual(messages);
  }
});
