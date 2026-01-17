import React from 'react';
import { CheckCircle2 } from 'lucide-react';

interface PhaseSidebarProps {
    phases: any[];
    activePhase: string;
    onSelectPhase: (id: string) => void;
}

export default function PhaseSidebar({ phases, activePhase, onSelectPhase }: PhaseSidebarProps) {
    return (
        <div className="w-64 bg-[#0b1015] border-r border-[#1f2d40] flex flex-col z-10 shrink-0">
            <div className="p-4 text-[10px] font-bold text-[#566a84] uppercase tracking-widest border-b border-[#1f2d40]/50">
                Research Workflow
            </div>
            
            <div className="flex-1 overflow-y-auto space-y-1 px-2 py-2">
                {phases.map((phase, index) => {
                    const isActive = activePhase === phase.id;
                    const isPast = phases.findIndex(p => p.id === activePhase) > index;
                    
                    return (
                        <button
                            key={phase.id}
                            onClick={() => onSelectPhase(phase.id)}
                            className={`w-full flex items-center gap-3 p-3 rounded-lg text-left transition-all group relative
                                ${isActive ? 'bg-[#1f2d40] border border-[#3c5d80]' : 'hover:bg-[#151a1f] border border-transparent'}
                            `}
                        >
                            {/* Vertical Connector Line (behind dots) */}
                            {index < phases.length - 1 && (
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
                            <div className="flex flex-col">
                                <span className={`text-xs font-bold ${isActive ? 'text-white' : 'text-[#91b9cd] group-hover:text-gray-300'}`}>
                                    {phase.label}
                                </span>
                                <span className="text-[9px] text-[#566a84] line-clamp-1 leading-tight mt-0.5">
                                    {phase.short_desc}
                                </span>
                            </div>
                        </button>
                    );
                })}
            </div>
        </div>
    );
}