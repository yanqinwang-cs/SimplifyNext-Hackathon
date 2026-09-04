"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import { getSourceDocument } from "../../../../../lib/case-service";

function MarkdownDocument({ text }: { text: string }) {
  const lines = text.split(/\r?\n/); const blocks: ReactElement[] = []; let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (line.startsWith("```") ) { const code: string[] = []; index += 1; while (index < lines.length && !lines[index].startsWith("```")) code.push(lines[index++]); index += 1; blocks.push(<pre key={index} className="overflow-x-auto rounded bg-slate-100 p-4 text-sm"><code>{code.join("\n")}</code></pre>); continue; }
    if (/^#{1,6} /.test(line)) { const level = line.match(/^#+/)?.[0].length ?? 1; const content = line.replace(/^#+ /, ""); blocks.push(level === 1 ? <h1 key={index} className="text-3xl font-black">{content}</h1> : <h2 key={index} className="text-xl font-bold">{content}</h2>); index += 1; continue; }
    if (line.startsWith("- ")) { const items: string[] = []; while (index < lines.length && lines[index].startsWith("- ")) items.push(lines[index++].slice(2)); blocks.push(<ul key={index} className="list-disc pl-6">{items.map((item) => <li key={item}>{item}</li>)}</ul>); continue; }
    if (!line.trim()) { index += 1; continue; }
    blocks.push(<p key={index}>{line.split(/(\*\*[^*]+\*\*)/).map((part, partIndex) => part.startsWith("**") ? <strong key={partIndex}>{part.slice(2, -2)}</strong> : part)}</p>); index += 1;
  }
  return <article className="space-y-4 leading-7">{blocks}</article>;
}

export default function SourcePage() {
  const { caseId, sourceHandle } = useParams<{ caseId: string; sourceHandle: string }>(); const [source, setSource] = useState<{ fileName: string; documentFormat: string; content: string } | null>(null); const [error, setError] = useState(false);
  useEffect(() => { getSourceDocument(caseId, sourceHandle).then((result) => setSource(result.source)).catch(() => setError(true)); }, [caseId, sourceHandle]);
  return <main className="min-h-screen bg-slate-50 p-6 text-slate-900"><div className="mx-auto max-w-3xl"><Link href={`/cases/${caseId}`} className="font-bold text-blue-700">← Back to case</Link>{error ? <p className="mt-10">Source not found.</p> : !source ? <p className="mt-10">Loading source…</p> : <section className="mt-8 rounded-xl border bg-white p-8 shadow-sm"><p className="text-xs font-bold uppercase tracking-widest text-slate-500">{source.documentFormat}</p><h1 className="mt-2 mb-8 text-3xl font-black">{source.fileName}</h1>{source.documentFormat === "markdown" ? <MarkdownDocument text={source.content}/> : <div className="whitespace-pre-wrap leading-7">{source.content}</div>}</section>}</div></main>;
}
