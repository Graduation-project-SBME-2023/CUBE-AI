import React, { memo, useState, useRef } from 'react';
import {
  Activity,
  CheckCircle2,
  XCircle,
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
  let borderColor = 'border-[#2a3a4e]';
  let bgColor = 'bg-[#111820]';
  let textColor = 'text-gray-200';
  let Icon = Circle;
  let iconColor = 'text-[#566a84]';
  let shadow = 'shadow-md';
  let statusLabel = 'Pending';
  let statusBg = 'bg-[#1a2332]';
  let statusText = 'text-[#566a84]';
  let accentBorder = '';

  if (status === 'active') {
    borderColor = 'border-[#5acce6]';
    bgColor = 'bg-[#0a1628]';
    textColor = 'text-[#5acce6]';
    Icon = Activity;
    iconColor = 'text-[#5acce6]';
    shadow = 'shadow-[0_0_20px_rgba(90,204,230,0.25)]';
    statusLabel = 'Running';
    statusBg = 'bg-[#0d2847]';
    statusText = 'text-[#5acce6]';
    accentBorder = 'ring-1 ring-[#5acce6]/30';
  } else if (status === 'completed') {
    borderColor = 'border-[#2d6a4f]';
    bgColor = 'bg-[#0d1a14]';
    textColor = 'text-[#6fe5b8]';
    Icon = CheckCircle2;
    iconColor = 'text-[#6fe5b8]';
    statusLabel = 'Done';
    statusBg = 'bg-[#0d3320]';
    statusText = 'text-[#6fe5b8]';
  } else if (status === 'failed') {
    borderColor = 'border-[#7a2d2d]';
    bgColor = 'bg-[#160a0a]';
    textColor = 'text-[#f87171]';
    Icon = XCircle;
    iconColor = 'text-[#f87171]';
    shadow = 'shadow-[0_0_20px_rgba(248,113,113,0.15)]';
    statusLabel = 'Failed';
    statusBg = 'bg-[#2d1010]';
    statusText = 'text-[#f87171]';
    accentBorder = 'ring-1 ring-[#f87171]/20';
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
          <div className="w-64 rounded-lg border border-[#3c5d80] bg-[#0d1117] px-3 py-2 text-[11px] leading-relaxed text-[#91b9cd] shadow-xl">
            {description}
          </div>
          {/* Arrow */}
          <div className="mx-auto h-0 w-0 border-x-4 border-t-4 border-x-transparent border-t-[#3c5d80]" />
        </div>
      )}

      <div
        className={`
          relative flex w-[280px] flex-col overflow-hidden rounded-lg
          border transition-all duration-300
          ${borderColor} ${bgColor} ${shadow} ${accentBorder}
          ${(status === 'active' || status === 'completed' || status === 'failed') ? 'cursor-pointer hover:brightness-110' : ''}
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
                  className="rounded p-0.5 text-[#91b9cd] hover:bg-[#000]/50"
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
            {(status === 'active' || status === 'completed' || status === 'failed') && (
              <div className="text-[#566a84]">
                {isExpanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
              </div>
            )}
          </div>
        </div>

        {/* Title — wraps naturally to fit content */}
        <div className="px-3 pt-2 pb-1" ref={headerRef}>
          <span className={`text-[13px] font-semibold leading-snug ${textColor} select-none block`}>
            {displayTitle}
          </span>
        </div>

        {/* Description — always visible, auto-expands */}
        {description && (
          <div className="border-t border-[#1f2d40]/60 px-3 py-2">
            <p className="select-none text-[11px] leading-relaxed text-[#7a8fa3]">
              {description}
            </p>
          </div>
        )}

        {/* Logs / Details Section — show whenever expanded and node is active/completed */}
        {isExpanded && (status === 'active' || status === 'completed' || status === 'failed') && (
          <div className="animate-in slide-in-from-top-2 space-y-1.5 border-t border-[#1f2d40] bg-[#080c10] p-3 font-mono text-xs duration-200">
            <div className="mb-2 flex items-center gap-2 text-[9px] font-bold uppercase tracking-widest text-[#5a8a9e]">
              <FileText size={10} /> System Trace
            </div>
            {logs.length === 0 && <span className="italic text-[#3a4a5a]">Awaiting events…</span>}
            {logs.slice(-5).map((log, i) => (
              <div
                key={i}
                className="flex gap-2 rounded bg-[#0d1117] px-2 py-1 text-gray-400"
              >
                <span className="select-none text-[#5acce6] opacity-40">{'▸'}</span>
                <span className="flex-1 break-words">
                  <AgentLogRenderer log={log} />
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Connection points anchored at the status-badge row center for clean edge alignment */}
      <div className="absolute left-0 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#0d1117] bg-[#566a84]" style={{ top: 14 }} />
      <div className="absolute right-0 h-2.5 w-2.5 translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-[#0d1117] bg-[#566a84]" style={{ top: 14 }} />
    </div>
  );
});

export default AgentNode;