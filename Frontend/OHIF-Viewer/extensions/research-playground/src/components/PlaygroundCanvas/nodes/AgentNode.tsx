import React, { memo, useState, useRef } from 'react';
import {
  Activity,
  CheckCircle2,
  Circle,
  Trash2,
  Edit3,
  ChevronDown,
  ChevronUp,
  FileText,
} from 'lucide-react';
import { AgentNodeProps } from '../types';
import { AgentLogRenderer } from '../logs/AgentLogRenderer';

const AgentNode = memo((props: AgentNodeProps) => {
  const { data, id } = props;
  const {
    title,
    name,
    description,
    status = 'pending',
    logs = [],
    isExpanded,
    onDelete,
    onEdit,
    onToggleExpand,
    mode,
  } = data;

  const displayTitle = title || name || 'Agent Step';
  const [isHovered, setIsHovered] = useState(false);
  const [tooltipVisible, setTooltipVisible] = useState(false);
  const headerRef = useRef<HTMLDivElement>(null);

  const isPlanning = mode === 'planning';
  const isExecuting = !isPlanning;

  // Styles based on status
  let borderColor = 'border-slate-700';
  let bgColor = 'bg-slate-900/95';
  let textColor = 'text-slate-100';
  let Icon = Circle;
  let iconColor = 'text-slate-500';
  let shadow = 'shadow-md';
  let statusLabel = 'Pending';
  let statusBg = 'bg-slate-800';
  let statusText = 'text-slate-400';
  let accentBorder = '';

  if (status === 'active') {
    borderColor = 'border-cyan-500/80';
    bgColor = 'bg-slate-900';
    textColor = 'text-cyan-300';
    Icon = Activity;
    iconColor = 'text-cyan-400';
    shadow = 'shadow-[0_0_20px_rgba(34,211,238,0.22)]';
    statusLabel = 'Running';
    statusBg = 'bg-cyan-950/70';
    statusText = 'text-cyan-300';
    accentBorder = 'ring-1 ring-cyan-500/30';
  } else if (status === 'completed') {
    borderColor = 'border-emerald-600/70';
    bgColor = 'bg-slate-900';
    textColor = 'text-emerald-300';
    Icon = CheckCircle2;
    iconColor = 'text-emerald-400';
    statusLabel = 'Done';
    statusBg = 'bg-emerald-950/60';
    statusText = 'text-emerald-300';
  }

  const handleEditClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onEdit?.(id);
  };

  const handleDeleteClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    onDelete?.(id);
  };

  const handleNodeClick = () => {
    if (status === 'active' || status === 'completed') {
      onToggleExpand?.(id);
    }
  };

  return (
    <div
      className="group relative"
      onMouseEnter={() => { setIsHovered(true); setTooltipVisible(true); }}
      onMouseLeave={() => { setIsHovered(false); setTooltipVisible(false); }}
      onClick={handleNodeClick}
    >
      {/* Execution-mode description tooltip */}
      {isExecuting && description && tooltipVisible && (
        <div
          className="absolute bottom-full left-1/2 z-50 mb-2 -translate-x-1/2 animate-in fade-in duration-150"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="w-64 rounded-lg border border-slate-600 bg-slate-900 px-3 py-2 text-[11px] leading-relaxed text-slate-300 shadow-xl">
            {description}
          </div>
          {/* Arrow */}
          <div className="mx-auto h-0 w-0 border-x-4 border-t-4 border-x-transparent border-t-slate-600" />
        </div>
      )}

      <div
        className={`
          relative flex w-[292px] flex-col overflow-hidden rounded-xl
          border transition-all duration-300
          ${borderColor} ${bgColor} ${shadow} ${accentBorder}
          ${(status === 'active' || status === 'completed') ? 'cursor-pointer hover:brightness-110 hover:shadow-lg' : ''}
        `}
      >
        {/* Status badge bar */}
        <div className={`flex items-center justify-between px-3 py-1 ${statusBg}`}>
          <div className="flex items-center gap-1.5">
            <Icon
              className={`${iconColor} ${status === 'active' ? 'animate-spin' : ''}`}
              size={10}
            />
            <span className={`text-[9px] font-bold uppercase tracking-widest ${statusText}`}>{statusLabel}</span>
          </div>
          <div className="flex items-center gap-1">
            {/* Edit/Delete Actions (Planning Mode) */}
            {isPlanning && (
              <div
                className={`flex gap-0.5 transition-opacity ${isHovered ? 'opacity-100' : 'opacity-0'}`}
              >
                <button
                  onClick={handleEditClick}
                  className="rounded p-0.5 text-slate-300 hover:bg-black/40"
                  title="Edit Description"
                >
                  <Edit3 size={11} />
                </button>
                <button
                  onClick={handleDeleteClick}
                  className="rounded p-0.5 text-red-400 hover:bg-[#000]/50"
                  title="Delete Step"
                >
                  <Trash2 size={11} />
                </button>
              </div>
            )}
            {/* Expand/Collapse Indicator */}
            {(status === 'active' || status === 'completed') && (
              <div className="text-slate-500">
                {isExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </div>
            )}
          </div>
        </div>

        {/* Title — wraps naturally to fit content */}
        <div className="px-3 pt-2.5 pb-1" ref={headerRef}>
          <span className={`text-[13px] font-semibold leading-snug ${textColor} select-none block`}>
            {displayTitle}
          </span>
        </div>

        {/* Description — always visible, auto-expands */}
        {description && (
          <div className="border-t border-slate-700/70 px-3 py-2">
            <p className="select-none text-[11px] leading-relaxed text-slate-400">
              {description}
            </p>
          </div>
        )}

        {/* Logs / Details Section — show whenever expanded and node is active/completed */}
        {isExpanded && (status === 'active' || status === 'completed') && (
          <div className="animate-in slide-in-from-top-2 space-y-1.5 border-t border-slate-700 bg-slate-950 p-3 font-mono text-xs duration-200">
            <div className="mb-2 flex items-center gap-2 text-[9px] font-bold uppercase tracking-widest text-sky-400">
              <FileText size={10} /> System Trace
            </div>
            {logs.length === 0 && <span className="italic text-slate-600">Awaiting events...</span>}
            {logs.slice(-5).map((log, i) => (
              <div
                key={i}
                className="flex gap-2 rounded bg-slate-900 px-2 py-1 text-slate-400 border border-slate-800"
              >
                <span className="select-none text-cyan-400 opacity-40">{'▸'}</span>
                <span className="flex-1 break-words">
                  <AgentLogRenderer log={log} />
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Connection points anchored at the status-badge row center for clean edge alignment */}
      <div className="absolute left-0 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-slate-950 bg-slate-500" style={{ top: 14 }} />
      <div className="absolute right-0 h-2.5 w-2.5 translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-slate-950 bg-slate-500" style={{ top: 14 }} />
    </div>
  );
});

export default AgentNode;