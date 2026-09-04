"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { createCase, getCases, openSample } from "../../lib/case-service";
import type { CaseListItem } from "../../lib/types";

const samples = [["law-exam", "Law Exam Investigation"], ["multi-candidate", "Multi-Candidate Collaboration Review"]] as const;

export default function CasesPage() {
  const [cases, setCases] = useState<CaseListItem[]>([]);
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { getCases().then(setCases).catch(() => setError("Could not load cases.")); }, []);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;
    try { const result = await createCase({ title: name.trim() }); window.location.href = `/cases/${result.caseId}`; }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Could not create case."); }
  }
  const userCases = cases;
  return <main className="min-h-screen bg-slate-50 px-6 py-10 text-slate-950"><div className="mx-auto max-w-4xl"><header className="flex items-start justify-between"><div><Link href="/" className="text-sm font-black uppercase tracking-[0.24em] text-blue-700">SimplifyNext</Link><h1 className="mt-4 text-4xl font-black">Cases</h1><p className="mt-2 text-slate-600">Choose a sample or open one of your cases.</p></div><div className="flex gap-4 text-sm font-bold"><Link href="/" className="text-slate-600 underline">Introduction</Link><Link href="/help" className="text-blue-700 underline">Full guide</Link></div></header><section className="mt-10 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"><div className="divide-y divide-slate-100">{samples.map(([id, title]) => <button key={id} onClick={() => { void openSample(id).then((result) => { window.location.href = `/cases/${result.caseId}`; }).catch(() => setError("Could not open sample.")); }} className="flex w-full items-center justify-between py-4 text-left"><span className="text-lg font-bold">{title}</span><span className="rounded-full bg-blue-50 px-3 py-1 text-xs font-bold text-blue-700">sample</span></button>)}{userCases.map((item) => <Link key={item.case_id} href={`/cases/${item.case_id}`} className="block py-4 text-lg font-bold hover:text-blue-700">{item.title}</Link>)}</div></section><section className="mt-8 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm"><h2 className="text-2xl font-black">Create a new case</h2><form onSubmit={submit} className="mt-5 flex flex-col gap-3 sm:flex-row"><input required value={name} onChange={(event) => setName(event.target.value)} placeholder="Case name" className="min-w-0 flex-1 rounded-lg border border-slate-300 px-4 py-3"/><button type="submit" className="rounded-lg bg-blue-700 px-5 py-3 text-sm font-bold text-white">Create case</button></form>{error && <p className="mt-3 text-sm font-semibold text-red-700">{error}</p>}</section></div></main>;
}
