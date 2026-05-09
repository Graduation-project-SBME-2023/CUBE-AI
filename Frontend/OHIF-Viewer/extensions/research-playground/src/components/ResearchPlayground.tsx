import React, { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, FlaskConical, MessageSquare, Settings } from 'lucide-react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import PhaseSidebar from './PlaygroundCanvas/PhaseSidebar';
import PlanningView from './PlaygroundCanvas/views/PlanningView';
import ExecutionView from './PlaygroundCanvas/views/ExecutionView';
import LiteratureReviewView from './PlaygroundCanvas/views/LiteratureReviewView';
import DataCohortView from './PlaygroundCanvas/views/DataCohortView';
import SegmentationView from './PlaygroundCanvas/views/SegmentationView';
import FeaturesView from './PlaygroundCanvas/views/FeaturesView';
import ModelingView from './PlaygroundCanvas/views/ModelingView';
import ReportingView from './PlaygroundCanvas/views/ReportingView';
import ChatSidebar from './PlaygroundCanvas/ChatSidebar';
import ClientSettingsPanel from './PlaygroundCanvas/ClientSettingsPanel';
import { RESEARCH_PHASES } from '../constants/researchPhases';
import { useResearchPhase } from '../hooks/useResearchPhase';
import { ResearchProvider } from '../context/ResearchContext';
import { useResearchData } from '../hooks/useResearchData';
import websocketService from '../services/websocket';
import { fetchSessionExecutionState, fetchSessionPlanState, replaySessionLogs } from '../services/api';
import { ResearchRouteState } from '../types';

function ResearchPlaygroundInner() {
  const location = useLocation();
  const navigate = useNavigate();
  const { phaseId } = useParams<{ phaseId?: string }>();
  const { isValidPhase, activePhase, defaultPhase, phases } = useResearchPhase(phaseId);
  const [isChatOpen, setIsChatOpen] = useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isPhaseSidebarCollapsed, setIsPhaseSidebarCollapsed] = useState(false);
  const [viewsReady, setViewsReady] = useState(false);
  const { data, setLiteratureOutput, setPopulationData, setSegmentationData, setFeaturesData, setImportanceData, setModelingData, setReportData, setLatexData } = useResearchData();

  const routeState = (location.state || {}) as ResearchRouteState;
  const hypothesis = routeState.hypothesis || 'No active hypothesis defined.';
  const sessionId = routeState.sessionId || routeState.session_id || null;
  const includeLitReview = routeState.includeLitReview ?? true;
  const sendInitialMessage = routeState.sendInitialMessage || false;
  const initialUserMessage = sendInitialMessage ? hypothesis : undefined;

  useEffect(() => {
    if (phaseId && !isValidPhase) {
      navigate(`/playground/${defaultPhase}`, { replace: true, state: location.state });
    }
  }, [phaseId, isValidPhase, defaultPhase, navigate, location.state]);

  useEffect(() => {
    if (!sessionId) return;
    const load = async () => {
      try {
        await fetchSessionPlanState(sessionId);
        await fetchSessionExecutionState(sessionId);
      } catch (err) {
        console.error('[ResearchPlayground] Failed to load session state:', err);
      }
    };
    load();
  }, [sessionId]);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setViewsReady(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    if (!sessionId || !viewsReady) return;
    const replay = async () => {
      websocketService.startReplayCapture();
      try {
        await replaySessionLogs(sessionId);
      } catch (err) {
        console.error('[ResearchPlayground] Failed to replay logs:', err);
      } finally {
        websocketService.stopReplayCapture();
      }
    };
    replay();
  }, [sessionId, viewsReady]);

  useEffect(() => {
    const unsubscribe = websocketService.subscribe((event: any) => {
      const { type, payload } = event;
      switch (type) {
        case 'population_output':
          setPopulationData(payload);
          break;
        case 'segmentation_output':
          setSegmentationData({ ...payload, sessionId: event.session_id });
          break;
        case 'features_output':
          if (payload?.tool === 'feature_importance') setImportanceData(payload);
          else setFeaturesData(payload);
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
    });
    return () => unsubscribe();
  }, [setFeaturesData, setImportanceData, setLatexData, setModelingData, setPopulationData, setReportData, setSegmentationData]);

  const viewNodes = useMemo(
    () => [
      {
        id: 'planning',
        node: <PlanningView initialHypothesis={hypothesis} sessionId={sessionId} includeLitReview={includeLitReview} onLiteratureOutput={setLiteratureOutput} />,
      },
      { id: 'execution', node: <ExecutionView sessionId={sessionId} /> },
      {
        id: 'literature',
        node: (
          <LiteratureReviewView
            literatureOutput={
              data.literatureOutput || routeState.literatureReviewOutput || routeState.literatureContext
            }
          />
        ),
      },
      {
        id: 'data',
        node: <DataCohortView populationData={data.populationData} segmentationData={data.segmentationData} />,
      },
      { id: 'segmentation', node: <SegmentationView segmentationData={data.segmentationData as any} /> },
      {
        id: 'features',
        node: <FeaturesView featuresData={data.featuresData} importanceData={data.importanceData} />,
      },
      { id: 'modeling', node: <ModelingView modelingData={data.modelingData as any} /> },
      { id: 'reporting', node: <ReportingView reportData={data.reportData} latexData={data.latexData} /> },
    ],
    [data, hypothesis, includeLitReview, routeState.literatureContext, routeState.literatureReviewOutput, sessionId, setLiteratureOutput]
  );

  return (
    <div className="flex h-screen w-full flex-col overflow-hidden bg-black font-sans text-white">
      <div className="z-20 flex h-14 shrink-0 items-center justify-between bg-[#0a1520] px-4 shadow-[0_1px_0_#0d1e2e,0_4px_24px_rgba(0,0,0,0.4)] border-b border-[#1a2a3a]">
        <div className="flex items-center">
            <button
                onClick={() => navigate('/agentic-home')}
                className="mr-3 p-1.5 rounded-md transition-all hover:bg-[#0d1e2e] text-[#5a8aaa] hover:text-[#5acce6] border border-transparent hover:border-[#1e3248]"
                title="Back to Home"
            >
                <ArrowLeftIcon size={16} />
            </button>

          <div className="flex items-center gap-2 pr-4">
            <div className="flex h-6 w-6 items-center justify-center rounded border border-[#2a5a8a] bg-[linear-gradient(135deg,#0d2035_0%,#1a3a54_100%)]">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none">
                <path
                  d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5"
                  stroke="#5acce6"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
            <span className="text-[11px] font-bold uppercase tracking-[0.18em] text-[#5acce6]">CUBE</span>
          </div>

          <div className="mx-1 w-px self-stretch bg-[#1a2a3a]" />
          <div className="px-4">
            <span className="text-[11px] font-medium tracking-wide text-[#7aa8c4]">Intelligent Medical Viewer</span>
          </div>
          <div className="mx-1 w-px self-stretch bg-[#1a2a3a]" />

          <div className="flex items-center gap-2 px-4">
            <FlaskConical size={12} className="text-[#5acce6]" />
            <span className="text-[9px] font-bold uppercase tracking-[0.14em] text-[#4a6580]">Scope:</span>
            <div className="flex items-center gap-1.5 rounded-md border border-[#1e3a54] bg-[#0d1e2e] px-2.5 py-1">
              <span className="relative flex h-1.5 w-1.5 shrink-0">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#2eca8e] opacity-60" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-[#2eca8e]" />
              </span>
              <span className="max-w-xl truncate font-mono text-[11px] text-[#b0d4ea]" title={hypothesis}>
                {hypothesis}
              </span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setIsSettingsOpen(true)}
            className="flex h-8 items-center gap-2 rounded-md border border-[#1e3248] bg-[#0d1e2e] px-3 text-[10px] font-semibold uppercase tracking-wider text-[#5a8aaa] transition-all hover:border-[#5acce6] hover:bg-[#111d29] hover:text-[#e0f0ff]"
          >
            <Settings size={13} /> Model
          </button>
          <button
            onClick={() => setIsChatOpen(!isChatOpen)}
            className={`flex h-8 items-center gap-2 rounded-md border px-3 text-[10px] font-semibold uppercase tracking-wider transition-all ${
              isChatOpen
                ? 'border-[#5acce6] bg-[#0d2540] text-[#5acce6] shadow-[0_0_12px_rgba(90,204,230,0.2)]'
                : 'border-[#1e3248] bg-[#0d1e2e] text-[#5a8aaa] hover:border-[#5acce6] hover:bg-[#111d29] hover:text-[#e0f0ff]'
            }`}
          >
            <MessageSquare size={13} /> AI Assistant
          </button>
        </div>
      </div>

      <div className="relative flex flex-1 overflow-hidden">
        <PhaseSidebar
          phases={phases}
          activePhase={activePhase}
          onSelectPhase={id => navigate(`/playground/${id}`, { state: location.state })}
          isCollapsed={isPhaseSidebarCollapsed}
          onToggleCollapse={() => setIsPhaseSidebarCollapsed(prev => !prev)}
        />

        <div className="relative flex min-w-0 flex-1 flex-col bg-black">
          <div className="relative flex-1 overflow-hidden">
            {viewNodes.map(view => (
              <div
                key={view.id}
                className={`absolute inset-0 ${activePhase === view.id ? '' : 'pointer-events-none invisible'}`}
              >
                {view.node}
              </div>
            ))}
          </div>
        </div>

        <div className={`relative z-30 shrink-0 ${isChatOpen ? 'animate-in slide-in-from-right duration-300' : 'hidden'}`}>
          <ChatSidebar
            isOpen={isChatOpen}
            onClose={() => setIsChatOpen(false)}
            initialUserMessage={initialUserMessage}
            externalSessionId={sessionId}
            sendInitialMessage={sendInitialMessage}
          />
        </div>
      </div>

      <div className="z-20 flex h-8 shrink-0 items-center border-t border-[#1a2a3a] bg-[#0a1520] px-4">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-2">
            <span className="h-1.5 w-1.5 rounded-full bg-[#2eca8e]" />
            <span className="text-[11px] font-medium text-[#5a8aaa]">
              {RESEARCH_PHASES.find(p => p.id === activePhase)?.label || 'Playground'}
            </span>
          </div>

          <div className="flex items-center gap-4 text-[10px] text-[#4a6580]">
            <div className="flex items-center gap-1.5"><span className="rounded border border-[#1e3248] bg-[#0d1e2e] px-1.5 py-0.5 text-[#5a8aaa]">Drag</span><span>Move</span></div>
            <div className="flex items-center gap-1.5"><span className="rounded border border-[#1e3248] bg-[#0d1e2e] px-1.5 py-0.5 text-[#5a8aaa]">Right-click</span><span>Menu</span></div>
            <div className="flex items-center gap-1.5"><span className="rounded border border-[#1e3248] bg-[#0d1e2e] px-1.5 py-0.5 text-[#5a8aaa]">Ctrl+Scroll</span><span>Zoom</span></div>
            <div className="flex items-center gap-1.5"><span className="rounded border border-[#1e3248] bg-[#0d1e2e] px-1.5 py-0.5 text-[#5a8aaa]">Space</span><span>Pan</span></div>
          </div>
        </div>
      </div>

      <ClientSettingsPanel isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />
    </div>
  );
}

export default function ResearchPlayground() {
  return (
    <ResearchProvider>
      <ResearchPlaygroundInner />
    </ResearchProvider>
  );
}
