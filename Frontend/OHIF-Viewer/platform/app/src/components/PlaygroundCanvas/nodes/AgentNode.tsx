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
  let borderColor = 'border-[#3c5d80]';
  let bgColor = 'bg-[#151a1f]';
  let textColor = 'text-white';
  let Icon = Circle;
  let iconColor = 'text-[#566a84]';
  let shadow = 'shadow-none';

  if (status === 'active') {
    borderColor = 'border-[#5acce6]';
    bgColor = 'bg-[#090c29]';
    textColor = 'text-[#5acce6]';
    Icon = Activity;
    iconColor = 'text-[#5acce6]';
    shadow = 'shadow-[0_0_15px_rgba(90,204,230,0.3)]';
  } else if (status === 'completed') {
    borderColor = 'border-[#5acce6]';
    bgColor = 'bg-[#151a1f]';
    textColor = 'text-[#5acce6]';
    Icon = CheckCircle2;
    iconColor = 'text-[#5acce6]';
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
    if (isExecuting && (status === 'active' || status === 'completed')) {
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
          <div className="w-48 rounded border border-[#3c5d80] bg-[#0d1117] px-3 py-2 text-[11px] leading-relaxed text-[#91b9cd] shadow-lg">
            {description}
          </div>
          {/* Arrow */}
          <div className="mx-auto h-0 w-0 border-x-4 border-t-4 border-x-transparent border-t-[#3c5d80]" />
        </div>
      )}

      <div
        className={`
          relative flex w-[220px] flex-col justify-center overflow-hidden rounded 
          border transition-all duration-300
          ${borderColor} ${bgColor} ${shadow}
          ${isExecuting && (status === 'active' || status === 'completed') ? 'cursor-pointer hover:bg-[#1f2630]' : ''}
        `}
      >
        {/* Header row — connection points anchor to this */}
        <div className="relative flex h-[50px] items-center justify-between px-3" ref={headerRef}>
          <div className="flex items-center gap-3">
            <Icon
              className={`${iconColor} ${status === 'active' ? 'animate-spin' : ''}`}
              size={16}
            />
            <span className={`text-sm font-medium ${textColor} select-none text-center`}>{displayTitle}</span>
          </div>

          {/* Edit/Delete Actions (Planning Mode) */}
          {isPlanning && (
            <div
              className={`flex gap-1 transition-opacity ${isHovered ? 'opacity-100' : 'opacity-0'}`}
            >
              <button
                onClick={handleEditClick}
                className="rounded p-1 text-[#91b9cd] hover:bg-[#000]"
                title="Edit Description"
              >
                <Edit3 size={14} />
              </button>
              <button
                onClick={handleDeleteClick}
                className="rounded p-1 text-red-400 hover:bg-[#000]"
                title="Delete Step"
              >
                <Trash2 size={14} />
              </button>
            </div>
          )}

          {/* Expand/Collapse Indicator (Execution Mode) */}
          {isExecuting && (status === 'active' || status === 'completed') && (
            <div className="text-[#566a84]">
              {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </div>
          )}
        </div>

        {/* Description — planning mode only */}
        {isPlanning && description && (
          <div className="border-t border-[#3c5d80]/40 px-3 py-1.5">
            <p className="select-none text-[11px] leading-relaxed text-[#566a84]">
              {description}
            </p>
          </div>
        )}

        {/* Logs / Details Section */}
        {isExpanded && isExecuting && (
          <div className="animate-in slide-in-from-top-2 space-y-1 border-t border-[#3c5d80] bg-[#000000] p-3 font-mono text-xs duration-200">
            <div className="mb-2 flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider text-[#91b9cd]">
              <FileText size={10} /> System Trace
            </div>
            {logs.length === 0 && <span className="italic text-gray-600">Initializing...</span>}
            {logs.slice(-5).map((log, i) => (
              <div
                key={i}
                className="flex gap-2 text-gray-400"
              >
                <span className="select-none text-[#5acce6] opacity-50">{'>'}</span>
                <span>
                  <AgentLogRenderer log={log} />
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Connection points stay at node center so edges look balanced even as content grows. */}
      <div className="absolute left-0 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-black bg-[#566a84]" style={{ top: '50%' }} />
      <div className="absolute right-0 h-2 w-2 translate-x-1/2 -translate-y-1/2 rounded-full border border-black bg-[#566a84]" style={{ top: '50%' }} />
    </div>
  );
});

export default AgentNode;