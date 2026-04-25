import React, { useCallback, useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { MessageSquare, FlaskConical, Layout, Database, Scan, BarChart3, FileText, BookOpenText, Settings } from 'lucide-react';

// Components
import PhaseSidebar from './PlaygroundCanvas/PhaseSidebar';
import PlanningView from './PlaygroundCanvas/views/PlanningView';
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
    const [modelingData, setModelingData] = useState<any>(null);
    const [reportData, setReportData] = useState<any>(null);
    const [latexData, setLatexData] = useState<any>(null);
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
    const sessionId = location.state?.sessionId || location.state?.session_id || null;
    const sendInitialMessage = location.state?.sendInitialMessage || false;

    // Load state from endpoints when session_id is provided
    useEffect(() => {
        const loadSessionState = async () => {
            if (!sessionId) return;
            
            try {
                // Load plan state
                const planRes = await fetch(`http://localhost:8150/session/${sessionId}/plan-state`);
                if (planRes.ok) {
                    const planData = await planRes.json();
                    console.log('[ResearchPlayground] Loaded plan state:', planData);
                }
                
                // Load execution state
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
                    setSegmentationData(payload);
                    break;
                case 'features_output':
                    // Backend sends tool="radiomic_extraction" or tool="feature_importance"
                    if (payload?.tool === 'feature_importance') {
                        setImportanceData(payload);
                    } else {
                        setFeaturesData(payload);
                    }
                    break;
                case 'modeling_output':
                    setModelingData(payload);
                    break;
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
                <PlanningView initialHypothesis={hypothesis} sessionId={sessionId} onLiteratureOutput={handleLiteratureOutput} />
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
            <div className="h-14 bg-[#0b1015] border-b border-[#1f2d40] flex items-center justify-between px-4 z-20 shrink-0 shadow-sm">
                <div className="flex items-center gap-6">
                    <div className="flex items-center gap-3">
                        <span className="font-bold text-[#5acce6] text-xs tracking-widest">CUBE</span>
                        <span className="text-gray-500 text-xs">/</span>
                        <span className="text-gray-300 text-xs font-medium">Intelligent Medical Viewer</span>
                    </div>
                    {/* Hypothesis Context */}
                    <div className="h-6 w-px bg-[#1f2d40]" />
                    <div className="flex items-center gap-3 text-xs">
                        <span className="text-[#5acce6] uppercase tracking-wider font-bold flex items-center gap-2">
                            <FlaskConicalIcon size={14} /> Scope:
                        </span>
                        <span className="text-gray-300 font-mono max-w-xl truncate opacity-90" title={hypothesis}>
                            {hypothesis}
                        </span>
                    </div>
                </div>
                
                <div className="flex items-center gap-2">
                    <button 
                        onClick={() => setIsSettingsOpen(true)}
                        className="p-2 rounded border transition-colors flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider bg-[#151a1f] text-[#566a84] border-[#3c5d80] hover:text-white"
                    >
                        <SettingsIcon size={14} /> Model
                    </button>
                    <button 
                        onClick={() => setIsChatOpen(!isChatOpen)}
                        className={`p-2 rounded border transition-colors flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider
                        ${isChatOpen ? 'bg-[#3c5d80] text-white border-[#5acce6]' : 'bg-[#151a1f] text-[#566a84] border-[#3c5d80] hover:text-white'}`}
                    >
                        <MessageSquareIcon size={14} /> AI Assistant
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
                    {/* Context Header for the active module */}
                    <div className="h-10 border-b border-[#1f2d40] bg-[#0b1015]/50 backdrop-blur flex items-center px-6 text-xs text-[#91b9cd]">
                        <span className="opacity-50 mr-2">Current Module:</span>
                        <span className="font-bold text-white uppercase tracking-wider">
                            {RESEARCH_PHASES.find(p => p.id === activePhase)?.label}
                        </span>
                        <span className="mx-3 opacity-30 text-[#3c5d80]">|</span>
                        <span className="italic opacity-70 truncate">
                            {RESEARCH_PHASES.find(p => p.id === activePhase)?.description}
                        </span>
                    </div>

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
                        initialUserMessage={hypothesis} 
                        externalSessionId={sessionId}
                        sendInitialMessage={sendInitialMessage}
                    />
                </div>
            </div>

            {/* Model Settings Modal */}
            <ClientSettingsPanel isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
        </div>
    );
}