import type {
  PrototypeCandidate,
  PrototypeCandidateGroup,
  PrototypeCase,
  PrototypeSuppressionRule,
} from "@/src/prototype/types";

export type WorkspaceDataSource = "LIVE" | "SCENARIO";

export type WorkspaceCandidate = PrototypeCandidate & {
  dataSource: WorkspaceDataSource;
  mailProfileVersion?: number | null;
  mailScanId?: string | null;
  mailDerived?: boolean;
};

export type WorkspaceCandidateGroup = PrototypeCandidateGroup & {
  dataSource: WorkspaceDataSource;
};

export type WorkspaceCase = PrototypeCase & {
  dataSource: WorkspaceDataSource;
};

export type WorkspaceSnapshot = {
  candidateGroups: WorkspaceCandidateGroup[];
  candidates: WorkspaceCandidate[];
  cases: WorkspaceCase[];
  dataSource: WorkspaceDataSource;
  loadedScenario: string | null;
};

export type WorkspaceStatus = "booting" | "ready" | "updating";

export type WorkspaceSuppression = PrototypeSuppressionRule;
