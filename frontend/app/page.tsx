"use client";

import { useEffect, useMemo, useState } from "react";
import { applyAwsCredentials, clearAwsCredentials, getAwsCredentialStatus, getWorkspace, markUnavailable, provideEvidence, runInvestigation, sendWorkspaceMessage } from "../lib/case-service";
import type { AwsCredentialStatus, CaseWorkspaceState, EvidenceRequest, WorkspaceMessage } from "../lib/types";

type Dialog = "evidence" | "unavailable" | "sources" | "details" | null;

function Avatar({ role }: { role: WorkspaceMessage["role"] }) {
  return <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-sm font-bold ${role === "simplifynext" ? "bg-blue-700 text-white" : "bg-slate-600 text-white"}`}>{role === "simplifynext" ? "✦" : "IR"}</span>;
}

function DialogShell({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return <div className="fixed inset-0 z-10 flex items-center justify-center bg-slate-950/30 p-4" role="presentation" onMouseDown={onClose}>
    <section aria-modal="true" role="dialog" aria-labelledby="dialog-title" className="w-full max-w-lg rounded-xl border border-slate-200 bg-white p-6 shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
      <div className="mb-5 flex items-start justify-between gap-4"><h2 id="dialog-title" className="text-xl font-bold text-slate-900">{title}</h2><button onClick={onClose} aria-label="Close dialog" className="text-2xl leading-none text-slate-500 hover:text-slate-900">×</button></div>
      {children}
    </section>
  </div>;
}

function RequestCard({ request, disabled, onEvidence, onUnavailable }: { request: EvidenceRequest; disabled: boolean; onEvidence: () => void; onUnavailable: () => void }) {
  return <div className="rounded-xl border border-slate-300 bg-white p-5 shadow-sm">
    <div className="mb-3 flex items-center gap-2 text-sm font-bold text-blue-700"><span aria-hidden="true">▣</span> Information requested</div>
    <p className="text-base leading-7 text-slate-800">{request.informationSought}</p>
    <div className="my-4 border-t border-slate-200" />
    <div className="mb-2 flex items-center gap-2 text-sm font-bold text-blue-700"><span aria-hidden="true">♡</span> Why this matters</div>
    <p className="text-sm leading-6 text-slate-600">{request.reason}</p>
    <div className="mt-5 flex flex-wrap gap-3">
      <button disabled={disabled} onClick={onEvidence} className="rounded-lg bg-blue-700 px-4 py-2.5 text-sm font-bold text-white shadow-sm hover:bg-blue-800 disabled:cursor-not-allowed disabled:opacity-50">Provide evidence</button>
      <button disabled={disabled} onClick={onUnavailable} className="rounded-lg border border-blue-700 bg-white px-4 py-2.5 text-sm font-bold text-blue-800 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50">Mark unavailable</button>
    </div>
  </div>;
}

function runtimeCopy(status: CaseWorkspaceState["runtimeStatus"]) {
  const copy = {
    RUNNING_INVESTIGATOR: ["Investigator is running…", "The current investigator turn is in progress."],
    RUNNING_STEWARD: ["Case Steward is running…", "Global case reassessment is in progress."],
    WAITING_FOR_EVIDENCE: ["Investigator needs information from you.", "Use Provide evidence or Mark unavailable."],
    FAILED: ["Investigation stopped because of a system error.", "Agent is not running because the last turn failed."],
    STOPPED: ["Autonomous investigation is not running.", "Autonomous run has stopped."],
    PAUSED: ["Investigation is paused.", "The case is not currently running."],
    IDLE: ["Agent is not currently running.", "The case remains active and can be resumed."],
  } as const;
  return copy[status];
}

export default function Home() {
  const [workspace, setWorkspace] = useState<CaseWorkspaceState | null>(null);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [awsStatus, setAwsStatus] = useState<AwsCredentialStatus | null>(null);
  const [accessKey, setAccessKey] = useState("");
  const [secretKey, setSecretKey] = useState("");
  const [sessionToken, setSessionToken] = useState("");
  const [credentialMessage, setCredentialMessage] = useState<string | null>(null);
  const [credentialApplying, setCredentialApplying] = useState(false);
  const [chatMessage, setChatMessage] = useState("");
  const [chatSending, setChatSending] = useState(false);
  const request = useMemo(() => workspace?.messages.find((message) => message.request)?.request, [workspace]);
  const runtime = workspace ? runtimeCopy(workspace.runtimeStatus) : null;

  useEffect(() => {
    getWorkspace("case-01").then(setWorkspace).catch((error: unknown) => setError(error instanceof Error ? error.message : "Could not load the case."));
  }, []);

  useEffect(() => {
    getAwsCredentialStatus().then(setAwsStatus).catch(() => setAwsStatus(null));
  }, []);

  useEffect(() => {
    if (!workspace || !workspace.runtimeStatus.startsWith("RUNNING_")) return;
    const timer = window.setInterval(() => { getWorkspace(workspace.caseId).then(setWorkspace).catch((pollError: unknown) => setError(pollError instanceof Error ? pollError.message : "Could not refresh the workspace.")); }, 2000);
    return () => window.clearInterval(timer);
  }, [workspace?.caseId, workspace?.runtimeStatus]);

  async function submitEvidence() {
    if (!workspace || !request) return;
    const result = await provideEvidence(workspace.caseId, request.request_id, workspace.caseRevision, file, note);
    setWorkspace(result);
    setDialog(null); setNote(""); setFile(null);
  }

  async function submitUnavailable() {
    if (!workspace || !request) return;
    const result = await markUnavailable(workspace.caseId, request.request_id, workspace.caseRevision, reason);
    setWorkspace(result);
    setDialog(null); setReason("");
  }

  async function startInvestigation() {
    if (!workspace || starting) return;
    setStarting(true); setError(null);
    try { setWorkspace(await runInvestigation(workspace.caseId)); } catch (runError: unknown) { setError(runError instanceof Error ? runError.message : "Could not start the investigation."); }
    finally { setStarting(false); }
  }

  async function sendMessage() {
    if (!workspace || !chatMessage.trim() || chatSending) return;
    setChatSending(true); setError(null);
    try { await sendWorkspaceMessage(workspace.caseId, chatMessage.trim()); setWorkspace(await getWorkspace(workspace.caseId)); setChatMessage(""); }
    catch (chatError: unknown) { setError(chatError instanceof Error ? chatError.message : "Could not send the message."); }
    finally { setChatSending(false); }
  }

  async function applyCredentials() {
    setCredentialMessage(null); setCredentialApplying(true);
    try {
      setAwsStatus(await applyAwsCredentials({ aws_access_key_id: accessKey, aws_secret_access_key: secretKey, aws_session_token: sessionToken }));
      setAccessKey(""); setSecretKey(""); setSessionToken(""); setCredentialMessage("Credentials applied in backend memory only.");
    } catch { setCredentialMessage("Credential override is disabled on the backend. Start the backend with SIMPLIFYNEXT_DEBUG_CREDENTIALS=1."); }
    finally { setCredentialApplying(false); }
  }

  async function clearCredentials() {
    try { setAwsStatus(await clearAwsCredentials()); setAccessKey(""); setSecretKey(""); setSessionToken(""); setCredentialMessage("Credentials cleared."); }
    catch { setCredentialMessage("Credential override is disabled on the backend. Start the backend with SIMPLIFYNEXT_DEBUG_CREDENTIALS=1."); }
  }

  if (!workspace) return <main className="flex min-h-screen items-center justify-center bg-slate-50 text-sm text-slate-600">Loading investigator workspace…</main>;

  const latestRun = workspace.latestRun;
  const rawTraceUrl = latestRun ? `${process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8000"}/api/cases/${encodeURIComponent(workspace.caseId)}/runs/${encodeURIComponent(latestRun.run_id)}/raw-traces` : null;
  return <main className="min-h-screen bg-slate-50">
    <header className="border-b border-slate-200 bg-white"><div className="mx-auto flex max-w-5xl items-center justify-between gap-6 px-5 py-4">
      <div className="flex min-w-0 items-center gap-3"><div className="text-lg font-extrabold tracking-tight text-blue-800">SimplifyNext</div><span className="text-slate-300">|</span><h1 className="truncate text-lg font-bold text-slate-900">{workspace.title}</h1></div>
      <div className="flex shrink-0 items-center gap-2"><span className="rounded-full bg-blue-50 px-3 py-1 text-sm font-bold text-blue-800">● Investigating</span><button onClick={() => setDialog("sources")} className="hidden rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 sm:block">Sources</button><button onClick={() => setDialog("details")} className="hidden rounded-lg border border-slate-300 px-3 py-1.5 text-sm font-semibold text-slate-700 hover:bg-slate-50 sm:block">Details</button></div>
    </div></header>

    <div className="mx-auto max-w-3xl px-5 py-7">
      <div className="mb-4 rounded-lg border border-slate-200 bg-white px-5 py-4 text-sm text-slate-700"><div className="flex flex-wrap gap-x-6 gap-y-2"><span><strong>Case status:</strong> {workspace.institutionalStatus}</span><span><strong>Investigation:</strong> {runtime?.[0]}</span></div></div>
      <section className="mb-4 rounded-lg border border-amber-200 bg-amber-50/60 px-5 py-4" aria-label="Testing AWS credentials"><div className="font-bold text-slate-900">Testing: AWS credentials</div><p className="mt-1 text-xs text-slate-600">Temporary values stay in backend memory and are never shown again.</p>{awsStatus ? <p className="mt-2 text-xs text-slate-700">Credential source: {awsStatus.credential_source} · Override active: {awsStatus.override_active ? "Yes" : "No"} · Last updated: {awsStatus.last_updated_at ?? "Never"}</p> : <p className="mt-2 text-xs text-amber-900">AWS credential override is disabled or unavailable on the backend.</p>}<div className="mt-3 grid gap-2 sm:grid-cols-3"><input disabled={credentialApplying} type="password" value={accessKey} onChange={(event) => setAccessKey(event.target.value)} placeholder="AWS Access Key ID" className="rounded border border-slate-300 bg-white px-2 py-2 text-xs disabled:opacity-60"/><input disabled={credentialApplying} type="password" value={secretKey} onChange={(event) => setSecretKey(event.target.value)} placeholder="AWS Secret Access Key" className="rounded border border-slate-300 bg-white px-2 py-2 text-xs disabled:opacity-60"/><input disabled={credentialApplying} type="password" value={sessionToken} onChange={(event) => setSessionToken(event.target.value)} placeholder="AWS Session Token" className="rounded border border-slate-300 bg-white px-2 py-2 text-xs disabled:opacity-60"/></div><div className="mt-3 flex flex-wrap items-center gap-2"><button disabled={credentialApplying} onClick={applyCredentials} className="rounded bg-amber-600 px-3 py-2 text-xs font-bold text-white disabled:opacity-60">{credentialApplying ? "Applying…" : "Apply credentials"}</button><button disabled={credentialApplying} onClick={clearCredentials} className="rounded border border-amber-700 px-3 py-2 text-xs font-bold text-amber-900 disabled:opacity-60">Clear credentials</button></div>{credentialMessage && <p className="mt-2 text-xs text-slate-700">{credentialMessage}</p>}</section>
      <div className="mb-4 rounded-lg border-l-4 border-blue-700 bg-blue-50/60 px-5 py-4"><div className="mb-1 text-sm font-bold text-blue-800">Current focus</div><p className="text-lg leading-7 text-slate-900">{workspace.currentFocus}</p></div>
      <div className={`mb-4 rounded-lg border px-5 py-4 ${workspace.runtimeStatus === "FAILED" ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-white"}`}><div className="font-bold text-slate-900">{runtime?.[0]}</div><p className="mt-1 text-sm text-slate-600">{runtime?.[1]}</p>{workspace.runtimeStatus === "IDLE" && <button disabled={starting} onClick={startInvestigation} className="mt-3 rounded-lg bg-blue-700 px-4 py-2 text-sm font-bold text-white disabled:opacity-50">{starting ? "Starting…" : "Run investigation"}</button>}{workspace.runtimeStatus === "FAILED" && <div className="mt-3 rounded-lg border border-amber-200 bg-white p-3 text-sm text-amber-900"><strong>Investigation recovered or paused safely.</strong><p className="mt-1">No invalid case change was committed. Open Debug to inspect the technical details.</p>{rawTraceUrl && <a href={rawTraceUrl} target="_blank" rel="noreferrer" className="mt-2 inline-block font-bold underline">Open debug trace</a>}</div>}</div>
      {latestRun && <section className="mb-4 rounded-lg border border-slate-200 bg-white px-5 py-4 text-sm"><div className="font-bold text-slate-900">Latest run</div><div className="mt-1 text-slate-700">{latestRun.run_id} · {latestRun.final_runtime_status ?? workspace.runtimeStatus}</div>{rawTraceUrl && <div className="mt-2 flex gap-4"><a href={rawTraceUrl} target="_blank" rel="noreferrer" className="font-semibold text-blue-700 underline">Open raw trace</a><a href={`/debug/cases/${encodeURIComponent(workspace.caseId)}/runs/${encodeURIComponent(latestRun.run_id)}`} target="_blank" rel="noreferrer" className="font-semibold text-blue-700 underline">View run artifacts</a></div>}</section>}
      {((workspace.runs?.length ?? 0) > 0 || (workspace.requestHistory?.length ?? 0) > 0) && <section className="mb-4 rounded-lg border border-slate-200 bg-white px-5 py-4 text-sm"><div className="font-bold text-slate-900">Investigation history</div><div className="mt-3 space-y-2">{workspace.runs?.map((run) => <div key={run.run_id} className="rounded border border-slate-100 p-2 text-slate-700">Investigation run {run.run_id.replace("run_", "#")} · {run.final_runtime_status ?? "completed"}</div>)}{workspace.requestHistory?.map((item) => <div key={item.request_id} className="rounded border border-slate-100 p-2 text-slate-700">Human information request: {item.informationSought} · {item.status}</div>)}</div></section>}
      <section aria-label="Investigation workspace" className="space-y-3">
        {workspace.messages.map((message) => <div key={message.id} className={`flex gap-3 rounded-xl border bg-white p-4 ${message.role === "simplifynext" ? "border-blue-200 shadow-sm" : "border-slate-200"}`}><Avatar role={message.role}/><div className="min-w-0 flex-1"><div className="mb-1 flex items-center gap-2"><span className="font-bold text-slate-900">{message.role === "simplifynext" ? "SimplifyNext Assistant" : "Investigator"}</span>{message.timestamp && <span className="text-xs text-slate-500">{message.timestamp}</span>}</div>{message.text && <p className="text-sm leading-6 text-slate-700">{message.text}</p>}{message.request && <div className="mt-4"><RequestCard request={message.request} disabled={workspace.runtimeStatus !== "WAITING_FOR_EVIDENCE"} onEvidence={() => setDialog("evidence")} onUnavailable={() => setDialog("unavailable")} /></div>}</div></div>)}
      </section>

      <section className="mt-5 rounded-xl border border-blue-100 bg-white p-4" aria-label="Workspace chat"><div className="mb-3 font-bold text-slate-900">Workspace assistant</div>{(!workspace.chatHistory || workspace.chatHistory.length === 0) && <p className="mb-3 text-sm leading-6 text-slate-600">SimplifyNext assists academic-integrity investigations by organising evidence, competing explanations, unresolved questions, and an auditable history. It does not decide guilt or make disciplinary decisions.</p>}<div className="mb-3 flex flex-wrap gap-2">{["Explain the current case", "What happened in the latest run?", "Show the evidence and history"].map((suggestion) => <button key={suggestion} onClick={() => setChatMessage(suggestion)} className="rounded-full border border-blue-200 px-3 py-1.5 text-xs font-semibold text-blue-800 hover:bg-blue-50">{suggestion}</button>)}</div>{workspace.chatHistory?.map((item, index) => <div key={`${item.role}-${index}`} className={`mb-2 rounded-lg p-3 text-sm ${item.role === "human" ? "ml-8 bg-slate-50 text-slate-700" : "mr-8 bg-blue-50 text-slate-800"}`}>{item.text}</div>)}<div className="flex items-center gap-3 rounded-lg border border-slate-300 p-2"><input value={chatMessage} onChange={(event) => setChatMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void sendMessage(); }} placeholder="Ask about the case or investigation…" className="min-w-0 flex-1 px-1 text-sm outline-none"/><button disabled={chatSending || !chatMessage.trim()} onClick={() => void sendMessage()} className="rounded-md bg-blue-700 px-3 py-2 text-sm font-bold text-white disabled:opacity-50">{chatSending ? "Sending…" : "Send"}</button></div></section>
    </div>

    {dialog === "evidence" && request && <DialogShell title="Provide evidence" onClose={() => setDialog(null)}><p className="mb-4 rounded-lg bg-slate-50 p-3 text-sm leading-6 text-slate-700"><strong>Requested information</strong><br/>{request.informationSought}</p><label className="mb-4 block text-sm font-semibold text-slate-800">Evidence<input type="file" onChange={(event) => setFile(event.target.files?.[0] ?? null)} className="mt-2 block w-full rounded-lg border border-slate-300 p-2 text-sm"/></label><label className="block text-sm font-semibold text-slate-800">Optional note<textarea value={note} onChange={(event) => setNote(event.target.value)} rows={3} className="mt-2 block w-full rounded-lg border border-slate-300 p-2 text-sm"/></label><div className="mt-6 flex justify-end gap-3"><button onClick={() => setDialog(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700">Cancel</button><button onClick={submitEvidence} className="rounded-lg bg-blue-700 px-4 py-2 text-sm font-bold text-white hover:bg-blue-800">Add evidence</button></div></DialogShell>}
    {dialog === "unavailable" && request && <DialogShell title="Mark requested information unavailable" onClose={() => setDialog(null)}><label className="block text-sm font-semibold text-slate-800">Optional reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} rows={4} className="mt-2 block w-full rounded-lg border border-slate-300 p-2 text-sm"/></label><div className="mt-6 flex justify-end gap-3"><button onClick={() => setDialog(null)} className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold text-slate-700">Cancel</button><button onClick={submitUnavailable} className="rounded-lg bg-blue-700 px-4 py-2 text-sm font-bold text-white hover:bg-blue-800">Confirm unavailable</button></div></DialogShell>}
    {dialog === "sources" && <DialogShell title="Visible sources" onClose={() => setDialog(null)}><ul className="space-y-3 text-sm text-slate-700">{workspace.visibleSources.map((source) => <li key={source.id} className="border-b border-slate-100 pb-2"><strong>{source.name}</strong><div className="text-xs text-slate-500">{source.sourceType}</div><p className="mt-1">{source.contentPreview}</p><details className="mt-1"><summary className="cursor-pointer font-semibold text-blue-700">View full source</summary><pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs">{source.content}</pre></details></li>)}</ul></DialogShell>}
    {dialog === "details" && <DialogShell title="Case details" onClose={() => setDialog(null)}><dl className="space-y-4 text-sm"><div><dt className="font-bold text-slate-500">Case</dt><dd>{workspace.title}</dd></div><div><dt className="font-bold text-slate-500">Status</dt><dd>{workspace.institutionalStatus}</dd></div><div><dt className="font-bold text-slate-500">Current focus</dt><dd>{workspace.currentFocus}</dd></div></dl></DialogShell>}
  </main>;
}
