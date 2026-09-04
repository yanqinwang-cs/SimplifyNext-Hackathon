import type { AwsCredentialStatus, CaseListItem, CaseWorkspaceState, ReportResponse, RunSummary, TraceEntry, WorkspaceChatResponse } from "./types";
import type { RuntimeSettings } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.error ?? `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export function getWorkspace(caseId: string): Promise<CaseWorkspaceState> {
  return request<CaseWorkspaceState>(`/api/cases/${encodeURIComponent(caseId)}/workspace`);
}

export function getReport(caseId: string): Promise<ReportResponse> {
  return request<ReportResponse>(`/api/cases/${encodeURIComponent(caseId)}/report`);
}

export async function getCases(): Promise<CaseListItem[]> {
  return (await request<{ cases: CaseListItem[] }>("/api/cases")).cases;
}

export function createCase(payload: { title: string }) {
  return request<{ caseId: string; workspace: CaseWorkspaceState }>("/api/cases", { method: "POST", body: JSON.stringify(payload) });
}

export async function getProductGuide(): Promise<string> {
  const response = await fetch(`${API_BASE}/api/product-guide`);
  if (!response.ok) throw new Error(`Guide request failed (${response.status})`);
  return response.text();
}

export async function getTraces(caseId: string): Promise<TraceEntry[]> {
  const result = await request<{ traces: TraceEntry[] }>(`/api/cases/${encodeURIComponent(caseId)}/traces`);
  return result.traces;
}

export async function getRuns(caseId: string): Promise<RunSummary[]> {
  const result = await request<{ runs: RunSummary[] }>(`/api/cases/${encodeURIComponent(caseId)}/runs`);
  return result.runs;
}

export function runInvestigation(caseId: string): Promise<CaseWorkspaceState> {
  return request<CaseWorkspaceState>(`/api/cases/${encodeURIComponent(caseId)}/run`, { method: "POST", body: "{}" });
}

export function sendWorkspaceMessage(caseId: string, message: string): Promise<WorkspaceChatResponse> {
  return request<WorkspaceChatResponse>(`/api/cases/${encodeURIComponent(caseId)}/workspace/chat`, { method: "POST", body: JSON.stringify({ message }) });
}

export async function addSource(caseId: string, payload: { display_name: string; content: string; source_type: string; metadata?: Record<string, unknown>; assessment_scope: Record<string, unknown>; case_revision?: number }) {
  return request<{ source: import("./types").VisibleSource; workspace: CaseWorkspaceState }>(`/api/cases/${encodeURIComponent(caseId)}/sources`, { method: "POST", body: JSON.stringify(payload) });
}

export function addSubject(caseId: string, payload: { subject_id?: string; display_name: string; candidate_number?: string; case_revision?: number }) {
  return request<{ subject: import("./types").AssessmentSubject; workspace: CaseWorkspaceState }>(`/api/cases/${encodeURIComponent(caseId)}/subjects`, { method: "POST", body: JSON.stringify(payload) });
}

export function renameSubject(caseId: string, subjectId: string, displayName: string, caseRevision?: number) {
  return request<{ student: import("./types").AssessmentSubject; workspace: CaseWorkspaceState }>(`/api/cases/${encodeURIComponent(caseId)}/subjects/${encodeURIComponent(subjectId)}/rename`, { method: "POST", body: JSON.stringify({ display_name: displayName, case_revision: caseRevision }) });
}

export function removeSubject(caseId: string, subjectId: string, caseRevision?: number) {
  return request<CaseWorkspaceState>(`/api/cases/${encodeURIComponent(caseId)}/subjects/${encodeURIComponent(subjectId)}`, { method: "DELETE", body: JSON.stringify({ case_revision: caseRevision }) });
}

export function addRelationship(caseId: string, payload: { relationship_id: string; subject_ids: string[]; relationship_type: string; description?: string; case_revision?: number }) {
  return request<{ relationship: import("./types").SubjectRelationship; workspace: CaseWorkspaceState }>(`/api/cases/${encodeURIComponent(caseId)}/relationships`, { method: "POST", body: JSON.stringify(payload) });
}

export function openSample(sampleId: string, caseId = `${sampleId}-working`) {
  return request<{ caseId: string }>("/api/samples/open", { method: "POST", body: JSON.stringify({ sample_id: sampleId, case_id: caseId }) });
}

export function resetSample(sampleId: string, caseId = `${sampleId}-working`) {
  return request<{ caseId: string; workspace: CaseWorkspaceState }>("/api/samples/reset", { method: "POST", body: JSON.stringify({ sample_id: sampleId, case_id: caseId }) });
}

export async function provideEvidence(caseId: string, requestId: string, caseRevision: number | undefined, file: File | null, note: string) {
  const sources = file ? [{ display_name: file.name, content: await file.text(), metadata: { media_type: file.type || "application/octet-stream" } }] : [];
  return request<CaseWorkspaceState>(`/api/cases/${encodeURIComponent(caseId)}/evidence-requests/${encodeURIComponent(requestId)}/fulfil`, { method: "POST", body: JSON.stringify({ case_revision: caseRevision, sources, note }) });
}

export function markUnavailable(caseId: string, requestId: string, caseRevision: number | undefined, reason: string) {
  return request<CaseWorkspaceState>(`/api/cases/${encodeURIComponent(caseId)}/evidence-requests/${encodeURIComponent(requestId)}/unavailable`, { method: "POST", body: JSON.stringify({ case_revision: caseRevision, note: reason }) });
}

export function getAwsCredentialStatus(): Promise<AwsCredentialStatus> {
  return request<AwsCredentialStatus>("/api/debug/aws-credentials/status");
}

export function applyAwsCredentials(credentials: { aws_access_key_id: string; aws_secret_access_key: string; aws_session_token: string }): Promise<AwsCredentialStatus> {
  return request<AwsCredentialStatus>("/api/debug/aws-credentials", { method: "POST", body: JSON.stringify(credentials) });
}

export function clearAwsCredentials(): Promise<AwsCredentialStatus> {
  return request<AwsCredentialStatus>("/api/debug/aws-credentials", { method: "DELETE" });
}

export function getRuntimeSettings(): Promise<RuntimeSettings> { return request<RuntimeSettings>("/api/debug/runtime-settings"); }
export function applyModelOverrides(payload: Record<string, string | null>): Promise<RuntimeSettings> { return request<RuntimeSettings>("/api/debug/runtime-settings/models", { method: "POST", body: JSON.stringify(payload) }); }
export function resetModelOverrides(): Promise<RuntimeSettings> { return request<RuntimeSettings>("/api/debug/runtime-settings/models", { method: "DELETE" }); }
