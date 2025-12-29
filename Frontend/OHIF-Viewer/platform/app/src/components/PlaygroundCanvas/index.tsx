import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { 
  Play, Pause, RotateCcw, MousePointer2, Wifi, WifiOff, Edit3, 
  Activity, CheckCircle2, Settings 
} from 'lucide-react';

import websocketService from '../../utils/WebsocketService';
import MiniFlow from './miniflow';
import StartNode from './nodes/StartNode';
import AgentNode from './nodes/AgentNode';
import CustomEdge from './edges/CustomEdges';
import { FlowNode, FlowEdge, ToolEvent } from './types';

// Mock Data
const initialNodesData: FlowNode[] = [
  { id: '1', type: 'start', position: { x: 50, y: 150 }, data: { title: 'Start', description: 'Entry point' } },
  { id: 'population_planner_agent', type: 'agent', position: { x: 250, y: 150 }, data: { title: 'Retrieve Study', description: 'Fetch DICOM metadata', status: 'pending', logs: [], isExpanded: false } },
  { id: 'segmentation_agent', type: 'agent', position: { x: 500, y: 50 }, data: { title: 'Segmentation', description: 'Run ML Segmentation Model', status: 'pending', logs: [], isExpanded: false } },
  { id: '4', type: 'agent', position: { x: 500, y: 250 }, data: { title: 'Measurements', description: 'Calculate ROI metrics', status: 'pending', logs: [], isExpanded: false } },
  { id: '5', type: 'agent', position: { x: 750, y: 150 }, data: { title: 'Save Report', description: 'Commit SR to PACS', status: 'pending', logs: [], isExpanded: false } },
];

const initialEdgesData: FlowEdge[] = [
  { id: 'e1-2', source: '1', target: '2', type: 'custom' },
  { id: 'e2-3', source: 'population_planner_agent', target: 'segmentation_agent', type: 'custom' },
  { id: 'e2-4', source: 'population_planner_agent', target: '4', type: 'custom' },
  { id: 'e3-5', source: 'segmentation_agent', target: '5', type: 'custom' },
  { id: 'e4-5', source: '4', target: '5', type: 'custom' },
];

const nodeTypes = { start: StartNode, agent: AgentNode };
const edgeTypes = { custom: CustomEdge };

export default function PlaygroundCanvas() {
  const [mode, setMode] = useState<'planning' | 'executing' | 'results'>('planning'); 
  const [nodes, setNodes] = useState<FlowNode[]>(initialNodesData);
  const [edges, setEdges] = useState<FlowEdge[]>(initialEdgesData);
  const [isPaused, setIsPaused] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  
  const isPausedRef = useRef(isPaused);
  const modeRef = useRef(mode);

  useEffect(() => { isPausedRef.current = isPaused; }, [isPaused]);
  useEffect(() => { modeRef.current = mode; }, [mode]);

  useEffect(() => {
    websocketService.connect();
    const handleWSMessage = (data: any) => {
      const { type, payload } = data;
      if (type === 'msg') return;
      if (type === 'node' || type === 'NODE_UPDATE') {
        updateNodeData(data.nodeId || data.id || payload?.nodeId, payload || data.data);
      }
      if (type === 'plan' || type === 'PLAN_UPDATE') {
        updatePlan(payload || data.data || data);
      }
      if (type === 'tool_update') {
        updateToolProgress(payload);
      }
    };
    const unsubscribe = websocketService.subscribe(handleWSMessage);
    return () => { unsubscribe(); };
  }, []);

  const updateNodeData = (id: string, patch: any) => {
    setNodes(prev => prev.map(n => n.id === id ? { ...n, data: { ...n.data, ...patch } } : n));
  };

  const updatePlan = (planPayload: any) => {
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
        position: { x: 50, y: 150 },
        data: { title: 'Start', description: planPayload.study_goal || 'Study workflow' }
      });
      planPayload.steps.forEach((step: any, index: number) => {
        newNodes.push({
          id: String(index + 1),
          type: "agent",
          position: { x: 50 + (index + 1) * 200, y: 150 },
          data: {
            title: step.name || `Step ${index + 1}`,
            description: step.description || "",
            status: step.status || "pending",
            logs: [],
            isExpanded: false,
            matched_module: step.matched_module
          }
        });
      });
      setNodes(newNodes);
      const newEdges: FlowEdge[] = [];
      for (let i = 0; i < newNodes.length - 1; i++) {
        newEdges.push({
          id: `e${newNodes[i].id}-${newNodes[i+1].id}`,
          source: newNodes[i].id,
          target: newNodes[i+1].id,
          type: 'custom'
        });
      }
      setEdges(newEdges);
    }
  };

  const updateToolProgress = (event: ToolEvent) => {
    setNodes(prev =>
      prev.map(node => {
        if (
          node.type !== 'agent' ||
          node.id !== event.source
        ) {
          return node;
        }

        return {
          ...node,
          data: {
            ...node.data,
            // auto-expand when logs arrive
            isExpanded: true,
            logs: [...(node.data.logs || []), event],
            // optionally update status
            // status:
            //   event.event_type === 'tool_started'
            //     ? 'active'
            //     : event.event_type === 'tool_finished'
            //     ? 'completed'
            //     : node.data.status,
          },
        };
      })
    );
  };

  const sendPlanToBackend = useCallback((nodesData: FlowNode[], edgesData: FlowEdge[]) => {
    websocketService.sendMessage({
      type: 'PLAN_UPDATE',
      nodes: nodesData,
      edges: edgesData,
      mode: mode,
      timestamp: new Date().toISOString()
    });
  }, [mode]);

  const deleteNode = useCallback((id: string) => {
    const updatedNodes = nodes.filter(n => n.id !== id);
    const updatedEdges = edges.filter(e => e.source !== id && e.target !== id);
    setNodes(updatedNodes);
    setEdges(updatedEdges);
    sendPlanToBackend(updatedNodes, updatedEdges);
  }, [nodes, edges, sendPlanToBackend]);

  const editNode = useCallback((id: string) => {
    const node = nodes.find(n => n.id === id);
    if (!node) return;
    const newDesc = window.prompt("Edit Step Description:", node.data.description);
    if (newDesc !== null) {
        const updatedNodes = nodes.map(n => n.id === id ? { ...n, data: { ...n.data, description: newDesc } } : n);
        setNodes(updatedNodes);
        sendPlanToBackend(updatedNodes, edges);
    }
  }, [nodes, edges, sendPlanToBackend]);

  const toggleExpand = useCallback((id: string) => {
    setNodes(prev => prev.map(n => {
      if (n.id === id && n.type === 'agent') {
        return { ...n, data: { ...n.data, isExpanded: !(n.data as any).isExpanded } };
      }
      return n;
    }));
  }, []);

  const nodesWithHandlers = useMemo(() => nodes.map(n => ({
    ...n,
    data: {
      ...n.data,
      mode: mode,
      onDelete: deleteNode,
      onEdit: editNode,
      onToggleExpand: toggleExpand
    }
  })), [nodes, mode, deleteNode, editNode, toggleExpand]);

  const startExecution = async () => {
    setMode('executing');
    setIsPaused(false);
    setNodes(prev => prev.map(n => ({ ...n, data: { ...n.data, status: 'pending', logs: [], isExpanded: false } })));
    setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));

    if (websocketService.isConnected()) {
        websocketService.sendMessage({ type: 'START_WORKFLOW' });
        return; 
    }

    // Mock Execution Fallback
    const executionIds = nodes.filter(n => n.type === 'agent').map(n => n.id);
    const wait = (ms: number) => new Promise(r => setTimeout(r, ms));

    for (const nodeId of executionIds) {
        if (modeRef.current === 'planning') return;
        while (isPausedRef.current) { await wait(100); if (modeRef.current === 'planning') return; }
        updateNodeData(nodeId, { status: 'active', isExpanded: true }); 
        const logs = ["Processing...", "Analyzing data...", "Task completed"];
        for (const log of logs) {
            while (isPausedRef.current) await wait(100);
            await wait(800);
            setNodes(prev => prev.map(n => (n.id === nodeId ? { ...n, data: { ...n.data, logs: [...(n.data.logs || []), log] } } : n)));
        }
        updateNodeData(nodeId, { status: 'completed', isExpanded: false });
        setEdges(prev => prev.map(e => e.source === nodeId ? { ...e, data: { ...e.data, animated: true } } : e));
        await wait(500);
    }
    setMode('results');
  };

  const resetWorkflow = () => {
    setMode('planning');
    setIsPaused(false);
    setNodes(initialNodesData);
    setEdges(initialEdgesData);
    if(websocketService.isConnected()) { websocketService.sendMessage({ type: 'RESET_WORKFLOW' }); }
  };

  return (
    <div className="w-full h-screen font-sans flex flex-col bg-[#000000] text-white">
      {/* HEADER */}
      <div className="h-12 bg-[#151a1f] border-b border-[#000000] flex items-center justify-between px-4 z-10">
        <div className="flex items-center gap-4">
            <h1 className="font-bold text-[#91b9cd] text-sm tracking-wide">WORKFLOW ENGINE</h1>
            <div className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border
                ${mode === 'planning' ? 'bg-[#1f2d40] text-[#91b9cd] border-[#3c5d80]' : 
                  mode === 'executing' ? 'bg-[#003f5c] text-[#5acce6] border-[#005f7d]' : 
                  'bg-[#06422d] text-[#6fe5b8] border-[#085a3c]'}`}>
                {mode}
            </div>
        </div>
        <div className="flex items-center gap-3">
             {mode === 'planning' && (
                 <button onClick={startExecution} className="flex items-center gap-2 px-4 py-1.5 bg-[#348cfd] hover:bg-[#599fff] text-white text-xs rounded font-medium transition-colors">
                    <Play size={14} fill="currentColor"/> Execute
                 </button>
             )}
             {mode === 'executing' && (
                 <>
                    <button onClick={() => setIsPaused(!isPaused)} className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium transition-colors border ${isPaused ? 'bg-[#3b2d1c] border-[#705228] text-[#f8ce46]' : 'bg-[#151a1f] border-[#3c5d80] text-[#91b9cd] hover:border-[#91b9cd]'}`}>
                        {isPaused ? <Play size={14} /> : <Pause size={14} />} {isPaused ? 'Resume' : 'Pause'}
                    </button>
                    <button onClick={resetWorkflow} className="flex items-center gap-2 px-3 py-1.5 bg-[#151a1f] hover:bg-[#2c1a1f] text-[#91b9cd] hover:text-[#ff6b6b] border border-[#3c5d80] rounded text-xs transition-colors">
                        <RotateCcw size={14} /> Stop
                    </button>
                 </>
             )}
             {mode === 'results' && (
                 <button onClick={resetWorkflow} className="flex items-center gap-2 px-4 py-1.5 bg-[#6fe5b8] hover:bg-[#91eac8] text-black text-xs rounded font-medium transition-colors">
                    <RotateCcw size={14} /> Reset
                 </button>
             )}
             <div className="h-4 w-px bg-[#3c5d80] mx-2"></div>
             <div className="flex items-center gap-1 text-[10px] text-[#566a84] font-medium uppercase" title={isConnected ? "WebSocket Connected" : "Using Mock Data"}>
                 {isConnected ? <Wifi size={12} className="text-[#6fe5b8]" /> : <WifiOff size={12} />}
             </div>
             <Settings size={14} className="text-[#566a84] hover:text-[#91b9cd] cursor-pointer" />
        </div>
      </div>

      {/* CANVAS */}
      <div className="flex-1 relative overflow-hidden">
        <MiniFlow
            nodes={nodesWithHandlers}
            edges={edges}
            onNodesChange={setNodes}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            readOnly={mode !== 'planning'}
        >
            <div className="absolute bottom-6 left-6 pointer-events-none">
                 <div className="bg-[#151a1f]/90 backdrop-blur p-3 rounded border border-[#3c5d80] text-[10px] space-y-2">
                    <div className="font-bold text-[#91b9cd] uppercase mb-1">
                        {mode === 'planning' ? 'Planning Tools' : 'Monitor Status'}
                    </div>
                    {mode === 'planning' ? (
                        <>
                            <div className="flex items-center gap-2 text-[#dadada]"><MousePointer2 size={10} /> Drag nodes</div>
                            <div className="flex items-center gap-2 text-[#dadada]"><Edit3 size={10} /> Edit / Delete</div>
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
                <div className="absolute inset-0 bg-black/70 backdrop-blur-[2px] z-50 flex items-center justify-center animate-in fade-in duration-500">
                    <div className="bg-[#151a1f] p-8 rounded border border-[#3c5d80] max-w-md text-center space-y-4 shadow-2xl">
                        <div className="w-12 h-12 bg-[#06422d] rounded-full flex items-center justify-center mx-auto mb-2 border border-[#6fe5b8]">
                            <CheckCircle2 size={24} className="text-[#6fe5b8]" />
                        </div>
                        <h2 className="text-xl font-bold text-white">Workflow Complete</h2>
                        <p className="text-[#91b9cd] text-sm">All workflow steps executed successfully.</p>
                        <button onClick={resetWorkflow} className="w-full py-2 bg-[#348cfd] text-white text-sm rounded hover:bg-[#599fff] transition-colors font-medium">Return to Worklist</button>
                    </div>
                </div>
            )}
        </MiniFlow>
      </div>
    </div>
  );
}