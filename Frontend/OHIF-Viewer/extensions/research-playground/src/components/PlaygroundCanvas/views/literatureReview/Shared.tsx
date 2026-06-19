import React, { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  ExternalLink,
  Award,
  ChevronDown,
  ChevronUp,
  Quote,
} from 'lucide-react';
import { Paper, KBSection } from './types';
import { getSectionColor, getSectionIcon, sectionToMarkdown, CATEGORY_COLORS } from './utils';

const ExternalLinkIcon: any = ExternalLink;
const AwardIcon: any = Award;
const ChevronDownIcon: any = ChevronDown;
const ChevronUpIcon: any = ChevronUp;
const QuoteIcon: any = Quote;

export const mdComponents: Record<string, React.FC<any>> = {
  h1: ({ children }: any) => (
    <h1 className="text-[18px] font-semibold text-slate-100 mt-6 mb-3 pb-2 border-b border-slate-700">
      {children}
    </h1>
  ),
  h2: ({ children }: any) => (
    <h2 className="text-[15px] font-semibold text-sky-300 mt-5 mb-2 pb-1.5 border-b border-slate-700/70">
      {children}
    </h2>
  ),
  h3: ({ children }: any) => (
    <h3 className="text-[12px] font-semibold text-slate-300 uppercase tracking-wide mt-4 mb-2 border-b border-slate-700/50 pb-1">
      {children}
    </h3>
  ),
  h4: ({ children }: any) => (
    <h4 className="text-[12px] font-semibold text-sky-300 mt-3 mb-1.5">{children}</h4>
  ),
  p: ({ children }: any) => (
    <p className="text-[12px] leading-7 text-slate-300 mb-2">{children}</p>
  ),
  strong: ({ children }: any) => (
    <strong className="text-white font-semibold">{children}</strong>
  ),
  em: ({ children }: any) => (
    <em className="text-sky-300 not-italic text-[11px]">{children}</em>
  ),
  hr: () => <hr className="border-slate-700 my-4" />,
  ul: ({ children }: any) => (
    <ul className="space-y-1 my-2 ml-1">{children}</ul>
  ),
  ol: ({ children }: any) => (
    <ol className="space-y-1 my-2 ml-4 list-decimal">{children}</ol>
  ),
  li: ({ children }: any) => (
    <li className="text-[12px] leading-[1.65] text-slate-300">{children}</li>
  ),
  blockquote: ({ children }: any) => (
    <blockquote className="border-l-2 border-cyan-500/50 pl-3 my-1.5 text-[11px] text-slate-300 italic">
      {children}
    </blockquote>
  ),
  a: ({ href, children }: any) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-[#5acce6] hover:underline">
      {children}
    </a>
  ),
  table: ({ children }: any) => (
    <div className="overflow-x-auto my-3 rounded-lg border border-slate-700">
      <table className="w-full text-[11px] text-left">{children}</table>
    </div>
  ),
  thead: ({ children }: any) => (
    <thead className="bg-slate-900 text-sky-300">{children}</thead>
  ),
  tbody: ({ children }: any) => <tbody className="divide-y divide-slate-700/60">{children}</tbody>,
  tr: ({ children }: any) => <tr className="hover:bg-slate-800/40">{children}</tr>,
  th: ({ children }: any) => (
    <th className="px-3 py-2 font-semibold text-[10.5px] uppercase tracking-wider">{children}</th>
  ),
  td: ({ children }: any) => (
    <td className="px-3 py-2 text-slate-300">{children}</td>
  ),
};

export function PicoRow({ icon, label, value, color }: { icon: React.ReactNode; label: string; value?: string; color: string }) {
  if (!value) return null;
  return (
    <div className="flex items-start gap-3 py-2">
      <div className="mt-0.5 shrink-0" style={{ color }}>{icon}</div>
      <div className="min-w-0">
        <span className="text-[10px] font-bold uppercase tracking-widest block" style={{ color }}>{label}</span>
        <span className="text-[12px] leading-relaxed text-gray-300 block mt-0.5">{value}</span>
      </div>
    </div>
  );
}

export function SectionCard({ section }: { section: KBSection }) {
  const color = getSectionColor(section.title);
  const icon = getSectionIcon(section.title);
  const markdown = useMemo(() => sectionToMarkdown(section.content), [section.content]);

  const displayTitle = section.title.replace(/\(.*?\)/, '').trim();
  const source = section.title.match(/\((.+?)\)/)?.[1];

  return (
    <div className="rounded-xl border border-slate-700 bg-slate-900/75 overflow-hidden shadow-sm">
      <div className="flex items-center gap-2 px-4 py-3 border-b border-slate-700/60" style={{ background: `linear-gradient(135deg, ${color}10, transparent)` }}>
        <div style={{ color }}>{icon}</div>
        <h3 className="text-[11px] font-bold uppercase tracking-wider flex-1" style={{ color }}>
          {displayTitle}
        </h3>
        {source && (
          <span className="text-[9px] rounded px-2 py-0.5 font-mono" style={{ color, background: `${color}15`, border: `1px solid ${color}30` }}>
            {source}
          </span>
        )}
      </div>
      <div className="p-4">
        <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
          {markdown}
        </ReactMarkdown>
      </div>
    </div>
  );
}

export function PaperCard({ paper }: { paper: Paper }) {
  const [expanded, setExpanded] = useState(false);
  const cat = CATEGORY_COLORS[paper.category || ''] || CATEGORY_COLORS.Review;

  return (
    <div className="rounded-lg border border-slate-700 bg-slate-900/75 p-4 hover:border-slate-500 transition-all duration-200 hover:shadow-md">
      <div className="flex items-center gap-2 mb-2">
        {paper.rank && (
          <span className="w-6 h-6 rounded-full bg-[#0d2847] text-[#5acce6] text-[10px] font-bold flex items-center justify-center border border-[#5acce6]/30">
            {paper.rank}
          </span>
        )}
        {paper.category && (
          <span
            className="text-[9px] font-bold uppercase tracking-wider rounded px-2 py-0.5"
            style={{ color: cat.text, background: cat.bg, border: `1px solid ${cat.border}` }}
          >
            {paper.category}
          </span>
        )}
        {paper.source && (
          <span className="text-[10px] text-slate-500 ml-auto">{paper.source}</span>
        )}
      </div>
      <h4 className="text-[12.5px] font-semibold text-white leading-snug mb-1.5">
        {paper.url ? (
          <a href={paper.url} target="_blank" rel="noopener noreferrer" className="hover:text-[#5acce6] transition-colors inline-flex items-start gap-1.5">
            {paper.title}
            <ExternalLinkIcon size={11} className="shrink-0 mt-0.5 text-[#566a84]" />
          </a>
        ) : paper.title}
      </h4>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-slate-400 mb-2">
        {paper.authors && <span>{paper.authors}</span>}
        {paper.journal && <span className="font-medium text-sky-300">{paper.journal}</span>}
        {paper.year && <span>({paper.year})</span>}
        {(paper.citation_count != null && paper.citation_count > 0) && (
          <span className="flex items-center gap-1">
            <AwardIcon size={10} /> {paper.citation_count} citations
          </span>
        )}
      </div>
      {paper.fields_of_study && paper.fields_of_study.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-2">
          {paper.fields_of_study.map((f, i) => (
            <span key={i} className="text-[9px] rounded px-1.5 py-0.5 bg-[#1a0d3d] text-[#a78bfa] border border-[#a78bfa]/20">
              {f}
            </span>
          ))}
        </div>
      )}
      {paper.topic && (
        <div className="text-[10px] text-slate-500 mb-2">
          <span className="font-medium">Topic:</span> {paper.topic}
        </div>
      )}
      {paper.abstract && (
        <div>
          <button
            onClick={() => setExpanded(v => !v)}
            className="flex items-center gap-1 text-[10px] text-[#5acce6] hover:text-[#7dd8f0] transition-colors mb-1"
          >
            {expanded ? <ChevronUpIcon size={12} /> : <ChevronDownIcon size={12} />}
            {expanded ? 'Hide abstract' : 'Show abstract'}
          </button>
          {expanded && (
            <div className="rounded-lg bg-[#060d14] border border-[#1f2d40]/50 p-3 mt-1">
              <div className="flex items-start gap-2">
                <QuoteIcon size={12} className="text-slate-500 shrink-0 mt-0.5" />
                <p className="text-[11px] leading-7 text-slate-300">{paper.abstract}</p>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
