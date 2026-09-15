import type { WorkspaceCase } from "@/src/workspace/types";
import { humanizeWorkTerms } from "@/src/workspace/plainLanguage";

export function humanEvidenceDetail(detail: string): string {
  if (/received_at|sender_domain|unix_ms/i.test(detail)) {
    return "Information verified through your connected service.";
  }
  return humanCopy(detail);
}

export function humanCopy(value: string): string {
  return humanizeWorkTerms(value)
    .replace(
      /fixture 근거를 결과 중심 계획으로 정리했어요\./gi,
      "Prepared from example data.",
    )
    .replace(/프로토타입 상태/gi, "Current tasks")
    .replace(/프로토타입\s+(.+?)\s+readback이/gi, "$1 results")
    .replace(/프로토타입\s+readback이/gi, "results")
    .replace(/readback이/gi, "results")
    .replace(/readback으로/gi, "using results")
    .replace(/fixture 근거/gi, "Example source")
    .replace(/API-visible/gi, "available from your connections")
    .replace(/readback/gi, "Results")
    .replace(/프로토타입/gi, "Example")
    .replace(/fixture/gi, "Example source")
    .replace(/\bcapabilit(?:y|ies)\b/gi, "Feature")
    .replace(/\bscopes?\b/gi, "Permissions")
    .replace(/\brevision\b/gi, "Record version")
    .replace(/확인된\s+확인(?:된)?\s+결과/g, "Results");
}

export function caseTypeLabel(type: WorkspaceCase["caseType"]) {
  return {
    CONNECTED_SIGNAL: "From connections",
    DIRECT_DELEGATION: "Direct requests",
    EXCEPTION_APPROVAL: "Refresh needed",
    ROUTINE_DISCOVERY: "Recurring information",
  }[type];
}
