export type WorkspaceStatus = "processing" | "waiting_for_evidence" | "ready" | "stopped" | "running" | "completed" | "failed";
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

export interface PublicSource { sourceHandle: string; fileName: string; documentFormat: string; }
export interface PublicSourceDocument { caseId: string; source: PublicSource & { content: string }; }

export interface AssessmentSubject { studentHandle: string; displayName: string; candidateNumber?: string | null; }
export interface GuidanceMaterial { statement: string; source_labels?: string[]; }

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
  outcome_type?: string;
  originating_actor?: string;
  start_revision?: number;
  final_committed_revision?: number;
  pending_request_id?: string | null;
  request_text?: string | null;
  vnext_status?: string | null;
  vnext_furthest_conclusion?: string | null;
  vnext_subject_conclusions?: Record<string, string> | null;
  model?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  latency_seconds?: number | null;
  finish_reason?: string | null;
}

export interface WorkspaceEvent {
  event_id: string;
  type: string;
  human_summary: string;
  created_at?: string;
  run_id?: string | null;
  request_id?: string | null;
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
  caseKind: "user" | "sample";
  sample: { sampleId: string; title: string } | null;
  capabilities: { editStudents: boolean; addEvidence: boolean; resetSample: boolean; runAssessment: boolean; useHelp: boolean; viewSources: boolean };
  preloadedSourceCount: number;
  runtimeStatus: "IDLE" | "RUNNING" | "COMPLETED" | "RUNNING_INVESTIGATOR" | "RUNNING_STEWARD" | "WAITING_FOR_EVIDENCE" | "FAILED" | "STOPPED" | "PAUSED";
  currentActor: "INVESTIGATOR" | "STEWARD" | "NONE";
  sources: PublicSource[];
  students: AssessmentSubject[];
  report: { state: string; assessmentIsStale: boolean };
  chatHistory: { role: string; text: string }[];
  activity: { type: string; summary: string; createdAt: string }[];
}
export interface CaseListItem { caseId: string; title: string; }
export interface SampleCatalogItem { sampleId: string; title: string; }
export interface AssessmentSummary { state: "not_started" | "running" | "complete" | "stale" | "failed_no_report" | "failed_previous_report_retained" | "stopped"; activeRun: { runHandle: string; startedAt: string } | null; latestAttempt: { runHandle: string; state: string; startedAt: string; endedAt: string | null; message: string } | null; reportAvailable: boolean; reportStale: boolean; }

export interface ReportMaterial { statement: string; sourceLabels: string[]; }
export interface ReportViolation { label: string; status: string; reasoningSummary: string; supportingMaterial: ReportMaterial[]; limitingMaterial: ReportMaterial[]; }
export interface ReportResponse { caseId: string; title: string; reportState: "available" | "unavailable"; assessmentIsStale: boolean; latestSuccessfulRun: { completedAt?: string | null } | null; students: Array<{ displayName: string; violations: ReportViolation[]; furthestConclusion: string }>; }
export interface RuntimeSettings { aws: { mode: "default_chain" | "temporary_override" | "unavailable" }; models: Record<string, { effective: string; override: string | null; active_in_vnext?: boolean }>; available_models: Array<{ name: string; label: string }>; }

export interface WorkspaceChatResponse {
  response: string;
  actions: string[];
  recovery: boolean;
}
