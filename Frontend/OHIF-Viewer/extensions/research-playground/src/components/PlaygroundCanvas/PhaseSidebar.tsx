import React from 'react';
import { CheckCircle2, Home, PanelLeftClose, PanelLeftOpen, Plus } from 'lucide-react';

const CheckCircle2Icon: any = CheckCircle2;
const HomeIcon: any = Home;
const PanelLeftCloseIcon: any = PanelLeftClose;
const PanelLeftOpenIcon: any = PanelLeftOpen;
const PlusIcon: any = Plus;

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
    const activeIndex = phases.findIndex(p => p.id === activePhase);
    const progressPct = Math.round(((activeIndex + 1) / phases.length) * 100);
    const activePhaseObj = phases.find(p => p.id === activePhase);

    return (
        <div
            className={`flex flex-col z-10 shrink-0 transition-[width] duration-200 ${
                isCollapsed ? 'w-16' : 'w-60'
            }`}
            style={{ background: '#0f1923', borderRight: '1px solid #1a2a3a' }}
        >
            {/* ── Header ── */}
            <div
                className={`border-b flex flex-col ${
                    isCollapsed ? 'items-center px-2 py-3' : 'px-4 py-3'
                }`}
                style={{ borderColor: '#1a2a3a' }}
            >
                {!isCollapsed && (
                    <>
                        <span
                            className="text-[9px] font-bold uppercase tracking-widest mb-0.5"
                            style={{ color: '#4a6580' }}
                        >
                            Current Module
                        </span>
                        <span
                            className="text-[13px] font-bold leading-tight"
                            style={{ color: '#e8f4ff', letterSpacing: '-0.01em' }}
                        >
                            {activePhaseObj?.label ?? ''}
                        </span>
                    </>
                )}

                {/* Action Buttons */}
                <div className={`flex items-center gap-1.5 ${isCollapsed ? '' : 'mt-3'}`}>
                    <button
                        type="button"
                        onClick={() => onSelectPhase('planning')}
                        className={`h-7 w-7 rounded-md flex items-center justify-center transition-all ${
                            activePhase === 'planning'
                                ? 'border-[#5acce6] text-[#5acce6] bg-[#0d1e2e]'
                                : 'border-[#1e3248] text-[#5a8aaa] hover:border-[#5acce6] hover:text-[#5acce6] hover:bg-[#0d1e2e]'
                        }`}
                        style={{ border: '1px solid' }}
                        title="Go to home (Planning)"
                        aria-label="Go to home (Planning)"
                    >
                        <HomeIcon size={13} />
                    </button>

                    <button
                        type="button"
                        onClick={onToggleCollapse}
                        className="h-7 w-7 rounded-md flex items-center justify-center transition-all border border-[#1e3248] text-[#5a8aaa] hover:border-[#5acce6] hover:text-[#5acce6] hover:bg-[#0d1e2e]"
                        title={isCollapsed ? 'Expand workflow sidebar' : 'Collapse workflow sidebar'}
                        aria-label={isCollapsed ? 'Expand workflow sidebar' : 'Collapse workflow sidebar'}
                    >
                        {isCollapsed ? <PanelLeftOpenIcon size={13} /> : <PanelLeftCloseIcon size={13} />}
                    </button>
                </div>
            </div>

            {/* ── Phase List ── */}
            <div className="flex-1 overflow-y-auto px-2 py-2.5 flex flex-col gap-1">
                {phases.map((phase, index) => {
                    const isActive = activePhase === phase.id;
                    const isPast = phases.findIndex(p => p.id === activePhase) > index;

                    return (
                        <div key={phase.id} className="relative flex flex-col">
                            <button
                                onClick={() => onSelectPhase(phase.id)}
                                title={isCollapsed ? `${phase.label} - ${phase.short_desc}` : undefined}
                                className={`w-full flex items-center transition-all rounded-xl text-left ${
                                    isCollapsed ? 'justify-center px-1 py-3' : 'gap-2.5 px-2.5 py-2.5'
                                } ${
                                    isActive
                                        ? 'bg-[#0d2540] border border-[#2a5a8a]'
                                        : 'border border-transparent hover:bg-[#111d29] hover:border-[#1e3248]'
                                }`}
                            >
                                {/* Status Dot */}
                                <div
                                    className={`relative flex items-center justify-center w-7 h-7 rounded-full flex-shrink-0 z-10 transition-all
                                        ${isActive
                                            ? 'border-[#5acce6] bg-[#0d2035] text-[#5acce6] shadow-[0_0_12px_rgba(90,204,230,0.25),inset_0_0_8px_rgba(90,204,230,0.08)]'
                                            : isPast
                                            ? 'border-[#2eca8e] bg-[#0d2420] text-[#2eca8e]'
                                            : 'border-[#1e3a54] bg-[#0b1015] text-[#3c5d80]'
                                        }`}
                                    style={{ border: '1.5px solid' }}
                                >
                                    {isPast
                                        ? <CheckCircle2Icon size={13} />
                                        : <span className="text-[10px] font-bold">{index + 1}</span>
                                    }
                                </div>

                                {/* Text Content */}
                                {!isCollapsed && (
                                    <div className="flex flex-col overflow-hidden">
                                        <span
                                            className={`text-[12px] font-semibold truncate leading-snug ${
                                                isActive ? 'text-[#e0f0ff]' : 'text-[#7aa8c4] group-hover:text-[#b0d4ea]'
                                            }`}
                                        >
                                            {phase.label}
                                        </span>
                                        <span
                                            className={`text-[10px] truncate mt-0.5 leading-snug ${
                                                isActive ? 'text-[#4d7a9a]' : 'text-[#3d5a72]'
                                            }`}
                                        >
                                            {phase.short_desc}
                                        </span>
                                    </div>
                                )}
                            </button>

                            {/* Vertical Connector Line */}
                            {!isCollapsed && index < phases.length - 1 && (
                                <div
                                    className="absolute w-px"
                                    style={{
                                        left: '22px',
                                        top: '46px',
                                        height: '8px',
                                        background: '#1a2d40',
                                        zIndex: 0,
                                    }}
                                />
                            )}
                        </div>
                    );
                })}
            </div>

            {/* ── Add Node Button ── */}
            <div className="px-2 pb-1">
                <button
                    type="button"
                    className={`w-full rounded-lg flex items-center justify-center gap-1.5 py-2 transition-all text-[11px] font-medium
                        border border-dashed border-[#1e3248] text-[#4a6a84]
                        hover:border-[#5acce6] hover:text-[#5acce6] hover:bg-[#0d1e2e]
                        ${isCollapsed ? 'px-1' : 'px-3'}`}
                >
                    <PlusIcon size={12} strokeWidth={2.5} />
                    {!isCollapsed && <span>Add Node</span>}
                </button>
            </div>

            {/* ── Progress Footer ── */}
            <div
                className="px-3.5 pt-2.5 pb-3.5 border-t"
                style={{ borderColor: '#1a2a3a' }}
            >
                {!isCollapsed && (
                    <div className="flex items-center justify-between mb-1.5">
                        <span
                            className="text-[9px] font-semibold uppercase tracking-wider"
                            style={{ color: '#3d5a72' }}
                        >
                            Progress
                        </span>
                        <span className="text-[9px] font-bold" style={{ color: '#5a8aaa' }}>
                            {activeIndex + 1}/{phases.length} steps
                        </span>
                    </div>
                )}
                <div
                    className="h-[3px] rounded-full overflow-hidden"
                    style={{ background: '#1a2a3a' }}
                >
                    <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{
                            width: `${progressPct}%`,
                            background: 'linear-gradient(90deg, #2eca8e, #5acce6)',
                        }}
                    />
                </div>
            </div>
        </div>
    );
}