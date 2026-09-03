import Link from "next/link";

const samples = [
  ["case-01", "Business Law Tutorial 5", "Existing single-case demo"],
  ["smart-device", "Smart-device concern", "Single-subject sample"],
  ["possible-collaboration", "Possible collaboration", "Two-subject sample"],
  ["multi-candidate", "Multi-candidate assessment", "Five-subject sample"],
];

export default function Home() {
  return <main className="min-h-screen bg-slate-50 px-6 py-16 text-slate-900"><div className="mx-auto max-w-3xl"><p className="text-sm font-bold uppercase tracking-[0.2em] text-blue-700">SimplifyNext</p><h1 className="mt-3 text-4xl font-black tracking-tight">Evidence-led investigation workspace</h1><p className="mt-4 max-w-2xl text-lg leading-8 text-slate-600">Add case context and sources, run one bounded assessment, then inspect the evidence-grounded result. Final institutional judgment remains human.</p><div className="mt-10 grid gap-4 sm:grid-cols-2">{samples.map(([id, title, description]) => <Link key={id} href={`/cases/${id}`} className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-blue-400 hover:shadow-md"><div className="font-bold">{title}</div><div className="mt-1 text-sm text-slate-600">{description}</div><div className="mt-4 text-sm font-bold text-blue-700">Open case →</div></Link>)}</div><Link href="/help" className="mt-8 inline-block text-sm font-bold text-blue-700 underline">How SimplifyNext works</Link></div></main>;
}
