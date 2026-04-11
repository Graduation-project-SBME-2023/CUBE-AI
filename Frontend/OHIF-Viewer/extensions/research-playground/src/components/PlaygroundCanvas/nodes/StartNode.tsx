import React, { memo, useState } from 'react';
import { StartNodeProps } from '../types';
import { FlaskConical, Users, Stethoscope, GitCompare, Target, ChevronDown, ChevronUp } from 'lucide-react';

const START_W = 340;

const PicoRow = ({ icon: Icon, label, value }: { icon: any; label: string; value?: string }) => {
  if (!value) return null;
  const display = typeof value === 'object' ? JSON.stringify(value) : value;
  return (
    <div className="flex items-start gap-2 py-0.5">
      <Icon size={11} className="mt-[2px] shrink-0 text-cyan-400" />
      <span className="text-[10px] font-semibold uppercase text-cyan-400">{label}:</span>
      <span className="text-[11px] leading-tight text-slate-300">{display}</span>
    </div>
  );
};

const StartNode = memo((props: StartNodeProps) => {
  const { data } = props;
  const hasPico = data.population || data.intervention || data.comparison || data.outcome;
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      style={{
        width: START_W,
        display: 'flex',
        flexDirection: 'column',
        borderRadius: '12px',
        background: '#0f172a',
        border: '1px solid #334155',
        color: '#cbd5e1',
        padding: '12px 16px',
        boxSizing: 'border-box',
        userSelect: 'none',
        position: 'relative',
        cursor: 'default',
        boxShadow: '0 10px 24px rgba(2, 6, 23, 0.35)',
        minHeight: 80,
      }}
    >
      {/* Header */}
      <div className="mb-1.5 flex items-center justify-center gap-2">
        <FlaskConical size={14} className="text-cyan-400" />
        <span className="text-xs font-semibold uppercase tracking-wider text-cyan-300">
          {data.title || 'Start'}
        </span>
        {data.state === 'confirmed' && (
          <span className="rounded bg-emerald-500/15 border border-emerald-500/35 px-2 py-0.5 text-[10px] font-semibold text-emerald-300">APPROVED</span>
        )}
      </div>

      {/* Hypothesis statement */}
      {data.hypothesis && (
        <div className="mb-1.5 w-full border-b border-slate-700/60 pb-1.5 text-center text-[11px] leading-relaxed text-slate-200">
          {data.hypothesis}
        </div>
      )}

      {/* Expand / Collapse toggle */}
      {hasPico && (
        <button
          onClick={() => setExpanded(v => !v)}
          className="flex items-center justify-center gap-1 w-full py-1 text-[10px] text-cyan-300 hover:text-cyan-200 transition-colors cursor-pointer bg-transparent border-none outline-none"
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? 'Hide PICO' : 'Show PICO'}
        </button>
      )}

      {/* PICO Fields — visible only when expanded */}
      {expanded && hasPico && (
        <div className="w-full space-y-0.5 mt-1 border-t border-slate-700/60 pt-1.5">
          <PicoRow icon={Users} label="P" value={data.population as string} />
          <PicoRow icon={Stethoscope} label="I" value={data.intervention as string} />
          <PicoRow icon={GitCompare} label="C" value={data.comparison as string} />
          <PicoRow icon={Target} label="O" value={data.outcome as string} />
        </div>
      )}

      {/* Fallback when no PICO */}
      {!hasPico && (
        <div className="w-full text-center text-[11px] leading-relaxed text-slate-400">
          {data.description || 'No hypothesis set.'}
        </div>
      )}

      {/* Research question — shown when expanded */}
      {expanded && data.question && (
        <div className="mt-1.5 w-full border-t border-slate-700/60 pt-1.5 text-center text-[10px] italic leading-relaxed text-slate-400">
          {data.question}
        </div>
      )}

      {/* Output handle */}
      <div
        style={{
          position: 'absolute',
          right: -5,
          top: '50%',
          transform: 'translateY(-50%)',
          width: 10,
          height: 10,
          background: '#64748b',
          border: '2px solid #020617',
          borderRadius: '50%',
        }}
      />
    </div>
  );
});

export default StartNode;