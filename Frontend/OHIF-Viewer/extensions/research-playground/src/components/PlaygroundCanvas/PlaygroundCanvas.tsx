import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import websocketService from '../../services/websocket';
import config from '../../config';
import { FlowEdge, FlowNode, ToolEvent, ToolLog, AgentNodeData } from './types';
import { createInitialNodes, getDynamicLayout, INITIAL_EDGES, START_W, AGENT_W } from './CanvasLayoutManager';
import CanvasHeader from './CanvasHeader';
import FlowEngineWrapper from './FlowEngineWrapper';
import { registerAllLogRenderers } from './logs/registerRenderers';
import { getAgentsApiBaseUrl } from '../../utils/apiBase';
import { resumeSessionPipeline } from '../../services/api';

interface PlaygroundCanvasProps {
  initialHypothesis?: string;
  session_id?: string;
  includeLitReview?: boolean;
  onLiteratureOutput?: (data: any) => void;
  /** Pre-resolved mode (e.g. 'executing') from session restore — avoids timing race with WS replay */
  initialMode?: 'planning' | 'executing' | 'results';
  /** Called when execution starts or restarts — parent should clear all tab data */
  onClearTabData?: () => void;
  /** Only true for a newly-created chat whose initial hypothesis should start planning. */
  autoStartPlanning?: boolean;
  /** Initial toolbar state restored from a live or hydrated paused session. */
  initialPaused?: boolean;
  /** True when Resume must perform safe interrupted-node recovery. */
  manualResumeRequired?: boolean;
}

export default function PlaygroundCanvas({
  initialHypothesis,
  session_id,
  includeLitReview = true,
  onLiteratureOutput,
  initialMode = 'planning',
  onClearTabData,
  autoStartPlanning = false,
  initialPaused = false,
  manualResumeRequired = false,
}: PlaygroundCanvasProps) {
  const normalizeKey = useCallback(
    (value?: string | null) =>
      String(value || '')
        .toLowerCase()
        .replace(/[^a-z0-9]/g, ''),
    []
  );

  // initialMode is resolved BEFORE render (passed from ResearchPlayground after checking
  // execution-state), so both the state and ref start correct — no race with WS replay.
  const [mode, setMode] = useState<'planning' | 'generated' | 'manual' | 'executing' | 'results'>(initialMode);
  const [nodes, setNodes] = useState<FlowNode[]>(() => createInitialNodes(initialHypothesis));
  const [edges, setEdges] = useState<FlowEdge[]>(INITIAL_EDGES);
  const [isPaused, setIsPaused] = useState(initialPaused || manualResumeRequired);
  const [requiresRecoveryResume, setRequiresRecoveryResume] = useState(manualResumeRequired);
  const [isResuming, setIsResuming] = useState(false);
  const [isStopping, setIsStopping] = useState(false);
  const [hasStopped, setHasStopped] = useState(false);
  const [isConnected, setIsConnected] = useState(websocketService.isConnected());
  const [pendingPrompt, setPendingPrompt] = useState<{ type: string; message: string } | null>(null);
  const [canExecute, setCanExecute] = useState(initialMode !== 'planning');
  const [fitViewTrigger, setFitViewTrigger] = useState(0);
  const [labellingMode, setLabellingMode] = useState<'manual' | 'automatic'>('automatic');
  const [labellingModeLocked, setLabellingModeLocked] = useState(initialMode !== 'planning');
  const [countdown, setCountdown] = useState<number | null>(null);
  const hasInitiatedPlan = useRef(false);
  const restartRequestedRef = useRef(initialMode === 'results');
  const modeRef = useRef<'planning' | 'generated' | 'manual' | 'executing' | 'results'>(initialMode);
  const nodesRef = useRef<FlowNode[]>([]);
  const hasExecutablePlanRef = useRef(initialMode !== 'planning');
  const sessionIdRef = useRef<string | undefined>(session_id);
  const literatureOutputRef = useRef<typeof onLiteratureOutput>(onLiteratureOutput);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    modeRef.current = mode;

    // Clear any lingering prompts when no longer in planning mode
    if (mode !== 'planning') {
      setPendingPrompt(null);
    }

    // Lock labelling mode once execution begins so the agent sees a stable config
    if (mode === 'executing') {
      setLabellingModeLocked(true);
    }
  }, [mode]);
  useEffect(() => {
    if (session_id) {
      sessionStorage.setItem('active_cube_session', String(session_id));
      console.log('[PlaygroundCanvas] Saved session ID to storage:', session_id);
    }
  }, [session_id]);




  useEffect(() => {
    literatureOutputRef.current = onLiteratureOutput;
  }, [onLiteratureOutput]);

  useEffect(() => {
    if (countdown === null) return;
    if (countdown <= 0) {
      setCountdown(null);
      startExecution();
      return;
    }
    const timer = setInterval(() => {
      setCountdown(prev => (prev !== null ? prev - 1 : null));
    }, 1000);
    return () => clearInterval(timer);
  }, [countdown]);

  useEffect(() => {
    setRequiresRecoveryResume(manualResumeRequired);
    setIsPaused(initialPaused || manualResumeRequired);
  }, [initialPaused, manualResumeRequired, session_id]);

  const updateNodeData = useCallback((id: string, patch: any) => {
    setNodes(prev => prev.map(n => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)));
  }, []);

  // ── Labelling Mode ───────────────────────────────────────────────────────────
  const toggleLabellingMode = useCallback(async () => {
    if (labellingModeLocked) return;
    const newMode = labellingMode === 'manual' ? 'automatic' : 'manual';
    setLabellingMode(newMode);
    window.dispatchEvent(new CustomEvent('labellingModeChanged', { detail: newMode }));
    const sid = sessionIdRef.current || sessionStorage.getItem('active_cube_session');
    if (sid) {
      try {
        await fetch(
          `${getAgentsApiBaseUrl()}/populations/set-labelling-mode?session_id=${sid}&mode=${newMode}`,
          { method: 'POST' }
        );
      } catch (e) {
        console.warn('[CanvasHeader] Failed to set labelling mode on server:', e);
      }
    }
  }, [labellingMode, labellingModeLocked]);

  const resolveAgentNodeId = useCallback(
    (source: string) => {
      const sourceKey = normalizeKey(source);
      if (!sourceKey) return null;

      const matched = nodesRef.current.find(node => {
        if (node.type !== 'agent') return false;
        const data = node.data as AgentNodeData;
        const candidates = [
          node.id,
          data.matched_module,
          data.title,
          data.name,
        ]
          .filter(Boolean)
          .map(v => normalizeKey(String(v)));
        return candidates.includes(sourceKey);
      });

      return matched?.id || null;
    },
    [normalizeKey]
  );

  const updateCurrentAgent = useCallback((current_agent: string) => {
    const resolvedCurrentId = resolveAgentNodeId(current_agent) || current_agent;

    setNodes(prev => {
      let changed = false;
      const next = prev.map(node => {
        if (node.type !== 'agent') return node;
        const agentData = node.data as AgentNodeData;
        if (node.id === resolvedCurrentId) {
          if (agentData.status === 'active' && agentData.isExpanded) {
            return node;
          }
          changed = true;
          return { ...node, data: { ...agentData, status: 'active', isExpanded: true } };
        }
        if (agentData.status === 'active') {
          changed = true;
          return { ...node, data: { ...agentData, status: 'completed', isExpanded: false } };
        }
        return node;
      });
      return changed ? next : prev;
    });
  }, [resolveAgentNodeId]);

  const updatePipelineAgentStatuses = useCallback(
    (agents: Array<{ name?: string; status?: string }>) => {
      if (!Array.isArray(agents) || agents.length === 0) return;

      const statusByNodeId = new Map<string, 'pending' | 'active' | 'completed' | 'failed'>();

      for (const agent of agents) {
        const resolvedNodeId = resolveAgentNodeId(agent?.name || '');
        if (!resolvedNodeId) continue;

        const backendStatus = String(agent?.status || '').toUpperCase();
        const status =
          backendStatus === 'RUNNING' || backendStatus.startsWith('WAITING')
            ? 'active'
            : backendStatus === 'COMPLETED' || backendStatus === 'DONE'
              ? 'completed'
              : backendStatus === 'FAILED'
                ? 'failed'
                : 'pending';

        statusByNodeId.set(resolvedNodeId, status);
      }

      if (statusByNodeId.size === 0) return;

      setNodes(prev => {
        let changed = false;
        const next = prev.map(node => {
          if (node.type !== 'agent') return node;
          const nextStatus = statusByNodeId.get(node.id);
          if (!nextStatus) return node;
          const agentData = node.data as AgentNodeData;
          const shouldExpand = nextStatus === 'active' ? true : agentData.isExpanded;
          if (agentData.status === nextStatus && agentData.isExpanded === shouldExpand) {
            return node;
          }
          changed = true;
          return {
            ...node,
            data: {
              ...agentData,
              status: nextStatus,
              isExpanded: shouldExpand,
            },
          };
        });
        return changed ? next : prev;
      });
    },
    [resolveAgentNodeId]
  );

  const appendToolEventToNode = useCallback(
    (toolEvent: ToolEvent) => {
      if (!toolEvent?.source) return;

      const targetNodeId = resolveAgentNodeId(toolEvent.source) || toolEvent.source;

      setNodes(prev => {
        let changed = false;
        const next = prev.map(node => {
          if (node.type !== 'agent' || node.id !== targetNodeId) return node;
          changed = true;
          const agentData = node.data as AgentNodeData;
          const existingLogs: ToolLog[] = agentData.logs ?? [];
          let updatedLogs: ToolLog[];

          if (toolEvent.event_type === 'tool_started') {
            updatedLogs = [
              ...existingLogs,
              {
                source: toolEvent.source,
                started_at: toolEvent.timestamp,
                last_updated_at: toolEvent.timestamp,
                events: [toolEvent],
                status: 'active',
              },
            ];
          } else if (existingLogs.length === 0) {
            updatedLogs = [
              {
                source: toolEvent.source,
                started_at: toolEvent.timestamp,
                last_updated_at: toolEvent.timestamp,
                events: [toolEvent],
                status:
                  toolEvent.event_type === 'tool_finished'
                    ? 'completed'
                    : toolEvent.event_type === 'error'
                      ? 'error'
                      : 'active',
              },
            ];
          } else {
            const last = existingLogs[existingLogs.length - 1];
            updatedLogs = [
              ...existingLogs.slice(0, -1),
              {
                ...last,
                last_updated_at: toolEvent.timestamp,
                events: [...last.events, toolEvent],
                status:
                  toolEvent.event_type === 'tool_finished'
                    ? 'completed'
                    : toolEvent.event_type === 'error'
                      ? 'error'
                      : 'active',
              },
            ];
          }

          return { ...node, data: { ...node.data, logs: updatedLogs, isExpanded: true } };
        });
        return changed ? next : prev;
      });
    },
    [resolveAgentNodeId]
  );

  const resetNodeLogs = useCallback(
    (prev: FlowNode[]) =>
      prev.map(n => {
        if (n.type !== 'agent') return n;
        // Literature review completes during planning — preserve its done state on execution start
        if (n.id === 'literature_review') return n;
        return { ...n, data: { ...n.data, status: 'pending', logs: [], isExpanded: false } };
      }),
    []
  );

  const applyBackendPlan = useCallback(
    (planPayload: any) => {
      if (planPayload.nodes) {
        hasExecutablePlanRef.current = planPayload.nodes.some(
          (node: FlowNode) => node.type === 'agent' && node.id !== 'literature_review'
        );
        setNodes(planPayload.nodes);
        if (planPayload.edges) setEdges(planPayload.edges);
        if (planPayload.mode) setMode(planPayload.mode);
        setFitViewTrigger(prev => prev + 1);
        return;
      }

      if (!Array.isArray(planPayload.steps)) return;
      if (planPayload.steps.length === 0) {
        hasExecutablePlanRef.current = false;
        setCountdown(null);
        setCanExecute(false);
        return;
      }

      hasExecutablePlanRef.current = true;

      let newNodes: FlowNode[] = [];
      setNodes(prev => {
        const existingStart = prev.find(n => n.type === 'start');
        const existingLitReview = prev.find(n => n.id === 'literature_review');
        const anchorCount = existingLitReview ? 2 : 1;
        const layout = getDynamicLayout(anchorCount + planPayload.steps.length - 1);
        const kept: FlowNode[] = [];

        kept.push(
          existingStart
            ? { ...existingStart, position: { x: layout.startX, y: layout.startY } }
            : {
              id: 'start',
              type: 'start',
              position: { x: layout.startX, y: layout.startY },
              data: {
                title: 'Research Hypothesis',
                description: planPayload.study_goal || initialHypothesis || 'Study workflow',
              },
            }
        );

        let nextX = layout.startX + START_W + layout.gap;
        if (existingLitReview) {
          kept.push({ ...existingLitReview, position: { x: nextX, y: layout.agentY } });
          nextX += AGENT_W + layout.gap;
        }

        planPayload.steps.forEach((step: any, index: number) => {
          const y = index % 2 === 0 ? layout.agentY - 70 : layout.agentY + 70;
          kept.push({
            id: step.id,
            type: 'agent',
            position: { x: nextX + index * (AGENT_W + layout.gap), y },
            data: {
              title: step.name || `Step ${index + 1}`,
              description: step.description || '',
              status: step.status || 'pending',
              logs: [],
              isExpanded: false,
              matched_module: step.matched_module || step.module || step.id,
            },
          });
        });

        newNodes = kept;
        return kept;
      });

      setTimeout(() => {
        const newEdges: FlowEdge[] = [];
        for (let i = 0; i < newNodes.length - 1; i++) {
          newEdges.push({
            id: `e${newNodes[i].id}-${newNodes[i + 1].id}`,
            source: newNodes[i].id,
            target: newNodes[i + 1].id,
            type: 'custom',
          });
        }
        setEdges(newEdges);
        setFitViewTrigger(prev => prev + 1);
      }, 0);
    },
    [initialHypothesis]
  );

  useEffect(() => {
    registerAllLogRenderers();
    if (session_id) {
      websocketService.connect(String(session_id));
    }

    const unsubscribeMsg = websocketService.subscribe((data: any) => {
      const { type, payload } = data;
      const incomingSessionId = String(data?.session_id);
      const currentSessionId = sessionIdRef.current;
      if (incomingSessionId && currentSessionId && incomingSessionId !== String(currentSessionId)) {
        return;
      }

      if (type === 'hypothesis') {
        setNodes(prev =>
          prev.map(n =>
            n.type === 'start'
              ? {
                ...n,
                data: {
                  ...n.data,
                  title: payload?.title || 'Research Hypothesis',
                  description: payload?.hypothesis || payload?.title || n.data.description,
                  population: payload?.population,
                  intervention: payload?.intervention,
                  comparison: payload?.comparison,
                  outcome: payload?.outcome,
                  hypothesis: payload?.hypothesis,
                  question: payload?.question,
                  clinical_context: payload?.clinical_context,
                  state: payload?.state,
                },
              }
              : n
          )
        );
        return;
      }

      if (type === 'hypothesis_approval' || type === 'lit_review_choice') {
        // Suppress approval prompts when we are already past the planning stage
        if (modeRef.current !== 'planning') {
          return;
        }
        setPendingPrompt({ type, message: payload?.message || payload?.text || '' });
        return;
      }

      if (type === 'planning_complete') {
        const backendHasPipeline =
          payload === true ||
          (payload?.has_pipeline === true && Number(payload?.step_count) > 0);
        if (!backendHasPipeline || !hasExecutablePlanRef.current) {
          setCountdown(null);
          setCanExecute(false);
          return;
        }
        if (modeRef.current === 'planning' || modeRef.current === 'generated') {
          setMode('generated');
          modeRef.current = 'generated';
          setCountdown(8);
        } else if (modeRef.current === 'manual') {
          // If manual (cancelled autorun), don't restart autorun
          setMode('manual');
          // Maybe show a snackbar notification here "Pipeline updated"
        }
        setCanExecute(true);
        return;
      }

      if (type === 'planning_failed') {
        setCountdown(null);
        setCanExecute(false);
        if (modeRef.current === 'generated') {
          setMode('planning');
          modeRef.current = 'planning';
        }
        return;
      }

      if (type === 'lit_review_output') {
        literatureOutputRef.current?.(payload);
        return;
      }

      if (type === 'lit_review_node') {
        const layout = getDynamicLayout(1);
        const litNode: FlowNode = {
          id: 'literature_review',
          type: 'agent',
          position: { x: layout.startX + START_W + layout.gap, y: layout.agentY },
          data: {
            title: 'Literature Review',
            description: 'Building domain knowledge base from medical databases',
            status: 'active',
            logs: [],
            isExpanded: true,
          },
        };
        setNodes(prev => [...prev.filter(n => n.id !== 'literature_review'), litNode]);
        setEdges(prev => [
          ...prev.filter(e => e.target !== 'literature_review'),
          { id: `e1-literature_review`, source: '1', target: 'literature_review', type: 'custom' },
        ]);
        return;
      }

      if (type === 'lit_review_done') {
        setNodes(prev =>
          prev.map(n => (n.id === 'literature_review' && n.type === 'agent' ? { ...n, data: { ...n.data, status: 'completed', isExpanded: false } } : n))
        );
        return;
      }

      if (type === 'node' || type === 'NODE_UPDATE') {
        updateNodeData(data.nodeId || data.id || payload?.nodeId, payload || data.data);
        return;
      }

      if (type === 'pipeline_update' || type === 'plan' || type === 'PLAN_UPDATE') {
        applyBackendPlan(payload || data.data || data);
        return;
      }

      if (type === 'tool_update') {
        appendToolEventToNode(payload as ToolEvent);
        return;
      }

      if (['tool_started', 'tool_progress', 'tool_log', 'tool_finished', 'error'].includes(type)) {
        const directToolEvent = payload?.event_type
          ? (payload as ToolEvent)
          : ({ ...(payload || {}), event_type: type } as ToolEvent);
        appendToolEventToNode(directToolEvent);
        return;
      }

      if (type === 'pipeline_agent_status' || type === 'Pipeline_agent_status') {
        // Seeing pipeline agent statuses means execution has started — restore mode
        if (modeRef.current === 'planning') {
          setMode('executing');
          modeRef.current = 'executing'; // Update immediately to prevent race conditions
          setPendingPrompt(null);        // Clear any approval prompts
          setLabellingModeLocked(true);
          setCanExecute(true);
        }
        updatePipelineAgentStatuses(payload?.agents || []);
        return;
      }

      if (type === 'run_status') {
        // run_status also implies we are executing
        if (modeRef.current === 'planning') {
          setMode('executing');
          modeRef.current = 'executing'; // Update immediately
          setPendingPrompt(null);        // Clear any approval prompts
          setLabellingModeLocked(true);
          setCanExecute(true);
        }
        updateCurrentAgent(payload.current_agent);
      }

      if (type === 'run_finished') {
        setMode('results');
        modeRef.current = 'results';
        setPendingPrompt(null);
        updateCurrentAgent('run_finished');
      }

      if (type === 'run_stopped') {
        // Pipeline was stopped by the user — mark all active agent nodes as cancelled
        // and stay in 'executing' mode so Restart / Re-run buttons are still visible
        setIsPaused(false);
        setHasStopped(true);
        setNodes(prev =>
          prev.map(n => {
            if (n.type !== 'agent') return n;
            const d = n.data as AgentNodeData;
            if (d.status === 'active') {
              return { ...n, data: { ...d, status: 'failed', isExpanded: false } };
            }
            return n;
          })
        );
      }
    });

    const unsubscribeStatus = websocketService.onStatusChange(status => setIsConnected(status));

    return () => {
      unsubscribeMsg();
      unsubscribeStatus();
    };
  }, [appendToolEventToNode, applyBackendPlan, session_id, updateCurrentAgent, updateNodeData, updatePipelineAgentStatuses]);

  useEffect(() => {
    if (autoStartPlanning && initialHypothesis && session_id && !hasInitiatedPlan.current) {
      hasInitiatedPlan.current = true;
      const startPlanning = async () => {
        try {
          await new Promise(r => setTimeout(r, 500));
          const response = await fetch(`${getAgentsApiBaseUrl()}/plan`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              message: initialHypothesis,
              session_id: String(session_id),
              do_lit_review: includeLitReview,
            }),
          });
          if (!response.ok) {
            console.error('Failed to initiate planning session');
            return;
          }
          const responseBody = await response.json().catch(() => ({}));
          if (responseBody?.status === 'queued') {
            websocketService.dispatchLocal({
              type: 'msg',
              payload: `Planning is queued${responseBody?.queue_position ? ` at position ${responseBody.queue_position}` : ''}. It will start when planning capacity is available.`,
              session_id: session_id,
            });
          }
        } catch (error) {
          console.error('Error contacting planner API:', error);
          hasInitiatedPlan.current = false;
        }
      };
      startPlanning();
    }
  }, [autoStartPlanning, includeLitReview, initialHypothesis, session_id]);

  const handleNodesChange = useCallback(
    (updatedNodes: FlowNode[]) => {
      const isUserStructuralEdit = updatedNodes.length !== nodes.length; // rough heuristic, ideally check for actual structural changes
      if (mode === 'generated' && isUserStructuralEdit) {
        setMode('manual');
        modeRef.current = 'manual';
        setCountdown(null);
      }
      setNodes(updatedNodes);
    },
    [mode, nodes.length]
  );

  const startExecution = async () => {
    setCountdown(null);
    if (!hasExecutablePlanRef.current) {
      setCanExecute(false);
      websocketService.dispatchLocal({
        type: 'msg',
        payload: 'Execution was not started because no valid pipeline plan exists. Please regenerate or revise the plan.',
        session_id: session_id,
      });
      return;
    }

    try {
      // No trailing slash: the backend route is POST /run. With the slash,
      // FastAPI answers 307 to /run -- and because OpenResty strips the
      // /agents prefix before the request arrives, that Location drops it too,
      // so the browser follows the redirect to /<root>/run and gets the SPA
      // index.html instead of the backend. The pipeline then never starts.
      const response = await fetch(`${getAgentsApiBaseUrl()}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: String(session_id),
          restart: restartRequestedRef.current || hasStopped,
        }),
      });
      if (!response.ok) {
        let message = 'The execution request was rejected.';
        try {
          const body = await response.json();
          if (body?.detail) message = body.detail;
        } catch (_) {
          // Keep the generic message when the server did not return JSON.
        }
        console.error('[PlaygroundCanvas] Execution request rejected:', response.status, message);
        websocketService.dispatchLocal({
          type: 'msg',
          payload: `Execution was not started: ${message}`,
          session_id: session_id,
        });
        return;
      }

      const responseBody = await response.json().catch(() => ({}));
      const wasQueued = responseBody?.status === 'queued';
      restartRequestedRef.current = false;
      onClearTabData?.();
      setMode('executing');
      modeRef.current = 'executing';
      setIsPaused(false);
      setRequiresRecoveryResume(false);
      setNodes(resetNodeLogs);
      setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));

      websocketService.dispatchLocal({
        type: 'msg',
        payload: wasQueued
          ? `Pipeline execution is queued${responseBody?.queue_position ? ` at position ${responseBody.queue_position}` : ''}. It will start when resources are available.`
          : 'Pipeline execution has started. You can track the progress of each agent on the canvas.',
        session_id: session_id,
      });
    } catch (error) {
      console.error('[PlaygroundCanvas] Execution request failed:', error);
      websocketService.dispatchLocal({
        type: 'msg',
        payload: 'Execution was not started because the server could not be reached.',
        session_id: session_id,
      });
    }
  };

  const togglePause = async () => {
    if (isResuming) return;

    if (requiresRecoveryResume) {
      setIsResuming(true);
      try {
        await resumeSessionPipeline(String(session_id));
        setRequiresRecoveryResume(false);
        setIsPaused(false);
        setHasStopped(false);
      } catch (error) {
        console.error('[PlaygroundCanvas] Safe pipeline resume failed:', error);
        setIsPaused(true);
      } finally {
        setIsResuming(false);
      }
      return;
    }

    const next = !isPaused;
    const response = await fetch(`${getAgentsApiBaseUrl()}/${next ? 'pause' : 'resume'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: String(session_id) }),
    });
    if (response.ok) {
      setIsPaused(next);
    } else {
      console.error('[PlaygroundCanvas] Pause/resume request rejected:', response.status);
    }
  };


  const stopExecution = async () => {
    if (isStopping) return;
    setIsStopping(true);
    try {
      await fetch(`${getAgentsApiBaseUrl()}/stop`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: String(session_id) }),
      });
    } finally {
      setIsStopping(false);
    }
  };

  const resetWorkflow = () => {
    restartRequestedRef.current = true;
    setMode('planning');
    setIsPaused(false);
    setRequiresRecoveryResume(false);
    setNodes(createInitialNodes(initialHypothesis));
    setEdges(INITIAL_EDGES);
  };

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault();
    const type = event.dataTransfer.getData('application/reactflow');
    const agentDataString = event.dataTransfer.getData('application/agentData');
    if (!type || !agentDataString) return;

    const agentData = JSON.parse(agentDataString);
    const reactFlowBounds = event.currentTarget.getBoundingClientRect();
    const position = {
      x: event.clientX - reactFlowBounds.left - 100,
      y: event.clientY - reactFlowBounds.top - 25,
    };

    const newNode: FlowNode = {
      id: `node-${Date.now()}`,
      type: 'agent',
      position,
      data: {
        title: agentData.title,
        description: agentData.role,
        status: 'pending',
        logs: [],
        isExpanded: false,
      },
    };

    const updatedNodes = nodes.concat(newNode);
    setNodes(updatedNodes);
  };

  const handleDragOver = (event: React.DragEvent) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = 'move';
  };

  const handlePromptSubmit = (message: string) => {
    websocketService.sendMessage({
      type: 'user_input',
      payload: { message },
      session_id,
    });
    websocketService.dispatchLocal({
      type: 'chat_echo',
      payload: { text: message },
      session_id,
    });
  };

  return (
    <div className="relative flex h-full w-full flex-col bg-[#000000]">
      <CanvasHeader
        mode={mode}
        literatureEnabled={!!nodes.find(n => n.id === 'literature_review')}
        canExecute={canExecute}
        isPaused={isPaused}
        isStopping={isStopping}
        isResuming={isResuming}
        hasStopped={hasStopped}
        isConnected={isConnected}
        labellingMode={labellingMode}
        labellingModeLocked={labellingModeLocked}
        onStart={startExecution}
        onTogglePause={togglePause}
        onStop={stopExecution}
        onReset={resetWorkflow}
        onToggleLabellingMode={toggleLabellingMode}
      />

      <FlowEngineWrapper
        mode={mode === 'generated' || mode === 'manual' ? 'planning' : mode}
        nodes={nodes}
        edges={edges}
        fitViewTrigger={fitViewTrigger}
        pendingPrompt={pendingPrompt}
        onNodesChange={handleNodesChange}
        onDrop={handleDrop}
        onDragOver={handleDragOver}
        onPromptSubmit={handlePromptSubmit}
        onPromptClose={() => setPendingPrompt(null)}
        onReset={resetWorkflow}
      />

      {/* Floating Countdown Banner */}
      {countdown !== null && (
        <div className="absolute bottom-5 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 rounded-md border border-[#33485d] bg-[#151a1f]/95 px-4 py-2 shadow-lg backdrop-blur-md">
          <span className="text-xs text-gray-200 whitespace-nowrap">
            Pipeline ready • Auto-running in{" "}
            <span className="font-semibold text-[#7ec8e3]">{countdown}s</span>
          </span>

          <button
            onClick={() => {
              setCountdown(null);
              startExecution();
            }}
            className="rounded bg-[#5e98ac] px-3 py-1 text-xs font-medium text-black transition hover:bg-[#72adc2]"
          >
            Run Now
          </button>

          <button
            onClick={() => {
              setCountdown(null);
              setMode("manual");
              modeRef.current = "manual";
            }}
            className="rounded border border-[#3c5d80] px-3 py-1 text-xs text-gray-300 transition hover:bg-[#243242] hover:text-white"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
