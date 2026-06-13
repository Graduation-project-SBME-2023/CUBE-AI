import React, { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { MessageSquare, FlaskConical, Layout, Database, Scan, BarChart3, FileText, BookOpenText, Settings, ArrowLeft, Users } from 'lucide-react';

// Components
import PhaseSidebar from './PlaygroundCanvas/PhaseSidebar';
import PlanningView from './PlaygroundCanvas/views/PlanningView';
import ExecutionView from './PlaygroundCanvas/views/ExecutionView';
import LiteratureReviewView from './PlaygroundCanvas/views/LiteratureReviewView';
import DataCohortView from './PlaygroundCanvas/views/DataCohortView';
import SegmentationView from './PlaygroundCanvas/views/SegmentationView'
import FeaturesView from './PlaygroundCanvas/views/FeaturesView';
import ModelingView from './PlaygroundCanvas/views/ModelingView';
import ReportingView from './PlaygroundCanvas/views/ReportingView';
import ChatSidebar from './PlaygroundCanvas/ChatSidebar';
import ClientSettingsPanel from './PlaygroundCanvas/ClientSettingsPanel';
import websocketService from '../utils/WebsocketService';

// Configuration of the 5 Research Modules
export const RESEARCH_PHASES = [
    {
        id: 'planning',
        label: 'Study Planning',
        short_desc: 'Design & Hypothesis',
        icon: Layout,
        description: 'Define the research question, scope, and plan structure'
    },
    {
        id: 'execution',
        label: 'Agent Execution',
        short_desc: 'Live Agent Chat',
        icon: Users,
        description: 'Real-time agent group chat'
    },
    {
        id: 'literature',
        label: 'Literature Review',
        short_desc: 'Evidence Synthesis',
        icon: BookOpenText,
        description: 'Knowledge base, source map, and hypothesis context'
    },
    {
        id: 'data',
        label: 'Data & Population',
        short_desc: 'Cohort Selection',
        icon: Database,
        description: 'Inclusion Criteria, Cohort Selection & QA'
    },
    {
        id: 'segmentation',
        label: 'Segmentation QC',
        short_desc: 'Segmentation HITL',
        icon: Scan,
        description: 'Radiologists Approval of Segmentations'
    },
    {
        id: 'features',
        label: 'Feature Extraction',
        short_desc: 'ROI & Radiomics',
        icon: Scan,
        description: 'ROI Definition, Radiomics & Preprocessing'
    },
    {
        id: 'modeling',
        label: 'Modeling & Stats',
        short_desc: 'Training & Testing',
        icon: BarChart3,
        description: 'Model Training, Validation & Statistical Tests'
    },
    {
        id: 'reporting',
        label: 'Paper Writing',
        short_desc: 'Results & Export',
        icon: FileText,
        description: 'Results Compilation & Literature Comparison'
    },
];

const MessageSquareIcon: any = MessageSquare;
const FlaskConicalIcon: any = FlaskConical;
const SettingsIcon: any = Settings;
const ArrowLeftIcon: any = ArrowLeft;

export default function ResearchPlayground() {
    const location = useLocation();
    const navigate = useNavigate();
    const { phaseId } = useParams<{ phaseId?: string }>();
    const [isChatOpen, setIsChatOpen] = useState(true);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [isPhaseSidebarCollapsed, setIsPhaseSidebarCollapsed] = useState(false);
    const [literatureOutput, setLiteratureOutput] = useState<any>(null);
    const [populationData, setPopulationData] = useState<any>(null);
    const [segmentationData, setSegmentationData] = useState<any>(null);
    const [featuresData, setFeaturesData] = useState<any>(null);
    const [importanceData, setImportanceData] = useState<any>(null);
    const [modelingData, setModelingData] = useState<Record<string, any>>({});
    const [reportData, setReportData] = useState<any>(null);
    const [latexData, setLatexData] = useState<any>(null);
    const [viewsReady, setViewsReady] = useState(false);
    const DEFAULT_PHASE = 'planning';
    const isValidPhase = RESEARCH_PHASES.some(phase => phase.id === phaseId);
    const activePhase = isValidPhase ? phaseId! : DEFAULT_PHASE;

    useEffect(() => {
        if (phaseId && !isValidPhase) {
            navigate('/playground/planning', { replace: true, state: location.state });
        }
    }, [phaseId, isValidPhase, navigate, location.state]);

// Retrieve hypothesis from navigation state or default
    const hypothesis = location.state?.hypothesis || 'No active hypothesis defined.';
    
    // Look in browser memory in case the page was refreshed
    const savedSessionId = sessionStorage.getItem('current_cube_session');
    
    // Fallback chain (Router State -> Browser Memory -> null)
    const sessionId = location.state?.sessionId || location.state?.session_id || savedSessionId;
    
    const includeLitReview = location.state?.includeLitReview ?? true;
    const sendInitialMessage = location.state?.sendInitialMessage || false;
    const initialUserMessage = sendInitialMessage ? hypothesis : undefined;

    // Whenever we detect a valid session ID, lock it into browser memory
    useEffect(() => {
        if (sessionId) {
            sessionStorage.setItem('current_cube_session', sessionId);
        }
    }, [sessionId]);

    // Load state from endpoints when session_id is provided
    // Effect 1: non-replay session state (unchanged behavior, fires immediately)
    useEffect(() => {
        const loadSessionState = async () => {
            if (!sessionId) return;
            try {
                const planRes = await fetch(`http://localhost:8150/session/${sessionId}/plan-state`);
                if (planRes.ok) {
                    const planData = await planRes.json();
                    console.log('[ResearchPlayground] Loaded plan state:', planData);
                }
                const execRes = await fetch(`http://localhost:8150/session/${sessionId}/execution-state`);
                if (execRes.ok) {
                    const execData = await execRes.json();
                    console.log('[ResearchPlayground] Loaded execution state:', execData);
                }
            } catch (err) {
                console.error('[ResearchPlayground] Failed to load session state:', err);
            }
        };
        loadSessionState();
    }, [sessionId]);

    // Effect 2: signal that all child views have mounted
    useEffect(() => {
        const raf = requestAnimationFrame(() => setViewsReady(true));
        return () => cancelAnimationFrame(raf);
    }, []);

    // Effect 3: replay logs — only fires after views (and their WS subscriptions) are ready
    useEffect(() => {
        if (!sessionId || !viewsReady) return;

        const triggerReplay = async () => {
            websocketService.startReplayCapture();
            try {
                const replayRes = await fetch(`http://localhost:8150/session/${sessionId}/replay-logs`);
                if (replayRes.ok) {
                    console.log('[ResearchPlayground] ws logs replayed');
                }
            } catch (err) {
                console.error('[ResearchPlayground] Failed to replay logs:', err);
            } finally {
                // Always clean up the buffer, even if the request failed
                websocketService.stopReplayCapture();
            }
        };

        triggerReplay();
    }, [sessionId, viewsReady]);

    useEffect(() => {
        console.log('[HypothesisFlow][ResearchPlayground] Route/context', {
            path: location.pathname,
            phaseId,
            activePhase,
            sessionId,
            sendInitialMessage,
            hasHypothesis: Boolean(hypothesis),
            hypothesisLength: hypothesis?.length || 0,
        });
    }, [location.pathname, phaseId, activePhase, sessionId, sendInitialMessage, hypothesis]);

    const handleSelectPhase = (id: string) => {
        console.log('[HypothesisFlow][ResearchPlayground] Navigating phase', {
            from: activePhase,
            to: id,
            sessionId,
        });
        navigate(`/playground/${id}`, { state: location.state });
    };

    const handleLiteratureOutput = useCallback((data: any) => {
        setLiteratureOutput(data);
    }, []);

    // Subscribe to view-specific WebSocket events from execution agents
    useEffect(() => {
        const handleViewWSMessage = (data: any) => {
            const { type, payload } = data;
            switch (type) {
                case 'population_output':
                    setPopulationData(payload);
                    break;
                case 'segmentation_output':
                    setSegmentationData({ ...payload, sessionId: data.session_id });
                    break;
                case 'features_output':
                    // Backend sends tool="radiomic_extraction" or tool="feature_importance"
                    if (payload?.tool === 'feature_importance') {
                        setImportanceData(payload);
                    } else {
                        setFeaturesData(payload);
                    }
                    break;
                case 'modeling_output': {
                    // Use experiment_name as the key; fall back to a counter based on current size.
                    const key: string =
                        payload?.experiment_name ||
                        `Experiment ${Object.keys(modelingData).length + 1}`;
                
                    setModelingData(prev => ({
                        ...prev,
                        // Merge rather than replace — if the same experiment name arrives again
                        // (e.g. a partial update from the backend), its entry is overwritten while
                        // all other experiments are preserved.
                        [key]: payload,
                    }));
                    break;
                }
                case 'report_output':
                    setReportData(payload);
                    break;
                case 'latex_output':
                    setLatexData(payload);
                    break;
            }
        };
        const unsubscribe = websocketService.subscribe(handleViewWSMessage);
        return () => { unsubscribe(); };
    }, []);

    // All views are rendered simultaneously but only the active one is visible.
    // This preserves component state (nodes, logs, WS subscriptions) across phase switches.
    const renderAllViews = () => (
        <>
            <div className={`absolute inset-0 ${activePhase === 'planning' ? '' : 'pointer-events-none invisible'}`}>
                <PlanningView initialHypothesis={hypothesis} sessionId={sessionId} includeLitReview={includeLitReview} onLiteratureOutput={handleLiteratureOutput} />
            </div>
            <div className={`absolute inset-0 ${activePhase === 'execution' ? '' : 'pointer-events-none invisible'}`}>
                <ExecutionView sessionId={sessionId} />
            </div>
            <div className={`absolute inset-0 ${activePhase === 'literature' ? '' : 'pointer-events-none invisible'}`}>
                <LiteratureReviewView literatureOutput={literatureOutput || location.state?.literatureReviewOutput || location.state?.literatureContext} />
            </div>
            <div className={`absolute inset-0 ${activePhase === 'data' ? '' : 'pointer-events-none invisible'}`}>
                <DataCohortView populationData={populationData} segmentationData={segmentationData} />
            </div>
            <div className={`absolute inset-0 ${activePhase === 'segmentation' ? '' : 'pointer-events-none invisible'}`}>
                <SegmentationView segmentationData={segmentationData} />
            </div>
            <div className={`absolute inset-0 ${activePhase === 'features' ? '' : 'pointer-events-none invisible'}`}>
                <FeaturesView featuresData={featuresData} importanceData={importanceData} />
            </div>
            <div className={`absolute inset-0 ${activePhase === 'modeling' ? '' : 'pointer-events-none invisible'}`}>
                <ModelingView modelingData={modelingData} />
            </div>
            <div className={`absolute inset-0 ${activePhase === 'reporting' ? '' : 'pointer-events-none invisible'}`}>
                <ReportingView reportData={reportData} latexData={latexData} />
            </div>
        </>
    );

    return (
        <div className="w-full h-screen font-sans flex flex-col bg-[#000000] text-white overflow-hidden">

            {/* 1. TOP HEADER */}
            <div
                className="h-14 flex items-center justify-between px-4 z-20 shrink-0"
                style={{
                    background: '#0a1520',
                    borderBottom: '1px solid #1a2a3a',
                    boxShadow: '0 1px 0 #0d1e2e, 0 4px 24px rgba(0,0,0,0.4)',
                }}
            >
                <div className="flex items-center">

                    {/* Back Button */}
                    <button
                        onClick={() => navigate('/agentic-home')}
                        className="mr-3 p-1.5 rounded-md transition-all hover:bg-[#0d1e2e] text-[#5a8aaa] hover:text-[#5acce6] border border-transparent hover:border-[#1e3248]"
                        title="Back to Home"
                    >
                        <ArrowLeftIcon size={16} />
                    </button>

                    {/* Brand */}
                    <div className="flex items-center gap-2 pr-4">
                        <div
                            className="w-6 h-6 rounded flex items-center justify-center flex-shrink-0"
                            style={{
                                background: 'linear-gradient(135deg, #0d2035 0%, #1a3a54 100%)',
                                border: '1px solid #2a5a8a',
                                boxShadow: '0 0 8px rgba(90,204,230,0.15)',
                            }}
                        >
                            <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
                                <path
                                    d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"
                                    stroke="#5acce6" strokeWidth="2"
                                    strokeLinecap="round" strokeLinejoin="round"
                                />
                            </svg>
                        </div>
                        <span className="font-bold text-[11px] tracking-[0.18em] uppercase" style={{ color: '#5acce6' }}>
                            CUBE
                        </span>
                    </div>

                    {/* Divider */}
                    <div className="w-px self-stretch mx-1" style={{ background: '#1a2a3a' }} />

                    {/* App name */}
                    <div className="px-4">
                        <span className="text-[11px] font-medium tracking-wide" style={{ color: '#7aa8c4' }}>
                            Intelligent Medical Viewer
                        </span>
                    </div>

                    {/* Divider */}
                    <div className="w-px self-stretch mx-1" style={{ background: '#1a2a3a' }} />

                    {/* Scope */}
                    <div className="flex items-center gap-2 px-4">
                        <FlaskConicalIcon size={12} style={{ color: '#5acce6', flexShrink: 0 }} />
                        <span className="text-[9px] font-bold uppercase tracking-[0.14em]" style={{ color: '#4a6580' }}>
                            Scope:
                        </span>
                        <div
                            className="flex items-center gap-1.5 px-2.5 py-1 rounded-md"
                            style={{ background: '#0d1e2e', border: '1px solid #1e3a54' }}
                        >
                            <span className="relative flex h-1.5 w-1.5 flex-shrink-0">
                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full opacity-60" style={{ background: '#2eca8e' }} />
                                <span className="relative inline-flex rounded-full h-1.5 w-1.5" style={{ background: '#2eca8e' }} />
                            </span>
                            <span className="text-[11px] font-mono max-w-xl truncate" style={{ color: '#b0d4ea' }} title={hypothesis}>
                                {hypothesis}
                            </span>
                        </div>
                    </div>
                </div>

                {/* Right buttons */}
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => setIsSettingsOpen(true)}
                        className="flex items-center gap-2 px-3 h-8 rounded-md text-[10px] font-semibold uppercase tracking-wider transition-all border border-[#1e3248] text-[#5a8aaa] bg-[#0d1e2e] hover:border-[#5acce6] hover:text-[#e0f0ff] hover:bg-[#111d29]"
                    >
                        <SettingsIcon size={13} /> Model
                    </button>
                    <button
                        onClick={() => setIsChatOpen(!isChatOpen)}
                        className={`flex items-center gap-2 px-3 h-8 rounded-md text-[10px] font-semibold uppercase tracking-wider transition-all border ${isChatOpen
                                ? 'border-[#5acce6] text-[#5acce6] bg-[#0d2540] shadow-[0_0_12px_rgba(90,204,230,0.2)]'
                                : 'border-[#1e3248] text-[#5a8aaa] bg-[#0d1e2e] hover:border-[#5acce6] hover:text-[#e0f0ff] hover:bg-[#111d29]'
                            }`}
                    >
                        <MessageSquareIcon size={13} /> AI Assistant
                    </button>
                </div>
            </div>

            {/* 2. MAIN WORKSPACE LAYOUT */}
            <div className="flex-1 flex overflow-hidden relative">

                {/* A. LEFT SIDEBAR (Phase Navigation) */}
                <PhaseSidebar
                    phases={RESEARCH_PHASES}
                    activePhase={activePhase}
                    onSelectPhase={handleSelectPhase}
                    isCollapsed={isPhaseSidebarCollapsed}
                    onToggleCollapse={() => setIsPhaseSidebarCollapsed(prev => !prev)}
                />

                {/* B. CENTER STAGE (Polymorphic View) */}
                <div className="flex-1 min-w-0 relative flex flex-col bg-[#000000]">

                    {/* The Dynamic Content — all views stay mounted, only active is visible */}
                    <div className="flex-1 relative overflow-hidden">
                        {renderAllViews()}
                    </div>
                </div>

                {/* C. RIGHT SIDEBAR (Chat) — always mounted to preserve WS state */}
                <div className={`shrink-0 z-30 relative ${isChatOpen ? 'animate-in slide-in-from-right duration-300' : 'hidden'}`}>
                    <ChatSidebar
                        isOpen={isChatOpen}
                        onClose={() => setIsChatOpen(false)}
                        initialUserMessage={initialUserMessage}
                        externalSessionId={sessionId}
                        sendInitialMessage={sendInitialMessage}
                    />
                </div>
            </div>

            {/* 3. FOOTER */}
            <div 
                className="h-8 flex items-center px-4 z-20 shrink-0"
                style={{
                    background: '#0a1520',
                    borderTop: '1px solid #1a2a3a',
                }}
            >
                <div className="flex items-center gap-6">
                    {/* Active Phase Indicator */}
                    <div className="flex items-center gap-2">
                        <span className="w-1.5 h-1.5 rounded-full" style={{ background: '#2eca8e' }}></span>
                        <span className="text-[11px] font-medium" style={{ color: '#5a8aaa' }}>
                            {RESEARCH_PHASES.find(p => p.id === activePhase)?.label || 'Playground'}
                        </span>
                    </div>

                    {/* Shortcuts */}
                    <div className="flex items-center gap-4 text-[10px]" style={{ color: '#4a6580' }}>
                        <div className="flex items-center gap-1.5">
                            <span className="rounded px-1.5 py-0.5" style={{ background: '#0d1e2e', border: '1px solid #1e3248', color: '#5a8aaa' }}>Drag</span>
                            <span>Move</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                            <span className="rounded px-1.5 py-0.5" style={{ background: '#0d1e2e', border: '1px solid #1e3248', color: '#5a8aaa' }}>Right-click</span>
                            <span>Menu</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                            <span className="rounded px-1.5 py-0.5" style={{ background: '#0d1e2e', border: '1px solid #1e3248', color: '#5a8aaa' }}>Ctrl+Scroll</span>
                            <span>Zoom</span>
                        </div>
                        <div className="flex items-center gap-1.5">
                            <span className="rounded px-1.5 py-0.5" style={{ background: '#0d1e2e', border: '1px solid #1e3248', color: '#5a8aaa' }}>Space</span>
                            <span>Pan</span>
                        </div>
                    </div>
                </div>
            </div>

            {/* Model Settings Modal */}
            <ClientSettingsPanel isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
        </div>
    );
}