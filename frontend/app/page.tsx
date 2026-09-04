"use client";

import Link from "next/link";
import { useState } from "react";

const slides = [
  ["From evidence to assessment", "A structured workflow for reviewing an existing concern.", "Identify the students, add the written case evidence, run the assessment, then review the findings and unresolved questions.", "/onboarding/01-evidence-to-assessment.png"],
  ["Set up your case", "Add the students being investigated and the case evidence.", "Use the same student identifier consistently throughout the written sources. Student identity is configured explicitly, not inferred from the documents.", "/onboarding/02-set-up-case.png"],
  ["Run the assessment", "Review every configured student independently.", "Each configured violation receives its own finding for each student. Findings may be supported, partially supported, conflicted, or not currently supported.", "/onboarding/03-run-assessment.png"],
  ["Ask what you need next", "Use Workspace Help to understand uncertainty and decide what is worth checking next.", "Ask what evidence matters most, what remains uncertain, what further verification could help, or whether further enquiry is still justified.", "/onboarding/04-ask-next.png"],
] as const;

export default function Home() {
  const [slide, setSlide] = useState(0);
  const current = slides[slide];
  const final = slide === slides.length - 1;
  return <main className="min-h-screen bg-slate-50 text-slate-950"><header className="border-b border-slate-200 bg-white px-6 py-5"><div className="mx-auto flex max-w-6xl items-center justify-between"><div><div className="text-sm font-black uppercase tracking-[0.24em] text-blue-700">SimplifyNext</div><h1 className="mt-1 text-xl font-black">Investigation Assistant</h1></div><Link href="/help" className="text-sm font-bold text-blue-700 underline">Full guide</Link></div></header><section className="mx-auto flex min-h-[calc(100vh-94px)] max-w-6xl flex-col justify-center px-6 py-10"><div className="mb-8 text-sm font-bold text-slate-500">{slide + 1} of 4</div><div className="grid items-center gap-10 lg:grid-cols-[2fr_3fr]"><div className="max-w-md"><h2 className="text-4xl font-black tracking-tight sm:text-5xl">{current[0]}</h2><p className="mt-6 text-lg font-bold leading-8 text-slate-700">{current[1]}</p><p className="mt-4 text-base leading-7 text-slate-600">{current[2]}</p>{final && <p className="mt-5 text-sm font-semibold text-slate-500">Final institutional judgment remains human.</p>}</div><div className="overflow-hidden rounded-2xl border border-blue-100 bg-white shadow-sm"><img src={current[3]} alt={current[0]} className="block h-auto w-full" /></div></div><div className="mt-10 flex flex-wrap items-center justify-between gap-5"><button onClick={() => { window.location.href = "/cases"; }} className="text-sm font-bold text-blue-700 underline">Skip introduction</button><div className="flex items-center gap-2" aria-label="Introduction progress">{slides.map((item, index) => <button key={item[0]} aria-label={`Go to slide ${index + 1}`} onClick={() => setSlide(index)} className={`h-2.5 w-2.5 rounded-full ${index === slide ? "bg-blue-700" : "bg-slate-300"}`} />)}</div><div className="flex gap-3">{slide > 0 && <button onClick={() => setSlide(slide - 1)} className="rounded-lg border border-slate-300 bg-white px-5 py-3 text-sm font-bold">Back</button>}<button onClick={() => final ? window.location.href = "/cases" : setSlide(slide + 1)} className="rounded-lg bg-blue-700 px-5 py-3 text-sm font-bold text-white">{final ? "Choose a case" : "Next"}</button></div></div></section></main>;
}
