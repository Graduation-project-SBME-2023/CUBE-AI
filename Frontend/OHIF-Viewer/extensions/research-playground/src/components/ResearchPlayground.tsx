import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowLeft, LayoutList, List, MessageSquare, PlayCircle, Rocket, Settings } from 'lucide-react';
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
import { RESEARCH_PHASES, MODULE_TO_PHASE_ID, resolveStepToPhaseId } from '../constants/researchPhases';
import { ResearchProvider } from '../context/ResearchContext';
import { useResearchData } from '../hooks/useResearchData';
import websocketService from '../services/websocket';
import {
  fetchSessionExecutionState,
  fetchSessionStatus,
  hydrateSession,
  replaySessionLogs,
} from '../services/api';
import { ResearchRouteState } from '../types';

const DEFAULT_PHASE = 'planning';

// An output can be emitted several times while an agent works.  A module is
// considered ready for a user-facing update only after its owning agent has
// reported COMPLETED through pipeline_agent_status.
const OUTPUT_EVENT_AGENTS: Record<string, { agent: string; phase: string }> = {
  lit_review_output: { agent: 'literature_review_agent', phase: 'literature' },
  population_output: { agent: 'study_population_planner_agent', phase: 'data' },
  segmentation_output: { agent: 'segmentation_agent', phase: 'segmentation' },
  features_output: { agent: 'feature_extraction_agent', phase: 'features' },
  modeling_output: { agent: 'modeling_agent', phase: 'modeling' },
  report_output: { agent: 'result_reporter_agent', phase: 'reporting' },
  latex_output: { agent: 'latex_reporter_agent', phase: 'reporting' },
};

const WAITING_MESSAGES: Record<string, string> = {
  WAITING_FOR_REVIEW: 'Agent is waiting for user review',
  WAITING_FOR_USER_REVIEW: 'Agent is waiting for user review',
  WAITING_FOR_LABELS: 'Waiting for user to upload labels',
  WAITING_FOR_USER_INPUT: 'Agent is waiting for user input',
};

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
      (
        value.chart_data ||
        value.experiment_name ||
        value.tool === 'classification' ||
        value.tool === 'regression'
      )
  );

  if (isExperiment(result)) {
    const experimentName = result.experiment_name || `${result.task_type || result.tool || 'modeling'}_experiment`;
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
  const [phaseUpdateMessages, setPhaseUpdateMessages] = useState<Record<string, string>>({});
  const [waitingPhaseIds, setWaitingPhaseIds] = useState<string[]>([]);
  const [viewsReady, setViewsReady] = useState(false);
  /**
   * Phase IDs that the backend plan actually includes.
   * null  = no plan received yet  → show all phases (fall-back)
   * []    = plan received but empty → show all phases (fall-back)
   * [...] = plan received          → only show these phases in the sidebar
   */
  const [activePipelinePhaseIds, setActivePipelinePhaseIds] = useState<string[] | null>(null);
  /**
   * The phase ID of the furthest agent that has started or completed.
   * Drives the progressive one-by-one sidebar unlock during execution.
   * null = execution not started or re-started.
   */
  const [furthestReachedPhaseId, setFurthestReachedPhaseId] = useState<string | null>(null);
  /** Mirror of sidebarPhases readable inside WS handler closures without stale state. */
  const sidebarPhasesRef = useRef<any[]>([]);
  const furthestReachedPhaseIdxRef = useRef<number>(-1);
  const [restoredMode, setRestoredMode] = useState<'planning' | 'executing' | 'results'>('planning');
  const [hydratedExecutionState, setHydratedExecutionState] = useState<any>(null);
  const [manualResumeRequired, setManualResumeRequired] = useState(false);
  const [restoredPaused, setRestoredPaused] = useState(false);
  // Gates the Data & Population tab: stays false until plan is approved and execution begins
  // Initialized from localStorage so it survives re-navigation within the same session
  const [isPlanApproved, setIsPlanApproved] = useState<boolean>(() => {
    const sid = routeState.sessionId || routeState.session_id || null;
    if (!sid) return false;
    try {
      return localStorage.getItem(`cube_plan_approved_${sid}`) === 'true';
    } catch (_) { return false; }
  });
  // True while agents are actively running — locks chat input to prevent noise during execution
  // Initialized from localStorage so lock persists across re-navigation
  const [isAgentExecuting, setIsAgentExecuting] = useState<boolean>(() => {
    const sid = routeState.sessionId || routeState.session_id || null;
    if (!sid) return false;
    try {
      return localStorage.getItem(`cube_agent_executing_${sid}`) === 'true';
    } catch (_) { return false; }
  });

  // Derive sessionId first so it can be used in useState initialisers below
  const hypothesis = routeState.hypothesis || 'No active hypothesis defined.';
  const sessionId = routeState.sessionId || routeState.session_id || null;
  const sendInitialMessage = routeState.sendInitialMessage || false;
  const initialUserMessage = sendInitialMessage ? hypothesis : undefined;
  // Explicitly archived routes are always historical. Completed sessions are
  // also marked historical after the status check below; do not infer history
  // solely from sendInitialMessage because a running session can be reopened.
  const isHistoricalNavigation = Boolean(routeState.isHistorical || routeState.archived);
  const activePhaseRef = useRef(activePhase);
  const viewedPhaseIdsRef = useRef<Set<string>>(new Set());
  const waitingPhaseIdsRef = useRef<Set<string>>(new Set());
  const updateStateRef = useRef({
    sessionId: sessionId ? String(sessionId) : '',
    isHydrating: Boolean(sessionId),
    isHistorical: isHistoricalNavigation,
    outputRevisionByAgent: new Map<string, number>(),
    completedAgents: new Set<string>(),
    notifiedRevisionByAgent: new Map<string, number>(),
  });

  // True once we know the restored mode — gates both canvas mount and log replay
  // so PlaygroundCanvas always receives the correct initialMode on first render.
  const [restoredModeReady, setRestoredModeReady] = useState(!sessionId);
  const { data, setData, setLiteratureOutput, setPopulationData, setSegmentationData, setFeaturesData, setImportanceData, setModelingData, setReportData, setLatexData } = useResearchData();

  const viewedPhasesStorageKey = useMemo(
    () => (sessionId ? `cube_viewed_phase_updates_${sessionId}` : null),
    [sessionId]
  );

  const markPhaseViewed = useCallback(
    (phaseId: string) => {
      if (!phaseId) return;
      viewedPhaseIdsRef.current.add(phaseId);
      if (!viewedPhasesStorageKey) return;
      try {
        localStorage.setItem(
          viewedPhasesStorageKey,
          JSON.stringify(Array.from(viewedPhaseIdsRef.current))
        );
      } catch (_) {
        // Storage is an enhancement only; an unavailable browser storage must
        // never prevent the workflow UI from rendering.
      }
    },
    [viewedPhasesStorageKey]
  );

  const showPhaseIndicator = useCallback((phaseId: string, message: string, isWaiting = false) => {
    setUpdatedPhaseIds(prev => (prev.includes(phaseId) ? prev : [...prev, phaseId]));
    setPhaseUpdateMessages(prev => (prev[phaseId] === message ? prev : { ...prev, [phaseId]: message }));
    if (isWaiting) {
      waitingPhaseIdsRef.current.add(phaseId);
      setWaitingPhaseIds(prev => (prev.includes(phaseId) ? prev : [...prev, phaseId]));
    }
  }, []);

  const clearPhaseIndicator = useCallback((phaseId: string) => {
    waitingPhaseIdsRef.current.delete(phaseId);
    setWaitingPhaseIds(prev => prev.filter(id => id !== phaseId));
    setUpdatedPhaseIds(prev => prev.filter(id => id !== phaseId));
    setPhaseUpdateMessages(prev => {
      if (!(phaseId in prev)) return prev;
      const { [phaseId]: _ignored, ...remaining } = prev;
      return remaining;
    });
  }, []);

  const tryShowCompletedUpdate = useCallback((agentName: string) => {
    const state = updateStateRef.current;
    const output = Object.values(OUTPUT_EVENT_AGENTS).find(item => item.agent === agentName);
    const revision = state.outputRevisionByAgent.get(agentName) || 0;
    const notifiedRevision = state.notifiedRevisionByAgent.get(agentName) || 0;

    // Ignore replayed/historical data and all intermediate agent output.  The
    // requestAnimationFrame below runs after React has committed the final data
    // update to the corresponding view.
    if (
      !output ||
      state.isHydrating ||
      state.isHistorical ||
      !state.completedAgents.has(agentName) ||
      revision <= notifiedRevision
    ) {
      return;
    }

    state.notifiedRevisionByAgent.set(agentName, revision);
    if (activePhaseRef.current === output.phase || viewedPhaseIdsRef.current.has(output.phase)) {
      return;
    }

    requestAnimationFrame(() => {
      const currentState = updateStateRef.current;
      if (
        currentState.isHydrating ||
        currentState.isHistorical ||
        activePhaseRef.current === output.phase ||
        viewedPhaseIdsRef.current.has(output.phase)
      ) {
        return;
      }
      showPhaseIndicator(output.phase, 'New update received');
    });
  }, [showPhaseIndicator]);

  useEffect(() => {
    activePhaseRef.current = activePhase;
    markPhaseViewed(activePhase);
  }, [activePhase, markPhaseViewed]);

  useEffect(() => {
    const state = updateStateRef.current;
    state.sessionId = sessionId ? String(sessionId) : '';
    state.isHydrating = Boolean(sessionId);
    state.isHistorical = isHistoricalNavigation;
    state.outputRevisionByAgent.clear();
    state.completedAgents.clear();
    state.notifiedRevisionByAgent.clear();
    viewedPhaseIdsRef.current = new Set();
    waitingPhaseIdsRef.current = new Set();
    setWaitingPhaseIds([]);
    setPhaseUpdateMessages({});

    if (viewedPhasesStorageKey) {
      try {
        const saved = JSON.parse(localStorage.getItem(viewedPhasesStorageKey) || '[]');
        if (Array.isArray(saved)) viewedPhaseIdsRef.current = new Set(saved.filter(Boolean));
      } catch (_) {
        // Treat invalid or unavailable storage as an empty viewed set.
      }
    }

    // The initial phase is already on screen, so it is viewed even when this
    // effect runs after the active-phase effect during a session change.
    markPhaseViewed(activePhaseRef.current);
  }, [isHistoricalNavigation, markPhaseViewed, sessionId, viewedPhasesStorageKey]);

  // Clear all tab results when the user restarts execution
  const clearAllTabData = useCallback(() => {
    setData(prev => ({
      ...prev,
      populationData: null,
      segmentationData: null,
      featuresData: null,
      importanceData: null,
      modelingData: null,
      reportData: null,
      latexData: null,
    }));
    setUpdatedPhaseIds([]);
    setPhaseUpdateMessages({});
    waitingPhaseIdsRef.current = new Set();
    setWaitingPhaseIds([]);
    // Reset approval and execution gates when execution restarts
    setIsPlanApproved(false);
    setIsAgentExecuting(false);
    // Reset the dynamic phase list so a new plan can re-populate the sidebar
    setActivePipelinePhaseIds(null);
    // Reset progressive unlock frontier
    setFurthestReachedPhaseId(null);
    furthestReachedPhaseIdxRef.current = -1;
    // Also clear persisted state so a hard restart begins fresh
    if (sessionId) {
      try {
        localStorage.removeItem(`cube_plan_approved_${sessionId}`);
        localStorage.removeItem(`cube_agent_executing_${sessionId}`);
      } catch (_) {}
    }
  }, [setData, sessionId]);
  /**
   * The full list of sidebar-eligible phases (everything except planning & execution).
   * Used as a fallback before the backend plan arrives.
   */
  const allSidebarPhases = useMemo(
    () => phases.filter(phase => phase.id !== 'planning' && phase.id !== 'execution'),
    [phases]
  );

  /**
   * Dynamically filtered sidebar phases — driven by the backend plan.
   * - Before a plan arrives (null / empty): show ALL sidebar phases so the
   *   user can see what steps exist (they will be locked).
   * - After a plan arrives: show ONLY the phases the backend selected, always
   *   prepending Literature Review when includeLitReview is true.
   */
  const sidebarPhases = useMemo(() => {
    if (!activePipelinePhaseIds || activePipelinePhaseIds.length === 0) {
      // No plan yet — show everything as a preview
      return allSidebarPhases;
    }

    // Build an ordered list that respects RESEARCH_PHASES ordering
    return allSidebarPhases.filter(phase => activePipelinePhaseIds.includes(phase.id));
  }, [activePipelinePhaseIds, allSidebarPhases]);

  // Keep sidebarPhasesRef in sync so WS handlers can read the latest list
  useEffect(() => {
    sidebarPhasesRef.current = sidebarPhases;
  }, [sidebarPhases]);

  // During the planning stage (before execution starts), lock every sidebar phase
  // except Literature Review so the user knows these steps exist but are not yet active.
  const lockedPhaseIds = useMemo(
    () => {
      if (!isPlanApproved) {
        // Planning stage: lock all sidebar phases except Literature Review
        return sidebarPhases
          .filter(phase => phase.id !== 'literature')
          .map(phase => phase.id);
      }

      // Execution stage — progressive one-by-one unlock:
      // Find the index of the furthest phase that has been reached.
      // Everything after that index stays locked.
      if (!furthestReachedPhaseId) {
        // Execution just started but no agent has been reported yet—keep locked
        return sidebarPhases
          .filter(phase => phase.id !== 'literature')
          .map(phase => phase.id);
      }

      const furthestIdx = sidebarPhases.findIndex(p => p.id === furthestReachedPhaseId);
      if (furthestIdx === -1) {
        // Phase ID not found in the current sidebar (shouldn’t happen), unlock all
        return [];
      }

      // Lock every phase that comes AFTER the furthest reached one
      return sidebarPhases.slice(furthestIdx + 1).map(p => p.id);
    },
    [isPlanApproved, furthestReachedPhaseId, sidebarPhases]
  );

  const isValidPhase = !phaseId || phases.some(phase => phase.id === phaseId);

    useEffect(() => {
        if (phaseId && !isValidPhase) {
            navigate('/playground/planning', { replace: true, state: location.state });
        }
    }, [phaseId, isValidPhase, navigate, location.state]);

    // Load the persisted snapshot before mounting the canvas. This effect only
    // hydrates state; opening a session must never create an execution task.
    useEffect(() => {
        const loadSessionState = async () => {
            if (!sessionId) return;
            try {
                const sessionKey = String(sessionId);
                const [liveStatus, execData] = await Promise.all([
                    fetchSessionStatus(sessionKey),
                    fetchSessionExecutionState(sessionKey),
                ]);
                setHydratedExecutionState(execData);

                const agents = Array.isArray(execData?.agents) ? execData.agents : [];
                const statuses = agents.map((agent: any) =>
                    String(agent?.status || '').trim().toUpperCase()
                );
                agents.forEach((agent: any) => {
                    const agentName = String(agent?.name || '');
                    const waitingStatus = String(agent?.status || '').trim().toUpperCase();
                    const outputForAgent = Object.values(OUTPUT_EVENT_AGENTS).find(
                        item => item.agent === agentName
                    );
                    const waitingMessage = WAITING_MESSAGES[waitingStatus] || (waitingStatus.startsWith('WAITING') ? 'Agent is waiting for user input' : undefined);
                    if (outputForAgent && waitingMessage) {
                        showPhaseIndicator(outputForAgent.phase, waitingMessage, true);
                        setActivePhase(outputForAgent.phase);
                    }
                });
                const allDone =
                    statuses.length > 0 &&
                    statuses.every(status => ['COMPLETED', 'DONE'].includes(status));
                const hasProgress = statuses.some(
                    status => status && !['PENDING', 'NOT_CONFIRMED', 'READY'].includes(status)
                );
                const liveState = String(liveStatus?.state || '').toUpperCase();
                const executionActive = Boolean(liveStatus?.execution_active);
                const requiresManualResume =
                    !sendInitialMessage &&
                    statuses.length > 0 &&
                    !allDone &&
                    !executionActive;
                setManualResumeRequired(requiresManualResume);
                setRestoredPaused(requiresManualResume || liveState === 'PAUSED');

                if (allDone || liveState === 'COMPLETED') {
                    updateStateRef.current.isHistorical = true;
                    setRestoredMode('results');
                } else if (
                    requiresManualResume ||
                    hasProgress ||
                    ['RUNNING', 'PAUSED', 'FAILED', 'CANCELLED'].includes(liveState)
                ) {
                    setRestoredMode('executing');
                } else {
                    setRestoredMode('planning');
                }

                // Rebuild the in-memory shell after a backend restart. The
                // endpoint is hydration-only unless restart=true is explicit.
                if (!liveStatus && execData) {
                    await hydrateSession(sessionKey);
                }
            } catch (err) {
                console.error('[ResearchPlayground] Failed to hydrate session state:', err);
            } finally {
                setRestoredModeReady(true);
            }
        };
        loadSessionState();
    }, [sendInitialMessage, sessionId, showPhaseIndicator]);

  // Once we know the restored mode, sync isPlanApproved:
  // - Restored executing/results sessions are already approved
  // - Fresh new sessions always start locked
  useEffect(() => {
    if (!restoredModeReady) return;
    if (restoredMode === 'executing' || restoredMode === 'results') {
      setIsPlanApproved(true);
    }
    // Note: do NOT reset to false here — if sessionStorage already has it true (user re-navigated
    // to a live session), we must keep it. Only the explicit clearAllTabData() resets it.
  }, [restoredModeReady, restoredMode]);

  // Persist isPlanApproved and isAgentExecuting to localStorage so they survive re-navigation
  useEffect(() => {
    if (!sessionId) return;
    try {
      localStorage.setItem(`cube_plan_approved_${sessionId}`, String(isPlanApproved));
    } catch (_) {}
  }, [sessionId, isPlanApproved]);

  useEffect(() => {
    if (!sessionId) return;
    try {
      localStorage.setItem(`cube_agent_executing_${sessionId}`, String(isAgentExecuting));
    } catch (_) {}
  }, [sessionId, isAgentExecuting]);

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
        // Log history can contain older status broadcasts. Apply the current
        // persisted snapshot last so replay cannot roll the UI backwards.
        if (Array.isArray(hydratedExecutionState?.agents)) {
          websocketService.dispatchLocal({
            type: 'pipeline_agent_status',
            payload: hydratedExecutionState,
            session_id: sessionId,
            replaying: true,
          });
        }
      } catch (err) {
        console.error('[ResearchPlayground] Failed to replay logs:', err);
      } finally {
        websocketService.stopReplayCapture();
        // Replayed events populate the views, but must never be presented as
        // a new update.  Future live events for an active session may alert.
        updateStateRef.current.isHydrating = false;
      }
    };
    replay();
  }, [hydratedExecutionState, sessionId, viewsReady, restoredModeReady]);

  // ── Plan-driven sidebar: listen for pipeline plan events ───────────────────
  useEffect(() => {
    const unsubscribe = websocketService.subscribe((event: any) => {
      const { type, payload } = event;
      const incomingSessionId = event?.session_id;
      if (incomingSessionId && sessionId && String(incomingSessionId) !== String(sessionId)) return;

      if (
        type === 'pipeline_update' ||
        type === 'plan' ||
        type === 'PLAN_UPDATE'
      ) {
        const planData = payload || event.data || event;
        const steps: any[] = Array.isArray(planData?.steps) ? planData.steps : [];

        if (steps.length === 0) return; // no meaningful plan yet

        // Map each backend step → sidebar phase ID, deduplicate, preserve order
        const seen = new Set<string>();
        const phaseIds: string[] = [];

        // Literature always comes first if enabled (arrives via lit_review_node,
        // not via steps[], so inject it here if not already present)
        if (includeLitReview) {
          seen.add('literature');
          phaseIds.push('literature');
        }

        for (const step of steps) {
          const phaseId = resolveStepToPhaseId(step);
          if (phaseId && !seen.has(phaseId)) {
            seen.add(phaseId);
            phaseIds.push(phaseId);
          }
        }

        if (phaseIds.length > 0) {
          setActivePipelinePhaseIds(phaseIds);
        }
      }
    });
    return unsubscribe;
  }, [sessionId, includeLitReview]);

  useEffect(() => {
    const unsubscribe = websocketService.subscribe((event: any) => {
      const { type, payload } = event;
      const incomingSessionId = event?.session_id;
      if (incomingSessionId && sessionId && String(incomingSessionId) !== String(sessionId)) {
        return;
      }

      switch (type) {
        case 'lit_review_output':
          // Keep the final literature payload in the same data store used by
          // the Literature module before its completion notification appears.
          setLiteratureOutput(payload);
          break;
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

      const output = OUTPUT_EVENT_AGENTS[type];
      if (output) {
        const state = updateStateRef.current;
        state.outputRevisionByAgent.set(
          output.agent,
          (state.outputRevisionByAgent.get(output.agent) || 0) + 1
        );
        tryShowCompletedUpdate(output.agent);
      }

      // Literature Review is a planning-stage agent, so it does not emit a
      // pipeline_agent_status entry.  Its own done event is the authoritative
      // completion signal and follows the same final-data-then-completed flow.
      if (type === 'lit_review_done') {
        updateStateRef.current.completedAgents.add('literature_review_agent');
        tryShowCompletedUpdate('literature_review_agent');
      }

      if (type === 'agent_waiting_for_user_input') {
        // If the population agent is already waiting for input, plan is approved
        setIsPlanApproved(true);
        // Note: do NOT touch isAgentExecuting here — chat stays locked via isPlanApproved
        const agentName = String(payload?.agent || '');
        const waitingStatus = String(payload?.status || '').toUpperCase();
        const outputForAgent = Object.values(OUTPUT_EVENT_AGENTS).find(item => item.agent === agentName);
        const message = WAITING_MESSAGES[waitingStatus] || (waitingStatus.startsWith('WAITING') ? 'Agent is waiting for user input' : undefined);

        if (outputForAgent && message) {
          if (updateStateRef.current.isHydrating) {
            showPhaseIndicator(outputForAgent.phase, message, true);
            setActivePhase(outputForAgent.phase);
          } else if (!updateStateRef.current.isHistorical) {
            showPhaseIndicator(outputForAgent.phase, message, true);
          }
        }
      }

      if (type === 'agent_waiting_resolved') {
        const agentName = String(payload?.agent || '');
        const outputForAgent = Object.values(OUTPUT_EVENT_AGENTS).find(item => item.agent === agentName);
        if (outputForAgent) clearPhaseIndicator(outputForAgent.phase);
      }

      if (type === 'pipeline_agent_status' || type === 'Pipeline_agent_status') {
        // Execution has started — unlock the Data & Population tab
        setIsPlanApproved(true);
        const agents = payload?.agents || [];
        const allDone = agents.length > 0 && agents.every((a: any) =>
          ['COMPLETED', 'DONE'].includes(String(a?.status || '').toUpperCase())
        );
        const anyWaiting = agents.some((a: any) =>
          String(a?.status || '').toUpperCase().startsWith('WAITING')
        );
        // Lock chat while actively running; unlock when all done or paused for input
        if (!updateStateRef.current.isHydrating && !updateStateRef.current.isHistorical) {
          setIsAgentExecuting(!allDone && !anyWaiting);
        }

        // ── Progressive sidebar unlock ──────────────────────────────────────
        // Find the furthest phase (in sidebarPhases order) where the
        // corresponding agent has STARTED (anything other than PENDING/READY).
        const phases = sidebarPhasesRef.current;
        let newFurthestIdx = furthestReachedPhaseIdxRef.current;
        let newFurthestId: string | null = null;

        for (const agent of agents) {
          const agentName = String(agent?.name || '').trim().toLowerCase();
          const status = String(agent?.status || '').trim().toUpperCase();
          // Consider an agent "reached" as soon as it is not merely pending/ready
          const hasStarted = agentName && !['PENDING', 'NOT_CONFIRMED', 'READY'].includes(status);
          if (!hasStarted) continue;

          const phaseId = MODULE_TO_PHASE_ID[agentName];
          if (!phaseId) continue;

          const idx = phases.findIndex(p => p.id === phaseId);
          if (idx > newFurthestIdx) {
            newFurthestIdx = idx;
            newFurthestId = phaseId;
          }
        }

        if (newFurthestId && newFurthestIdx > furthestReachedPhaseIdxRef.current) {
          furthestReachedPhaseIdxRef.current = newFurthestIdx;
          setFurthestReachedPhaseId(newFurthestId);
        }
        // ────────────────────────────────────────────────────────

        for (const agent of agents) {
          const agentName = String(agent?.name || '');
          if (
            ['COMPLETED', 'DONE'].includes(
              String(agent?.status || '').toUpperCase()
            )
          ) {
            updateStateRef.current.completedAgents.add(agentName);
            const outputForAgent = Object.values(OUTPUT_EVENT_AGENTS).find(
              item => item.agent === agentName
            );
            if (outputForAgent) clearPhaseIndicator(outputForAgent.phase);
            tryShowCompletedUpdate(agentName);
          }
        }
      }

      // run_status also carries the current active agent — use it to advance
      // the progressive unlock frontier in real-time between status broadcasts.
      if (type === 'run_status' && payload?.current_agent) {
        const agentName = String(payload.current_agent).trim().toLowerCase();
        const phaseId = MODULE_TO_PHASE_ID[agentName];
        if (phaseId) {
          const phases = sidebarPhasesRef.current;
          const idx = phases.findIndex(p => p.id === phaseId);
          if (idx > furthestReachedPhaseIdxRef.current) {
            furthestReachedPhaseIdxRef.current = idx;
            setFurthestReachedPhaseId(phaseId);
          }
        }
      }
    });
    return unsubscribe;
  }, [
    setFeaturesData,
    setImportanceData,
    setLatexData,
    setLiteratureOutput,
    setModelingData,
    setPopulationData,
    setReportData,
    setSegmentationData,
    clearPhaseIndicator,
    showPhaseIndicator,
    tryShowCompletedUpdate,
    sessionId,
  ]);

  useEffect(() => {
    if (!waitingPhaseIdsRef.current.has(activePhase)) {
      setUpdatedPhaseIds(prev => prev.filter(id => id !== activePhase));
      setPhaseUpdateMessages(prev => {
        if (!(activePhase in prev)) return prev;
        const { [activePhase]: _ignored, ...remaining } = prev;
        return remaining;
      });
    }
  }, [activePhase]);

  const selectPhase = (id: string) => {
    markPhaseViewed(id);
    if (!waitingPhaseIdsRef.current.has(id)) {
      clearPhaseIndicator(id);
    }
    setActivePhase(id);
  };

  const viewNodes = useMemo(
    () => [
      {
        id: 'planning',
        // Only render once the restored mode is known — prevents PlaygroundCanvas
        // from mounting with mode='planning' then receiving plan_approval during replay.
        node: restoredModeReady
          ? <PlanningView initialHypothesis={hypothesis} sessionId={sessionId} includeLitReview={includeLitReview} onLiteratureOutput={setLiteratureOutput} initialMode={restoredMode} onRestart={clearAllTabData} autoStartPlanning={sendInitialMessage} initialPaused={restoredPaused} manualResumeRequired={manualResumeRequired} />
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
        node: <DataCohortView populationData={data.populationData} segmentationData={data.segmentationData} isPlanApproved={isPlanApproved} />,
      },
      { id: 'segmentation', node: <SegmentationView segmentationData={data.segmentationData as any} /> },
      {
        id: 'features',
        node: <FeaturesView featuresData={data.featuresData} importanceData={data.importanceData} />,
      },
      { id: 'modeling', node: <ModelingView modelingData={data.modelingData as any} /> },
      { id: 'reporting', node: <ReportingView reportData={data.reportData} latexData={data.latexData} sessionId={sessionId || undefined} /> },
    ],
    [clearAllTabData, data, hypothesis, includeLitReview, manualResumeRequired, restoredMode, restoredModeReady, restoredPaused, routeState.literatureContext, routeState.literatureReviewOutput, sendInitialMessage, sessionId, setLiteratureOutput]
  );

  return (
    <div className="flex h-screen w-full overflow-hidden bg-black font-sans text-white">
      <PhaseSidebar
        phases={sidebarPhases}
        activePhase={activePhase}
        updatedPhaseIds={updatedPhaseIds}
        updateMessages={phaseUpdateMessages}
        forceVisibleUpdatePhaseIds={waitingPhaseIds}
        lockedPhaseIds={lockedPhaseIds}
        planReceived={activePipelinePhaseIds !== null}
        onSelectPhase={selectPhase}
        isCollapsed={isPhaseSidebarCollapsed}
        onToggleCollapse={() => setIsPhaseSidebarCollapsed(prev => !prev)}
      />

      <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
        {/* Top Navbar */}
        <div className="z-20 flex h-14 shrink-0 items-center justify-between bg-[#0a1520]/95 backdrop-blur-md px-4 shadow-sm border-b border-[#1a2a3a] relative">
          {/* Subtle top highlight line */}
          <div className="absolute top-0 left-0 right-0 h-px bg-gradient-to-r from-transparent via-[#2a4a6c]/30 to-transparent"></div>
          
          <div className="flex items-center">
            {/* Mode Segmented Control */}
            <div className="flex items-center rounded-lg border border-[#233547] bg-[#07111a] p-1 shadow-inner">
              <button
                onClick={() => selectPhase('planning')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-[10px] font-bold uppercase tracking-widest transition-all duration-300 ${
                  activePhase === 'planning'
                    ? 'bg-gradient-to-r from-[#1d3249] to-[#264463] text-[#e0f0ff] shadow-[0_2px_12px_rgba(40,80,120,0.4)] border border-[#3b5b80]'
                    : 'text-[#6f8db3] hover:text-[#c7def6] hover:bg-[#111e2b] border border-transparent'
                }`}
              >
                <PlayCircle size={13} /> Study Planning
              </button>
              <button
                onClick={() => selectPhase('execution')}
                className={`flex items-center gap-1.5 rounded-md px-3 py-1 text-[10px] font-bold uppercase tracking-widest transition-all duration-300 ${
                  activePhase === 'execution'
                    ? 'bg-gradient-to-r from-[#38234a] to-[#4f3166] text-[#f4e6ff] shadow-[0_2px_12px_rgba(90,40,120,0.4)] border border-[#6b478c]'
                    : 'text-[#8a6b9e] hover:text-[#dfc6f2] hover:bg-[#1d1226] border border-transparent'
                }`}
              >
                <Rocket size={13} /> Agent Execution
              </button>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <button
                onClick={() => window.open('/worklist', '_blank')}
                className="flex h-8 items-center gap-2 rounded-lg border border-[#233547] bg-[#0a1520] px-3 text-[10px] font-bold uppercase tracking-wider text-[#7a9ab5] transition-all duration-300 hover:border-[#5acce6] hover:bg-[#11202e] hover:text-[#5acce6] hover:shadow-[0_0_12px_rgba(90,204,230,0.15)] group"
                title="Go to Worklist"
            >
              <LayoutList size={13} className="transition-transform group-hover:scale-110" /> Worklist
            </button>
            
            <div className="w-px h-4 bg-[#233547] mx-1"></div> {/* Divider */}
            
            <button
              onClick={() => setIsSettingsOpen(true)}
              className="flex h-8 items-center gap-2 rounded-lg border border-[#233547] bg-[#0a1520] px-3 text-[10px] font-bold uppercase tracking-wider text-[#7a9ab5] transition-all duration-300 hover:border-[#5acce6] hover:bg-[#11202e] hover:text-[#5acce6] hover:shadow-[0_0_12px_rgba(90,204,230,0.15)] group"
            >
              <Settings size={13} className="transition-transform group-hover:rotate-45" /> Model
            </button>
            <button
              onClick={() => setIsChatOpen(!isChatOpen)}
              className={`flex h-8 items-center gap-2 rounded-lg border px-3 text-[10px] font-bold uppercase tracking-wider transition-all duration-300 group ${
                isChatOpen
                  ? 'border-[#5acce6] bg-gradient-to-r from-[#0d2540] to-[#143659] text-[#5acce6] shadow-[0_0_15px_rgba(90,204,230,0.3)]'
                  : 'border-[#233547] bg-[#0a1520] text-[#7a9ab5] hover:border-[#5acce6] hover:bg-[#11202e] hover:text-[#5acce6] hover:shadow-[0_0_12px_rgba(90,204,230,0.15)]'
              }`}
            >
              <MessageSquare size={13} className={`transition-transform ${isChatOpen ? 'scale-110' : 'group-hover:scale-110'}`} /> AI Assistant
            </button>
          </div>
        </div>

        {/* Main Content Area */}
        <div className="relative flex flex-1 overflow-hidden">
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
            isChatLocked={isPlanApproved}
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
