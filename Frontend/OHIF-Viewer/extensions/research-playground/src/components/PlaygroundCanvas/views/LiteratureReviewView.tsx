import React, { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  BookOpenText,
  Globe,
  FlaskConical,
  FileSearch,
  Microscope,
  Tag,
  TrendingUp,
  Newspaper,
  ShieldCheck,
  Database,
  Loader2,
  Users,
  Stethoscope,
  GitCompare,
  Target,
  ChevronRight,
  ExternalLink,
  Award,
  ChevronDown,
  ChevronUp,
  Quote,
  FileText,
} from 'lucide-react';

/* ── Types ────────────────────────────────────────────────────────────────── */

type HypothesisDetails = {
  title?: string;
  question?: string;
  hypothesis?: string;
  population?: string;
  intervention?: string;
  comparison?: string;
  outcome?: string;
};

type Paper = {
  rank?: number;
  title?: string;
  authors?: string;
  year?: string | number;
  journal?: string;
  abstract?: string;
  source?: string;
  url?: string;
  category?: string;
  topic?: string;
  citation_count?: number;
  fields_of_study?: string[];
};

type LiteratureOutput = {
  knowledge_base?: string;
  sources_guide?: string;
  hypothesis?: HypothesisDetails;
  papers?: Paper[];
  report?: string;
};

interface LiteratureReviewViewProps {
  literatureOutput?: LiteratureOutput | string;
}

/* ── Icon aliases for lucide-react JSX type compat ────────────────────────── */
const BookOpenTextIcon: any = BookOpenText;
const GlobeIcon: any = Globe;
const FlaskConicalIcon: any = FlaskConical;
const FileSearchIcon: any = FileSearch;
const MicroscopeIcon: any = Microscope;
const TagIcon: any = Tag;
const TrendingUpIcon: any = TrendingUp;
const NewspaperIcon: any = Newspaper;
const ShieldCheckIcon: any = ShieldCheck;
const DatabaseIcon: any = Database;
const Loader2Icon: any = Loader2;
const UsersIcon: any = Users;
const StethoscopeIcon: any = Stethoscope;
const GitCompareIcon: any = GitCompare;
const TargetIcon: any = Target;
const ChevronRightIcon: any = ChevronRight;
const ExternalLinkIcon: any = ExternalLink;
const AwardIcon: any = Award;
const ChevronDownIcon: any = ChevronDown;
const ChevronUpIcon: any = ChevronUp;
const QuoteIcon: any = Quote;
const FileTextIcon: any = FileText;

/* ── Helpers ──────────────────────────────────────────────────────────────── */

const parseOutput = (input?: LiteratureOutput | string): LiteratureOutput => {
  if (!input) return {};
  if (typeof input === 'string') {
    try { return JSON.parse(input); } catch { return { knowledge_base: input }; }
  }
  return input;
};

interface KBSection {
  title: string;
  content: string;
}

/** Parse the knowledge base text into titled sections */
function parseKnowledgeBase(kb: string): KBSection[] {
  if (!kb) return [];

  // Split on section headers like ─── TITLE ───
  const sectionRegex = /─{3,}\s*(.+?)\s*─{3,}/g;
  const sections: KBSection[] = [];
  const matches: { title: string; index: number }[] = [];

  let match: RegExpExecArray | null;
  while ((match = sectionRegex.exec(kb)) !== null) {
    matches.push({ title: match[1].trim(), index: match.index + match[0].length });
  }

  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].index;
    const end = i + 1 < matches.length
      ? kb.lastIndexOf('─', matches[i + 1].index - 1)
      : kb.length;
    const content = kb.slice(start, end).replace(/^[═─\s]+|[═─\s]+$/g, '').trim();
    if (content) {
      sections.push({ title: matches[i].title, content });
    }
  }

  // If no sections found, treat the whole thing as one section
  if (sections.length === 0 && kb.trim()) {
    sections.push({ title: 'Knowledge Base', content: kb.trim() });
  }

  return sections;
}

/**
 * Convert the knowledge_builder's custom text format (│ prefixed, [i] refs,
 * • bullets) into proper markdown for react-markdown rendering.
 */
function sectionToMarkdown(raw: string): string {
  let md = raw
    // strip leading │ pipes used as visual borders
    .replace(/^│\s?/gm, '')
    // convert topic headers like "LIVER RADIOMICS — Recent Evidence" to ### headers
    .replace(/^([A-Z][A-Z &,/\-—()]+(?:\s—\s.+)?)$/gm, '\n### $1\n')
    // convert numbered refs like [1] Title  to **1.** **Title**
    .replace(/^\s*\[(\d+)\]\s*(.+)$/gm, '**$1.** **$2**')
    // convert arrows → to blockquote-style finding lines
    .replace(/^\s*→\s*(.+)$/gm, '> $1')
    // convert bullets • to markdown list items
    .replace(/^\s*•\s*/gm, '- ')
    // convert indented info lines (Journal (Year), Fields:, TLDR:)
    .replace(/^\s{4,}((?:Fields|TLDR|PMID):?\s*.+)$/gm, '  *$1*')
    .replace(/^\s{4,}(.+\(\d{4}\).*)$/gm, '  *$1*')
    // clean up excessive blank lines
    .replace(/\n{3,}/g, '\n\n');

  return md.trim();
}

/** Map section title keywords to icons */
function getSectionIcon(title: string): React.ReactNode {
  const t = title.toLowerCase();
  if (t.includes('anatomy') || t.includes('pathology') || t.includes('clinical'))
    return <MicroscopeIcon size={14} />;
  if (t.includes('mesh') || t.includes('vocabulary') || t.includes('terminology'))
    return <TagIcon size={14} />;
  if (t.includes('state-of-the-art') || t.includes('evidence') || t.includes('review'))
    return <BookOpenTextIcon size={14} />;
  if (t.includes('method') || t.includes('trend'))
    return <TrendingUpIcon size={14} />;
  if (t.includes('journal') || t.includes('publication'))
    return <NewspaperIcon size={14} />;
  if (t.includes('guideline') || t.includes('practice'))
    return <ShieldCheckIcon size={14} />;
  if (t.includes('dataset') || t.includes('benchmark'))
    return <DatabaseIcon size={14} />;
  return <FileSearchIcon size={14} />;
}

/** Map section title to accent color */
function getSectionColor(title: string): string {
  const t = title.toLowerCase();
  if (t.includes('anatomy') || t.includes('pathology')) return '#e879a5';
  if (t.includes('mesh') || t.includes('vocabulary')) return '#d4a843';
  if (t.includes('state-of-the-art') || t.includes('evidence')) return '#5acce6';
  if (t.includes('method') || t.includes('trend')) return '#a78bfa';
  if (t.includes('journal') || t.includes('publication')) return '#f59e42';
  if (t.includes('guideline')) return '#6fe5b8';
  if (t.includes('dataset') || t.includes('benchmark')) return '#60a5fa';
  return '#5acce6';
}

const CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  Review: { bg: '#0d2847', text: '#5acce6', border: '#5acce630' },
  Trending: { bg: '#1a0d3d', text: '#a78bfa', border: '#a78bfa30' },
  Guideline: { bg: '#0d3320', text: '#6fe5b8', border: '#6fe5b830' },
  Dataset: { bg: '#1a2a0d', text: '#60a5fa', border: '#60a5fa30' },
};

/* ── Markdown custom components for dark theme ────────────────────────────── */

const mdComponents: Record<string, React.FC<any>> = {
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

/* ── Subcomponents ────────────────────────────────────────────────────────── */

function PicoRow({ icon, label, value, color }: { icon: React.ReactNode; label: string; value?: string; color: string }) {
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

function SectionCard({ section }: { section: KBSection }) {
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

function PaperCard({ paper }: { paper: Paper }) {
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

/* ── Main Component ───────────────────────────────────────────────────────── */

export default function LiteratureReviewView({ literatureOutput }: LiteratureReviewViewProps) {
  const parsed = parseOutput(literatureOutput);
  const hypothesis = parsed.hypothesis || {};
  const papers = parsed.papers || [];
  const hasPico = hypothesis.population || hypothesis.intervention || hypothesis.comparison || hypothesis.outcome;
  const hasData = Boolean(parsed.knowledge_base || parsed.sources_guide || papers.length || parsed.report);

  const kbSections = useMemo(
    () => parseKnowledgeBase(parsed.knowledge_base || ''),
    [parsed.knowledge_base]
  );

  const sourcesMarkdown = useMemo(
    () => parsed.sources_guide ? sectionToMarkdown(parsed.sources_guide.replace(/[═─]/g, '').trim()) : '',
    [parsed.sources_guide]
  );

  const papersByCategory = useMemo(() => {
    const groups: Record<string, Paper[]> = {};
    for (const p of papers) {
      const cat = p.category || 'Other';
      (groups[cat] ??= []).push(p);
    }
    return groups;
  }, [papers]);

  const [papersExpanded, setPapersExpanded] = useState(false);

  // Empty / loading state
  if (!hasData) {
    return (
      <div className="w-full h-full flex items-center justify-center bg-slate-950">
        <div className="text-center space-y-4 max-w-md">
          <div className="mx-auto w-16 h-16 rounded-full bg-slate-900 flex items-center justify-center border border-slate-700">
            <Loader2Icon size={24} className="text-cyan-400 animate-spin" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-slate-100 mb-1">Literature Review</h2>
            <p className="text-[12px] text-slate-500 leading-relaxed">
              Waiting for the knowledge base to be built. The literature review will appear here
              once the agent finishes searching PubMed, Semantic Scholar, and other databases.
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="w-full h-full overflow-auto bg-slate-950">
      <div className="max-w-5xl mx-auto p-6 space-y-5">

        {/* ── Header ──────────────────────────────────────────────────── */}
        <div className="rounded-xl border border-slate-700 bg-slate-900/75 px-5 py-4 flex items-center gap-4 shadow-sm">
          <div className="w-10 h-10 rounded-lg bg-[#0d2847] flex items-center justify-center border border-[#5acce6]/30">
            <BookOpenTextIcon size={20} className="text-[#5acce6]" />
          </div>
          <div className="flex-1">
            <p className="text-[11px] uppercase tracking-widest text-slate-500 font-semibold">Literature Review</p>
            <p className="text-sm text-slate-100 font-medium">Domain Knowledge Base &amp; Evidence Summary</p>
          </div>
          <div className="flex items-center gap-2">
            {papers.length > 0 && (
              <span className="text-[10px] rounded-full px-3 py-1 bg-cyan-950/70 text-cyan-300 font-semibold border border-cyan-500/30">
                {papers.length} papers
              </span>
            )}
            {kbSections.length > 0 && (
              <span className="text-[10px] rounded-full px-3 py-1 bg-emerald-950/60 text-emerald-300 font-semibold border border-emerald-500/30">
                {kbSections.length} sections
              </span>
            )}
          </div>
        </div>

        {/* ── Hypothesis & PICO ────────────────────────────────────────── */}
        {(hypothesis.hypothesis || hasPico) && (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
            {/* Hypothesis */}
            {hypothesis.hypothesis && (
              <div className="rounded-xl border border-[#1f2d40] bg-[#0b1015] overflow-hidden">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-[#1f2d40]/50 bg-gradient-to-r from-[#5acce608] to-transparent">
                  <FlaskConicalIcon size={14} className="text-[#5acce6]" />
                  <h3 className="text-[11px] font-bold uppercase tracking-wider text-[#5acce6]">Hypothesis</h3>
                </div>
                <div className="p-4 space-y-2">
                  {hypothesis.title && (
                    <p className="text-[13px] font-semibold text-white">{hypothesis.title}</p>
                  )}
                  <p className="text-[12px] leading-relaxed text-gray-300">{hypothesis.hypothesis}</p>
                  {hypothesis.question && (
                    <p className="text-[11px] italic text-[#91b9cd] border-t border-[#1f2d40]/50 pt-2 mt-2">
                      <ChevronRightIcon size={10} className="inline mr-1" />
                      {hypothesis.question}
                    </p>
                  )}
                </div>
              </div>
            )}

            {/* PICO */}
            {hasPico && (
              <div className="rounded-xl border border-[#1f2d40] bg-[#0b1015] overflow-hidden">
                <div className="flex items-center gap-2 px-4 py-3 border-b border-[#1f2d40]/50 bg-gradient-to-r from-[#a78bfa08] to-transparent">
                  <FileSearchIcon size={14} className="text-[#a78bfa]" />
                  <h3 className="text-[11px] font-bold uppercase tracking-wider text-[#a78bfa]">PICO Framework</h3>
                </div>
                <div className="p-4 space-y-0.5">
                  <PicoRow icon={<UsersIcon size={13} />} label="Population" value={hypothesis.population} color="#5acce6" />
                  <PicoRow icon={<StethoscopeIcon size={13} />} label="Intervention" value={hypothesis.intervention} color="#6fe5b8" />
                  <PicoRow icon={<GitCompareIcon size={13} />} label="Comparison" value={hypothesis.comparison} color="#f59e42" />
                  <PicoRow icon={<TargetIcon size={13} />} label="Outcome" value={hypothesis.outcome} color="#e879a5" />
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── Literature Report ────────────────────────────────────── */}
        {parsed.report && (
          <div className="rounded-xl border border-[#1f2d40] bg-[#0b1015] overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-[#1f2d40]/50 bg-gradient-to-r from-[#5acce608] to-transparent">
              <FileTextIcon size={14} className="text-[#5acce6]" />
              <h3 className="text-[11px] font-bold uppercase tracking-wider text-[#5acce6]">Literature Report</h3>
            </div>
            <div className="p-4">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                {parsed.report}
              </ReactMarkdown>
            </div>
          </div>
        )}

        {/* ── Ranked Papers (expandable) ────────────────────────────── */}
        {papers.length > 0 && (
          <div className="space-y-4">
            <div className="rounded-xl border border-[#1f2d40] bg-[#0b1015] overflow-hidden">
              <button
                onClick={() => setPapersExpanded(v => !v)}
                className="w-full flex items-center gap-2 px-4 py-3 border-b border-[#1f2d40]/50 bg-gradient-to-r from-[#a78bfa08] to-transparent hover:from-[#a78bfa12] transition-colors cursor-pointer"
              >
                <AwardIcon size={14} className="text-[#a78bfa]" />
                <h3 className="text-[11px] font-bold uppercase tracking-wider text-[#a78bfa] flex-1 text-left">
                  Ranked Papers
                </h3>
                <span className="text-[10px] text-[#566a84] mr-2">{papers.length} papers found</span>
                {papersExpanded ? <ChevronUpIcon size={14} className="text-[#566a84]" /> : <ChevronDownIcon size={14} className="text-[#566a84]" />}
              </button>
              {papersExpanded && (
                <div className="p-4 space-y-6">
                  {Object.entries(papersByCategory).map(([category, catPapers]) => (
                    <div key={category}>
                      <div className="flex items-center gap-2 mb-3">
                        <span
                          className="text-[10px] font-bold uppercase tracking-wider rounded px-2 py-0.5"
                          style={{
                            color: (CATEGORY_COLORS[category] || CATEGORY_COLORS.Review).text,
                            background: (CATEGORY_COLORS[category] || CATEGORY_COLORS.Review).bg,
                            border: `1px solid ${(CATEGORY_COLORS[category] || CATEGORY_COLORS.Review).border}`,
                          }}
                        >
                          {category}
                        </span>
                        <span className="text-[10px] text-[#566a84]">{catPapers.length} papers</span>
                        <div className="flex-1 border-t border-[#1f2d40]/30" />
                      </div>
                      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                        {catPapers.map((paper, i) => (
                          <PaperCard key={i} paper={paper} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        )}

        {/* ── Knowledge Base Sections ────────────────────────────────── */}
        {kbSections.length > 0 && (
          <div className="space-y-4">
            {kbSections.map((section, i) => (
              <SectionCard key={i} section={section} />
            ))}
          </div>
        )}

        {/* ── Sources Guide ────────────────────────────────────────── */}
        {sourcesMarkdown && (
          <div className="rounded-xl border border-[#1f2d40] bg-[#0b1015] overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-3 border-b border-[#1f2d40]/50 bg-gradient-to-r from-[#f59e4208] to-transparent">
              <GlobeIcon size={14} className="text-[#f59e42]" />
              <h3 className="text-[11px] font-bold uppercase tracking-wider text-[#f59e42]">Search Sources Guide</h3>
            </div>
            <div className="p-4">
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                {sourcesMarkdown}
              </ReactMarkdown>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
