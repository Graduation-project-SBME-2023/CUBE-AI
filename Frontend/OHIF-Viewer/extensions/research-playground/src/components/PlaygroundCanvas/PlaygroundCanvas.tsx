import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import websocketService from '../../services/websocket';
import config from '../../config';
import { FlowEdge, FlowNode, ToolEvent, ToolLog, AgentNodeData } from './types';
import { createInitialNodes, getDynamicLayout, INITIAL_EDGES, START_W, AGENT_W } from './CanvasLayoutManager';
import CanvasHeader from './CanvasHeader';
import FlowEngineWrapper from './FlowEngineWrapper';
import { registerAllLogRenderers } from './logs/registerRenderers';
import { getAgentsApiBaseUrl } from '../../utils/apiBase';

interface PlaygroundCanvasProps {
  initialHypothesis?: string;
  session_id?: string;
  includeLitReview?: boolean;
  onLiteratureOutput?: (data: any) => void;
}

export default function PlaygroundCanvas({
  initialHypothesis,
  session_id,
  includeLitReview = true,
  onLiteratureOutput,
}: PlaygroundCanvasProps) {
  const normalizeKey = useCallback(
    (value?: string | null) =>
      String(value || '')
        .toLowerCase()
        .replace(/[^a-z0-9]/g, ''),
    []
  );

  const [mode, setMode] = useState<'planning' | 'executing' | 'results'>('planning');
  const [nodes, setNodes] = useState<FlowNode[]>(() => createInitialNodes(initialHypothesis));
  const [edges, setEdges] = useState<FlowEdge[]>(INITIAL_EDGES);
  const [isPaused, setIsPaused] = useState(false);
  const [isConnected, setIsConnected] = useState(websocketService.isConnected());
  const [pendingPrompt, setPendingPrompt] = useState<{ type: string; message: string } | null>(null);
  const [canExecute, setCanExecute] = useState(false);
  const [fitViewTrigger, setFitViewTrigger] = useState(0);
  const hasInitiatedPlan = useRef(false);
  const modeRef = useRef<'planning' | 'executing' | 'results'>('planning');
  const nodesRef = useRef<FlowNode[]>([]);
  const sessionIdRef = useRef<string | undefined>(session_id);
  const literatureOutputRef = useRef<typeof onLiteratureOutput>(onLiteratureOutput);

  useEffect(() => {
    nodesRef.current = nodes;
  }, [nodes]);

  useEffect(() => {
    modeRef.current = mode;
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

  const updateNodeData = useCallback((id: string, patch: any) => {
    setNodes(prev => prev.map(n => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)));
  }, []);

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
          backendStatus === 'RUNNING'
            ? 'active'
            : backendStatus === 'COMPLETED'
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
      prev.map(n =>
        n.type === 'agent'
          ? { ...n, data: { ...n.data, status: 'pending', logs: [], isExpanded: false } }
          : n
      ),
    []
  );

  const applyBackendPlan = useCallback(
    (planPayload: any) => {
      if (planPayload.nodes) {
        setNodes(planPayload.nodes);
        if (planPayload.edges) setEdges(planPayload.edges);
        if (planPayload.mode) setMode(planPayload.mode);
        setFitViewTrigger(prev => prev + 1);
        return;
      }

      if (!planPayload.steps) return;

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
              matched_module: step.matched_module,
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
    websocketService.connect();

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

      if (type === 'hypothesis_approval' || type === 'lit_review_choice' || type === 'plan_approval') {
        setPendingPrompt({ type, message: payload?.message || payload?.text || '' });
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

      if (type === 'plan' || type === 'PLAN_UPDATE') {
        setCanExecute(true);
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
        updatePipelineAgentStatuses(payload?.agents || []);
        return;
      }

      if (type === 'run_status') {
        updateCurrentAgent(payload.current_agent);
      }

      if (type === 'run_finished') {
        updateCurrentAgent('run_finished');
      }
    });

    const unsubscribeStatus = websocketService.onStatusChange(status => setIsConnected(status));

    return () => {
      unsubscribeMsg();
      unsubscribeStatus();
    };
  }, [appendToolEventToNode, applyBackendPlan, updateCurrentAgent, updateNodeData, updatePipelineAgentStatuses]);

  useEffect(() => {
    if (initialHypothesis && !hasInitiatedPlan.current) {
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
          }
        } catch (error) {
          console.error('Error contacting planner API:', error);
          hasInitiatedPlan.current = false;
        }
      };
      startPlanning();
    }
  }, [includeLitReview, initialHypothesis, session_id]);

  const sendPlanToBackend = useCallback(
    (nodesData: FlowNode[], edgesData: FlowEdge[]) => {
      websocketService.sendMessage({
        type: 'PLAN_UPDATE',
        nodes: nodesData,
        edges: edgesData,
        mode,
        session_id,
        timestamp: new Date().toISOString(),
      });
    },
    [mode, session_id]
  );

  const handleNodesChange = useCallback(
    (updatedNodes: FlowNode[]) => {
      setNodes(updatedNodes);
      if (mode === 'planning') {
        sendPlanToBackend(updatedNodes, edges);
      }
    },
    [mode, edges, sendPlanToBackend]
  );

  const startExecution = async () => {
    setMode('executing');
    setIsPaused(false);
    setNodes(resetNodeLogs);
    setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));
    await fetch(`${getAgentsApiBaseUrl()}/run/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: String(session_id) }),
    });
  };

  const togglePause = async () => {
    const next = !isPaused;
    setIsPaused(next);
    await fetch(`${getAgentsApiBaseUrl()}/${next ? 'pause' : 'resume'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: String(session_id) }),
    });
  };

  const restartExecution = async () => {
    await fetch(`${getAgentsApiBaseUrl()}/restart`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: String(session_id) }),
    });
    setIsPaused(false);
    setMode('executing');
    setNodes(resetNodeLogs);
    setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));
  };

  const resetWorkflow = () => {
    setMode('planning');
    setIsPaused(false);
    setNodes(createInitialNodes(initialHypothesis));
    setEdges(INITIAL_EDGES);
    if (websocketService.isConnected()) {
      websocketService.sendMessage({ type: 'RESET_WORKFLOW' });
    }
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
    sendPlanToBackend(updatedNodes, edges);
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
        literatureEnabled = {!!nodes.find(n => n.id === 'literature_review')}
        canExecute={canExecute}
        isPaused={isPaused}
        isConnected={isConnected}
        onStart={startExecution}
        onTogglePause={togglePause}
        onRestart={restartExecution}
        onReset={resetWorkflow}
      />

      <FlowEngineWrapper
        mode={mode}
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
    </div>
  );
}
