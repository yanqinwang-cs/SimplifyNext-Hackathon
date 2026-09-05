import type { AssessmentSummary, AwsCredentialStatus, CaseListItem, CaseWorkspaceState, PublicSource, PublicSourceDocument, ReportResponse, RunSummary, RuntimeSettings, SampleCatalogItem, TraceEntry, WorkspaceChatResponse } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.error ?? `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export function getWorkspace(caseId: string): Promise<CaseWorkspaceState> {
  return request<CaseWorkspaceState>(`/api/cases/${encodeURIComponent(caseId)}/workspace`);
}
export function getSourceDocument(caseId: string, sourceHandle: string): Promise<PublicSourceDocument> { return request<PublicSourceDocument>(`/api/cases/${encodeURIComponent(caseId)}/sources/${encodeURIComponent(sourceHandle)}`); }

export function getReport(caseId: string): Promise<ReportResponse> {
  return request<ReportResponse>(`/api/cases/${encodeURIComponent(caseId)}/report`);
}

export async function getCases(): Promise<CaseListItem[]> {
  return (await request<{ cases: CaseListItem[] }>("/api/cases")).cases;
}
export async function getSamples(): Promise<SampleCatalogItem[]> { return (await request<{ samples: SampleCatalogItem[] }>("/api/samples")).samples; }

export function createCase(payload: { title: string }) {
  return request<{ caseId: string; workspace: CaseWorkspaceState }>("/api/cases", { method: "POST", body: JSON.stringify(payload) });
}

export async function getProductGuide(): Promise<string> {
  const response = await fetch(`${API_BASE}/api/product-guide`);
  if (!response.ok) throw new Error(`Guide request failed (${response.status})`);
  return response.text();
}

export async function getRuns(caseId: string): Promise<RunSummary[]> {
  const result = await request<{ runs: RunSummary[] }>(`/api/cases/${encodeURIComponent(caseId)}/runs`);
  return result.runs;
}

export async function getTraces(caseId: string): Promise<TraceEntry[]> {
  const result = await request<{ traces: TraceEntry[] }>(`/api/cases/${encodeURIComponent(caseId)}/traces`);
  return result.traces;
}

export function runInvestigation(caseId: string): Promise<{ run: { runHandle: string; state: string; startedAt: string }; workspace: CaseWorkspaceState }> {
  return request<{ run: { runHandle: string; state: string; startedAt: string }; workspace: CaseWorkspaceState }>(`/api/cases/${encodeURIComponent(caseId)}/run`, { method: "POST", body: "{}" });
}
export function getAssessmentRun(caseId: string, runHandle: string): Promise<{ runHandle: string; state: string; startedAt: string; endedAt: string | null; message: string; reportAvailable: boolean; reportStale: boolean; workspace: CaseWorkspaceState }> { return request(`/api/cases/${encodeURIComponent(caseId)}/assessment-runs/${encodeURIComponent(runHandle)}`); }

export function sendWorkspaceMessage(caseId: string, message: string): Promise<WorkspaceChatResponse> {
  return request<WorkspaceChatResponse>(`/api/cases/${encodeURIComponent(caseId)}/workspace/chat`, { method: "POST", body: JSON.stringify({ message }) });
}

export async function addSource(caseId: string, payload: { fileName: string; content: string; mediaType?: string; caseRevision?: number }) {
  return request<{ source: PublicSource; workspace: CaseWorkspaceState }>(`/api/cases/${encodeURIComponent(caseId)}/sources`, { method: "POST", body: JSON.stringify(payload) });
}

export function addSubject(caseId: string, payload: { displayName: string; caseRevision?: number }) {
  return request<{ student: import("./types").AssessmentSubject; workspace: CaseWorkspaceState }>(`/api/cases/${encodeURIComponent(caseId)}/students`, { method: "POST", body: JSON.stringify(payload) });
}

export function renameSubject(caseId: string, studentHandle: string, displayName: string, caseRevision?: number) {
  return request<{ student: import("./types").AssessmentSubject; workspace: CaseWorkspaceState }>(`/api/cases/${encodeURIComponent(caseId)}/students/${encodeURIComponent(studentHandle)}/rename`, { method: "POST", body: JSON.stringify({ displayName, caseRevision }) });
}

export function removeSubject(caseId: string, studentHandle: string, caseRevision?: number) {
  return request<CaseWorkspaceState>(`/api/cases/${encodeURIComponent(caseId)}/students/${encodeURIComponent(studentHandle)}`, { method: "DELETE", body: JSON.stringify({ caseRevision }) });
}

export function openSample(sampleId: string) {
  return request<{ caseId: string }>("/api/samples/open", { method: "POST", body: JSON.stringify({ sampleId }) });
}

export function resetSample(sampleId: string) {
  return request<{ caseId: string; workspace: CaseWorkspaceState }>("/api/samples/reset", { method: "POST", body: JSON.stringify({ sampleId }) });
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
