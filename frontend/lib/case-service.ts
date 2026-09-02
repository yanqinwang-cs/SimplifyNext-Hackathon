import type { AwsCredentialStatus, CaseWorkspaceState, RunSummary, TraceEntry } from "./types";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (!response.ok) throw new Error((await response.json().catch(() => null))?.error ?? `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

export function getWorkspace(caseId: string): Promise<CaseWorkspaceState> {
  return request<CaseWorkspaceState>(`/api/cases/${encodeURIComponent(caseId)}/workspace`);
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
