import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import websocketService from '../../services/websocket';
import { FlowEdge, FlowNode, ToolEvent, ToolLog, AgentNodeData } from './types';
import { createInitialNodes, getDynamicLayout, INITIAL_EDGES, START_W, AGENT_W } from './CanvasLayoutManager';
import CanvasHeader from './CanvasHeader';
import FlowEngineWrapper from './FlowEngineWrapper';
import { registerAllLogRenderers } from './logs/registerRenderers';

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
  const [mode, setMode] = useState<'planning' | 'executing' | 'results'>('planning');
  const [nodes, setNodes] = useState<FlowNode[]>(() => createInitialNodes(initialHypothesis));
  const [edges, setEdges] = useState<FlowEdge[]>(INITIAL_EDGES);
  const [isPaused, setIsPaused] = useState(false);
  const [isConnected, setIsConnected] = useState(websocketService.isConnected());
  const [pendingPrompt, setPendingPrompt] = useState<{ type: string; message: string } | null>(null);
  const [canExecute, setCanExecute] = useState(false);
  const hasInitiatedPlan = useRef(false);

  const updateNodeData = useCallback((id: string, patch: any) => {
    setNodes(prev => prev.map(n => (n.id === id ? { ...n, data: { ...n.data, ...patch } } : n)));
  }, []);

  const updateCurrentAgent = useCallback((current_agent: string) => {
    setNodes(prev =>
      prev.map(node => {
        if (node.type !== 'agent') return node;
        const agentData = node.data as AgentNodeData;
        if (node.id === current_agent) {
          return { ...node, data: { ...agentData, status: 'active', isExpanded: true } };
        }
        if (agentData.status === 'active') {
          return { ...node, data: { ...agentData, status: 'completed', isExpanded: false } };
        }
        return node;
      })
    );
  }, []);

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
      if (incomingSessionId && session_id && incomingSessionId !== String(session_id)) {
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
        onLiteratureOutput?.(payload);
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
        const toolEvent = payload as ToolEvent;
        if (!toolEvent?.source) return;

        setNodes(prev =>
          prev.map(node => {
            if (node.type !== 'agent' || node.id !== toolEvent.source) return node;
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

            return { ...node, data: { ...node.data, logs: updatedLogs } };
          })
        );
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

    if (initialHypothesis && !hasInitiatedPlan.current) {
      hasInitiatedPlan.current = true;
      const startPlanning = async () => {
        try {
          await new Promise(r => setTimeout(r, 500));
          const response = await fetch('http://localhost:8150/plan', {
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

    return () => {
      unsubscribeMsg();
      unsubscribeStatus();
    };
  }, [applyBackendPlan, includeLitReview, initialHypothesis, onLiteratureOutput, session_id, updateCurrentAgent, updateNodeData]);

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

  const restartExecution = async () => {
    await fetch('http://localhost:8150/restart', {
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
