"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { addSource, addSubject, downloadAuditTrace, getAssessmentRun, getAuditTrace, getReport, getWorkspace, removeSubject, renameSubject, resetSample, runInvestigation, sendWorkspaceMessage, updateCaseTitle } from "../../../lib/case-service";
import type { AuditTraceResponse, CaseWorkspaceState, ReportResponse } from "../../../lib/types";
import RuntimeSettingsControl from "../../components/runtime-settings";

const terminal = new Set(["completed", "failed", "stopped", "interrupted"]);
const quickPrompts = ["What remains uncertain?", "What evidence would be useful next?", "Why is the current conclusion limited?", "Is it reasonable to stop here?"];
const copy = (state: CaseWorkspaceState["assessment"]["state"]) => ({
  not_started: "No assessment has been run for this case yet.", running: "Assessment is running. Case changes are temporarily disabled.", complete: "Assessment complete. The current report is up to date.", stale: "The report is stale because the case changed after the last assessment.", failed_no_report: "Assessment failed before a report was produced.", failed_previous_report_retained: "Assessment failed. The previous report is retained for review.", stopped: "Assessment stopped. No new report was committed.",
}[state]);

export default function CasePage() {
  const { caseId } = useParams<{ caseId: string }>();
  const [workspace, setWorkspace] = useState<CaseWorkspaceState | null>(null);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [auditTrace, setAuditTrace] = useState<AuditTraceResponse | null>(null);
  const [question, setQuestion] = useState("");
  const [draftTitle, setDraftTitle] = useState("");
  const [editingTitle, setEditingTitle] = useState(false);
  const [studentName, setStudentName] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [polling, setPolling] = useState(false);
  const [helpBusy, setHelpBusy] = useState(false);
  const [helpError, setHelpError] = useState<string | null>(null);
  const [uploadBusy, setUploadBusy] = useState(false);
  const [studentBusy, setStudentBusy] = useState(false);
  const [titleBusy, setTitleBusy] = useState(false);
  const [resetBusy, setResetBusy] = useState(false);
  const [auditDownloadBusy, setAuditDownloadBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const handle = useRef<string | null>(null);
  const generation = useRef(0);
  const pollHandle = useRef<string | null>(null);
  const pollAbort = useRef<AbortController | null>(null);

  useEffect(() => {
    let mounted = true;
    const controller = new AbortController();
    void Promise.all([getWorkspace(caseId, controller.signal), getReport(caseId, undefined, controller.signal)]).then(async ([next, nextReport]) => {
      if (!mounted) return;
      setWorkspace(next); setReport(nextReport); setDraftTitle(next.title); handle.current = next.assessment.activeRun?.runHandle ?? null;
      const latestHandle = next.assessment.latestAttempt?.runHandle;
      if (latestHandle && next.assessment.latestAttempt?.state !== "running") {
        try { setAuditTrace(await getAuditTrace(caseId, latestHandle)); } catch { /* diagnostics are intentionally absent when disabled */ }
      }
    }).catch((exc) => mounted && !(exc instanceof DOMException && exc.name === "AbortError") && setError("Could not load this case."));
    return () => { mounted = false; controller.abort(); generation.current += 1; pollAbort.current?.abort(); pollAbort.current = null; pollHandle.current = null; handle.current = null; };
  }, [caseId]);

  async function poll(runHandle: string) {
    if (pollHandle.current === runHandle) return;
    pollAbort.current?.abort();
    const token = ++generation.current; const deadline = Date.now() + 15 * 60 * 1000; const controller = new AbortController(); pollAbort.current = controller; pollHandle.current = runHandle; handle.current = runHandle; setPolling(true); setStatusError(null);
    try {
      while (Date.now() < deadline && token === generation.current) {
        const next = await getAssessmentRun(caseId, runHandle, controller.signal); if (token !== generation.current) return; setWorkspace(next.workspace);
        if (terminal.has(next.state)) { handle.current = null; if (next.state === "completed") { const nextReport = await getReport(caseId, undefined, controller.signal); if (token === generation.current) setReport(nextReport); } else if (next.message && token === generation.current) setStatusError(next.message); try { const audit = await getAuditTrace(caseId, runHandle); if (token === generation.current) setAuditTrace(audit); } catch { /* diagnostics are intentionally absent when disabled */ } return; }
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
      }
      if (token === generation.current) setStatusError("Status refresh timed out after 15 minutes. Retry the status check.");
    } catch (exc) { if (token === generation.current && !(exc instanceof DOMException && exc.name === "AbortError")) setStatusError("Could not refresh assessment status. Retry the status check."); }
    finally { if (token === generation.current) { setPolling(false); pollHandle.current = null; if (pollAbort.current === controller) pollAbort.current = null; } }
  }

  useEffect(() => { if (workspace?.assessment.activeRun && !polling && !runBusy) void poll(workspace.assessment.activeRun.runHandle); /* persisted handle resumes after reload */ // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace?.assessment.activeRun?.runHandle]);

  async function run() { if (runBusy || polling) return; setRunBusy(true); setError(null); try { const started = await runInvestigation(caseId); setWorkspace(started.workspace); await poll(started.run.runHandle); } catch (exc) { setError(exc instanceof Error ? exc.message : "Could not start the assessment."); } finally { setRunBusy(false); } }
  async function upload() { if (!workspace || !file || uploadBusy) return; setUploadBusy(true); try { const result = await addSource(caseId, { fileName: file.name, content: await file.text(), mediaType: file.type || "text/plain", caseRevision: workspace.caseRevision }); setWorkspace(result.workspace); setFile(null); } catch (exc) { setError(exc instanceof Error ? exc.message : "Could not add evidence."); } finally { setUploadBusy(false); } }
  async function addStudentNow() { if (!workspace || !studentName.trim() || studentBusy) return; setStudentBusy(true); try { const result = await addSubject(caseId, { displayName: studentName.trim(), caseRevision: workspace.caseRevision }); setWorkspace(result.workspace); setStudentName(""); } catch (exc) { setError(exc instanceof Error ? exc.message : "Could not add student."); } finally { setStudentBusy(false); } }
  async function ask(text = question) { if (!text.trim() || helpBusy) return; setHelpBusy(true); setHelpError(null); try { await sendWorkspaceMessage(caseId, text.trim()); setQuestion(""); setWorkspace(await getWorkspace(caseId)); } catch (exc) { setHelpError(exc instanceof Error ? exc.message : "Help is temporarily unavailable."); } finally { setHelpBusy(false); } }
  async function saveTitle() { if (!workspace || titleBusy) return; setTitleBusy(true); setError(null); try { const result = await updateCaseTitle(caseId, draftTitle); setWorkspace(result.workspace); setDraftTitle(result.workspace.title); setEditingTitle(false); setMessage("Case name updated."); } catch (exc) { setError(exc instanceof Error ? exc.message : "Could not update the case name."); } finally { setTitleBusy(false); } }
  async function reset() { if (!workspace || resetBusy || !window.confirm("Reset this sample?")) return; setResetBusy(true); try { const result = await resetSample(workspace.sample?.sampleId ?? ""); setWorkspace(result.workspace); setReport(await getReport(caseId)); setMessage("Sample restored to its original evidence."); } catch (exc) { setError(exc instanceof Error ? exc.message : "Could not reset this sample."); } finally { setResetBusy(false); } }
  async function downloadAudit(runHandle: string) { if (auditDownloadBusy) return; setAuditDownloadBusy(true); try { await downloadAuditTrace(caseId, runHandle); } catch (exc) { setError(exc instanceof Error ? exc.message : "Could not download the audit trace."); } finally { setAuditDownloadBusy(false); } }
  async function rename(handleValue: string, name: string) { if (!workspace || studentBusy) return; setStudentBusy(true); try { setWorkspace((await renameSubject(caseId, handleValue, name, workspace.caseRevision)).workspace); } catch (exc) { setError(exc instanceof Error ? exc.message : "Could not rename student."); } finally { setStudentBusy(false); } }
  async function remove(handleValue: string) { if (!workspace || workspace.students.length === 1 || studentBusy) return; setStudentBusy(true); try { setWorkspace(await removeSubject(caseId, handleValue, workspace.caseRevision)); } catch (exc) { setError(exc instanceof Error ? exc.message : "Could not remove student."); } finally { setStudentBusy(false); } }

  if (!workspace) return <main className="p-8">{error ?? "Loading workspace…"}</main>;
  const sample = workspace.caseKind === "sample";
  const active = workspace.assessment.state === "running" || polling;
  const locked = active || uploadBusy || studentBusy || resetBusy;
  const statusText = workspace.assessment.state.startsWith("failed") && workspace.assessment.latestAttempt?.message ? workspace.assessment.latestAttempt.message : copy(workspace.assessment.state);
  return <main className="min-h-screen bg-slate-50 p-6 text-slate-900">
    <header className="mx-auto flex max-w-6xl items-start justify-between border-b pb-5">
      <div><Link href="/cases" className="font-black uppercase tracking-widest text-blue-700">SimplifyNext</Link>{editingTitle && !sample ? <div className="mt-2 flex gap-2"><label htmlFor="case-title" className="sr-only">Case name</label><input id="case-title" value={draftTitle} onChange={(event) => setDraftTitle(event.target.value)} maxLength={160} autoFocus className="rounded border px-2 py-1 text-2xl font-black"/><button disabled={titleBusy} onClick={() => void saveTitle()} className="rounded bg-blue-700 px-3 py-1 text-sm font-bold text-white">{titleBusy ? "Saving…" : "Save"}</button><button disabled={titleBusy} onClick={() => { setDraftTitle(workspace.title); setEditingTitle(false); }} className="rounded border px-3 py-1 text-sm font-bold">Cancel</button></div> : <div className="mt-2 flex items-center gap-3"><h1 className="text-2xl font-black">{workspace.title}</h1>{!sample && <button onClick={() => setEditingTitle(true)} className="text-sm font-bold text-blue-700 underline">Edit</button>}{sample && <span className="rounded-full bg-blue-50 px-2 py-1 text-xs font-bold uppercase text-blue-700">Sample</span>}</div>}</div>
      <div className="flex gap-3"><RuntimeSettingsControl caseId={caseId}/>{report?.reportState === "available" && <Link href={`/report/${caseId}`} className="rounded border px-4 py-2 text-sm font-bold">View report</Link>}<button disabled={active || runBusy} onClick={() => void run()} className="rounded bg-blue-700 px-4 py-2 text-sm font-bold text-white disabled:bg-slate-400">{active ? "Assessment running…" : runBusy ? "Starting…" : "Run assessment"}</button>{sample && <button disabled={locked} onClick={() => void reset()} className="rounded border px-3 py-2 text-sm font-bold">Reset sample</button>}</div>
    </header>
    <section aria-live="polite" className="mx-auto mt-5 max-w-6xl rounded-xl border bg-white p-4"><h2 className="font-black">Assessment status</h2><p className="mt-1 text-sm">{statusText}</p>{statusError && <div className="mt-3 flex justify-between rounded bg-red-50 p-3 text-sm text-red-800"><span>{statusError}</span>{handle.current && <button onClick={() => void poll(handle.current as string)} className="font-bold underline">Retry status check</button>}</div>}{auditTrace && <section aria-label="Assessment audit" className="mt-4 border-t pt-3"><div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-black">Assessment audit</h3>{workspace.assessment.latestAttempt?.runHandle && <button disabled={auditDownloadBusy} className="rounded border px-3 py-2 text-sm font-bold text-blue-700 disabled:opacity-50" onClick={() => void downloadAudit(workspace.assessment.latestAttempt?.runHandle as string)}>{auditDownloadBusy ? "Preparing audit trace…" : "Download audit trace"}</button>}</div><dl className="mt-2 grid gap-1 text-sm sm:grid-cols-2"><div><dt className="font-semibold">Outcome</dt><dd>{auditTrace.outcome}</dd></div><div><dt className="font-semibold">Model</dt><dd>{auditTrace.model.logicalModel ?? "No model call"}</dd></div><div><dt className="font-semibold">Model calls</dt><dd>{auditTrace.counters.modelCalls}</dd></div><div><dt className="font-semibold">Proposal corrections</dt><dd>{auditTrace.counters.proposalCorrectionCalls}</dd></div><div><dt className="font-semibold">Clean retries</dt><dd>{auditTrace.counters.cleanExecutionRetries}</dd></div>{auditTrace.failure && <div><dt className="font-semibold">Failure</dt><dd>{auditTrace.failure.category ?? auditTrace.failure.technicalType}</dd></div>}</dl></section>}</section>
    <div className="mx-auto grid max-w-6xl gap-6 py-6 lg:grid-cols-[220px_1fr_260px]">
      <aside><h2 className="font-black">Students</h2><ul className="mt-3 space-y-2">{workspace.students.map((student) => <li key={student.studentHandle} className="flex gap-2"><span className="flex-1 truncate">{student.displayName}</span>{!sample && <><button disabled={locked} onClick={() => { const name = window.prompt("Student name", student.displayName); if (name?.trim()) void rename(student.studentHandle, name.trim()); }} className="text-xs text-blue-700">Rename</button><button disabled={locked || workspace.students.length === 1} onClick={() => void remove(student.studentHandle)} className="text-xs text-red-700">Remove</button></>}</li>)}</ul>{!sample && <div className="mt-3 flex gap-2"><label htmlFor="new-student" className="sr-only">Add student</label><input id="new-student" disabled={locked} value={studentName} onChange={(event) => setStudentName(event.target.value)} placeholder="Add student" className="min-w-0 flex-1 rounded border px-2 py-1 text-xs"/><button disabled={locked} onClick={() => void addStudentNow()} className="rounded bg-blue-700 px-2 py-1 text-xs text-white">Add</button></div>}</aside>
      <section className="space-y-6"><section className="rounded-xl border bg-white p-5"><h2 className="text-xl font-black">Workspace Help</h2><p className="mt-1 text-sm text-slate-600">Read-only help about this case, its evidence, and the assessment.</p><div className="mt-4 flex flex-wrap gap-2">{quickPrompts.map((prompt) => <button key={prompt} disabled={helpBusy} onClick={() => void ask(prompt)} className="rounded-full border border-blue-200 px-3 py-2 text-sm font-semibold text-blue-700 disabled:opacity-50">{prompt}</button>)}</div><div className="mt-4 min-h-40 space-y-2">{workspace.chatHistory.length === 0 && <p className="rounded bg-slate-50 p-3 text-sm text-slate-600">Ask a question about uncertainty, evidence, findings, or whether further enquiry may be useful.</p>}{workspace.chatHistory.map((item, index) => <p key={`${item.role}-${index}`} className={`rounded p-3 text-sm ${item.role === "human" ? "ml-8 bg-blue-50" : "bg-slate-50"}`}>{item.text}</p>)}</div>{helpError && <p role="alert" className="mt-3 rounded bg-red-50 p-3 text-sm text-red-800">{helpError}</p>}<div className="mt-4 flex gap-2"><label htmlFor="help-question" className="sr-only">Ask Workspace Help</label><input id="help-question" disabled={helpBusy} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void ask(); }} placeholder="Ask about the case or current assessment…" className="min-w-0 flex-1 rounded border px-3 py-2"/><button disabled={helpBusy} onClick={() => void ask()} className="rounded bg-blue-700 px-4 py-2 font-bold text-white">{helpBusy ? "Sending…" : "Send"}</button></div></section>{!sample && <section className="rounded-xl border bg-white p-5"><h2 className="text-xl font-black">Add evidence</h2><p className="mt-1 text-sm text-slate-600">Add readable .txt or .md case material.</p><input disabled={locked} type="file" accept=".txt,.md" onChange={(event) => setFile(event.target.files?.[0] ?? null)}/><button disabled={!file || locked} onClick={() => void upload()} className="ml-3 rounded bg-blue-700 px-3 py-2 text-sm font-bold text-white">{uploadBusy ? "Adding…" : "Add evidence"}</button></section>}</section>
      <aside className="rounded-xl border bg-white p-4"><h2 className="font-black">Sources</h2><div className="mt-3 space-y-2">{workspace.sources.map((source) => <Link key={source.sourceHandle} href={`/cases/${caseId}/sources/${source.sourceHandle}`} className="block truncate rounded border p-2 text-sm">▣ {source.fileName} →</Link>)}</div></aside>
    </div>
    {message && <div role="status" className="fixed bottom-4 left-1/2 -translate-x-1/2 rounded bg-emerald-700 px-4 py-3 text-white">{message}</div>}{error && <div role="alert" className="fixed bottom-4 left-1/2 -translate-x-1/2 rounded bg-red-700 px-4 py-3 text-white">{error}</div>}
  </main>;
}
