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

export interface VisibleSource {
  id: string;
  name: string;
  sourceType: string;
  content: string;
  contentPreview: string;
  metadata?: Record<string, unknown>;
}

export interface AssessmentSubject { subject_id: string; display_name: string; candidate_number?: string | null; metadata?: Record<string, unknown>; }
export interface SubjectRelationship { relationship_id: string; subject_ids: string[]; relationship_type: string; description?: string | null; source_ids?: string[]; metadata?: Record<string, unknown>; }
export interface AssessmentContext { assessment_id: string; title?: string | null; assessment_type?: string | null; venue?: string | null; start_time?: string | null; end_time?: string | null; metadata?: Record<string, unknown>; }
export interface GuidanceContext { current_case_revision: number; latest_assessment_revision?: number | null; assessment_is_stale: boolean; per_subject_assessments: Array<{ subject_id: string; violation_assessments: Array<{ violation_id: string; status: string; reasoning_summary: string; supporting_node_ids: string[]; mitigating_node_ids: string[]; unresolved_points: string[] }>; furthest_conclusion: { statement: string; confidence: string } }>; }

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
  trace_path?: string | null;
  vnext_status?: string | null;
  vnext_result_path?: string | null;
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
  runtimeStatus: "IDLE" | "RUNNING" | "COMPLETED" | "RUNNING_INVESTIGATOR" | "RUNNING_STEWARD" | "WAITING_FOR_EVIDENCE" | "FAILED" | "STOPPED" | "PAUSED";
  currentActor: "INVESTIGATOR" | "STEWARD" | "NONE";
  visibleSources: VisibleSource[];
  assessmentContext?: AssessmentContext | null;
  subjects?: AssessmentSubject[];
  relationships?: SubjectRelationship[];
  guidance?: GuidanceContext;
  lastError?: { failure_category?: string; message?: string; actor?: string; step?: number } | null;
  lastTraceStep?: number | null;
  lastUpdatedAt?: string;
  latestRun?: RunSummary | null;
  requestHistory?: EvidenceRequest[];
  runs?: RunSummary[];
  chatHistory?: { role: string; text: string }[];
  workspaceEvents?: WorkspaceEvent[];
}

export interface WorkspaceChatResponse {
  response: string;
  actions: string[];
  recovery: boolean;
}
