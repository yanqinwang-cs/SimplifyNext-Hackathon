import type { AssessmentSummary, ApprovedModel, AuditTraceResponse, AwsCredentialStatus, CaseListItem, CaseWorkspaceState, PublicSource, PublicSourceDocument, ReportResponse, RunSummary, RuntimeSettings, SampleCatalogItem, TraceEntry, WorkspaceChatResponse } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.error ?? `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export function getWorkspace(caseId: string, signal?: AbortSignal): Promise<CaseWorkspaceState> {
  return request<CaseWorkspaceState>(`/api/cases/${encodeURIComponent(caseId)}/workspace`, { signal });
}
export function getSourceDocument(caseId: string, sourceHandle: string): Promise<PublicSourceDocument> { return request<PublicSourceDocument>(`/api/cases/${encodeURIComponent(caseId)}/sources/${encodeURIComponent(sourceHandle)}`); }

export function getReport(caseId: string, assessment?: string, signal?: AbortSignal): Promise<ReportResponse> {
  const query = assessment ? `?assessment=${encodeURIComponent(assessment)}` : "";
  return request<ReportResponse>(`/api/cases/${encodeURIComponent(caseId)}/report${query}`, { signal });
}

export function getHistoricalSourceDocument(caseId: string, assessment: string, sourceHandle: string): Promise<PublicSourceDocument & { source: PublicSourceDocument["source"] & { assessmentDate?: string | null } }> {
  return request<PublicSourceDocument & { source: PublicSourceDocument["source"] & { assessmentDate?: string | null } }>(`/api/cases/${encodeURIComponent(caseId)}/assessment-runs/${encodeURIComponent(assessment)}/sources/${encodeURIComponent(sourceHandle)}`);
}

export async function getCases(): Promise<CaseListItem[]> {
  return (await request<{ cases: CaseListItem[] }>("/api/cases")).cases;
}
export async function getSamples(): Promise<SampleCatalogItem[]> { return (await request<{ samples: SampleCatalogItem[] }>("/api/samples")).samples; }

export function createCase(payload: { title: string }) {
  return request<{ caseId: string; workspace: CaseWorkspaceState }>("/api/cases", { method: "POST", body: JSON.stringify(payload) });
}

export function updateCaseTitle(caseId: string, title: string) {
  return request<{ workspace: CaseWorkspaceState }>(`/api/cases/${encodeURIComponent(caseId)}`, { method: "PATCH", body: JSON.stringify({ title }) });
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
export function getAssessmentRun(caseId: string, runHandle: string, signal?: AbortSignal): Promise<{ runHandle: string; state: string; startedAt: string; endedAt: string | null; message: string; reportAvailable: boolean; reportStale: boolean; workspace: CaseWorkspaceState }> { return request<{ runHandle: string; state: string; startedAt: string; endedAt: string | null; message: string; reportAvailable: boolean; reportStale: boolean; workspace: CaseWorkspaceState }>(`/api/cases/${encodeURIComponent(caseId)}/assessment-runs/${encodeURIComponent(runHandle)}`, { signal }); }
export function getAuditTrace(caseId: string, runHandle: string): Promise<AuditTraceResponse> { return request<AuditTraceResponse>(`/api/cases/${encodeURIComponent(caseId)}/assessment-runs/${encodeURIComponent(runHandle)}/audit-trace`); }
export function getAuditTraceDownloadUrl(caseId: string, runHandle: string): string { return `${API_BASE}/api/cases/${encodeURIComponent(caseId)}/assessment-runs/${encodeURIComponent(runHandle)}/audit-trace/download`; }
export async function downloadAuditTrace(caseId: string, runHandle: string): Promise<void> {
  const response = await fetch(getAuditTraceDownloadUrl(caseId, runHandle));
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.error ?? `Request failed (${response.status})`);
  const blobUrl = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = `caselens-${caseId}-${runHandle}-audit-trace.jsonl`;
  link.click();
  URL.revokeObjectURL(blobUrl);
}

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

export function getAwsCredentialStatus(): Promise<AwsCredentialStatus> { return request<RuntimeSettings>("/api/runtime-settings").then((settings) => settings.aws); }

export function applyAwsCredentials(credentials: { aws_access_key_id: string; aws_secret_access_key: string; aws_session_token: string }): Promise<AwsCredentialStatus> { return request<RuntimeSettings>("/api/runtime-settings/aws-credentials", { method: "POST", body: JSON.stringify(credentials) }).then((settings) => settings.aws); }

export function clearAwsCredentials(): Promise<AwsCredentialStatus> { return request<RuntimeSettings>("/api/runtime-settings/aws-credentials", { method: "DELETE" }).then((settings) => settings.aws); }

export function getRuntimeSettings(caseId?: string): Promise<RuntimeSettings> { return request<RuntimeSettings>(`/api/runtime-settings${caseId ? `?caseId=${encodeURIComponent(caseId)}` : ""}`); }
export function applyModelOverrides(payload: { investigator: ApprovedModel; workspaceHelp: ApprovedModel }): Promise<RuntimeSettings> { return request<RuntimeSettings>("/api/runtime-settings/models", { method: "POST", body: JSON.stringify(payload) }); }
export function resetModelOverrides(): Promise<RuntimeSettings> { return request<RuntimeSettings>("/api/runtime-settings/models/reset", { method: "POST", body: "{}" }); }
