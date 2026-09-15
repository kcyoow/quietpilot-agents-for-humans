export type MailPreviewMessage = {
  id: string;
  sender: string;
  title: string;
  preview: string;
  body: string;
  timeLabel: string;
  topics: readonly string[];
};

export const MAIL_EXAMPLE_KEYWORDS: readonly string[] = [
  "학교",
  "취업",
  "프로젝트",
  "생활",
];

// Fictional messages for the frontend preview, never a real mailbox or AI result.
const PREVIEW_MAIL: readonly MailPreviewMessage[] = [
  {
    id: "example-school-courses",
    sender: "예시대학교 학사팀",
    title: "수강 신청 일정 안내",
    preview: "개설 강좌와 수강 신청 절차를 한곳에서 확인할 수 있어요.",
    body: "개설 강좌 목록과 수강 신청 절차를 안내합니다. 강의별 수업 방식과 수강 대상은 강좌 소개에서 확인할 수 있습니다.\n\n수강 계획을 세울 때 참고할 수 있도록 자주 묻는 질문도 함께 정리했습니다.",
    timeLabel: "오늘",
    topics: ["학교", "수업", "수강 신청"],
  },
  {
    id: "example-school-scholarships",
    sender: "예시대학교 학생지원팀",
    title: "교내 장학 제도 안내",
    preview: "성적·생활 지원 등 교내 장학금의 종류를 소개합니다.",
    body: "학교에서 운영하는 장학 제도의 종류와 기본 자격을 소개합니다. 성적 장학과 생활 지원 장학은 심사 기준이 서로 다릅니다.\n\n이번 메일은 제도를 알아볼 수 있는 안내 자료입니다. 개별 모집 일정과 제출 서류는 별도 공지에서 안내합니다.",
    timeLabel: "오늘",
    topics: ["학교", "장학금", "학생 지원"],
  },
  {
    id: "example-school-library",
    sender: "예시대학교 도서관",
    title: "도서관 조용한 열람 공간 안내",
    preview: "집중 열람실과 대화 가능한 공간의 차이를 안내해요.",
    body: "도서관에는 혼자 집중하기 좋은 열람 공간과 함께 이야기를 나눌 수 있는 협업 공간이 있습니다. 각 구역의 이용 방식은 입구 안내판에 표시되어 있습니다.\n\n소음에 민감한 이용자를 위한 조용한 구역도 운영합니다. 도서관을 둘러보기 전에 참고할 수 있는 공간 소개입니다.",
    timeLabel: "어제",
    topics: ["학교", "도서관", "열람실"],
  },
  {
    id: "example-career-stories",
    sender: "예시 커리어센터",
    title: "직무별 커리어 이야기",
    preview: "개발·기획·디자인 직무의 하루를 소개하는 인터뷰 모음이에요.",
    body: "여러 직무의 실무자가 일하는 방식과 팀에서 맡는 역할을 소개합니다. 개발, 기획, 디자인 분야의 인터뷰를 함께 담았습니다.\n\n취업과 진로를 고민할 때 직무를 비교해 볼 수 있는 읽을거리입니다. 관심 있는 분야의 이야기부터 살펴볼 수 있습니다.",
    timeLabel: "어제",
    topics: ["취업", "진로", "직무"],
  },
  {
    id: "example-career-resume",
    sender: "예시 취업지원실",
    title: "이력서 작성 가이드",
    preview: "경험을 읽기 쉽게 정리하는 방법과 예시를 담았어요.",
    body: "프로젝트 경험을 역할, 과정, 결과로 나누어 설명하는 이력서 작성 가이드입니다. 분량보다 구체적으로 전달하는 방법에 초점을 맞췄습니다.\n\n처음 이력서를 작성하는 사람도 참고할 수 있도록 짧은 예시와 점검 항목을 함께 정리했습니다.",
    timeLabel: "이틀 전",
    topics: ["취업", "이력서"],
  },
  {
    id: "example-project-notes",
    sender: "예시 AI 스터디",
    title: "AI 프로젝트 회의 메모",
    preview: "데이터 정리 방식과 다음 실험에서 살펴볼 질문을 공유했어요.",
    body: "이번 프로젝트 회의에서는 데이터 정리 방식과 실험 결과를 기록하는 형식을 이야기했습니다. 서로 다른 결과를 비교할 수 있도록 같은 항목을 남기기로 했습니다.\n\n메모에는 아직 결정하지 않은 질문도 함께 적었습니다. 다음 논의에서 참고할 수 있는 공유 자료입니다.",
    timeLabel: "오늘",
    topics: ["프로젝트", "AI", "스터디"],
  },
  {
    id: "example-project-design",
    sender: "예시 디자인 팀",
    title: "프로젝트 화면 구성 공유",
    preview: "탐색 화면의 흐름과 주요 문구에 대한 의견을 모았어요.",
    body: "프로젝트 탐색 화면의 구성과 문구 초안을 공유합니다. 처음 들어온 사용자가 어디서 시작할지 알 수 있도록 화면 흐름을 정리했습니다.\n\n현재 문서에는 검토 중인 의견과 확정된 내용을 구분해 적었습니다. 디자인 논의의 맥락을 확인할 수 있습니다.",
    timeLabel: "어제",
    topics: ["프로젝트", "디자인", "화면 구성"],
  },
  {
    id: "example-life-neighborhood",
    sender: "예시마을 소식",
    title: "동네 생활 공간 안내",
    preview: "산책 공간과 작은 도서관 등 가까운 생활 정보를 모았어요.",
    body: "동네에서 이용할 수 있는 산책 공간과 작은 도서관을 소개합니다. 각 공간의 특징과 이용할 때 참고할 정보를 모았습니다.\n\n생활에 필요한 장소를 천천히 알아볼 수 있는 소식입니다. 프로그램 신청 없이 읽어 볼 수 있는 지역 안내입니다.",
    timeLabel: "이틀 전",
    topics: ["생활", "동네", "산책"],
  },
];

function normalizeQuery(value: string) {
  return value.normalize("NFKC").trim().toLowerCase();
}

export function findPreviewMail(query: string): MailPreviewMessage[] {
  const normalized = normalizeQuery(query);
  if (!normalized) return [];

  return PREVIEW_MAIL.filter((message) =>
    [
      ...message.topics,
      message.sender,
      message.title,
      message.preview,
      message.body,
    ].some((value) => normalizeQuery(value).includes(normalized)),
  );
}
