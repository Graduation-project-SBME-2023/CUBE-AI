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

  const [activeTab, setActiveTab] = useState<'overview' | 'report' | 'papers'>('overview');

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
    <div className="w-full h-full overflow-auto bg-[#06090e] flex flex-col [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-[#1f2d40] [&::-webkit-scrollbar-thumb]:rounded-full hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]/50 transition-colors">
      <div className="max-w-6xl mx-auto p-6 md:p-8 space-y-6 w-full flex-1">
        {/* ── Header ──────────────────────────────────────────────────── */}
        <div className="rounded-3xl border border-white/5 bg-[#0b1015]/80 backdrop-blur-md px-6 py-5 flex items-center gap-5 shadow-lg shadow-black/20">
          <div className="w-12 h-12 rounded-2xl bg-[#0d2847]/80 flex items-center justify-center border border-[#5acce6]/20 shadow-[0_0_15px_rgba(90,204,230,0.15)]">
            <BookOpenTextIcon size={22} className="text-[#5acce6]" />
          </div>
          <div className="flex-1">
            <p className="text-[10px] uppercase tracking-[0.2em] text-[#5acce6]/70 font-bold mb-0.5">Literature Review</p>
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

        {/* ── Tabs Navigation ────────────────────────────────────────── */}
        <div className="flex items-center gap-2 p-1.5 bg-[#0b1015]/60 border border-white/5 rounded-2xl w-fit backdrop-blur-md shadow-sm">
          <button
            onClick={() => setActiveTab('overview')}
            className={`px-5 py-2.5 rounded-xl text-[11px] font-bold uppercase tracking-wider transition-all duration-300 ${
              activeTab === 'overview'
                ? 'bg-[#5acce6]/15 text-[#5acce6] shadow-sm shadow-[#5acce6]/10'
                : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
            }`}
          >
            Overview & PICO
          </button>
          {parsed.report && (
            <button
              onClick={() => setActiveTab('report')}
              className={`px-5 py-2.5 rounded-xl text-[11px] font-bold uppercase tracking-wider transition-all duration-300 ${
                activeTab === 'report'
                  ? 'bg-[#a78bfa]/15 text-[#a78bfa] shadow-sm shadow-[#a78bfa]/10'
                  : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
              }`}
            >
              Full Report
            </button>
          )}
          {papers.length > 0 && (
            <button
              onClick={() => setActiveTab('papers')}
              className={`px-5 py-2.5 rounded-xl text-[11px] font-bold uppercase tracking-wider transition-all duration-300 ${
                activeTab === 'papers'
                  ? 'bg-[#5acce6]/15 text-[#5acce6] shadow-sm shadow-[#5acce6]/10'
                  : 'text-slate-500 hover:text-slate-300 hover:bg-white/5'
              }`}
            >
              Ranked Papers
            </button>
          )}
        </div>

        {/* ── Tab Content ───────────────────────────────────────────── */}
        <div className="pt-2 pb-10">
          {/* OVERVIEW TAB */}
          {activeTab === 'overview' && (
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
              {/* Hypothesis */}
              {hypothesis.hypothesis && (
                <div className="rounded-3xl border border-white/5 bg-[#0b1015]/60 backdrop-blur-md overflow-hidden shadow-lg shadow-black/20 h-fit transition-all duration-300 hover:border-white/10 hover:shadow-cyan-900/5">
                  <div className="flex items-center gap-3 px-6 py-4 border-b border-white/5 bg-gradient-to-r from-[#5acce6]/[0.03] to-transparent">
                    <FlaskConicalIcon size={16} className="text-[#5acce6]" />
                    <h3 className="text-[11px] font-bold uppercase tracking-widest text-[#5acce6]">Hypothesis</h3>
                  </div>
                  <div className="p-6 space-y-4">
                    {hypothesis.title && (
                      <p className="text-[15px] font-medium text-white tracking-wide">{hypothesis.title}</p>
                    )}
                    <p className="text-[13px] leading-relaxed text-slate-300">{hypothesis.hypothesis}</p>
                    {hypothesis.question && (
                      <div className="rounded-xl bg-[#5acce6]/5 border border-[#5acce6]/10 p-3 mt-4">
                        <p className="text-[12px] italic text-[#91b9cd] flex items-start gap-2">
                          <ChevronRightIcon size={14} className="mt-0.5 shrink-0 text-[#5acce6]/70" />
                          <span>{hypothesis.question}</span>
                        </p>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* PICO */}
              {hasPico && (
                <div className="rounded-3xl border border-white/5 bg-[#0b1015]/60 backdrop-blur-md overflow-hidden shadow-lg shadow-black/20 h-fit transition-all duration-300 hover:border-white/10 hover:shadow-purple-900/5">
                  <div className="flex items-center gap-3 px-6 py-4 border-b border-white/5 bg-gradient-to-r from-[#a78bfa]/[0.03] to-transparent">
                    <FileSearchIcon size={16} className="text-[#a78bfa]" />
                    <h3 className="text-[11px] font-bold uppercase tracking-widest text-[#a78bfa]">PICO Framework</h3>
                  </div>
                  <div className="p-6 space-y-2">
                    <PicoRow icon={<UsersIcon size={14} />} label="Population" value={hypothesis.population} color="#5acce6" />
                    <PicoRow icon={<StethoscopeIcon size={14} />} label="Intervention" value={hypothesis.intervention} color="#6fe5b8" />
                    <PicoRow icon={<GitCompareIcon size={14} />} label="Comparison" value={hypothesis.comparison} color="#f59e42" />
                    <PicoRow icon={<TargetIcon size={14} />} label="Outcome" value={hypothesis.outcome} color="#e879a5" />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* REPORT TAB */}
          {activeTab === 'report' && parsed.report && (
            <div className="rounded-3xl border border-white/5 bg-[#0b1015]/60 backdrop-blur-md overflow-hidden shadow-lg shadow-black/20">
              <div className="flex items-center gap-3 px-6 py-4 border-b border-white/5 bg-gradient-to-r from-[#a78bfa]/[0.03] to-transparent">
                <FileTextIcon size={16} className="text-[#a78bfa]" />
                <h3 className="text-[11px] font-bold uppercase tracking-widest text-[#a78bfa]">Full Literature Report</h3>
              </div>
              <div className="p-8 max-w-4xl mx-auto">
                <ReactMarkdown remarkPlugins={[remarkGfm]} components={mdComponents}>
                  {parsed.report}
                </ReactMarkdown>
              </div>
            </div>
          )}

          {/* PAPERS TAB */}
          {activeTab === 'papers' && papers.length > 0 && (
            <div className="rounded-3xl border border-white/5 bg-[#0b1015]/60 backdrop-blur-md overflow-hidden shadow-lg shadow-black/20">
              <div className="flex items-center gap-3 px-6 py-4 border-b border-white/5 bg-gradient-to-r from-[#5acce6]/[0.03] to-transparent">
                <AwardIcon size={16} className="text-[#5acce6]" />
                <h3 className="text-[11px] font-bold uppercase tracking-widest text-[#5acce6] flex-1">
                  Ranked Papers
                </h3>
                <span className="text-[10px] font-medium text-slate-400 bg-black/20 px-3 py-1 rounded-full border border-white/5">{papers.length} papers found</span>
              </div>
              <div className="p-6 md:p-8 space-y-10">
                {Object.entries(papersByCategory).map(([category, catPapers]) => (
                  <div key={category}>
                    <div className="flex items-center gap-3 mb-5">
                      <span
                        className="text-[10px] font-bold uppercase tracking-widest rounded-lg px-3 py-1 shadow-sm"
                        style={{
                          color: (CATEGORY_COLORS[category] || CATEGORY_COLORS.Review).text,
                          background: (CATEGORY_COLORS[category] || CATEGORY_COLORS.Review).bg,
                          border: `1px solid ${(CATEGORY_COLORS[category] || CATEGORY_COLORS.Review).border}`,
                        }}
                      >
                        {category}
                      </span>
                      <span className="text-[11px] font-medium text-slate-500">{catPapers.length} papers</span>
                      <div className="flex-1 border-t border-white/5 ml-2" />
                    </div>
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                      {catPapers.map((paper, i) => (
                        <PaperCard key={i} paper={paper} />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

