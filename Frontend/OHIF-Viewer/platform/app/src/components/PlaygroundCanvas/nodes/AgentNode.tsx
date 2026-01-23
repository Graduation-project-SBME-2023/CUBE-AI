import React, { memo, useState } from 'react';
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
    // Only allow expanding if we are executing/viewing results
    if (mode !== 'planning' && (status === 'active' || status === 'completed')) {
      onToggleExpand?.(id);
    }
  };

  return (
    <div
      className="group relative"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={handleNodeClick}
    >
      <div
        className={`
          relative flex min-w-[200px] flex-col justify-center overflow-hidden rounded 
          border transition-all duration-300
          ${borderColor} ${bgColor} ${shadow}
          ${mode !== 'planning' && (status === 'active' || status === 'completed') ? 'cursor-pointer hover:bg-[#1f2630]' : ''}
        `}
      >
        <div className="flex h-[50px] items-center justify-between px-3">
          <div className="flex items-center gap-3">
            <Icon
              className={`${iconColor} ${status === 'active' ? 'animate-spin' : ''}`}
              size={16}
            />
            <span className={`text-sm font-medium ${textColor} select-none`}>{displayTitle}</span>
          </div>

          {/* Edit/Delete Actions (Planning Mode) */}
          {mode === 'planning' && (
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
          {mode !== 'planning' && (status === 'active' || status === 'completed') && (
            <div className="text-[#566a84]">
              {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </div>
          )}
        </div>

        {/* Logs / Details Section */}
        {isExpanded && mode !== 'planning' && (
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

      {/* Tooltip Description */}
      {isHovered && description && (
        <div className="animate-in fade-in zoom-in-95 pointer-events-none absolute bottom-full left-1/2 z-50 mb-2 w-48 -translate-x-1/2 rounded border border-[#3c5d80] bg-[#090c29] p-2 text-xs text-[#dadada] shadow-xl">
          {description}
        </div>
      )}

      {/* Connection Points (Visual only) */}
      <div className="absolute left-0 top-[25px] h-2 w-2 -translate-y-1/2 -translate-x-1/2 rounded-full border border-black bg-[#566a84]" />
      <div className="absolute right-0 top-[25px] h-2 w-2 -translate-y-1/2 translate-x-1/2 rounded-full border border-black bg-[#566a84]" />
    </div>
  );
});

export default AgentNode;
