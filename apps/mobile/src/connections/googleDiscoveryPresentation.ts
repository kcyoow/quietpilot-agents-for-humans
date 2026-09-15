import { formatConnectionTime } from "@/src/connections/googleConnectionView";
import type { PrototypeConnection } from "@/src/prototype/types";
import type { WorkspaceStatus } from "@/src/workspace/types";

export type GoogleDiscoveryKind =
  | "NO_CONNECTION"
  | "CHECKING_CONNECTION"
  | "LINKING"
  | "SCANNING"
  | "INCOMPATIBLE_SERVER"
  | "REANALYSIS_REQUIRED"
  | "LOADING_RESULTS"
  | "HAS_WORK"
  | "NO_WORK"
  | "ERROR"
  | "DISCONNECTING";

export type GoogleDiscoveryPresentation = {
  badge: string;
  body: string;
  kind: GoogleDiscoveryKind;
  progress: number | null;
  retryable: boolean;
  title: string;
  tone: "accent" | "success" | "warning" | "muted";
};

export function googleDiscoveryPresentation({
  candidateCount,
  connection,
  connectionChecking,
  connectionError,
  workspaceError,
  workspaceStatus,
}: {
  candidateCount: number;
  connection: PrototypeConnection;
  connectionChecking: boolean;
  connectionError: string | null;
  workspaceError: string | null;
  workspaceStatus: WorkspaceStatus;
}): GoogleDiscoveryPresentation {
  if (connection.status === "CONNECTING") {
    return {
      badge: "Connecting",
      body: "Finish connecting your account in Google.",
      kind: "LINKING",
      progress: null,
      retryable: false,
      title: "Connecting Google",
      tone: "accent",
    };
  }

  if (connection.status === "SCANNING") {
    return scanningPresentation(connection);
  }

  if (connection.status === "REVOKING") {
    return {
      badge: "Disconnecting",
      body: "Removing Google access.",
      kind: "DISCONNECTING",
      progress: null,
      retryable: false,
      title: "Disconnecting Google",
      tone: "muted",
    };
  }

  if (connection.status === "DISCONNECTED") {
    if (connectionChecking) {
      return {
        badge: "Checking",
        body: "Checking connection status.",
        kind: "CHECKING_CONNECTION",
        progress: null,
        retryable: false,
        title: "Checking Google connection",
        tone: "accent",
      };
    }
    if (connectionError) {
      return errorPresentation(false);
    }
    return {
      badge: "Not connected",
      body: "Connect Gmail to find appointments, deadlines and messages needing a reply.",
      kind: "NO_CONNECTION",
      progress: null,
      retryable: false,
      title: "Google not connected",
      tone: "muted",
    };
  }

  if (
    connection.status === "ERROR" &&
    connection.error === "DISCOVERY_INCOMPLETE"
  ) {
    return {
      badge: "Partially checked",
      body: "Some mail was not checked. Retry to finish.",
      kind: "ERROR",
      progress: connection.scanProgress,
      retryable: true,
      title: "Some mail could not be checked",
      tone: "warning",
    };
  }

  if (connection.status === "ERROR" || connectionError) {
    return errorPresentation(true);
  }

  if (
    connection.status === "CONNECTED" &&
    (connection.scanProgress < 100 || !connection.lastCheckedAt)
  ) {
    return {
      badge: "Scan interrupted",
      body: "Your connection is active. Retry the scan.",
      kind: "REANALYSIS_REQUIRED",
      progress: connection.scanProgress,
      retryable: true,
      title: "Mail scan interrupted",
      tone: "warning",
    };
  }

  if (connection.scanProgress < 100 || !connection.lastCheckedAt) {
    return scanningPresentation(connection);
  }

  if (connection.discoveryRevision === null) {
    return {
      badge: "Scan error",
      body: "Connected, but these results cannot be displayed. Refresh or update the app.",
      kind: "INCOMPATIBLE_SERVER",
      progress: null,
      retryable: false,
      title: "Google results unavailable",
      tone: "warning",
    };
  }

  if (connection.discoveryRevision < 1) {
    return {
      badge: "Refresh needed",
      body: "These results use older criteria. Scan recent mail again.",
      kind: "REANALYSIS_REQUIRED",
      progress: null,
      retryable: true,
      title: "Scan mail again",
      tone: "warning",
    };
  }

  if (workspaceStatus === "booting") {
    return {
      badge: "Organizing",
      body: "Mail checked. Loading suggestions.",
      kind: "LOADING_RESULTS",
      progress: null,
      retryable: false,
      title: "Organizing Google results",
      tone: "accent",
    };
  }

  if (workspaceError) {
    return errorPresentation(true);
  }

  const checkedAt = formatConnectionTime(connection.lastCheckedAt);
  if (candidateCount > 0) {
    return {
      badge: `${candidateCount} items`,
      body: `${checkedAt} is the latest mail checked for these suggestions.`,
      kind: "HAS_WORK",
      progress: null,
      retryable: false,
      title: `Tasks from Google ${candidateCount} items`,
      tone: "success",
    };
  }

  return {
    badge: "Checked",
    body: `Last ${connection.lookbackDays} days of Gmail checked ${checkedAt}; new mail will be checked automatically.`,
    kind: "NO_WORK",
    progress: null,
    retryable: false,
    title: "No new tasks from Google",
    tone: "muted",
  };
}

function scanningPresentation(
  connection: PrototypeConnection,
): GoogleDiscoveryPresentation {
  const progress = Math.max(0, Math.min(100, connection.scanProgress));
  return {
    badge: progress > 0 ? `${progress}%` : "Checking",
    body: `Last ${connection.lookbackDays} days of Gmail are being checked for events, deadlines and replies.`,
    kind: "SCANNING",
    progress,
    retryable: false,
    title: "Checking Gmail",
    tone: "accent",
  };
}

function errorPresentation(connected: boolean): GoogleDiscoveryPresentation {
  return {
    badge: "Needs attention",
    body: connected
      ? "Connected. Refresh the results."
      : "Could not load Google connection status. Please refresh.",
    kind: "ERROR",
    progress: null,
    retryable: true,
    title: connected
      ? "Could not load Google results"
      : "Could not verify Google connection",
    tone: "warning",
  };
}
