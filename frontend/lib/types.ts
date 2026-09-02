export type WorkspaceStatus = "processing" | "waiting_for_evidence" | "ready" | "stopped";
export type EvidenceRequestStatus = "pending" | "fulfilled" | "unavailable";

export interface EvidenceRequest {
  request_id: string;
  informationSought: string;
  reason: string;
  status: EvidenceRequestStatus;
  originating_run_id?: string | null;
  originating_actor?: string | null;
  resumed_run_id?: string | null;
}

export interface VisibleSource {
  id: string;
  name: string;
  sourceType: string;
  content: string;
  contentPreview: string;
}

export interface TraceEntry {
  step?: number;
  actor?: string;
  case_revision?: number;
  failure_category?: string;
  [key: string]: unknown;
}

export interface RunSummary {
  run_id: string;
  started_at?: string;
  ended_at?: string | null;
  termination_reason?: string | null;
  final_runtime_status?: string;
  final_error?: { failure_category?: string; message?: string } | null;
}

export interface AwsCredentialStatus {
  override_active: boolean;
  credential_source: string;
  last_updated_at: string | null;
  region: string;
}

export interface WorkspaceMessage {
  id: string;
  role: "investigator" | "simplifynext";
  timestamp?: string;
  text?: string;
  request?: EvidenceRequest;
}

export interface CaseWorkspaceState {
  caseId: string;
  caseRevision?: number;
  title: string;
  status: WorkspaceStatus;
  institutionalStatus: "Investigating" | "Paused" | "Closed";
  currentFocus: string;
  messages: WorkspaceMessage[];
  caseStatus: "ACTIVE" | "HANDED_OFF" | "CLOSED";
  runtimeStatus: "IDLE" | "RUNNING_INVESTIGATOR" | "RUNNING_STEWARD" | "WAITING_FOR_EVIDENCE" | "FAILED" | "STOPPED" | "PAUSED";
  currentActor: "INVESTIGATOR" | "STEWARD" | "NONE";
  visibleSources: VisibleSource[];
  lastError?: { failure_category?: string; message?: string; actor?: string; step?: number } | null;
  lastTraceStep?: number | null;
  lastUpdatedAt?: string;
  latestRun?: RunSummary | null;
  requestHistory?: EvidenceRequest[];
  runs?: RunSummary[];
}
