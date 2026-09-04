"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getProductGuide } from "../../lib/case-service";

export default function HelpPage() {
  const [guide, setGuide] = useState<string | null>(null);
  useEffect(() => { getProductGuide().then(setGuide).catch(() => setGuide("The product guide is temporarily unavailable.")); }, []);
  return <main className="min-h-screen bg-slate-50 px-6 py-10 text-slate-900"><article className="mx-auto max-w-4xl rounded-2xl border border-slate-200 bg-white p-8 shadow-sm"><Link href="/" className="text-sm font-bold text-blue-700">← Workspace</Link><h1 className="mt-6 text-3xl font-black">How to use the Investigation Assistant</h1><pre className="mt-6 whitespace-pre-wrap text-sm leading-7 text-slate-700">{guide ?? "Loading guide…"}</pre></article></main>;
}
