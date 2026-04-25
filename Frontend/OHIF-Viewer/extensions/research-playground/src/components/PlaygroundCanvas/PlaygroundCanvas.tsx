import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import {
  Play,
  Pause,
  RotateCcw,
  Wifi,
  WifiOff,
  Activity,
  CheckCircle2,
  MousePointer2,
  Edit3,
  Settings,
  ZoomIn,
  ZoomOut,
  AlertCircle,
  ChevronDown,
  ChevronUp,
  X,
} from 'lucide-react';

import websocketService from '../../utils/WebsocketService';
import MiniFlow from './miniflow';
import StartNode from './nodes/StartNode';
import AgentNode from './nodes/AgentNode';
import CustomEdge from './edges/CustomEdges';
import { FlowNode, FlowEdge, ToolEvent } from './types';
import { registerAllLogRenderers } from './logs/registerRenderers';
import { AgentNodeData } from './types';

// Icon aliases
const PlayIcon: any = Play;
const PauseIcon: any = Pause;
const RotateCcwIcon: any = RotateCcw;
const MousePointer2Icon: any = MousePointer2;
const WifiIcon: any = Wifi;
const WifiOffIcon: any = WifiOff;
const Edit3Icon: any = Edit3;
const ActivityIcon: any = Activity;
const CheckCircle2Icon: any = CheckCircle2;
const SettingsIcon: any = Settings;
const AlertCircleIcon: any = AlertCircle;
const ChevronDownIcon: any = ChevronDown;
const ChevronUpIcon: any = ChevronUp;

// ─── Layout constants ────────────────────────────────────────────────────────
const CENTER_LINE = 400; // Horizontal canvas center (Y axis)
const START_W = 340;
const AGENT_W = 280; // Must match w-[280px] in AgentNode
const DEFAULT_GAP = 120;
const MIN_GAP = 48;
const MAX_GAP = 180;
const START_H_ESTIMATE = 80;
const AGENT_H_ESTIMATE = 84;

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

const getDynamicLayout = (agentCount: number) => {
  const viewportWidth =
    typeof window !== 'undefined' ? Math.max(window.innerWidth, 1200) : 1600;

  // Reserve room for side panels and keep comfortable side margins.
  const usableWidth = Math.max(1000, viewportWidth - 420);
  const gaps = Math.max(1, agentCount);
  const nodeWidthTotal = START_W + agentCount * AGENT_W;
  const computedGap = (usableWidth - nodeWidthTotal) / gaps;
  const gap = clamp(Number.isFinite(computedGap) ? computedGap : DEFAULT_GAP, MIN_GAP, MAX_GAP);
  const rowWidth = nodeWidthTotal + gap * gaps;
  const startX = Math.max(80, (usableWidth - rowWidth) / 2 + 60);

  return {
    gap,
    startX,
    startY: CENTER_LINE - START_H_ESTIMATE / 2,
    agentY: CENTER_LINE - AGENT_H_ESTIMATE / 2,
  };
};

const INITIAL_LAYOUT = getDynamicLayout(0);
// ─────────────────────────────────────────────────────────────────────────────

const INITIAL_NODES: FlowNode[] = [
  {
    id: '1',
    type: 'start',
    position: { x: INITIAL_LAYOUT.startX, y: INITIAL_LAYOUT.startY },
    data: { title: 'Research Hypothesis', description: 'Entry point' },
  },
];

const INITIAL_EDGES: FlowEdge[] = [];

const nodeTypes = { start: StartNode, agent: AgentNode };
const edgeTypes = { custom: CustomEdge };

interface PlaygroundCanvasProps {
  initialHypothesis?: string;
  session_id?: string;
  onNodeSelect?: (node: FlowNode | null) => void;
  onLiteratureOutput?: (data: any) => void;
}

export default function PlaygroundCanvas({
  initialHypothesis,
  session_id,
  onNodeSelect,
  onLiteratureOutput,
}: PlaygroundCanvasProps) {
  const [mode, setMode] = useState<'planning' | 'executing' | 'results'>('planning');

  const [nodes, setNodes] = useState<FlowNode[]>(() => {
    if (!initialHypothesis) {
      return INITIAL_NODES;
    }
    return INITIAL_NODES.map(node =>
      node.type === 'start'
        ? { ...node, data: { ...node.data, description: initialHypothesis } }
        : node
    );
  });

  const [edges, setEdges] = useState<FlowEdge[]>(INITIAL_EDGES);
  const [isPaused, setIsPaused] = useState(false);
  const [isConnected, setIsConnected] = useState(websocketService.isConnected());
  const [pendingPrompt, setPendingPrompt] = useState<{ type: string; message: string } | null>(null);
  const [promptExpanded, setPromptExpanded] = useState(true);
  const [canExecute, setCanExecute] = useState(false);

  const isPausedRef = useRef(isPaused);
  const modeRef = useRef(mode);
  useEffect(() => {
    isPausedRef.current = isPaused;
  }, [isPaused]);
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  const hasInitiatedPlan = useRef(false);

  useEffect(() => {
    websocketService.connect();
    console.log('[HypothesisFlow][PlaygroundCanvas] Mounted', {
      sessionId: session_id,
      hasHypothesis: Boolean(initialHypothesis),
      hypothesisLength: initialHypothesis?.length || 0,
    });

    const handleWSMessage = (data: any) => {
      console.log('[HypothesisFlow][PlaygroundCanvas] WS event received', {
        type: data?.type,
        sessionId: data?.session_id,
        localSessionId: session_id,
      });
      const { type, payload } = data;
      const incomingSessionId = String(data?.session_id);
      if (incomingSessionId && session_id && incomingSessionId !== String(session_id)) {
        console.log('[HypothesisFlow][PlaygroundCanvas] Ignoring WS event for different session', {
          incomingSessionId,
          localSessionId: session_id,
        });
        return;
      }
      if (type === 'msg') {
        // Detect plan JSON that the backend failed to send as type 'plan'
        const msgText = typeof payload === 'string' ? payload.trim() : '';
        if (msgText.startsWith('{') && msgText.includes('"steps"')) {
          try {
            const planData = JSON.parse(msgText);
            if (planData.steps && Array.isArray(planData.steps)) {
              console.log('[HypothesisFlow][PlaygroundCanvas] Recovered plan from msg payload');
              applyBackendPlan(planData);
              return;
            }
          } catch (_) { /* not valid JSON, ignore */ }
        }
        return;
      }
      if (type === 'hypothesis') {
        // Update start node with full PICO hypothesis data
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
        setPromptExpanded(true);
        return;
      }
      if (type === 'lit_review_node') {
        // Add a literature review agent node in active state
        const layout = getDynamicLayout(1);
        const litNode: FlowNode = {
          id: 'literature_review',
          type: 'agent',
          position: {
            x: layout.startX + START_W + layout.gap,
            y: layout.agentY,
          },
          data: {
            title: 'Literature Review',
            description: 'Building domain knowledge base from medical databases',
            status: 'active',
            logs: [],
            isExpanded: true,
          },
        };
        setNodes(prev => {
          const without = prev.filter(n => n.id !== 'literature_review');
          return [...without, litNode];
        });
        setEdges(prev => {
          const without = prev.filter(e => e.target !== 'literature_review');
          const startNode = nodes.find(n => n.type === 'start');
          const sourceId = startNode?.id || '1';
          return [...without, {
            id: `e${sourceId}-literature_review`,
            source: sourceId,
            target: 'literature_review',
            type: 'custom',
          }];
        });
        return;
      }
      if (type === 'lit_review_log') {
        // Append a short log entry to the literature review node
        setNodes(prev =>
          prev.map(n => {
            if (n.id !== 'literature_review' || n.type !== 'agent') return n;
            const agentData = n.data as any;
            const logs = agentData.logs ?? [];
            const logEntry = {
              source: 'knowledge_builder',
              started_at: new Date().toISOString(),
              last_updated_at: new Date().toISOString(),
              status: payload?.status || 'active',
              events: [{
                event_type: 'tool_log' as const,
                source: 'knowledge_builder',
                content_type: 'text' as const,
                content: { message: payload?.message || '' },
                timestamp: new Date().toISOString(),
              }],
            };
            return {
              ...n,
              data: { ...agentData, isExpanded: true, logs: [...logs, logEntry] },
            };
          })
        );
        return;
      }
      if (type === 'lit_review_output') {
        // Forward literature output to parent for the Literature Review view
        onLiteratureOutput?.(payload);
        return;
      }
      if (type === 'lit_review_done') {
        // Mark the literature review node as completed
        setNodes(prev =>
          prev.map(n => {
            if (n.id !== 'literature_review' || n.type !== 'agent') return n;
            return { ...n, data: { ...n.data, status: 'completed', isExpanded: false } };
          })
        );
        return;
      }
      if (type === 'node' || type === 'NODE_UPDATE') {
        updateNodeData(data.nodeId || data.id || payload?.nodeId, payload || data.data);
      }
      if (type === 'plan' || type === 'PLAN_UPDATE') {
        setCanExecute(true);
        applyBackendPlan(payload || data.data || data);
      }
      if (type === 'tool_update') {
        // Append logs to the respective agent
        const agentName = payload?.agent_name;
        const logEntry = payload?.log;
        if (agentName && logEntry) {
          setNodes(prevNodes =>
            prevNodes.map(node => {
              if (node.type === 'agent' && 'name' in node.data) {
                const agentData = payload?.agents?.find((agent: { name: string }) => agent.name === (node.data as AgentNodeData).name);
                if (agentData) {
                  return {
                    ...node,
                    data: {
                      ...node.data,
                      logs: [...(node.data.logs || []), logEntry],
                    },
                  };
                }
              }
              return node;
            })
          );
        }
        return;
      }
      if (type === 'Pipeline_agent_status') {
        // Update agent statuses
        const agents = payload?.agents || [];
        setNodes(prevNodes => {
          const updatedNodes = prevNodes.map(node => {
            if (node.type === 'agent' && 'name' in node.data) {
              const agentData = payload?.agents?.find((agent: { name: string }) => agent.name === (node.data as AgentNodeData).name);
              if (agentData) {
                return {
                  ...node,
                  data: {
                    ...node.data,
                    status: agentData.status,
                    started_at: agentData.started_at,
                    completed_at: agentData.completed_at,
                  },
                };
              }
            }
            return node;
          });
          return [...updatedNodes]; // Ensure a new array reference
        });
        return;
      }
      if (type === 'run_status') {
        updateCurrentAgent(payload.current_agent);
      }
      if (type === 'run_finished') {
        updateCurrentAgent('run_finished');
      }
      if (type === 'pipeline_agent_status') {
        console.log('[WS] pipeline_agent_status RAW payload:', payload);

        const agents: Array<{
          name: string;
          status: 'RUNNING' | 'COMPLETED' | 'PENDING' | string;
          started_at: string | null;
          finished_at: string | null;
        }> = Array.isArray(payload.agents) ? payload.agents : [];

        console.log('[WS] Parsed agents:', agents);

        // Build a quick lookup so we only iterate nodes once
        const statusMap = new Map(
          agents
            .filter(a => a.status === 'RUNNING' || a.status === 'COMPLETED')
            .map(a => [a.name, a])
        );

        console.log('[WS] Filtered statusMap entries:', Array.from(statusMap.entries()));

        if (statusMap.size === 0) {
          console.warn('[WS] No RUNNING or COMPLETED agents found.');
          return;
        }

        setNodes(prev => {
          console.log('[WS] Previous nodes:', prev);

          const updatedNodes = prev.map(node => {
            if (node.type !== 'agent') return node;

            const agentData = node.data as AgentNodeData;
            const key = agentData.name ?? agentData.title;

            console.log('[WS] Checking node:', {
              nodeId: node.id,
              key,
              nodeData: agentData,
            });

            const match = statusMap.get(node.id);

            if (!match) {
              console.log('[WS] No match found for:', key);
              return node;
            }

            console.log('[WS] Match found:', {
              key,
              match,
            });

            const nextStatus =
              match.status === 'RUNNING' ? 'active' : 'completed';

            const updatedNode = {
              ...node,
              data: {
                ...node.data,
                status: nextStatus,
                started_at: match.started_at ?? (node.data as any).started_at,
                isExpanded: nextStatus === 'active',
              },
            };

            console.log('[WS] Updated node:', updatedNode);

            return updatedNode;
          });

          console.log('[WS] Updated nodes result:', updatedNodes);

          return updatedNodes;
        });

        return;
      }
    };

    const handleStatus = (status: boolean) => setIsConnected(status);
    const unsubscribeMsg = websocketService.subscribe(handleWSMessage);
    const unsubscribeStatus = websocketService.onStatusChange(handleStatus);

    if (initialHypothesis && !hasInitiatedPlan.current) {
      hasInitiatedPlan.current = true;
      const startPlanning = async () => {
        try {
          await new Promise(r => setTimeout(r, 500));
          console.log('[HypothesisFlow][PlaygroundCanvas] Sending /plan request', {
            hypothesisPreview: initialHypothesis.slice(0, 120),
            sessionId: session_id,
          });
          const response = await fetch('http://localhost:8150/plan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: initialHypothesis, session_id: String(session_id) }),
          });
          console.log('[HypothesisFlow][PlaygroundCanvas] /plan response', {
            ok: response.ok,
            status: response.status,
            sessionId: session_id,
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

    return () => {
      unsubscribeMsg();
      unsubscribeStatus();
    };
  }, [initialHypothesis]);

  // ─── Data handlers ──────────────────────────────────────────────────────────
  const applyBackendPlan = (planPayload: any) => {
    if (planPayload.nodes) {
      setNodes(planPayload.nodes);
      if (planPayload.edges) {
        setEdges(planPayload.edges);
      }
      if (planPayload.mode) {
        setMode(planPayload.mode);
      }
      return;
    }

    if (planPayload.steps) {
      setNodes(prev => {
        // Preserve existing start and literature review nodes
        const existingStart = prev.find(n => n.type === 'start');
        const existingLitReview = prev.find(n => n.id === 'literature_review');

        // Count anchor nodes that precede the plan steps
        const anchorCount = (existingLitReview ? 2 : 1); // start + optional lit review
        const layout = getDynamicLayout(anchorCount + planPayload.steps.length - 1);

        const keptNodes: FlowNode[] = [];

        // Keep existing start node (with PICO data), just reposition
        if (existingStart) {
          keptNodes.push({
            ...existingStart,
            position: { x: layout.startX, y: layout.startY },
          });
        } else {
          keptNodes.push({
            id: 'start',
            type: 'start',
            position: { x: layout.startX, y: layout.startY },
            data: {
              title: 'Research Hypothesis',
              description: planPayload.study_goal || initialHypothesis || 'Study workflow',
            },
          });
        }

        // Keep existing literature review node, reposition after start
        let nextX = layout.startX + START_W + layout.gap;
        if (existingLitReview) {
          keptNodes.push({
            ...existingLitReview,
            position: { x: nextX, y: layout.agentY },
          });
          nextX += AGENT_W + layout.gap;
        }

        // Append new plan step nodes after anchors
        planPayload.steps.forEach((step: any, index: number) => {
          keptNodes.push({
            id: step.id,
            type: 'agent',
            position: {
              x: nextX + index * (AGENT_W + layout.gap),
              y: layout.agentY,
            },
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

        return keptNodes;
      });

      // Rebuild edges as a chain through all nodes
      setNodes(currentNodes => {
        const newEdges: FlowEdge[] = [];
        for (let i = 0; i < currentNodes.length - 1; i++) {
          newEdges.push({
            id: `e${currentNodes[i].id}-${currentNodes[i + 1].id}`,
            source: currentNodes[i].id,
            target: currentNodes[i + 1].id,
            type: 'custom',
          });
        }
        setEdges(newEdges);
        return currentNodes; // no mutation, just reading
      });
    }
  };

  const updateCurrentAgent = (current_agent: string) => {
    setNodes(prev =>
      prev.map(node => {
        if (node.type !== 'agent') {
          return node;
        }
        const agentData = node.data as any;
        const isCurrent = node.id === current_agent;
        const wasActive = agentData.status === 'active';
        if (isCurrent) {
          return { ...node, data: { ...node.data, status: 'active', isExpanded: true } };
        }
        if (wasActive && !isCurrent) {
          return { ...node, data: { ...node.data, status: 'completed', isExpanded: false } };
        }
        return node;
      })
    );
  };

  const updateToolProgress = (event: ToolEvent) => {
    setNodes(prev =>
      prev.map(node => {
        if (node.type !== 'agent' || node.id !== event.source) {
          return node;
        }
        const agentData = node.data as any;
        const logs = agentData.logs ?? [];
        if (event.event_type === 'tool_started') {
          return {
            ...node,
            data: {
              ...node.data,
              isExpanded: true,
              logs: [
                ...logs,
                {
                  source: event.source,
                  started_at: event.timestamp,
                  last_updated_at: event.timestamp,
                  status: 'active',
                  events: [event],
                },
              ],
            },
          };
        }
        if (logs.length === 0) {
          return node;
        }
        const lastLog = logs[logs.length - 1];
        const updatedLastLog = {
          ...lastLog,
          last_updated_at: event.timestamp,
          status:
            event.event_type === 'tool_finished'
              ? 'completed'
              : event.event_type === 'error'
                ? 'error'
                : lastLog.status,
          events: [...lastLog.events, event],
        };
        return {
          ...node,
          data: { ...node.data, isExpanded: true, logs: [...logs.slice(0, -1), updatedLastLog] },
        };
      })
    );
  };

  const updateNodeData = (id: string, patch: any) => {
    setNodes(prev => prev.map(n => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)));
  };

  const resetNodeLogs = (prev: FlowNode[]) =>
    prev.map(n =>
      n.type === 'agent'
        ? { ...n, data: { ...n.data, status: 'pending', logs: [], isExpanded: false } }
        : n
    );

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
        clearTimeout((window as any)._nodeSyncTimeout);
        (window as any)._nodeSyncTimeout = setTimeout(() => {
          sendPlanToBackend(updatedNodes, edges);
        }, 300);
      }
    },
    [mode, edges, sendPlanToBackend]
  );

  const deleteNode = useCallback(
    (id: string) => {
      const updatedNodes = nodes.filter(n => n.id !== id);
      const updatedEdges = edges.filter(e => e.source !== id && e.target !== id);
      setNodes(updatedNodes);
      setEdges(updatedEdges);
      sendPlanToBackend(updatedNodes, updatedEdges);
    },
    [nodes, edges, sendPlanToBackend]
  );

  const editNode = useCallback(
    (id: string) => {
      const node = nodes.find(n => n.id === id);
      if (!node) {
        return;
      }
      const newDesc = window.prompt('Edit Step Description:', node.data.description);
      if (newDesc !== null) {
        const updatedNodes = nodes.map(n =>
          n.id === id ? { ...n, data: { ...n.data, description: newDesc } } : n
        );
        setNodes(updatedNodes);
        sendPlanToBackend(updatedNodes, edges);
      }
    },
    [nodes, edges, sendPlanToBackend]
  );

  const toggleExpand = useCallback((id: string) => {
    setNodes(prev =>
      prev.map(n => {
        if (n.id === id && n.type === 'agent') {
          return { ...n, data: { ...n.data, isExpanded: !(n.data as any).isExpanded } };
        }
        return n;
      })
    );
  }, []);

  const startExecution = async () => {
    setMode('executing');
    setIsPaused(false);
    setNodes(resetNodeLogs);
    setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));
    await fetch('http://localhost:8150/run/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: String(session_id) }),
    });
  };

  const togglePause = async () => {
    const next = !isPaused;
    setIsPaused(next);
    await fetch(`http://localhost:8150/${next ? 'pause' : 'resume'}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: String(session_id) }),
    });
  };

  const stopExecution = async () => {
    await fetch(`http://localhost:8150/stop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: String(session_id) }),
    });
    setIsPaused(false);
    setMode('planning');           // go back to planning so user can re-execute
    setNodes(resetNodeLogs);       // keep nodes, wipe logs only
    setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));
  };

  const restartExecution = async () => {
    await fetch(`http://localhost:8150/restart`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: String(session_id) }),
    });
    setIsPaused(false);
    setMode('executing');
    setNodes(resetNodeLogs);       // keep nodes, wipe logs only
    setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));
  };

  const resetWorkflow = () => {
    setMode('planning');
    setIsPaused(false);
    const layout = getDynamicLayout(0);
    const resetNodes = INITIAL_NODES.map(node =>
      node.type === 'start' && initialHypothesis
        ? {
            ...node,
            position: { x: layout.startX, y: layout.startY },
            data: { ...node.data, description: initialHypothesis },
          }
        : node.type === 'start'
          ? { ...node, position: { x: layout.startX, y: layout.startY } }
        : node
    );
    setNodes(resetNodes);
    setEdges(INITIAL_EDGES);
    if (websocketService.isConnected()) {
      websocketService.sendMessage({ type: 'RESET_WORKFLOW' });
    }
  };

  const handleDrop = (event: React.DragEvent) => {
    event.preventDefault();
    const type = event.dataTransfer.getData('application/reactflow');
    const agentDataString = event.dataTransfer.getData('application/agentData');
    if (!type || !agentDataString) {
      return;
    }
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

  const nodesWithHandlers = useMemo(
    () =>
      nodes.map(n => ({
        ...n,
        data: {
          ...n.data,
          mode,
          onDelete: deleteNode,
          onEdit: editNode,
          onToggleExpand: toggleExpand,
        },
      })),
    [nodes, mode, deleteNode, editNode, toggleExpand]
  );

  useEffect(() => {
    // Register log renderers during extension initialization
    registerAllLogRenderers();
  }, []);

  return (
    <div className="relative flex h-full w-full flex-col bg-[#000000]">
      <div className="absolute top-6 left-1/2 z-40 flex -translate-x-1/2 items-center gap-4 rounded-lg border border-[#3c5d80] bg-[#151a1f]/90 p-2 shadow-2xl backdrop-blur-md">
        <div className="flex items-center gap-3 border-r border-[#3c5d80] pl-2 pr-4">
          <h1 className="hidden text-[10px] font-bold uppercase tracking-wide text-[#91b9cd] xl:block">
            Workflow Engine
          </h1>
          <div
            className={`rounded border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider
              ${
                mode === 'planning'
                  ? 'border-[#3c5d80] bg-[#1f2d40] text-[#91b9cd]'
                  : mode === 'executing'
                    ? 'border-[#005f7d] bg-[#003f5c] text-[#5acce6]'
                    : 'border-[#085a3c] bg-[#06422d] text-[#6fe5b8]'
              }`}
          >
            {mode}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {mode === 'planning' && (
            <button
              onClick={startExecution}
              disabled={!canExecute}
              className={`flex items-center gap-2 rounded px-4 py-1.5 text-xs font-medium text-white shadow-lg transition-colors ${
                canExecute
                  ? 'bg-[#348cfd] hover:bg-[#599fff] cursor-pointer'
                  : 'bg-[#348cfd] opacity-50 cursor-not-allowed'
              }`}
            >
              <Play size={14} fill="currentColor" /> Execute
            </button>
          )}
          {mode === 'executing' && (
            <>
              <button
                onClick={togglePause}
                className={`flex items-center gap-2 rounded border px-3 py-1.5 text-xs font-medium transition-colors ${isPaused ? 'border-[#705228] bg-[#3b2d1c] text-[#f8ce46]' : 'border-[#3c5d80] bg-[#151a1f] text-[#91b9cd] hover:border-[#91b9cd]'}`}
              >
                {isPaused ? <Play size={14} /> : <Pause size={14} />}
                {isPaused ? 'Resume' : 'Pause'}
              </button>
              <button
                onClick={restartExecution}
                className="flex items-center gap-2 rounded border border-[#3c5d80] bg-[#151a1f] px-3 py-1.5 text-xs text-[#91b9cd] transition-colors hover:bg-[#2c1a1f] hover:text-[#ff6b6b]"
              >
                <RotateCcw size={14} /> Stop
              </button>
            </>
          )}
          {mode === 'results' && (
            <button
              onClick={resetWorkflow}
              className="flex items-center gap-2 rounded bg-[#6fe5b8] px-4 py-1.5 text-xs font-medium text-black transition-colors hover:bg-[#91eac8]"
            >
              <RotateCcw size={14} /> Reset
            </button>
          )}
        </div>

        <div
          className="flex items-center border-l border-[#3c5d80] pl-2 text-[10px] text-[#566a84]"
          title={isConnected ? 'WebSocket Connected' : 'Using Mock Data'}
        >
          {isConnected ? (
            <Wifi
              size={14}
              className="text-[#6fe5b8]"
            />
          ) : (
            <WifiOff size={14} />
          )}
        </div>
      </div>

      <div
        className="relative flex-1 overflow-hidden"
        onDrop={handleDrop}
        onDragOver={handleDragOver}
      >
        <MiniFlow
          nodes={nodesWithHandlers}
          edges={edges}
          onNodesChange={handleNodesChange}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          readOnly={mode !== 'planning'}
        >
          <div className="pointer-events-none absolute bottom-24 left-6">
            <div className="space-y-2 rounded border border-[#3c5d80] bg-[#151a1f]/90 p-3 text-[10px] backdrop-blur">
              <div className="mb-1 font-bold uppercase text-[#91b9cd]">
                {mode === 'planning' ? 'Planning Tools' : 'Monitor Status'}
              </div>
              {mode === 'planning' ? (
                <>
                  <div className="flex items-center gap-2 text-[#dadada]">
                    <MousePointer2 size={10} /> Drag nodes & canvas
                  </div>
                  <div className="flex items-center gap-2 text-[#dadada]">
                    <Edit3 size={10} /> Edit / Delete
                  </div>
                  <div className="flex items-center gap-2 text-[#dadada]">
                    <ZoomIn size={10} /> Ctrl+Scroll to zoom
                  </div>
                </>
              ) : (
                <>
                  <div className="flex items-center gap-2 text-[#dadada]">
                    <Activity
                      size={10}
                      className="text-[#5acce6]"
                    />{' '}
                    Active
                  </div>
                  <div className="flex items-center gap-2 text-[#dadada]">
                    <CheckCircle2
                      size={10}
                      className="text-[#6fe5b8]"
                    />{' '}
                    Complete
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Compact expandable approval notification panel */}
          {pendingPrompt && (
            <div className="animate-in slide-in-from-bottom-2 pointer-events-auto absolute bottom-6 right-6 z-50 duration-200">
              <div className="w-72 overflow-hidden rounded-lg border border-[#5acce6]/60 bg-[#151a1f] shadow-[0_0_20px_rgba(90,204,230,0.15)]">
                {/* Collapsed header — always visible */}
                <button
                  onClick={() => setPromptExpanded(prev => !prev)}
                  className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[#1f2630]"
                >
                  <AlertCircleIcon size={14} className="shrink-0 animate-pulse text-[#f8ce46]" />
                  <span className="flex-1 text-xs font-bold text-white">
                    {pendingPrompt.type === 'hypothesis_approval'
                      ? 'Approve Hypothesis?'
                      : pendingPrompt.type === 'plan_approval'
                        ? 'Approve Pipeline Plan?'
                        : 'Run Literature Review?'}
                  </span>
                  {promptExpanded ? (
                    <ChevronDownIcon size={12} className="text-[#566a84]" />
                  ) : (
                    <ChevronUpIcon size={12} className="text-[#566a84]" />
                  )}
                </button>

                {/* Expanded body */}
                {promptExpanded && (
                  <div className="space-y-3 border-t border-[#3c5d80]/50 px-3 pb-3 pt-2">
                    {pendingPrompt.message && (
                      <p className="text-[11px] leading-relaxed text-[#91b9cd]">{pendingPrompt.message}</p>
                    )}
                    <div className="flex gap-2">
                      {pendingPrompt.type === 'hypothesis_approval' || pendingPrompt.type === 'plan_approval' ? (
                        <>
                          <button
                            onClick={() => {
                              websocketService.sendMessage({
                                type: 'user_input',
                                payload: { message: 'APPROVE' },
                                session_id,
                              });
                              websocketService.dispatchLocal({
                                type: 'chat_echo',
                                payload: { text: 'APPROVE' },
                                session_id,
                              });
                              setPendingPrompt(null);
                            }}
                            className="flex-1 rounded bg-[#6fe5b8] py-1.5 text-xs font-bold text-black hover:bg-[#91eac8]"
                          >
                            Approve
                          </button>
                          <button
                            onClick={() => {
                              const feedback = window.prompt('Describe your changes:');
                              if (feedback) {
                                websocketService.sendMessage({
                                  type: 'user_input',
                                  payload: { message: feedback },
                                  session_id,
                                });
                                websocketService.dispatchLocal({
                                  type: 'chat_echo',
                                  payload: { text: feedback },
                                  session_id,
                                });
                              }
                              setPendingPrompt(null);
                            }}
                            className="flex-1 rounded border border-[#3c5d80] bg-[#1f2d40] py-1.5 text-xs font-medium text-[#91b9cd] hover:bg-[#2a3a4f]"
                          >
                            Revise
                          </button>
                        </>
                      ) : (
                        <>
                          <button
                            onClick={() => {
                              websocketService.sendMessage({
                                type: 'user_input',
                                payload: { message: 'YES' },
                                session_id,
                              });
                              websocketService.dispatchLocal({
                                type: 'chat_echo',
                                payload: { text: 'Yes, Build Knowledge' },
                                session_id,
                              });
                              setPendingPrompt(null);
                            }}
                            className="flex-1 rounded bg-[#348cfd] py-1.5 text-xs font-bold text-white hover:bg-[#599fff]"
                          >
                            Yes, Build Knowledge
                          </button>
                          <button
                            onClick={() => {
                              websocketService.sendMessage({
                                type: 'user_input',
                                payload: { message: 'NO' },
                                session_id,
                              });
                              websocketService.dispatchLocal({
                                type: 'chat_echo',
                                payload: { text: 'Skip Literature Review' },
                                session_id,
                              });
                              setPendingPrompt(null);
                            }}
                            className="flex-1 rounded border border-[#3c5d80] bg-[#1f2d40] py-1.5 text-xs font-medium text-[#91b9cd] hover:bg-[#2a3a4f]"
                          >
                            Skip
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {mode === 'results' && (
            <div className="animate-in fade-in absolute inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-[2px] duration-500">
              <div className="max-w-md space-y-4 rounded border border-[#3c5d80] bg-[#151a1f] p-8 text-center shadow-2xl">
                <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full border border-[#6fe5b8] bg-[#06422d]">
                  <CheckCircle2
                    size={24}
                    className="text-[#6fe5b8]"
                  />
                </div>
                <h2 className="text-xl font-bold text-white">Workflow Complete</h2>
                <button
                  onClick={resetWorkflow}
                  className="w-full rounded bg-[#348cfd] py-2 text-sm font-medium text-white transition-colors hover:bg-[#599fff]"
                >
                  Return to Worklist
                </button>
              </div>
            </div>
          )}
        </MiniFlow>
      </div>
    </div>
  );
}
