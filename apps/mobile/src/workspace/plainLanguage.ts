const legacySystemCopy: Record<string, string> = {
  "확인된 프로토타입 결과를 기록했어요.": "Recorded the example result.",
  "제목 없는 Gmail 메일": "Untitled Gmail message",
  "승인한 일정이 Calendar에 저장된 것을 확인했어요.":
    "Verified that the approved event was saved to Calendar.",
  "승인한 일정을 등록하고 결과를 확인하고 있어요.":
    "Saving the approved event and checking the result.",
  "선택한 메일에 다시 접근하지 못했어요. 연결과 원문을 확인해 주세요.":
    "Could not reopen the selected email. Check the connection and source.",
  "추가로 준비할 작업은 없어요. 판단 근거를 확인할 수 있어요.":
    "No further preparation is needed. You can review the reason.",
  "새 메일을 바탕으로 필요한 작업을 준비하고 있어요.":
    "Preparing a task from the new email.",
  "새 마감 메일 준비": "Prepare new deadline mail",
  "최신 상태를 확인하고 있어요.": "Checking the latest status.",
  "새 근거에서 이 Case를 준비했어요.": "Prepared from new source information.",
  "근거와 실행 범위를 검토하는 중이에요.":
    "Reviewing sources and the action scope.",
  "계획 준비 완료": "Plan prepared",
  "로컬 준비 완료": "Preparation complete",
  "추가 작업 없음": "No further action",
  "일정 결과 확인": "Calendar result verified",
  "일정 등록": "Create Calendar event",
  "Google Calendar에 일정 등록": "Create Google Calendar event",
  "알림 내용 준비": "Prepare reminder",
  "답장 초안 준비": "Draft reply",
  "할 일과 체크 항목 준비": "Prepare checklist",
};

export function systemCopy(value: string): string {
  return legacySystemCopy[value] ?? value;
}

export function humanizeWorkTerms(value: string): string {
  const copy = systemCopy(value);
  if (/\bCase\b(를|가|는|와|로|랑)/.test(copy)) {
    const particles: Record<string, string> = {
      를: "을",
      가: "이",
      는: "은",
      와: "과",
      로: "으로",
      랑: "이랑",
    };
    return copy.replace(
      /\bCase\b(를|가|는|와|로|랑)?/g,
      (_match, particle: string = "") =>
        "작업" + (particles[particle] ?? particle),
    );
  }
  return copy.replace(/\bCase\b/g, "task");
}
