import React from 'react';
import { Home, Lock, PanelLeftClose, PanelLeftOpen, Plus, Rocket, ArrowLeft, BrainCircuit } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

const HomeIcon: any = Home;
const LockIcon: any = Lock;
const PanelLeftCloseIcon: any = PanelLeftClose;
const PanelLeftOpenIcon: any = PanelLeftOpen;
const PlusIcon: any = Plus;
const RocketIcon: any = Rocket;
const ArrowLeftIcon: any = ArrowLeft;
const BrainCircuitIcon: any = BrainCircuit;

interface PhaseSidebarProps {
    phases: any[];
    activePhase: string;
    updatedPhaseIds?: string[];
    updateMessages?: Record<string, string>;
    forceVisibleUpdatePhaseIds?: string[];
    /** Phase IDs that are locked (not yet accessible). Locked phases are shown
     *  grayed-out and cannot be selected. */
    lockedPhaseIds?: string[];
    /**
     * True once the backend has sent a plan. Used to distinguish between
     * "plan not yet received" (show a waiting hint) and "plan received but
     * execution not started yet" (show a step count).
     */
    planReceived?: boolean;
    onSelectPhase: (id: string) => void;
    isCollapsed: boolean;
    onToggleCollapse: () => void;
}

export default function PhaseSidebar({
    phases,
    activePhase,
    updatedPhaseIds = [],
    updateMessages = {},
    forceVisibleUpdatePhaseIds = [],
    lockedPhaseIds = [],
    planReceived = false,
    onSelectPhase,
    isCollapsed,
    onToggleCollapse,
}: PhaseSidebarProps) {
    const navigate = useNavigate();
    const activePhaseObj = phases.find(p => p.id === activePhase);

    // Count how many phases are actually active (not locked) for the step badge
    const unlockedCount = phases.filter(p => !lockedPhaseIds.includes(p.id)).length;
    const totalCount = phases.length;

    return (
        <div
            className={`flex flex-col z-10 shrink-0 transition-[width] duration-200 ${
                isCollapsed ? 'w-16' : 'w-64'
            }`}
            style={{ background: '#0a1118', borderRight: '1px solid #1a2a3a' }}
        >
            {/* ── Header ── */}
            <div
                className={`border-b flex flex-col gap-5 ${
                    isCollapsed ? 'items-center px-2 py-4' : 'px-5 py-5'
                }`}
                style={{ borderColor: '#1a2a3a' }}
            >
                {!isCollapsed ? (
                    <div className="flex items-center gap-3 w-full">
                        <button
                            type="button"
                            onClick={() => navigate('/agentic-home')}
                            className="h-8 w-8 shrink-0 rounded-lg flex items-center justify-center transition-all bg-[#0f1923] border border-[#1e3248] text-[#5a8aaa] hover:border-[#5acce6] hover:text-[#5acce6] hover:bg-[#152537] shadow-sm"
                            title="Back to Dashboard"
                        >
                            <ArrowLeftIcon size={14} strokeWidth={2.5} />
                        </button>
                        <div className="flex items-center gap-3">
                            <img src="/assets/full_cube.png" alt="Icon" className="h-8 w-auto object-contain drop-shadow-[0_0_12px_rgba(90,204,230,0.3)] transition-transform hover:scale-105" />
                           
                        </div>
                    </div>
                ) : (
                    <div className="flex flex-col items-center gap-3">
                        <button
                            type="button"
                            onClick={() => navigate('/agentic-home')}
                            className="h-8 w-8 rounded-lg flex items-center justify-center transition-all bg-[#0f1923] border border-[#1e3248] text-[#5a8aaa] hover:border-[#5acce6] hover:text-[#5acce6] hover:bg-[#152537] shadow-sm"
                            title="Back to Dashboard"
                        >
                            <ArrowLeftIcon size={14} strokeWidth={2.5} />
                        </button>
                        <img src="/assets/full_cube.png" alt="Icon" className="h-8 w-auto object-contain drop-shadow-[0_0_12px_rgba(90,204,230,0.3)] transition-transform hover:scale-105" title="CUBE.AI" />
                    </div>
                )}
                {/* Navigation and Actions */}
                <div className={`flex items-center ${isCollapsed ? 'flex-col gap-3' : 'justify-between'}`}>
                    <div className={`flex items-center ${isCollapsed ? 'flex-col gap-3' : 'gap-2'}`}>
                        <button
                            type="button"
                            onClick={() => onSelectPhase('planning')}
                            className={`h-8 w-8 rounded-lg flex items-center justify-center transition-all shadow-sm ${
                                activePhase === 'planning'
                                    ? 'border border-[#5acce6] text-[#5acce6] bg-[#0d2540] shadow-[0_0_10px_rgba(90,204,230,0.15)]'
                                    : 'border border-[#1e3248] text-[#5a8aaa] bg-[#0f1923] hover:border-[#5acce6] hover:text-[#5acce6] hover:bg-[#152537]'
                            }`}
                            title="Study Planning"
                        >
                            <HomeIcon size={14} strokeWidth={activePhase === 'planning' ? 2.5 : 2} />
                        </button>
                    </div>

                    <button
                        type="button"
                        onClick={onToggleCollapse}
                        className="h-8 w-8 rounded-lg flex items-center justify-center transition-all text-[#5a8aaa] hover:text-[#5acce6] hover:bg-[#0f1923]"
                        title={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
                    >
                        {isCollapsed ? <PanelLeftOpenIcon size={16} strokeWidth={2.5} /> : <PanelLeftCloseIcon size={16} strokeWidth={2.5} />}
                    </button>
                </div>

                {!isCollapsed && (
                    <div className="flex flex-col mt-2">
                        <span
                            className="text-[9px] font-bold uppercase tracking-widest mb-0.5"
                            style={{ color: '#4a6580' }}
                        >
                            Current Module
                        </span>
                        <span
                            className="text-[14px] font-bold leading-tight"
                            style={{ color: '#e8f4ff', letterSpacing: '-0.01em' }}
                        >
                            {activePhaseObj?.label ?? ''}
                        </span>
                    </div>
                )}

            </div>

            {/* ── Phase List ── */}
            <div className="flex-1 overflow-y-auto px-2 py-2.5 flex flex-col gap-1">
                {phases.map((phase, index) => {
                    const isActive = activePhase === phase.id;
                    const isCoreView = phase.id === 'planning' || phase.id === 'execution';
                    const isLocked = lockedPhaseIds.includes(phase.id);
                    const PhaseIcon = (phase.icon as any) || PlusIcon;
                    const isWaiting = forceVisibleUpdatePhaseIds.includes(phase.id);
                    const hasUpdate = !isLocked && updatedPhaseIds.includes(phase.id) && (
                        !isActive || isWaiting
                    );
                    const updateMessage = updateMessages[phase.id] || (isWaiting ? 'Waiting for user input' : 'New update received');

                    // Locked tooltip text
                    const lockedTitle = isCollapsed
                        ? `${phase.label} — Not started yet. This step will unlock once execution begins.`
                        : 'Not started yet. This step will unlock once execution begins.';

                    return (
                        <div key={phase.id} className="relative flex flex-col">
                            <button
                                onClick={() => !isLocked && onSelectPhase(phase.id)}
                                disabled={isLocked}
                                title={
                                    isLocked
                                        ? lockedTitle
                                        : isCollapsed
                                        ? `${phase.label} - ${phase.short_desc}`
                                        : undefined
                                }
                                aria-disabled={isLocked}
                                className={`w-full flex items-center transition-all rounded-xl text-left ${
                                    isCollapsed ? 'justify-center px-1 py-3' : 'gap-2.5 px-2.5 py-2.5'
                                } ${
                                    isLocked
                                        ? 'border border-transparent opacity-60 cursor-not-allowed'
                                        : hasUpdate
                                        ? 'border border-[#2a82c8] bg-[#071a2a] shadow-[0_0_0_1px_rgba(42,130,200,0.45),0_0_14px_rgba(42,130,200,0.22)]'
                                        : isActive
                                        ? isCoreView
                                            ? 'bg-[linear-gradient(135deg,#23142f_0%,#3a1f4d_100%)] border border-[#8f53bf]'
                                            : 'bg-[#0d2540] border border-[#2a5a8a]'
                                        : isCoreView
                                        ? 'border border-transparent hover:bg-[#1a1224] hover:border-[#5c3a78]'
                                        : 'border border-transparent hover:bg-[#111d29] hover:border-[#1e3248]'
                                }`}
                            >
                                {/* Phase Icon */}
                                <div
                                    className={`relative flex items-center justify-center w-7 h-7 rounded-full flex-shrink-0 z-10 transition-all
                                        ${isLocked
                                            ? 'border-[#2a3a4a] bg-[#0a1218] text-[#4a6580]'
                                            : isActive
                                            ? isCoreView
                                                ? 'border-[#c98cff] bg-[#2d1b3a] text-[#d8adff] shadow-[0_0_12px_rgba(216,173,255,0.25),inset_0_0_8px_rgba(216,173,255,0.08)]'
                                                : 'border-[#5acce6] bg-[#0d2035] text-[#5acce6] shadow-[0_0_12px_rgba(90,204,230,0.25),inset_0_0_8px_rgba(90,204,230,0.08)]'
                                            : hasUpdate
                                            ? 'border-[#2a82c8] bg-[#0b2035] text-[#7ec8f0] shadow-[0_0_12px_rgba(42,130,200,0.22)]'
                                            : isCoreView
                                            ? 'border-[#5c3a78] bg-[#160f1d] text-[#8f6cab]'
                                            : 'border-[#1e3a54] bg-[#0b1015] text-[#3c5d80]'
                                        }`}
                                    style={{ border: '1.5px solid' }}
                                >
                                    {isLocked
                                        ? <LockIcon size={11} />
                                        : phase.id === 'execution'
                                        ? <RocketIcon size={13} />
                                        : <PhaseIcon size={13} />}
                                </div>

                                {/* Text Content */}
                                {!isCollapsed && (
                                    <div className="flex flex-col overflow-hidden">
                                        <span
                                            className={`text-[13px] font-semibold truncate leading-snug ${
                                                isLocked
                                                    ? 'text-[#6a8da6]'
                                                    : hasUpdate
                                                    ? 'text-[#8cd2f6]'
                                                    : isActive
                                                    ? 'text-white'
                                                    : isCoreView
                                                    ? 'text-[#ccaede]'
                                                    : 'text-[#a6c8de]'
                                            }`}
                                        >
                                            {phase.label}
                                        </span>
                                        <span
                                            className={`text-[11px] truncate mt-0.5 leading-snug ${
                                                isLocked
                                                    ? 'text-[#5a7b99] italic'
                                                    : hasUpdate
                                                    ? 'text-[#61a4c9]'
                                                    : isActive
                                                    ? isCoreView
                                                        ? 'text-[#dcbdf2]'
                                                        : 'text-[#77aed4]'
                                                    : isCoreView
                                                    ? 'text-[#a584c2]'
                                                    : 'text-[#6488a6]'
                                            }`}
                                        >
                                            {isLocked ? 'Not started yet' : phase.short_desc}
                                        </span>
                                        {isLocked && (
                                            <span className="mt-0.5 text-[8px] font-medium uppercase tracking-wide text-[#4a6580] truncate">
                                                Unlocks after execution
                                            </span>
                                        )}
                                        {!isLocked && hasUpdate && (
                                            <span className="mt-0.5 text-[9px] font-semibold uppercase tracking-wide text-[#5acce6]">
                                                {updateMessage}
                                            </span>
                                        )}
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



        </div>
    );
}
