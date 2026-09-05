"use client";

import type { ReactElement } from "react";

function inline(text: string): ReactElement[] {
  return text.split(/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g).map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("*") && part.endsWith("*")) return <em key={index}>{part.slice(1, -1)}</em>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index} className="rounded bg-slate-100 px-1 py-0.5 text-sm">{part.slice(1, -1)}</code>;
    return <span key={index}>{part}</span>;
  });
}

export default function MarkdownDocument({ text }: { text: string }) {
  const lines = text.split(/\r?\n/);
  const blocks: ReactElement[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) { index += 1; continue; }
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = heading[1].length;
      const content = inline(heading[2]);
      if (level === 1) blocks.push(<h1 key={index} className="text-3xl font-black">{content}</h1>);
      else if (level === 2) blocks.push(<h2 key={index} className="text-2xl font-black">{content}</h2>);
      else if (level === 3) blocks.push(<h3 key={index} className="text-xl font-bold">{content}</h3>);
      else if (level === 4) blocks.push(<h4 key={index} className="text-lg font-bold">{content}</h4>);
      else if (level === 5) blocks.push(<h5 key={index} className="text-base font-bold">{content}</h5>);
      else blocks.push(<h6 key={index} className="text-sm font-bold uppercase tracking-wide">{content}</h6>);
      index += 1;
      continue;
    }
    if (/^[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^[-*]\s+/.test(lines[index])) items.push(lines[index++].replace(/^[-*]\s+/, ""));
      blocks.push(<ul key={index} className="list-disc space-y-1 pl-6">{items.map((item, itemIndex) => <li key={`${item}-${itemIndex}`}>{inline(item)}</li>)}</ul>);
      continue;
    }
    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\d+\.\s+/.test(lines[index])) items.push(lines[index++].replace(/^\d+\.\s+/, ""));
      blocks.push(<ol key={index} className="list-decimal space-y-1 pl-6">{items.map((item, itemIndex) => <li key={`${item}-${itemIndex}`}>{inline(item)}</li>)}</ol>);
      continue;
    }
    const paragraph: string[] = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !/^#{1,6}\s+/.test(lines[index]) && !/^[-*]\s+/.test(lines[index]) && !/^\d+\.\s+/.test(lines[index])) paragraph.push(lines[index++]);
    blocks.push(<p key={index} className="leading-7">{paragraph.map((part, partIndex) => <span key={partIndex}>{part}{partIndex < paragraph.length - 1 ? " " : ""}</span>).flatMap((part) => [part])}</p>);
  }
  return <article className="space-y-5">{blocks}</article>;
}
