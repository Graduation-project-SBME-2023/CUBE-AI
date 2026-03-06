import React from 'react';
import { CheckCircle2, PanelLeftClose, PanelLeftOpen } from 'lucide-react';

interface PhaseSidebarProps {
    phases: any[];
    activePhase: string;
    onSelectPhase: (id: string) => void;
    isCollapsed: boolean;
    onToggleCollapse: () => void;
}

export default function PhaseSidebar({
    phases,
    activePhase,
    onSelectPhase,
    isCollapsed,
    onToggleCollapse,
}: PhaseSidebarProps) {
    return (
        <div
            className={`bg-[#0b1015] border-r border-[#1f2d40] flex flex-col z-10 shrink-0 transition-[width] duration-200 ${
                isCollapsed ? 'w-16' : 'w-64'
            }`}
        >
            <div
                className={`h-12 border-b border-[#1f2d40]/50 flex items-center ${
                    isCollapsed ? 'justify-center px-2' : 'justify-between px-4'
                }`}
            >
                {!isCollapsed && (
                    <span className="text-[10px] font-bold text-[#566a84] uppercase tracking-widest">
                        Research Workflow
                    </span>
                )}
                <button
                    type="button"
                    onClick={onToggleCollapse}
                    className="h-7 w-7 rounded border border-[#2b3f57] text-[#7ca5be] hover:text-white hover:border-[#5acce6] flex items-center justify-center transition-colors"
                    title={isCollapsed ? 'Expand workflow sidebar' : 'Collapse workflow sidebar'}
                    aria-label={isCollapsed ? 'Expand workflow sidebar' : 'Collapse workflow sidebar'}
                >
                    {isCollapsed ? <PanelLeftOpen size={14} /> : <PanelLeftClose size={14} />}
                </button>
            </div>
            
            <div className="flex-1 overflow-y-auto space-y-1 px-2 py-2">
                {phases.map((phase, index) => {
                    const isActive = activePhase === phase.id;
                    const isPast = phases.findIndex(p => p.id === activePhase) > index;
                    
                    return (
                        <button
                            key={phase.id}
                            onClick={() => onSelectPhase(phase.id)}
                            className={`w-full flex items-center ${isCollapsed ? 'justify-center px-1 py-3' : 'gap-3 p-3'} rounded-lg text-left transition-all group relative
                                ${isActive ? 'bg-[#1f2d40] border border-[#3c5d80]' : 'hover:bg-[#151a1f] border border-transparent'}
                            `}
                            title={isCollapsed ? `${phase.label} - ${phase.short_desc}` : undefined}
                        >
                            {/* Vertical Connector Line (behind dots) */}
                            {!isCollapsed && index < phases.length - 1 && (
                                <div className={`absolute left-[27px] top-8 w-px h-full bg-[#1f2d40] -z-10`} />
                            )}

                            {/* Status Dot */}
                            <div className={`relative flex items-center justify-center w-6 h-6 rounded-full border z-10 bg-[#0b1015]
                                ${isActive ? 'border-[#5acce6] text-[#5acce6] shadow-[0_0_10px_rgba(90,204,230,0.3)]' : 
                                  isPast ? 'border-[#6fe5b8] bg-[#6fe5b8]/10 text-[#6fe5b8]' : 'border-[#3c5d80] text-[#3c5d80]'}
                            `}>
                                {isPast ? <CheckCircle2 size={14} /> : <span className="text-[10px] font-bold">{index + 1}</span>}
                            </div>
                            
                            {/* Text Content */}
                            {!isCollapsed && (
                                <div className="flex flex-col">
                                    <span className={`text-xs font-bold ${isActive ? 'text-white' : 'text-[#91b9cd] group-hover:text-gray-300'}`}>
                                        {phase.label}
                                    </span>
                                    <span className="text-[9px] text-[#566a84] line-clamp-1 leading-tight mt-0.5">
                                        {phase.short_desc}
                                    </span>
                                </div>
                            )}
                        </button>
                    );
                })}
            </div>
        </div>
    );
}