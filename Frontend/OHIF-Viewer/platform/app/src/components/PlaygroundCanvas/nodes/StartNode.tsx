import React, { memo } from 'react';
import { StartNodeProps } from '../types';
import { FlaskConical, Users, Stethoscope, GitCompare, Target } from 'lucide-react';

const START_W = 340;

const PicoRow = ({ icon: Icon, label, value }: { icon: any; label: string; value?: string }) => {
  if (!value) return null;
  const display = typeof value === 'object' ? JSON.stringify(value) : value;
  return (
    <div className="flex items-start gap-2 py-[2px]">
      <Icon size={11} className="mt-[2px] shrink-0 text-[#5acce6]" />
      <span className="text-[10px] font-semibold uppercase text-[#5acce6]">{label}:</span>
      <span className="text-[10px] leading-tight text-gray-300">{display}</span>
    </div>
  );
};

const StartNode = memo((props: StartNodeProps) => {
  const { data } = props;
  const hasPico = data.population || data.intervention || data.comparison || data.outcome;

  return (
    <div
      style={{
        width: START_W,
        display: 'flex',
        flexDirection: 'column',
        borderRadius: '12px',
        background: '#151a1f',
        border: '2px solid #3c5d80',
        color: '#91b9cd',
        padding: '10px 16px',
        boxSizing: 'border-box',
        userSelect: 'none',
        position: 'relative',
        cursor: 'default',
        boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
        minHeight: 80,
      }}
    >
      {/* Header */}
      <div className="mb-1 flex items-center justify-center gap-2">
        <FlaskConical size={14} className="text-[#5acce6]" />
        <span className="text-xs font-bold uppercase tracking-wider text-[#5acce6]">
          {data.title || 'Start'}
        </span>
        {data.state === 'confirmed' && (
          <span className="rounded bg-[#0d3320] px-1.5 py-0.5 text-[9px] font-bold text-[#6fe5b8]">APPROVED</span>
        )}
      </div>

      {/* Hypothesis statement */}
      {data.hypothesis && (
        <div className="mb-1 w-full border-b border-[#3c5d80]/40 pb-1 text-center text-[11px] leading-tight text-gray-200">
          {data.hypothesis}
        </div>
      )}

      {/* PICO Fields */}
      {hasPico ? (
        <div className="w-full space-y-[1px]">
          <PicoRow icon={Users} label="P" value={data.population as string} />
          <PicoRow icon={Stethoscope} label="I" value={data.intervention as string} />
          <PicoRow icon={GitCompare} label="C" value={data.comparison as string} />
          <PicoRow icon={Target} label="O" value={data.outcome as string} />
        </div>
      ) : (
        <div className="w-full text-center text-[11px] leading-tight text-gray-400">
          {data.description || 'No hypothesis set.'}
        </div>
      )}

      {/* Research question */}
      {data.question && (
        <div className="mt-1 w-full border-t border-[#3c5d80]/40 pt-1 text-center text-[10px] italic leading-tight text-gray-400">
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
          background: '#566a84',
          border: '2px solid #0d1117',
          borderRadius: '50%',
        }}
      />
    </div>
  );
});

export default StartNode;