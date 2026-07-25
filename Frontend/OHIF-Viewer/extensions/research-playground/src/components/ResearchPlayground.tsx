import React, { useEffect, useMemo, useState } from 'react';
import { ArrowLeft, FlaskConical, List, MessageSquare, PlayCircle, Rocket, Settings } from 'lucide-react';
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
import { ResearchProvider } from '../context/ResearchContext';
import { useResearchData } from '../hooks/useResearchData';
import websocketService from '../services/websocket';
import { fetchSessionExecutionState, fetchSessionPlanState, replaySessionLogs } from '../services/api';
import { ResearchRouteState } from '../types';

const DEFAULT_PHASE = 'planning';

/**
 * Modeling updates can arrive as either the raw PyCaret response or the
 * experiment-keyed payload produced by PipelineMonitor. Convert both forms
 * before state is updated so live updates and replayed messages render alike.
 */
function normalizeModelingPayload(payload: any): Record<string, any> {
  if (!payload || typeof payload !== 'object') return {};

  const result = payload.data && typeof payload.data === 'object' ? payload.data : payload;
  const isExperiment = (value: any) => Boolean(
    value &&
      typeof value === 'object' &&
      (value.chart_data || value.experiment_name || value.tool === 'classification')
  );

  if (isExperiment(result)) {
    const experimentName = result.experiment_name || 'classification_experiment';
    return { [experimentName]: result };
  }

  // Agent-output summaries are ordinary objects too, but their fields
  // (inputs, outputs, errors, warnings) are not modeling experiments.
  return Object.fromEntries(
    Object.entries(result).filter(([, value]) => isExperiment(value))
  );
}

function ResearchPlaygroundInner() {
  const location = useLocation();
  const navigate = useNavigate();
  const { phaseId } = useParams<{ phaseId?: string }>();
  const routeState = (location.state || {}) as ResearchRouteState;
  const includeLitReview = routeState.includeLitReview ?? true;
  const phases = useMemo(
    () => RESEARCH_PHASES.filter(phase => includeLitReview || phase.id !== 'literature'),
    [includeLitReview]
  );
  const [activePhase, setActivePhase] = useState<string>(() => {
    if (phaseId && phases.some(phase => phase.id === phaseId)) {
      return phaseId;
    }
    return DEFAULT_PHASE;
  });
  const [isChatOpen, setIsChatOpen] = useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [isPhaseSidebarCollapsed, setIsPhaseSidebarCollapsed] = useState(false);
  const [updatedPhaseIds, setUpdatedPhaseIds] = useState<string[]>([]);
  const [viewsReady, setViewsReady] = useState(false);
  const [restoredMode, setRestoredMode] = useState<'planning' | 'executing' | 'results'>('planning');

  // Derive sessionId first so it can be used in useState initialisers below
  const hypothesis = routeState.hypothesis || 'No active hypothesis defined.';
  const sessionId = routeState.sessionId || routeState.session_id || null;
  const sendInitialMessage = routeState.sendInitialMessage || false;
  const initialUserMessage = sendInitialMessage ? hypothesis : undefined;

  // True once we know the restored mode — gates both canvas mount and log replay
  // so PlaygroundCanvas always receives the correct initialMode on first render.
  const [restoredModeReady, setRestoredModeReady] = useState(!sessionId);
  const { data, setLiteratureOutput, setPopulationData, setSegmentationData, setFeaturesData, setImportanceData, setModelingData, setReportData, setLatexData } = useResearchData();
  const sidebarPhases = useMemo(
    () => phases.filter(phase => phase.id !== 'planning' && phase.id !== 'execution'),
    [phases]
  );

  const isValidPhase = !phaseId || phases.some(phase => phase.id === phaseId);

    useEffect(() => {
        if (phaseId && !isValidPhase) {
            navigate('/playground/planning', { replace: true, state: location.state });
        }
    }, [phaseId, isValidPhase, navigate, location.state]);

    // Load state from endpoints when session_id is provided
    // Effect 1: non-replay session state — also derives the initial canvas mode
    useEffect(() => {
        const loadSessionState = async () => {
            if (!sessionId) return;
            try {
                // Check the live session status to determine initial canvas mode
                const statusRes = await fetch(`http://localhost:8150/status/${sessionId}`);
                if (statusRes.ok) {
                    const { state } = await statusRes.json();
                    const upperState = state?.toUpperCase();
                    if (upperState === 'COMPLETED') {
                        setRestoredMode('results');
                    } else if (['RUNNING', 'PAUSED', 'FAILED'].includes(upperState)) {
                        setRestoredMode('executing');
                    }
                } else {
                    // Backend has no in-memory session (e.g. restarted) — check execution-state
                    const execRes = await fetch(`http://localhost:8150/session/${sessionId}/execution-state`);
                    if (execRes.ok) {
                        const execData = await execRes.json();
                        if (Array.isArray(execData?.agents) && execData.agents.length > 0) {
                            const allDone = execData.agents.every((a: any) => a.status === 'COMPLETED');
                            setRestoredMode(allDone ? 'results' : 'executing');
                        }
                    }
                }
            } catch (err) {
                console.error('[ResearchPlayground] Failed to load session state:', err);
            } finally {
                // Always mark ready — even on error we fall back to 'planning'
                setRestoredModeReady(true);
            }
        };
        loadSessionState();
    }, [sessionId]);

  useEffect(() => {
    const raf = requestAnimationFrame(() => setViewsReady(true));
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    // Wait for both: views mounted AND mode resolved before replaying
    if (!sessionId || !viewsReady || !restoredModeReady) return;
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
  }, [sessionId, viewsReady, restoredModeReady]);

  useEffect(() => {
    const eventToPhaseMap: Record<string, string> = {
      literature_output: 'literature',
      population_output: 'data',
      segmentation_output: 'segmentation',
      features_output: 'features',
      modeling_output: 'modeling',
      report_output: 'reporting',
      latex_output: 'reporting',
    };

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
          // setModelingData is a custom context helper, not React's raw
          // setState function. Pass the normalized object directly; the
          // helper performs the merge with existing experiments.
          setModelingData(normalizeModelingPayload(payload));
          break;
        case 'report_output':
          setReportData({ ...payload, sessionId: event.session_id || sessionId });
          setActivePhase('reporting');
          break;
        case 'latex_output':
          setLatexData({ ...payload, sessionId: event.session_id || sessionId });
          setActivePhase('reporting');
          break;
      }

      const phaseId = eventToPhaseMap[type];
      if (phaseId && phases.some(phase => phase.id === phaseId) && phaseId !== activePhase) {
        setUpdatedPhaseIds(prev => (prev.includes(phaseId) ? prev : [...prev, phaseId]));
      }
    });
    return unsubscribe;
  }, [
    activePhase,
    phases,
    setFeaturesData,
    setImportanceData,
    setLatexData,
    setModelingData,
    setPopulationData,
    setReportData,
    setSegmentationData,
  ]);

  useEffect(() => {
    setUpdatedPhaseIds(prev => prev.filter(id => id !== activePhase));
  }, [activePhase]);

  const selectPhase = (id: string) => {
    setUpdatedPhaseIds(prev => prev.filter(phaseId => phaseId !== id));
    setActivePhase(id);
  };

  const viewNodes = useMemo(
    () => [
      {
        id: 'planning',
        // Only render once the restored mode is known — prevents PlaygroundCanvas
        // from mounting with mode='planning' then receiving plan_approval during replay.
        node: restoredModeReady
          ? <PlanningView initialHypothesis={hypothesis} sessionId={sessionId} includeLitReview={includeLitReview} onLiteratureOutput={setLiteratureOutput} initialMode={restoredMode} />
          : null,
      },
      { id: 'execution', node: <ExecutionView sessionId={sessionId} /> },
      ...(includeLitReview
        ? [
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
          ]
        : []),
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
      { id: 'reporting', node: <ReportingView reportData={data.reportData} latexData={data.latexData} sessionId={sessionId || undefined} /> },
    ],
    [data, hypothesis, includeLitReview, restoredMode, restoredModeReady, routeState.literatureContext, routeState.literatureReviewOutput, sessionId, setLiteratureOutput]
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
              <ArrowLeft size={16} />
            </button>

          


          <div className="flex items-center gap-2 px-4">
            <div className="flex items-center rounded-md border border-[#2a3a4c] bg-[#0d1b2a] p-0.5">
              <button
                onClick={() => selectPhase('planning')}
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide transition-all ${
                  activePhase === 'planning'
                    ? 'border border-[#6f8db3] bg-[#1d3249] text-[#d1e7ff]'
                    : 'border border-transparent text-[#7fa2c4] hover:bg-[#152537] hover:text-[#c7def6]'
                }`}
              >
                <PlayCircle size={12} /> Study Planning
              </button>
              <button
                onClick={() => selectPhase('execution')}
                className={`flex items-center gap-1.5 rounded px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide transition-all ${
                  activePhase === 'execution'
                    ? 'border border-[#a06fd4] bg-[#2b1b38] text-[#f0ddff]'
                    : 'border border-transparent text-[#a987c4] hover:bg-[#20152b] hover:text-[#dfc6f2]'
                }`}
              >
                <Rocket size={12} /> Agent Execution
              </button>
            </div>

          </div>
        </div>
        
        

        <div className="flex items-center gap-2">
           <button
                onClick={() => navigate('/worklist')}
                className="mr-2 p-1.5 rounded-md transition-all hover:bg-[#0d1e2e] text-[#5a8aaa] hover:text-[#5acce6] border border-transparent hover:border-[#1e3248]"
                title="Go to Worklist"
                aria-label="Go to Worklist"
            >
              <List size={16} />
            </button>
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
          phases={sidebarPhases}
          activePhase={activePhase}
          updatedPhaseIds={updatedPhaseIds}
          onSelectPhase={selectPhase}
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
            onPlanApprovalNeeded={() => setActivePhase('planning')}
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
