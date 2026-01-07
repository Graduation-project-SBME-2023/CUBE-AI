import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { 
    Play, Pause, RotateCcw, Wifi, WifiOff, Activity, CheckCircle2, 
    MousePointer2, Edit3 
} from 'lucide-react';
import websocketService from '../../utils/WebsocketService'; 
import MiniFlow from './miniflow'; 
import StartNode from './nodes/StartNode';
import AgentNode from './nodes/AgentNode';
import CustomEdge from './edges/CustomEdges';
import { FlowNode, FlowEdge, ToolEvent } from './types';

// UPDATED LAYOUT CONSTANTS
const CENTER_LINE = 400; // Visual center of the canvas
const START_W = 300;
const AGENT_W = 280;

const START_X = CENTER_LINE - (START_W / 2); // 250
const AGENT_X = CENTER_LINE - (AGENT_W / 2); // 260

const Y_START = 50;
const Y_GAP = 140; // More breathing room

const INITIAL_NODES: FlowNode[] = [
  { id: '1', type: 'start', position: { x: START_X, y: Y_START }, data: { title: 'Research Hypothesis', description: 'Entry point' } },
  { id: 'study_population_planner_agent', type: 'agent', position: { x: AGENT_X, y: Y_START + Y_GAP }, data: { title: 'Retrieve Study', description: 'Fetch DICOM metadata', status: 'pending', logs: [], isExpanded: false } },
  { id: 'segmentation_agent', type: 'agent', position: { x: AGENT_X, y: Y_START + Y_GAP * 2 }, data: { title: 'Segmentation', description: 'Run ML Segmentation Model', status: 'pending', logs: [], isExpanded: false } },
  { id: 'feature_extraction_agent', type: 'agent', position: { x: AGENT_X, y: Y_START + Y_GAP * 3 }, data: { title: 'Radiomics Feature Extraction', description: 'Calculate ROI metrics', status: 'pending', logs: [], isExpanded: false } },
  { id: 'modeling_agent', type: 'agent', position: { x: AGENT_X, y: Y_START + Y_GAP * 4 }, data: { title: 'ML Modeling', description: 'Train/Inference Model', status: 'pending', logs: [], isExpanded: false } },
  { id: 'result_reporter_agent', type: 'agent', position: { x: AGENT_X, y: Y_START + Y_GAP * 5 }, data: { title: 'Analyze Results', description: 'Generate Findings', status: 'pending', logs: [], isExpanded: false } },
  { id: 'latex_reporter_agent', type: 'agent', position: { x: AGENT_X, y: Y_START + Y_GAP * 6 }, data: { title: 'Write PDF Report', description: 'Compile Final Report', status: 'pending', logs: [], isExpanded: false } },
];

const INITIAL_EDGES: FlowEdge[] = [
  { id: 'e1-2', source: '1', target: 'study_population_planner_agent', type: 'custom' },
  { id: 'e2-3', source: 'study_population_planner_agent', target: 'segmentation_agent', type: 'custom' },
  { id: 'e3-4', source: 'segmentation_agent', target: 'feature_extraction_agent', type: 'custom' },
  { id: 'e4-5', source: 'feature_extraction_agent', target: 'modeling_agent', type: 'custom' },
  { id: 'e5-6', source: 'modeling_agent', target: 'result_reporter_agent', type: 'custom' },
  { id: 'e6-7', source: 'result_reporter_agent', target: 'latex_reporter_agent', type: 'custom' },
];

const nodeTypes = { start: StartNode, agent: AgentNode };
const edgeTypes = { custom: CustomEdge };

interface PlaygroundCanvasProps {
    initialHypothesis?: string;
    onNodeSelect?: (node: FlowNode | null) => void;
}

export default function PlaygroundCanvas({ initialHypothesis, onNodeSelect }: PlaygroundCanvasProps) {
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
    const [isConnected, setIsConnected] = useState(false);

    const isPausedRef = useRef(isPaused);
    const modeRef = useRef(mode);
    useEffect(() => { isPausedRef.current = isPaused; }, [isPaused]);
    useEffect(() => { modeRef.current = mode; }, [mode]);

    useEffect(() => {
        websocketService.connect();
        
        if (initialHypothesis) {
             setTimeout(() => {
                websocketService.sendMessage({ type: 'hypothesis', payload: initialHypothesis });
             }, 500);
        }

        if (initialHypothesis) {
            const startPlanning = async () => {
                try {
                    const response = await fetch('http://localhost:8150/plan', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: initialHypothesis }),
                    });
                    if (!response.ok) console.error('Failed to initiate planning session');
                } catch (error) {
                    console.error('Error contacting planner API:', error);
                }
            };
            startPlanning();
        }

        const handleWSMessage = (data: any) => {
            const { type, payload } = data;
            if (type === 'msg') return;
            
            if (type === 'node' || type === 'NODE_UPDATE') {
                updateNodeData(data.nodeId || data.id || payload?.nodeId, payload || data.data);
            }
            if (type === 'plan' || type === 'PLAN_UPDATE') {
                applyBackendPlan(payload || data.data || data);
            }
            if (type === 'tool_update') {
                updateToolProgress(payload);
            }
            if (type === 'run_status') {
                updateCurrentAgent(payload.current_agent);
            }
            if (type === 'run_finished') {
                updateCurrentAgent('run_finished');
            }
        };

        const handleStatus = (status: boolean) => setIsConnected(status);
        const unsubscribeMsg = websocketService.subscribe(handleWSMessage);
        const unsubscribeStatus = websocketService.onStatusChange(handleStatus);
        
        return () => { unsubscribeMsg(); unsubscribeStatus(); };
    }, [initialHypothesis]);

    // --- LOGIC: DATA HANDLERS ---
    const applyBackendPlan = (planPayload: any) => {
        if (planPayload.nodes) {
            setNodes(planPayload.nodes);
            if (planPayload.edges) setEdges(planPayload.edges);
            if (planPayload.mode) setMode(planPayload.mode);
            return;
        }

        if (planPayload.steps) {
            const newNodes: FlowNode[] = [];
            
            // Start Node
            newNodes.push({
                id: 'start',
                type: 'start',
                position: { x: START_X, y: Y_START },
                data: { 
                    title: 'Research Hypothesis', 
                    description: planPayload.study_goal || initialHypothesis || 'Study workflow' 
                }
            });

            // Step Nodes (Vertical Stack)
            planPayload.steps.forEach((step: any, index: number) => {
                newNodes.push({
                    id: String(index + 1),
                    type: "agent",
                    position: { x: AGENT_X, y: Y_START + Y_GAP + (index * Y_GAP) }, 
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

    const updateCurrentAgent = (current_agent: string) => {
        setNodes(prev => prev.map(node => {
            if (node.type !== 'agent') return node;
            const isCurrent = node.id === current_agent;
            const wasActive = node.data.status === 'active';
            if (isCurrent) return { ...node, data: { ...node.data, status: 'active', isExpanded: true } };
            if (wasActive && !isCurrent) return { ...node, data: { ...node.data, status: 'completed', isExpanded: false } };
            return node;
        }));
    };

    const updateToolProgress = (event: ToolEvent) => {
        setNodes(prev => prev.map(node => {
            if (node.type !== 'agent' || node.id !== event.source) return node;
            const logs = node.data.logs ?? [];
            if (event.event_type === 'tool_started') {
                return { ...node, data: { ...node.data, isExpanded: true, logs: [...logs, { source: event.source, started_at: event.timestamp, last_updated_at: event.timestamp, status: 'active', events: [event] }] } };
            }
            if (logs.length === 0) return node;
            const lastLog = logs[logs.length - 1];
            const updatedLastLog = { ...lastLog, last_updated_at: event.timestamp, status: event.event_type === 'tool_finished' ? 'completed' : event.event_type === 'error' ? 'error' : lastLog.status, events: [...lastLog.events, event] };
            return { ...node, data: { ...node.data, isExpanded: true, logs: [...logs.slice(0, -1), updatedLastLog] } };
        }));
    };

    const updateNodeData = (id: string, patch: any) => {
        setNodes(prev => prev.map(n => n.id === id ? { ...n, data: { ...n.data, ...patch } } : n));
    };

    const sendPlanToBackend = useCallback((nodesData: FlowNode[], edgesData: FlowEdge[]) => {
        websocketService.sendMessage({ type: 'PLAN_UPDATE', nodes: nodesData, edges: edgesData, mode: mode, timestamp: new Date().toISOString() });
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
            if (n.id === id && n.type === 'agent') return { ...n, data: { ...n.data, isExpanded: !(n.data as any).isExpanded } };
            return n;
        }));
    }, []);

    const startExecution = async () => {
        setMode('executing');
        setIsPaused(false);
        setNodes(prev => prev.map(n => ({ ...n, data: { ...n.data, status: 'pending', logs: [], isExpanded: false } })));
        setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));

        if (websocketService.isConnected()) {
            websocketService.sendMessage({ type: 'START_WORKFLOW' });
            return;
        }

        const executionIds = nodes.filter(n => n.type === 'agent').map(n => n.id);
        const wait = (ms: number) => new Promise(r => setTimeout(r, ms));

        for (const nodeId of executionIds) {
            if (modeRef.current === 'planning') return;
            while (isPausedRef.current) { await wait(100); if (modeRef.current === 'planning') return; }
            updateNodeData(nodeId, { status: 'active', isExpanded: true });
            await wait(1000); 
            updateNodeData(nodeId, { logs: ['Processing...'] });
            await wait(1000);
            updateNodeData(nodeId, { status: 'completed', isExpanded: false });
            setEdges(prev => prev.map(e => e.source === nodeId ? { ...e, data: { ...e.data, animated: true } } : e));
        }
        setMode('results');
    };

    const resetWorkflow = () => {
        setMode('planning');
        setIsPaused(false);
        const resetNodes = INITIAL_NODES.map(node => node.type === 'start' && initialHypothesis ? { ...node, data: { ...node.data, description: initialHypothesis } } : node);
        setNodes(resetNodes);
        setEdges(INITIAL_EDGES);
        if(websocketService.isConnected()) websocketService.sendMessage({ type: 'RESET_WORKFLOW' });
    };

    const handleDrop = (event: React.DragEvent) => {
        event.preventDefault();
        const type = event.dataTransfer.getData('application/reactflow');
        const agentDataString = event.dataTransfer.getData('application/agentData');
        if (!type || !agentDataString) return;
        const agentData = JSON.parse(agentDataString);
        const reactFlowBounds = event.currentTarget.getBoundingClientRect();
        const position = { x: event.clientX - reactFlowBounds.left - 100, y: event.clientY - reactFlowBounds.top - 25 };
        const newNode: FlowNode = {
            id: `node-${Date.now()}`,
            type: 'agent',
            position,
            data: { title: agentData.title, description: agentData.role, status: 'pending', logs: [], isExpanded: false }
        };
        setNodes((nds) => nds.concat(newNode));
    };

    const handleDragOver = (event: React.DragEvent) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
    };

    const nodesWithHandlers = useMemo(() => nodes.map(n => ({
        ...n,
        data: { ...n.data, mode, onDelete: deleteNode, onEdit: editNode, onToggleExpand: toggleExpand }
    })), [nodes, mode, deleteNode, editNode, toggleExpand]);

    return (
        <div className="w-full h-full flex flex-col relative bg-[#000000]">
            <div className="absolute top-6 left-1/2 -translate-x-1/2 z-40 flex items-center gap-4 bg-[#151a1f]/90 backdrop-blur-md border border-[#3c5d80] p-2 rounded-lg shadow-2xl">
                <div className="flex items-center gap-3 pl-2 border-r border-[#3c5d80] pr-4">
                     <h1 className="font-bold text-[#91b9cd] text-[10px] tracking-wide uppercase hidden xl:block">Workflow Engine</h1>
                     <div className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider border
                        ${mode === 'planning' ? 'bg-[#1f2d40] text-[#91b9cd] border-[#3c5d80]' : 
                          mode === 'executing' ? 'bg-[#003f5c] text-[#5acce6] border-[#005f7d]' : 
                          'bg-[#06422d] text-[#6fe5b8] border-[#085a3c]'}`}>
                        {mode}
                    </div>
                </div>
                <div className="flex items-center gap-2">
                    {mode === 'planning' && (
                        <button onClick={startExecution} className="flex items-center gap-2 px-4 py-1.5 bg-[#348cfd] hover:bg-[#599fff] text-white text-xs rounded font-medium transition-colors shadow-lg">
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
                </div>
                <div className="pl-2 border-l border-[#3c5d80] flex items-center text-[10px] text-[#566a84]" title={isConnected ? "WebSocket Connected" : "Using Mock Data"}>
                     {isConnected ? <Wifi size={14} className="text-[#6fe5b8]" /> : <WifiOff size={14} />}
                </div>
            </div>

            <div className="flex-1 relative overflow-hidden" onDrop={handleDrop} onDragOver={handleDragOver}>
                <MiniFlow
                    nodes={nodesWithHandlers}
                    edges={edges}
                    onNodesChange={setNodes}
                    nodeTypes={nodeTypes}
                    edgeTypes={edgeTypes}
                    readOnly={mode !== 'planning'}
                >
                    <div className="absolute bottom-24 left-6 pointer-events-none">
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
                                <button onClick={resetWorkflow} className="w-full py-2 bg-[#348cfd] text-white text-sm rounded hover:bg-[#599fff] transition-colors font-medium">Return to Worklist</button>
                             </div>
                        </div>
                    )}
                </MiniFlow>
            </div>
        </div>
    );
}