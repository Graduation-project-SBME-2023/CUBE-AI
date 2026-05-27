import React, { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  BookOpenText,
  FlaskConical,
  FileSearch,
  Loader2,
  ChevronDown,
  ChevronUp,
  FileText,
  Globe,
  Award,
  Users,
  Stethoscope,
  GitCompare,
  Target,
  ChevronRight,
} from 'lucide-react';
import { LiteratureReviewViewProps, Paper } from './literatureReview/types';
import { parseOutput, parseKnowledgeBase, CATEGORY_COLORS, sectionToMarkdown } from './literatureReview/utils';
import { mdComponents, PicoRow, SectionCard, PaperCard } from './literatureReview/Shared';

const BookOpenTextIcon: any = BookOpenText;
const FlaskConicalIcon: any = FlaskConical;
const FileSearchIcon: any = FileSearch;
const FileTextIcon: any = FileText;
const Loader2Icon: any = Loader2;
const ChevronDownIcon: any = ChevronDown;
const ChevronUpIcon: any = ChevronUp;
const AwardIcon: any = Award;
const GlobeIcon: any = Globe;
const UsersIcon: any = Users;
const StethoscopeIcon: any = Stethoscope;
const GitCompareIcon: any = GitCompare;
const TargetIcon: any = Target;
const ChevronRightIcon: any = ChevronRight;

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

        {/* ── Knowledge Base Sections ──────────────────────────────────
        {kbSections.length > 0 && (
          <div className="space-y-4">
            {kbSections.map((section, i) => (
              <SectionCard key={i} section={section} />
            ))}
          </div>
        )} */}

        {/* ── Sources Guide ────────────────────────────────────────── */}
        {/* {sourcesMarkdown && (
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
        )} */}
      </div>
    </div>
  );
}
