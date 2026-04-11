import React from 'react';
import { CheckCircle2, Home, PanelLeftClose, PanelLeftOpen } from 'lucide-react';

const CheckCircle2Icon: any = CheckCircle2;
const HomeIcon: any = Home;
const PanelLeftCloseIcon: any = PanelLeftClose;
const PanelLeftOpenIcon: any = PanelLeftOpen;

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
            className={`bg-slate-950 border-r border-slate-800 flex flex-col z-10 shrink-0 transition-[width] duration-200 ${
                isCollapsed ? 'w-16' : 'w-64'
            }`}
        >
            <div
                className={`h-12 border-b border-slate-800/70 flex items-center ${
                    isCollapsed ? 'justify-center px-2' : 'justify-between px-4'
                }`}
            >
                {!isCollapsed && (
                    <span className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">
                        Research Workflow
                    </span>
                )}
                <div className={`flex items-center ${isCollapsed ? 'gap-1' : 'gap-2'}`}>
                    <button
                        type="button"
                        onClick={() => onSelectPhase('planning')}
                        className={`h-7 w-7 rounded-lg border flex items-center justify-center transition-all duration-200 ${
                            activePhase === 'planning'
                                ? 'border-cyan-500 text-cyan-300 bg-cyan-950/40 shadow-[0_0_12px_rgba(34,211,238,0.18)]'
                                : 'border-slate-700 text-slate-400 hover:text-slate-100 hover:border-cyan-500'
                        }`}
                        title="Go to home (Planning)"
                        aria-label="Go to home (Planning)"
                    >
                        <HomeIcon size={14} />
                    </button>

                    <button
                        type="button"
                        onClick={onToggleCollapse}
                        className="h-7 w-7 rounded-lg border border-slate-700 text-slate-400 hover:text-slate-100 hover:border-cyan-500 flex items-center justify-center transition-all duration-200"
                        title={isCollapsed ? 'Expand workflow sidebar' : 'Collapse workflow sidebar'}
                        aria-label={isCollapsed ? 'Expand workflow sidebar' : 'Collapse workflow sidebar'}
                    >
                        {isCollapsed ? <PanelLeftOpenIcon size={14} /> : <PanelLeftCloseIcon size={14} />}
                    </button>
                </div>
            </div>
            
            <div className="flex-1 overflow-y-auto space-y-1.5 px-2 py-2">
                {phases.map((phase, index) => {
                    const isActive = activePhase === phase.id;
                    const isPast = phases.findIndex(p => p.id === activePhase) > index;
                    
                    return (
                        <button
                            key={phase.id}
                            onClick={() => onSelectPhase(phase.id)}
                            className={`w-full flex items-center ${isCollapsed ? 'justify-center px-1 py-3' : 'gap-3 p-3'} rounded-xl text-left transition-all duration-200 group relative
                                ${isActive ? 'bg-slate-900 border border-slate-600 shadow-sm' : 'hover:bg-slate-900/70 border border-transparent'}
                            `}
                            title={isCollapsed ? `${phase.label} - ${phase.short_desc}` : undefined}
                        >
                            {/* Vertical Connector Line (behind dots) */}
                            {!isCollapsed && index < phases.length - 1 && (
                                <div className={`absolute left-[27px] top-8 w-px h-full bg-slate-800 -z-10`} />
                            )}

                            {/* Status Dot */}
                                                        <div className={`relative flex items-center justify-center w-6 h-6 rounded-full border z-10 bg-slate-950
                                                                ${isActive ? 'border-cyan-500 text-cyan-300 shadow-[0_0_10px_rgba(34,211,238,0.28)]' : 
                                                                    isPast ? 'border-emerald-500 bg-emerald-500/10 text-emerald-300' : 'border-slate-600 text-slate-500'}
                            `}>
                                {isPast ? <CheckCircle2Icon size={14} /> : <span className="text-[10px] font-bold">{index + 1}</span>}
                            </div>
                            
                            {/* Text Content */}
                            {!isCollapsed && (
                                <div className="flex flex-col">
                                    <span className={`text-xs font-semibold ${isActive ? 'text-slate-100' : 'text-slate-300 group-hover:text-slate-100'}`}>
                                        {phase.label}
                                    </span>
                                    <span className="text-[10px] text-slate-500 line-clamp-1 leading-tight mt-0.5">
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