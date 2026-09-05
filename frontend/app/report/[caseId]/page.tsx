"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { getReport } from "../../../lib/case-service";
import type { ReportResponse } from "../../../lib/types";

const statusLabels: Record<string, string> = { SUPPORTED: "Supported", PARTIALLY_SUPPORTED: "Partially supported", CONFLICTED: "Conflicted", NOT_CURRENTLY_SUPPORTED: "Not currently supported" };

function Material({ material, caseId, runHandle }: { material: { statement: string; sources: { sourceHandle: string; fileName: string }[] }; caseId: string; runHandle: string }) {
  return <li className="mb-2 leading-6">{material.statement}{material.sources.length > 0 && <span className="block text-xs text-slate-500">{material.sources.map((source) => <Link key={source.sourceHandle} className="mr-2 font-semibold text-blue-700 underline" href={`/report/${encodeURIComponent(caseId)}/sources/${encodeURIComponent(source.sourceHandle)}?assessment=${encodeURIComponent(runHandle)}`}>{source.fileName}</Link>)}</span>}</li>;
}

export default function ReportPage() {
  const { caseId } = useParams<{ caseId: string }>();
  const searchParams = useSearchParams();
  const assessment = searchParams.get("assessment") ?? undefined;
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [error, setError] = useState(false);
  const load = useCallback(() => { setError(false); setReport(null); void getReport(caseId, assessment).then(setReport).catch(() => setError(true)); }, [caseId, assessment]);
  useEffect(load, [load]);
  if (error) return <main className="min-h-screen bg-slate-50 p-6 text-slate-900"><article className="mx-auto max-w-3xl rounded-2xl border bg-white p-8 shadow-sm"><p className="font-bold">This report could not be loaded.</p><div className="mt-5 flex gap-4"><button className="rounded bg-blue-700 px-4 py-2 font-bold text-white" onClick={load}>Retry</button><Link className="rounded border px-4 py-2 font-bold text-blue-700" href={`/cases/${encodeURIComponent(caseId)}`}>Back to case</Link></div></article></main>;
  if (!report) return <main className="min-h-screen bg-slate-50 p-8 text-slate-900">Loading report…</main>;
  const assessmentView = report.assessment;
  return <main className="min-h-screen bg-slate-50 px-5 py-10 text-slate-900"><article className="mx-auto max-w-5xl rounded-2xl border border-slate-200 bg-white p-8 shadow-sm">
    <Link href={`/cases/${encodeURIComponent(caseId)}`} className="text-sm font-bold text-blue-700">← Back to case</Link>
    <div className="mt-5 flex flex-wrap items-start justify-between gap-4"><div><h1 className="text-3xl font-black">Investigation report</h1><p className="mt-1 text-sm text-slate-500">{assessmentView?.caseNameAtAssessment ?? report.currentCaseName ?? report.title}</p>{assessmentView?.completedAt && <p className="mt-1 text-xs text-slate-500">Assessment date: {new Date(assessmentView.completedAt).toLocaleString()}</p>}</div>{report.assessmentIsStale && <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-bold text-amber-800">Assessment out of date</span>}</div>
    {report.reportState === "unavailable" && <p className="mt-8 text-sm text-slate-600">No assessment available. Run an assessment before viewing the report.</p>}
    {report.reportState === "historical_unavailable" && <p className="mt-8 text-sm text-slate-600">{report.message}</p>}
    {report.reportState === "available" && assessmentView && <>
      {!report.isLatestSuccessfulAssessment && <div className="mt-6 rounded-lg bg-blue-50 p-4 text-sm"><p className="font-bold">You are viewing an earlier assessment.</p><Link className="mt-2 inline-block font-semibold text-blue-700 underline" href={`/report/${encodeURIComponent(caseId)}`}>View latest assessment</Link></div>}
      {report.assessmentIsStale && <p className="mt-4 rounded-lg bg-amber-50 p-4 text-sm">The case has changed since this assessment. Run a new assessment to include those changes.</p>}
      <section className="mt-8"><h2 className="text-xl font-black">Findings by student</h2><div className="mt-4 space-y-8">{assessmentView.students.map((student) => <section id={student.sectionHandle} key={student.sectionHandle} className="scroll-mt-6 border-t border-slate-200 pt-5"><h3 className="text-lg font-bold">{student.displayName}</h3>{student.violations.map((item) => <div key={item.label} className="mt-4 rounded-lg bg-slate-50 p-4"><div className="flex flex-wrap justify-between gap-3"><strong>{item.label}</strong><span className="text-xs font-bold uppercase">{statusLabels[item.status] ?? item.status.replaceAll("_", " ")}</span></div><p className="mt-2 text-sm leading-6">{item.reasoningSummary}</p><div className="mt-4 grid gap-4 text-sm sm:grid-cols-2"><div><strong>Supporting material</strong><ul className="mt-2 list-disc pl-5">{item.supportingMaterial.length ? item.supportingMaterial.map((material, index) => <Material key={`${material.statement}-${index}`} material={material} caseId={caseId} runHandle={assessmentView.runHandle} />) : <li>No supporting material recorded.</li>}</ul></div><div><strong>Limiting or conflicting material</strong><ul className="mt-2 list-disc pl-5">{item.limitingMaterial.length ? item.limitingMaterial.map((material, index) => <Material key={`${material.statement}-${index}`} material={material} caseId={caseId} runHandle={assessmentView.runHandle} />) : <li>No limiting material recorded.</li>}</ul></div></div>{item.unresolvedPoints.length > 0 && <div className="mt-4"><strong>Unresolved points</strong><ul className="mt-1 list-disc pl-5">{item.unresolvedPoints.map((point) => <li key={point}>{point}</li>)}</ul></div>}</div>)}{(student.alternativeExplanations ?? []).length > 0 && <div className="mt-4 rounded-lg border border-slate-200 p-4 text-sm"><strong>Alternative explanations</strong><ul className="mt-2 list-disc pl-5">{student.alternativeExplanations?.map((item) => <li key={item.statement}>{item.statement}</li>)}</ul></div>}<p className="mt-4 text-sm font-semibold">Furthest justified conclusion: {student.furthestConclusion}</p></section>)}</div></section>
    </>}
    <div className="mt-10 space-y-2 border-t border-slate-200 pt-5 text-sm leading-6 text-slate-600"><p>NOT_CURRENTLY_SUPPORTED means the available record does not currently support the potential violation. It does not establish innocence.</p><p>Final institutional judgment remains with authorised humans.</p></div>
  </article></main>;
}
