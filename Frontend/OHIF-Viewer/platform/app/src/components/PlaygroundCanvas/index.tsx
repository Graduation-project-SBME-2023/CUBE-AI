import React, { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { 
    Play, Pause, RotateCcw, Wifi, WifiOff, Activity, CheckCircle2, 
    MousePointer2, Edit3 
} from 'lucide-react';
import { useDrop } from 'react-dnd'; // Optional: If you use DnD, or standard HTML5 drop below
import websocketService from '../../utils/WebsocketService'; // Adjust path as needed
import MiniFlow from './miniflow'; 
import StartNode from './nodes/StartNode';
import AgentNode from './nodes/AgentNode';
import CustomEdge from './edges/CustomEdges';
import { FlowNode, FlowEdge } from './types'; // Adjust path to your types

// --- CONSTANTS & MOCKS ---
const INITIAL_NODES: FlowNode[] = [
    { id: '1', type: 'start', position: { x: 50, y: 150 }, data: { title: 'Start', description: 'Entry point' } }
];
const INITIAL_EDGES: FlowEdge[] = [];

const nodeTypes = { start: StartNode, agent: AgentNode };
const edgeTypes = { custom: CustomEdge };

interface PlaygroundCanvasProps {
    initialHypothesis?: string;
    onNodeSelect?: (node: FlowNode | null) => void;
}

export default function PlaygroundCanvas({ initialHypothesis, onNodeSelect }: PlaygroundCanvasProps) {
    // --- STATE ---
    const [mode, setMode] = useState<'planning' | 'executing' | 'results'>('planning');
    const [nodes, setNodes] = useState<FlowNode[]>(INITIAL_NODES);
    const [edges, setEdges] = useState<FlowEdge[]>(INITIAL_EDGES);
    const [isPaused, setIsPaused] = useState(false);
    const [isConnected, setIsConnected] = useState(false);

    // --- REFS ---
    const isPausedRef = useRef(isPaused);
    const modeRef = useRef(mode);
    
    // --- SYNC REFS ---
    useEffect(() => { isPausedRef.current = isPaused; }, [isPaused]);
    useEffect(() => { modeRef.current = mode; }, [mode]);

    // --- WEBSOCKET SETUP ---
    useEffect(() => {
        websocketService.connect();
        
        // Send initial hypothesis if present
        if (initialHypothesis) {
             // Small timeout to ensure connection or queue it
             setTimeout(() => {
                websocketService.sendMessage({ type: 'hypothesis', payload: initialHypothesis });
             }, 500);
        }

        const handleWSMessage = (data: any) => {
            const { type, payload } = data;
            if (type === 'msg') return;
            
            // Handle Backend Node Updates
            if (type === 'node' || type === 'NODE_UPDATE') {
                updateNodeData(data.nodeId || data.id || payload?.nodeId, payload || data.data);
            }
            
            // Handle Backend Plan Generation
            if (type === 'plan' || type === 'PLAN_UPDATE') {
                applyBackendPlan(payload || data.data || data);
            }
        };

        const handleStatus = (status: boolean) => setIsConnected(status);

        const unsubscribeMsg = websocketService.subscribe(handleWSMessage);
        const unsubscribeStatus = websocketService.onStatusChange(handleStatus);
        
        return () => { unsubscribeMsg(); unsubscribeStatus(); };
    }, [initialHypothesis]);

    // --- LOGIC: DATA HANDLERS ---
    const updateNodeData = useCallback((id: string, patch: any) => {
        setNodes(prev => prev.map(n => n.id === id ? { ...n, data: { ...n.data, ...patch } } : n));
    }, []);

    const applyBackendPlan = (planPayload: any) => {
        if (planPayload.nodes) {
            setNodes(planPayload.nodes);
            if (planPayload.edges) setEdges(planPayload.edges);
            if (planPayload.mode) setMode(planPayload.mode);
            return;
        }
        // ... (Include the "steps to nodes" conversion logic here if needed)
    };

    // --- LOGIC: EXECUTION LOOP ---
    const startExecution = async () => {
        setMode('executing');
        setIsPaused(false);
        // Reset node states
        setNodes(prev => prev.map(n => ({ 
            ...n, 
            data: { ...n.data, status: 'pending', logs: [], isExpanded: false } 
        })));
        setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));

        if (websocketService.isConnected()) {
            websocketService.sendMessage({ type: 'START_WORKFLOW' });
            return;
        }

        // Fallback: Local Mock Execution
        const executionIds = nodes.filter(n => n.type === 'agent').map(n => n.id);
        const wait = (ms: number) => new Promise(r => setTimeout(r, ms));

        for (const nodeId of executionIds) {
            if (modeRef.current === 'planning') return;
            while (isPausedRef.current) { await wait(100); if (modeRef.current === 'planning') return; }
            
            updateNodeData(nodeId, { status: 'active', isExpanded: true });
            
            // Simulate steps
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
        setNodes(INITIAL_NODES);
        setEdges(INITIAL_EDGES);
        if(websocketService.isConnected()) websocketService.sendMessage({ type: 'RESET_WORKFLOW' });
    };

    // --- LOGIC: DRAG & DROP (From Registry) ---
    const handleDrop = (event: React.DragEvent) => {
        event.preventDefault();
        const type = event.dataTransfer.getData('application/reactflow');
        const agentDataString = event.dataTransfer.getData('application/agentData');
        
        if (!type || !agentDataString) return;
        
        const agentData = JSON.parse(agentDataString);
        
        // Calculate position (simplified, real implementation needs screenToFlowPosition)
        const position = { 
            x: event.clientX - 300, // Offset for sidebar/margins
            y: event.clientY - 100 
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
                isExpanded: false
            }
        };

        setNodes((nds) => nds.concat(newNode));
    };

    const handleDragOver = (event: React.DragEvent) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = 'move';
    };

    // --- NODES CALLBACKS (Memoized) ---
    const nodesWithHandlers = useMemo(() => nodes.map(n => ({
        ...n,
        data: {
            ...n.data,
            mode,
            onDelete: (id: string) => setNodes(nds => nds.filter(node => node.id !== id)),
            onEdit: (id: string) => { /* logic */ },
            onToggleExpand: (id: string) => {
                 setNodes(nds => nds.map(node => 
                    node.id === id ? { ...node, data: { ...node.data, isExpanded: !node.data.isExpanded } } : node
                 ));
            }
        }
    })), [nodes, mode]);

    return (
        <div className="w-full h-full flex flex-col relative bg-[#000000]">
            {/* 1. INTERNAL HEADER / TOOLBAR */}
            <div className="h-12 bg-[#151a1f] border-b border-[#000000] flex items-center justify-between px-4 z-10 shrink-0">
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
                    
                    <div className="h-4 w-px bg-[#3c5d80] mx-2"></div>
                    <div className="flex items-center gap-1 text-[10px] text-[#566a84] font-medium uppercase" title={isConnected ? "WebSocket Connected" : "Using Mock Data"}>
                        {isConnected ? <Wifi size={12} className="text-[#6fe5b8]" /> : <WifiOff size={12} />}
                    </div>
                </div>
            </div>

            {/* 2. CANVAS AREA */}
            <div className="flex-1 relative overflow-hidden" onDrop={handleDrop} onDragOver={handleDragOver}>
                <MiniFlow
                    nodes={nodesWithHandlers}
                    edges={edges}
                    onNodesChange={setNodes}
                    nodeTypes={nodeTypes}
                    edgeTypes={edgeTypes}
                    readOnly={mode !== 'planning'}
                >
                    {/* Canvas Overlays (Legend) */}
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

                    {/* Completion Overlay */}
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