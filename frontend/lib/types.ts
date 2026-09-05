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
  model_calls?: number;
  proposal_correction_calls?: number;
  clean_execution_retries?: number;
  correction_retries?: number;
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

export type ApprovedModel = "anthropic.claude-sonnet-4-5" | "anthropic.claude-opus-4-5";
export interface AwsCredentialStatus { mode: "default_chain" | "temporary_credentials"; statusLabel: string; lastUpdatedAt: string | null; region: string; }
export interface RuntimeModelUse { model: ApprovedModel; label: string; usedAt: string | null; outcome: "completed" | "failed"; }
export interface RuntimeRoleSettings { effectiveModel: ApprovedModel; effectiveLabel: string; source: "default" | "environment" | "runtime_selection"; lastUsed: RuntimeModelUse | null; noModelCallRequired?: boolean; }

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
  assessment: AssessmentSummary;
  chatHistory: { role: string; text: string }[];
  activity: { type: string; summary: string; createdAt: string }[];
}
export interface CaseListItem { caseId: string; title: string; }
export interface SampleCatalogItem { sampleId: string; title: string; }
export interface AssessmentSummary { state: "not_started" | "running" | "complete" | "stale" | "failed_no_report" | "failed_previous_report_retained" | "stopped"; activeRun: { runHandle: string; startedAt: string } | null; latestAttempt: { runHandle: string; state: string; startedAt: string; endedAt: string | null; message: string } | null; reportAvailable: boolean; reportStale: boolean; }

export interface AuditTraceEvent { event?: string; actor?: string; runtime_status?: string; attempt_number?: number; retry_mode?: string; failure_category?: string; technical_error_type?: string; error_type?: string; error?: string; model?: string | null; input_tokens?: number | null; output_tokens?: number | null; latency_seconds?: number | null; finish_reason?: string | null; step?: number; repairable?: boolean; }
export interface AuditTraceResponse { caseId: string; runHandle: string; outcome: string; model: { logicalModel: string | null }; counters: { modelCalls: number; proposalCorrectionCalls: number; cleanExecutionRetries: number }; failure: { category?: string; technicalType?: string; message?: string } | null; trace: AuditTraceEvent[]; }

export interface ReportSourceReference { sourceHandle: string; fileName: string; }
export interface ReportMaterial { statement: string; sources: ReportSourceReference[]; }
export interface ReportViolation { label: string; status: string; reasoningSummary: string; supportingMaterial: ReportMaterial[]; limitingMaterial: ReportMaterial[]; unresolvedPoints: string[]; }
export interface ReportStudent { sectionHandle: string; displayName: string; violations: ReportViolation[]; furthestConclusion: string; }
export interface ReportResponse {
  caseId: string;
  currentCaseName: string;
  reportState: "available" | "unavailable" | "historical_unavailable";
  assessmentIsStale: boolean;
  isLatestSuccessfulAssessment: boolean;
  message?: string;
  assessment: { runHandle: string; caseNameAtAssessment: string; completedAt: string; students: ReportStudent[] } | null;
  /** Legacy fields are accepted only so old mocked API responses remain type-safe. */
  title?: string;
  latestSuccessfulRun?: { completedAt?: string | null } | null;
  students?: Array<{ displayName: string; violations: ReportViolation[]; furthestConclusion: string }>;
}
export interface RuntimeSettings { aws: AwsCredentialStatus; models: { investigator: RuntimeRoleSettings; workspaceHelp: RuntimeRoleSettings }; availableModels: Array<{ model: ApprovedModel; label: string }>; }

export interface WorkspaceChatResponse {
  response: string;
  actions: string[];
  recovery: boolean;
}
