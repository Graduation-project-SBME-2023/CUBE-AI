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
} from 'lucide-react';

import websocketService from '../../utils/WebsocketService';
import MiniFlow from './miniflow';
import StartNode from './nodes/StartNode';
import AgentNode from './nodes/AgentNode';
import CustomEdge from './edges/CustomEdges';
import { FlowNode, FlowEdge, ToolEvent } from './types';

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

// ─── Layout constants ────────────────────────────────────────────────────────
const CENTER_LINE = 400;      // Horizontal canvas center (Y axis)
const START_W = 300;
const AGENT_W = 220;          // Must match w-[220px] in AgentNode
const H_GAP = 120;            // Horizontal gap between nodes

// Handle anchors (must match node components + MiniFlow's HANDLE_Y_BY_TYPE)
const AGENT_HEADER_MID = 25;  // top:25 in AgentNode connection dots
const START_HANDLE_Y = 40;    // top:40 in StartNode connection dot

// All nodes are positioned so their handle sits at the same canvas Y.
// Agent nodes:  node.y + AGENT_HEADER_MID = SHARED_HANDLE_Y
// Start node:   node.y + START_HANDLE_Y   = SHARED_HANDLE_Y
const SHARED_HANDLE_Y = CENTER_LINE;

const START_NODE_Y = SHARED_HANDLE_Y - START_HANDLE_Y;   // 360
const AGENT_NODE_Y = SHARED_HANDLE_Y - AGENT_HEADER_MID; // 375

const START_X = CENTER_LINE - START_W / 2;
const AGENT_X_OFFSET = START_X + START_W + H_GAP;
// ─────────────────────────────────────────────────────────────────────────────

const INITIAL_NODES: FlowNode[] = [
  {
    id: '1',
    type: 'start',
    position: { x: START_X, y: START_NODE_Y },
    data: { title: 'Research Hypothesis', description: 'Entry point' },
  },
];

const INITIAL_EDGES: FlowEdge[] = [];

const nodeTypes = { start: StartNode, agent: AgentNode };
const edgeTypes = { custom: CustomEdge };

interface PlaygroundCanvasProps {
  initialHypothesis?: string;
  onNodeSelect?: (node: FlowNode | null) => void;
}

export default function PlaygroundCanvas({
  initialHypothesis,
  onNodeSelect,
}: PlaygroundCanvasProps) {
  const [mode, setMode] = useState<'planning' | 'executing' | 'results'>('planning');

  const [nodes, setNodes] = useState<FlowNode[]>(() => {
    if (!initialHypothesis) return INITIAL_NODES;
    return INITIAL_NODES.map(node =>
      node.type === 'start'
        ? { ...node, data: { ...node.data, description: initialHypothesis } }
        : node
    );
  });

  const [edges, setEdges] = useState<FlowEdge[]>(INITIAL_EDGES);
  const [isPaused, setIsPaused] = useState(false);
  const [isConnected, setIsConnected] = useState(websocketService.isConnected());

  const isPausedRef = useRef(isPaused);
  const modeRef = useRef(mode);
  useEffect(() => { isPausedRef.current = isPaused; }, [isPaused]);
  useEffect(() => { modeRef.current = mode; }, [mode]);

  const hasInitiatedPlan = useRef(false);

  useEffect(() => {
    websocketService.connect();

    const handleWSMessage = (data: any) => {
      const { type, payload } = data;
      if (type === 'msg') return;
      if (type === 'node' || type === 'NODE_UPDATE')
        updateNodeData(data.nodeId || data.id || payload?.nodeId, payload || data.data);
      if (type === 'plan' || type === 'PLAN_UPDATE')
        applyBackendPlan(payload || data.data || data);
      if (type === 'tool_update')
        updateToolProgress(payload);
      if (type === 'run_status')
        updateCurrentAgent(payload.current_agent);
      if (type === 'run_finished')
        updateCurrentAgent('run_finished');
    };

    const handleStatus = (status: boolean) => setIsConnected(status);
    const unsubscribeMsg = websocketService.subscribe(handleWSMessage);
    const unsubscribeStatus = websocketService.onStatusChange(handleStatus);

    if (initialHypothesis && !hasInitiatedPlan.current) {
      hasInitiatedPlan.current = true;
      const startPlanning = async () => {
        try {
          await new Promise(r => setTimeout(r, 500));
          const response = await fetch('http://localhost:8150/plan', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: initialHypothesis }),
          });
          if (!response.ok) console.error('Failed to initiate planning session');
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
      if (planPayload.edges) setEdges(planPayload.edges);
      if (planPayload.mode) setMode(planPayload.mode);
      return;
    }

    if (planPayload.steps) {
      const newNodes: FlowNode[] = [];

      newNodes.push({
        id: 'start',
        type: 'start',
        position: { x: START_X, y: START_NODE_Y },
        data: {
          title: 'Research Hypothesis',
          description: planPayload.study_goal || initialHypothesis || 'Study workflow',
        },
      });

      planPayload.steps.forEach((step: any, index: number) => {
        newNodes.push({
          id: step.id,
          type: 'agent',
          position: {
            x: AGENT_X_OFFSET + index * (AGENT_W + H_GAP),
            y: AGENT_NODE_Y,
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

      setNodes(newNodes);

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
    }
  };

  const updateCurrentAgent = (current_agent: string) => {
    setNodes(prev =>
      prev.map(node => {
        if (node.type !== 'agent') return node;
        const isCurrent = node.id === current_agent;
        const wasActive = node.data.status === 'active';
        if (isCurrent)
          return { ...node, data: { ...node.data, status: 'active', isExpanded: true } };
        if (wasActive && !isCurrent)
          return { ...node, data: { ...node.data, status: 'completed', isExpanded: false } };
        return node;
      })
    );
  };

  const updateToolProgress = (event: ToolEvent) => {
    setNodes(prev =>
      prev.map(node => {
        if (node.type !== 'agent' || node.id !== event.source) return node;
        const logs = node.data.logs ?? [];
        if (event.event_type === 'tool_started') {
          return {
            ...node,
            data: {
              ...node.data,
              isExpanded: true,
              logs: [...logs, {
                source: event.source,
                started_at: event.timestamp,
                last_updated_at: event.timestamp,
                status: 'active',
                events: [event],
              }],
            },
          };
        }
        if (logs.length === 0) return node;
        const lastLog = logs[logs.length - 1];
        const updatedLastLog = {
          ...lastLog,
          last_updated_at: event.timestamp,
          status:
            event.event_type === 'tool_finished' ? 'completed'
            : event.event_type === 'error' ? 'error'
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

  const sendPlanToBackend = useCallback(
    (nodesData: FlowNode[], edgesData: FlowEdge[]) => {
      websocketService.sendMessage({
        type: 'PLAN_UPDATE',
        nodes: nodesData,
        edges: edgesData,
        mode,
        timestamp: new Date().toISOString(),
      });
    },
    [mode]
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
      if (!node) return;
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
        if (n.id === id && n.type === 'agent')
          return { ...n, data: { ...n.data, isExpanded: !(n.data as any).isExpanded } };
        return n;
      })
    );
  }, []);

  const startExecution = async () => {
    const hypothesis = nodes.find(node => node.type === 'start')?.data.description ?? '';
    setMode('executing');
    setIsPaused(false);
    setNodes(prev =>
      prev.map(n => ({ ...n, data: { ...n.data, status: 'pending', logs: [], isExpanded: false } }))
    );
    setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));
    await fetch('http://localhost:8150/run/', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hypothesis }),
    });
  };

  const resetWorkflow = () => {
    setMode('planning');
    setIsPaused(false);
    const resetNodes = INITIAL_NODES.map(node =>
      node.type === 'start' && initialHypothesis
        ? { ...node, data: { ...node.data, description: initialHypothesis } }
        : node
    );
    setNodes(resetNodes);
    setEdges(INITIAL_EDGES);
    if (websocketService.isConnected())
      websocketService.sendMessage({ type: 'RESET_WORKFLOW' });
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

  return (
    <div className="relative flex h-full w-full flex-col bg-[#000000]">
      <div className="absolute top-6 left-1/2 z-40 flex -translate-x-1/2 items-center gap-4 rounded-lg border border-[#3c5d80] bg-[#151a1f]/90 p-2 shadow-2xl backdrop-blur-md">
        <div className="flex items-center gap-3 border-r border-[#3c5d80] pl-2 pr-4">
          <h1 className="hidden text-[10px] font-bold uppercase tracking-wide text-[#91b9cd] xl:block">
            Workflow Engine
          </h1>
          <div
            className={`rounded border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider
              ${mode === 'planning' ? 'border-[#3c5d80] bg-[#1f2d40] text-[#91b9cd]'
              : mode === 'executing' ? 'border-[#005f7d] bg-[#003f5c] text-[#5acce6]'
              : 'border-[#085a3c] bg-[#06422d] text-[#6fe5b8]'}`}
          >
            {mode}
          </div>
        </div>

        <div className="flex items-center gap-2">
          {mode === 'planning' && (
            <button
              onClick={startExecution}
              className="flex items-center gap-2 rounded bg-[#348cfd] px-4 py-1.5 text-xs font-medium text-white shadow-lg transition-colors hover:bg-[#599fff]"
            >
              <Play size={14} fill="currentColor" /> Execute
            </button>
          )}
          {mode === 'executing' && (
            <>
              <button
                onClick={() => setIsPaused(!isPaused)}
                className={`flex items-center gap-2 rounded border px-3 py-1.5 text-xs font-medium transition-colors ${isPaused ? 'border-[#705228] bg-[#3b2d1c] text-[#f8ce46]' : 'border-[#3c5d80] bg-[#151a1f] text-[#91b9cd] hover:border-[#91b9cd]'}`}
              >
                {isPaused ? <Play size={14} /> : <Pause size={14} />}
                {isPaused ? 'Resume' : 'Pause'}
              </button>
              <button
                onClick={resetWorkflow}
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
          {isConnected ? <Wifi size={14} className="text-[#6fe5b8]" /> : <WifiOff size={14} />}
        </div>
      </div>

      <div className="relative flex-1 overflow-hidden" onDrop={handleDrop} onDragOver={handleDragOver}>
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
                  <div className="flex items-center gap-2 text-[#dadada]"><MousePointer2 size={10} /> Drag nodes & canvas</div>
                  <div className="flex items-center gap-2 text-[#dadada]"><Edit3 size={10} /> Edit / Delete</div>
                  <div className="flex items-center gap-2 text-[#dadada]"><ZoomIn size={10} /> Ctrl+Scroll to zoom</div>
                </>
              ) : (
                <>
                  <div className="flex items-center gap-2 text-[#dadada]"><Activity size={10} className="text-[#5acce6]" /> Active</div>
                  <div className="flex items-center gap-2 text-[#dadada]"><CheckCircle2 size={10} className="text-[#6fe5b8]" /> Complete</div>
                </>
              )}
            </div>
          </div>

          {mode === 'results' && (
            <div className="animate-in fade-in absolute inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-[2px] duration-500">
              <div className="max-w-md space-y-4 rounded border border-[#3c5d80] bg-[#151a1f] p-8 text-center shadow-2xl">
                <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full border border-[#6fe5b8] bg-[#06422d]">
                  <CheckCircle2 size={24} className="text-[#6fe5b8]" />
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