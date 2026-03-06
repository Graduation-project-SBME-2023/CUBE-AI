import React, { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { MessageSquare, FlaskConical, Layout, Database, Scan, BarChart3, FileText, BookOpenText } from 'lucide-react';

// Components
import PhaseSidebar from './PlaygroundCanvas/PhaseSidebar';
import PlanningView from './PlaygroundCanvas/views/PlanningView';
import LiteratureReviewView from './PlaygroundCanvas/views/LiteratureReviewView';
import DataCohortView from './PlaygroundCanvas/views/DataCohortView';
import FeaturesView from './PlaygroundCanvas/views/FeaturesView';
import ModelingView from './PlaygroundCanvas/views/ModelingView';
import ReportingView from './PlaygroundCanvas/views/ReportingView';
import ChatSidebar from './PlaygroundCanvas/ChatSidebar';

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

export default function ResearchPlayground() {
    const location = useLocation();
    const DEFAULT_PHASE = 'planning';
    const [activePhase, setActivePhase] = useState(DEFAULT_PHASE);
    const [isChatOpen, setIsChatOpen] = useState(false);
    const [isPhaseSidebarCollapsed, setIsPhaseSidebarCollapsed] = useState(false);
    
    // Retrieve hypothesis from navigation state or default
    const hypothesis = location.state?.hypothesis || 'No active hypothesis defined.';

    // Render the correct center view based on the active phase
    const renderActiveView = () => {
        switch (activePhase) {
            case 'planning': return <PlanningView initialHypothesis={hypothesis} />;
            case 'literature': return <LiteratureReviewView literatureOutput={location.state?.literatureReviewOutput || location.state?.literatureContext} />;
            case 'data': return <DataCohortView />;
            case 'features': return <FeaturesView />;
            case 'modeling': return <ModelingView />;
            case 'reporting': return <ReportingView />;
            default: return <PlanningView initialHypothesis={hypothesis} />;
        }
    };

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
                            <FlaskConical size={14} /> Scope:
                        </span>
                        <span className="text-gray-300 font-mono max-w-xl truncate opacity-90" title={hypothesis}>
                            {hypothesis}
                        </span>
                    </div>
                </div>
                
                <button 
                    onClick={() => setIsChatOpen(!isChatOpen)}
                    className={`p-2 rounded border transition-colors flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider
                    ${isChatOpen ? 'bg-[#3c5d80] text-white border-[#5acce6]' : 'bg-[#151a1f] text-[#566a84] border-[#3c5d80] hover:text-white'}`}
                >
                    <MessageSquare size={14} /> AI Assistant
                </button>
            </div>

            {/* 2. MAIN WORKSPACE LAYOUT */}
            <div className="flex-1 flex overflow-hidden relative">
                
                {/* A. LEFT SIDEBAR (Phase Navigation) */}
                <PhaseSidebar 
                    phases={RESEARCH_PHASES} 
                    activePhase={activePhase} 
                    onSelectPhase={setActivePhase} 
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

                    {/* The Dynamic Content */}
                    <div className="flex-1 relative overflow-hidden">
                        {renderActiveView()}
                    </div>
                </div>

                {/* C. RIGHT SIDEBAR (Chat) */}
                {isChatOpen && (
                    <div className="shrink-0 z-30 relative animate-in slide-in-from-right duration-300">
                        <ChatSidebar 
                            isOpen={isChatOpen} 
                            onClose={() => setIsChatOpen(false)} 
                            initialUserMessage={hypothesis} 
                        />
                    </div>
                )}
            </div>
        </div>
    );
}