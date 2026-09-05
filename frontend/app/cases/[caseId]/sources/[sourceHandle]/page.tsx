"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { getSourceDocument } from "../../../../../lib/case-service";
import MarkdownDocument from "../../../../components/markdown-document";

export default function SourcePage() {
  const { caseId, sourceHandle } = useParams<{ caseId: string; sourceHandle: string }>(); const [source, setSource] = useState<{ fileName: string; documentFormat: string; content: string } | null>(null); const [error, setError] = useState(false);
  useEffect(() => { getSourceDocument(caseId, sourceHandle).then((result) => setSource(result.source)).catch(() => setError(true)); }, [caseId, sourceHandle]);
  return <main className="min-h-screen bg-slate-50 p-6 text-slate-900"><div className="mx-auto max-w-3xl"><Link href={`/cases/${caseId}`} className="font-bold text-blue-700">← Back to case</Link>{error ? <p className="mt-10">Source not found.</p> : !source ? <p className="mt-10">Loading source…</p> : <section className="mt-8 rounded-xl border bg-white p-8 shadow-sm"><p className="text-xs font-bold uppercase tracking-widest text-slate-500">{source.documentFormat}</p><h1 className="mt-2 mb-8 text-3xl font-black">{source.fileName}</h1>{source.documentFormat === "markdown" ? <MarkdownDocument text={source.content}/> : <div className="whitespace-pre-wrap leading-7">{source.content}</div>}</section>}</div></main>;
}
