import React from 'react';
import { BookOpenText, Globe, FlaskConical, FileSearch } from 'lucide-react';

type HypothesisDetails = {
  title?: string;
  question?: string;
  hypothesis?: string;
  population?: string;
  intervention?: string;
  comparison?: string;
  outcome?: string;
};

type LiteratureOutput = {
  knowledge_base?: string;
  sources_guide?: string;
  hypothesis?: HypothesisDetails;
};

interface LiteratureReviewViewProps {
  literatureOutput?: LiteratureOutput | string;
}

const cleanBlock = (content?: string) => {
  if (!content) {
    return 'No data available yet.';
  }

  // Preserve the original text output and trim edge spacing from generated payloads.
  return content.replace(/\r\n/g, '\n').trim();
};

const parseOutput = (input?: LiteratureOutput | string): LiteratureOutput => {
  if (!input) {
    return {};
  }

  if (typeof input === 'string') {
    try {
      const parsed = JSON.parse(input);
      return parsed;
    } catch {
      return { knowledge_base: input };
    }
  }

  return input;
};

function InfoCard({ title, children, icon }: { title: string; children: React.ReactNode; icon: React.ReactNode }) {
  return (
    <div className="bg-[#151a1f] border border-[#3c5d80] rounded-xl p-4">
      <div className="text-[11px] tracking-wider uppercase font-bold text-[#5acce6] mb-3 flex items-center gap-2">
        {icon}
        {title}
      </div>
      <div className="text-xs text-gray-200 leading-relaxed">{children}</div>
    </div>
  );
}

export default function LiteratureReviewView({ literatureOutput }: LiteratureReviewViewProps) {
  const parsed = parseOutput(literatureOutput);
  const hypothesis = parsed.hypothesis || {};

  return (
    <div className="w-full h-full overflow-auto bg-[#000000]">
      <div className="p-6 space-y-4">
        <div className="bg-[#0b1015] border border-[#1f2d40] rounded-xl px-4 py-3 flex items-center gap-3">
          <BookOpenText size={18} className="text-[#5acce6]" />
          <div>
            <p className="text-[11px] uppercase tracking-widest text-[#566a84] font-bold">Literature Review</p>
            <p className="text-sm text-white">Evidence Summary, Search Sources, and Hypothesis Context</p>
          </div>
        </div>

        <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
          <InfoCard title="Hypothesis Summary" icon={<FlaskConical size={14} />}>
            <div className="space-y-2">
              <div>
                <span className="text-[#91b9cd]">Title: </span>
                <span>{hypothesis.title || 'Not available'}</span>
              </div>
              <div>
                <span className="text-[#91b9cd]">Question: </span>
                <span>{hypothesis.question || 'Not available'}</span>
              </div>
              <div>
                <span className="text-[#91b9cd]">Hypothesis: </span>
                <span>{hypothesis.hypothesis || 'Not available'}</span>
              </div>
            </div>
          </InfoCard>

          <InfoCard title="PICO Snapshot" icon={<FileSearch size={14} />}>
            <div className="space-y-2">
              <div>
                <span className="text-[#91b9cd]">Population: </span>
                <span>{hypothesis.population || 'Not available'}</span>
              </div>
              <div>
                <span className="text-[#91b9cd]">Intervention: </span>
                <span>{hypothesis.intervention || 'Not available'}</span>
              </div>
              <div>
                <span className="text-[#91b9cd]">Comparison: </span>
                <span>{hypothesis.comparison || 'Not available'}</span>
              </div>
              <div>
                <span className="text-[#91b9cd]">Outcome: </span>
                <span>{hypothesis.outcome || 'Not available'}</span>
              </div>
            </div>
          </InfoCard>
        </div>

        <InfoCard title="Knowledge Base Output" icon={<BookOpenText size={14} />}>
          <pre className="text-[11px] leading-5 text-gray-300 bg-[#0b1015] border border-[#1f2d40] rounded-lg p-4 overflow-auto whitespace-pre-wrap font-mono">
            {cleanBlock(parsed.knowledge_base)}
          </pre>
        </InfoCard>

        <InfoCard title="Sources Guide" icon={<Globe size={14} />}>
          <pre className="text-[11px] leading-5 text-gray-300 bg-[#0b1015] border border-[#1f2d40] rounded-lg p-4 overflow-auto whitespace-pre-wrap font-mono">
            {cleanBlock(parsed.sources_guide)}
          </pre>
        </InfoCard>
      </div>
    </div>
  );
}
