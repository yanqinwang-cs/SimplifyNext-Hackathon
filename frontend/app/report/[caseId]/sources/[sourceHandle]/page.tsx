"use client";

import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { getHistoricalSourceDocument } from "../../../../../lib/case-service";
import MarkdownDocument from "../../../../components/markdown-document";

export default function HistoricalSourcePage() {
  const { caseId, sourceHandle } = useParams<{ caseId: string; sourceHandle: string }>();
  const assessment = useSearchParams().get("assessment") ?? "";
  const [source, setSource] = useState<{ fileName: string; documentFormat: string; content: string; assessmentDate?: string | null } | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => { setError(false); void getHistoricalSourceDocument(caseId, assessment, sourceHandle).then((result) => setSource(result.source)).catch(() => setError(true)); }, [assessment, caseId, sourceHandle]);
  return <main className="min-h-screen bg-slate-50 p-6 text-slate-900"><div className="mx-auto max-w-3xl"><Link href={`/report/${encodeURIComponent(caseId)}?assessment=${encodeURIComponent(assessment)}`} className="font-bold text-blue-700">← Back to report</Link>{error ? <p className="mt-10">Historical source not found.</p> : !source ? <p className="mt-10">Loading source…</p> : <section className="mt-8 rounded-xl border bg-white p-8 shadow-sm"><p className="text-xs font-bold uppercase tracking-widest text-slate-500">Source as reviewed in this assessment · {source.documentFormat}</p><h1 className="mt-2 break-words text-3xl font-black">{source.fileName}</h1>{source.assessmentDate && <p className="mt-2 text-sm text-slate-500">Assessment date: {new Date(source.assessmentDate).toLocaleString()}</p>}<div className="mt-8">{source.documentFormat === "markdown" ? <MarkdownDocument text={source.content}/> : <div className="whitespace-pre-wrap leading-7">{source.content}</div>}</div></section>}</div></main>;
}
