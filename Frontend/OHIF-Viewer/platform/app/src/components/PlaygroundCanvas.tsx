import React, { useState, useRef, useEffect, useCallback, useMemo, memo } from 'react';
import { 
  Play, Pause, RotateCcw, Activity, CheckCircle2, Circle, 
  X, Trash2, MousePointer2, Wifi, WifiOff, Edit3, 
  ChevronDown, ChevronUp, FileText, Settings 
} from 'lucide-react';
import websocketService from '../utils/WebsocketService';

// ==========================================
// TYPE DEFINITIONS
// ==========================================
interface StartNodeData {
  title: string;
  description: string;
}

interface AgentNodeData {
  title: string;
  description: string;
  status: string;
  logs: any[];
  isExpanded?: boolean;
  mode?: 'planning' | 'executing' | 'results';
  matched_module?: string;
  onDelete?: (id: string) => void;
  onEdit?: (id: string) => void;
  onToggleExpand?: (id: string) => void;
}

interface FlowNode {
  id: string;
  type: string;
  position: { x: number; y: number };
  data: StartNodeData | AgentNodeData;
}

interface FlowEdge {
  id: string;
  source: string;
  target: string;
  type: string;
}

// ==========================================
// 1. MINI-FLOW ENGINE (OHIF Theme)
// ==========================================
const MiniFlow = ({ nodes, edges, onNodesChange, nodeTypes, edgeTypes, children, readOnly }) => {
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [scale, setScale] = useState(1);
  const [isDraggingCanvas, setIsDraggingCanvas] = useState(false);
  const [lastMousePos, setLastMousePos] = useState({ x: 0, y: 0 });
  const [draggingNodeId, setDraggingNodeId] = useState(null);
  const containerRef = useRef(null);

  const handleCanvasMouseDown = (e) => {
    if (e.button === 0 && !e.target.closest('.flow-node')) {
      setIsDraggingCanvas(true);
      setLastMousePos({ x: e.clientX, y: e.clientY });
    }
  };

  const handleMouseMove = (e) => {
    if (isDraggingCanvas) {
      const dx = e.clientX - lastMousePos.x;
      const dy = e.clientY - lastMousePos.y;
      setOffset(prev => ({ x: prev.x + dx, y: prev.y + dy }));
      setLastMousePos({ x: e.clientX, y: e.clientY });
    }

    if (draggingNodeId && !readOnly) {
      const dx = (e.movementX) / scale;
      const dy = (e.movementY) / scale;
      onNodesChange(nodes.map(n => 
        n.id === draggingNodeId 
          ? { ...n, position: { x: n.position.x + dx, y: n.position.y + dy } } 
          : n
      ));
    }
  };

  const handleMouseUp = () => {
    setIsDraggingCanvas(false);
    setDraggingNodeId(null);
  };

  return (
    <div 
      ref={containerRef}
      // OHIF Theme: Main background is deep black/navy (#000000 or #090c29)
      className={`w-full h-full bg-[#000000] relative overflow-hidden select-none ${readOnly ? 'cursor-default' : 'cursor-grab active:cursor-grabbing'}`}
      onMouseDown={handleCanvasMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={handleMouseUp}
      onMouseLeave={handleMouseUp}
    >
      {/* Background Grid - Adjusted for dark mode visibility */}
      <div 
        className="absolute inset-0 pointer-events-none opacity-25"
        style={{
          backgroundImage: 'radial-gradient(#44626f 1px, transparent 1px)', // Blue-ish grey dots
          backgroundSize: `${20 * scale}px ${20 * scale}px`,
          backgroundPosition: `${offset.x}px ${offset.y}px`
        }}
      />

      <div 
        className="transform-gpu origin-top-left"
        style={{ 
          transform: `translate(${offset.x}px, ${offset.y}px) scale(${scale})`,
          width: '100%', 
          height: '100%' 
        }}
      >
        {/* Layer 1: Edges */}
        <svg className="absolute top-0 left-0 w-[5000px] h-[5000px] pointer-events-none overflow-visible">
          {edges.map(edge => {
            const source = nodes.find(n => n.id === edge.source);
            const target = nodes.find(n => n.id === edge.target);
            if (!source || !target) return null;

            const sx = source.position.x + (edge.sourceHandleX || 150);
            const sy = source.position.y + 30; 
            const tx = target.position.x;
            const ty = target.position.y + 30;
            
            const EdgeComponent = edgeTypes[edge.type] || edgeTypes.default;
            return (
              <EdgeComponent 
                key={edge.id}
                id={edge.id}
                sourceX={sx} sourceY={sy}
                targetX={tx} targetY={ty}
                data={edge.data}
              />
            );
          })}
        </svg>

        {/* Layer 2: Nodes */}
        {nodes.map(node => {
          const NodeComponent = nodeTypes[node.type] || nodeTypes.default;
          return (
            <div
              key={node.id}
              className="absolute flow-node"
              style={{ transform: `translate(${node.position.x}px, ${node.position.y}px)` }}
              onMouseDown={(e) => {
                if (!readOnly) {
                  e.stopPropagation();
                  setDraggingNodeId(node.id);
                }
              }}
            >
              <NodeComponent data={node.data} id={node.id} mode={readOnly ? 'executing' : 'planning'} />
            </div>
          );
        })}
      </div>
      
      {/* Layer 3: UI Overlay */}
      <div className="absolute inset-0 pointer-events-none">
        {children}
      </div>
    </div>
  );
};

// ==========================================
// 2. COMPONENTS (OHIF Styled)
// ==========================================

const CustomEdge = ({ sourceX, sourceY, targetX, targetY, data }) => {
  const isAnimated = data?.animated;
  const cX = (sourceX + targetX) / 2;
  const path = `M${sourceX},${sourceY} C${cX},${sourceY} ${cX},${targetY} ${targetX},${targetY}`;

  // OHIF: Edges are subtle slate/blue, active ones are bright cyan
  return (
    <g>
      <path d={path} stroke="#1f2d40" strokeWidth="4" fill="none" />
      <path 
        d={path} 
        stroke={isAnimated ? '#5acce6' : '#566a84'} 
        strokeWidth="2" 
        fill="none"
        strokeDasharray={isAnimated ? "5,5" : "none"}
        className={isAnimated ? "animate-flow" : ""}
      />
      <style>{`
        .animate-flow { animation: dash 1s linear infinite; }
        @keyframes dash { to { stroke-dashoffset: -10; } }
      `}</style>
    </g>
  );
};

const StartNode = memo(({ data }: { data: StartNodeData }) => {
  return (
    // OHIF: Dark panel background with light border
    <div className="px-4 py-2 shadow-md rounded-full bg-[#151a1f] border border-[#3c5d80] min-w-[100px] text-center">
      <div className="font-bold text-[#91b9cd] select-none uppercase tracking-wider text-xs">{data.title}</div>
    </div>
  );
});

const AgentNode = memo(({ data, id }: { data: AgentNodeData; id: string }) => {
  const { title, description, status = 'pending', logs = [], isExpanded, onDelete, onEdit, onToggleExpand } = data;
  const [isHovered, setIsHovered] = useState(false);

  // --- OHIF Style Logic ---
  // Default: Dark background (#151a1f), Border (#3c5d80), Text White
  let borderColor = 'border-[#3c5d80]';
  let bgColor = 'bg-[#151a1f]';
  let textColor = 'text-white';
  let Icon = Circle;
  let iconColor = 'text-[#566a84]'; // Muted blue-grey
  let shadow = 'shadow-none';

  if (status === 'active') {
    // OHIF Active: Cyan (#5acce6)
    borderColor = 'border-[#5acce6]';
    bgColor = 'bg-[#090c29]'; // Slightly darker for active
    textColor = 'text-[#5acce6]';
    Icon = Activity;
    iconColor = 'text-[#5acce6]';
    shadow = 'shadow-[0_0_15px_rgba(90,204,230,0.3)]';
  } else if (status === 'completed') {
    // OHIF Success: Green (#6fe5b8)
    borderColor = 'border-[#6fe5b8]';
    bgColor = 'bg-[#151a1f]';
    textColor = 'text-[#6fe5b8]';
    Icon = CheckCircle2;
    iconColor = 'text-[#6fe5b8]';
  }

  const handleEditClick = (e) => {
    e.stopPropagation();
    onEdit(id);
  };

  const handleDeleteClick = (e) => {
    e.stopPropagation();
    onDelete(id);
  };

  const handleNodeClick = (e) => {
    if (data.mode !== 'planning' && (status === 'active' || status === 'completed')) {
      onToggleExpand(id);
    }
  };

  return (
    <div
      className="relative group"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={handleNodeClick}
    >
      {/* Node Body */}
      <div className={`
          relative flex flex-col justify-center min-w-[200px] rounded border 
          transition-all duration-300 overflow-hidden
          ${borderColor} ${bgColor} ${shadow}
          ${data.mode !== 'planning' && (status === 'active' || status === 'completed') ? 'cursor-pointer hover:bg-[#1f2630]' : ''}
        `}
      >
        <div className="h-[50px] flex items-center justify-between px-3">
          {/* Status Icon & Title */}
          <div className="flex items-center gap-3">
            <Icon className={`${iconColor} ${status === 'active' ? 'animate-spin' : ''}`} size={16} />
            <span className={`font-medium text-sm ${textColor} select-none`}>{title}</span>
          </div>

          {/* Planning Mode Controls */}
          {data.mode === 'planning' && (
            <div className={`flex gap-1 transition-opacity ${isHovered ? 'opacity-100' : 'opacity-0'}`}>
              <button onClick={handleEditClick} className="p-1 hover:bg-[#000] rounded text-[#91b9cd]"><Edit3 size={14}/></button>
              <button onClick={handleDeleteClick} className="p-1 hover:bg-[#000] rounded text-red-400"><Trash2 size={14}/></button>
            </div>
          )}

          {/* Executing Mode Expand Indicator */}
          {data.mode !== 'planning' && (status === 'active' || status === 'completed') && (
            <div className="text-[#566a84]">
               {isExpanded ? <ChevronUp size={14}/> : <ChevronDown size={14}/>}
            </div>
          )}
        </div>

        {/* EXPANDED LOGS VIEW (Inside Node) */}
        {isExpanded && data.mode !== 'planning' && (
          <div className="border-t border-[#3c5d80] bg-[#000000] p-3 text-xs font-mono space-y-1 animate-in slide-in-from-top-2 duration-200">
            <div className="flex items-center gap-2 text-[#91b9cd] mb-2 uppercase tracking-wider font-bold text-[10px]">
              <FileText size={10} /> System Trace
            </div>
            {logs.length === 0 && <span className="text-gray-600 italic">Initializing...</span>}
            {logs.slice(-5).map((log, i) => (
              <div key={i} className="flex gap-2 text-gray-400">
                <span className="opacity-50 select-none text-[#5acce6]">{'>'}</span>
                <span>{log}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Description Tooltip (OHIF Style) */}
      {isHovered && description && (
        <div className="absolute bottom-full mb-2 left-1/2 -translate-x-1/2 w-48 bg-[#090c29] border border-[#3c5d80] text-[#dadada] text-xs p-2 rounded shadow-xl pointer-events-none z-50 animate-in fade-in zoom-in-95">
          {description}
        </div>
      )}

      {/* Connection Handles (Visual Only) */}
      <div className="absolute left-0 top-[25px] -translate-y-1/2 -translate-x-1/2 w-2 h-2 bg-[#566a84] rounded-full border border-black" />
      <div className="absolute right-0 top-[25px] -translate-y-1/2 translate-x-1/2 w-2 h-2 bg-[#566a84] rounded-full border border-black" />
    </div>
  );
});

// ==========================================
// 3. MAIN APPLICATION
// ==========================================

const initialNodesData = [
  { id: '1', type: 'start', position: { x: 50, y: 150 }, data: { title: 'Start', description: 'Entry point' } },
  { id: '2', type: 'agent', position: { x: 250, y: 150 }, data: { title: 'Retrieve Study', description: 'Fetch DICOM metadata', status: 'pending', logs: [] } },
  { id: '3', type: 'agent', position: { x: 500, y: 50 }, data: { title: 'Segmentation', description: 'Run ML Segmentation Model', status: 'pending', logs: [] } },
  { id: '4', type: 'agent', position: { x: 500, y: 250 }, data: { title: 'Measurements', description: 'Calculate ROI metrics', status: 'pending', logs: [] } },
  { id: '5', type: 'agent', position: { x: 750, y: 150 }, data: { title: 'Save Report', description: 'Commit SR to PACS', status: 'pending', logs: [] } },
];

const initialEdgesData = [
  { id: 'e1-2', source: '1', target: '2', type: 'custom' },
  { id: 'e2-3', source: '2', target: '3', type: 'custom' },
  { id: 'e2-4', source: '2', target: '4', type: 'custom' },
  { id: 'e3-5', source: '3', target: '5', type: 'custom' },
  { id: 'e4-5', source: '4', target: '5', type: 'custom' },
];

const nodeTypes = { start: StartNode, agent: AgentNode };
const edgeTypes = { custom: CustomEdge };

const MOCK_DATA_STEPS = {
  '2': ["Connecting to PACS...", "Querying SeriesInstanceUID...", "Retrieved 128 instances", "Metadata parsing complete"],
  '3': ["Loading Tensor model...", "Inference started...", "Mask generated: Liver (0.98)", "Smoothing contours..."],
  '4': ["Calculating volume...", "Volume: 1450 cc", "Mean HU: 45", "Generating SR structure"],
  '5': ["Serializing DICOM SR...", "Association negotiated...", "C-STORE request sent", "Transfer Complete"]
};

export default function AgentWorkflowCanvas() {
  const [mode, setMode] = useState<'planning' | 'executing' | 'results'>('planning'); 
  const [nodes, setNodes] = useState<FlowNode[]>(initialNodesData);
  const [edges, setEdges] = useState<FlowEdge[]>(initialEdgesData);
  const [isPaused, setIsPaused] = useState(false);
  const [socket, setSocket] = useState(null);
  const [isConnected, setIsConnected] = useState(false);
  
  const isPausedRef = useRef(isPaused);
  const modeRef = useRef(mode);

  useEffect(() => { isPausedRef.current = isPaused; }, [isPaused]);
  useEffect(() => { modeRef.current = mode; }, [mode]);

  useEffect(() => {
  websocketService.connect()
  msg => 
  handleServerMessage(msg);
  }  , []);

  const handleServerMessage = (msg: any) => {
    if (msg.type === 'node') {
        updateNodeData(msg.nodeId, msg.payload);
    }

    if (msg.type === 'plan') {
        updatePlan(msg.payload);
    }
  };

  const updateNodeData = (id: string, patch: any) => {
    setNodes(prev => prev.map(n => n.id === id ? { ...n, data: { ...n.data, ...patch } } : n));
  };

  const updatePlan = (planPayload: any) => {
    // Transform backend plan structure to frontend nodes/edges
    // Backend structure: { hypothesis_id, study_goal, steps: [{name, description, matched_module, status}], state }
    
    if (planPayload.steps) {
      // Create nodes from steps
      const newNodes: FlowNode[] = [];
      
      // Add start node
      newNodes.push({
        id: 'start',
        type: 'start',
        position: { x: 50, y: 50 },
        data: {
          title: 'Start',
          description: planPayload.study_goal || 'Study workflow'
        }
      });
      
      // Add step nodes
      planPayload.steps.forEach((step: any, index: number) => {
        const nodeId = String(index + 1);
        newNodes.push({
          id: nodeId,
          type: 'agent',
          position: { x: 50 + index * 250, y: 150 },
          data: {
            title: step.name || `Step ${index + 1}`,
            description: step.description || '',
            status: step.status || 'pending',
            logs: [],
            isExpanded: false,
            matched_module: step.matched_module
          } as AgentNodeData
        });
      });
      
      setNodes(newNodes);
      console.log('New Nodes:', newNodes);
      // Create edges connecting nodes sequentially
      const newEdges: FlowEdge[] = [];
      for (let i = 0; i < newNodes.length - 1; i++) {
        const fromId = newNodes[i].id;
        const toId = newNodes[i + 1].id;
        newEdges.push({
          id: `e${fromId}-${toId}`,
          source: fromId,
          target: toId,
          type: 'default'
        });
      }
      console.log('New Edges:', newEdges);
      setEdges(newEdges);
    }
    
    // Update nodes if provided directly
    if (planPayload.nodes) {
      setNodes(planPayload.nodes as FlowNode[]);
    }
    
    // Update edges if provided directly
    if (planPayload.edges) {
      setEdges(planPayload.edges as FlowEdge[]);
    }
    
    // Update mode if provided
    if (planPayload.mode) {
      setMode(planPayload.mode);
    }
  };

  // Send entire plan to backend
  const sendPlanToBackend = useCallback((nodesData, edgesData) => {
    if (!isConnected || !socket) return;
    
    const planPayload = {
      type: 'PLAN_UPDATE',
      nodes: nodesData.map(n => ({
        id: n.id,
        type: n.type,
        position: n.position,
        data: {
          title: n.data.title,
          description: n.data.description,
          status: n.data.status,
          logs: n.data.logs
        }
      })),
      edges: edgesData.map(e => ({
        id: e.id,
        source: e.source,
        target: e.target,
        type: e.type
      })),
      mode: mode,
      timestamp: new Date().toISOString()
    };
    
    socket.send(JSON.stringify(planPayload));
  }, [isConnected, socket, mode]);

  const deleteNode = useCallback((id) => {
    const updatedNodes = nodes.filter(n => n.id !== id);
    const updatedEdges = edges.filter(e => e.source !== id && e.target !== id);
    setNodes(updatedNodes);
    setEdges(updatedEdges);
    sendPlanToBackend(updatedNodes, updatedEdges);
  }, [nodes, edges, sendPlanToBackend]);

  const editNode = useCallback((id) => {
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
    const updatedNodes = nodes.map(n => {
      if (n.id === id && n.type === 'agent') {
        return { ...n, data: { ...(n.data as AgentNodeData), isExpanded: !(n.data as AgentNodeData).isExpanded } };
      }
      return n;
    });
    setNodes(updatedNodes);
    sendPlanToBackend(updatedNodes, edges);
  }, [nodes, edges, sendPlanToBackend]);

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

  const animateEdgesFrom = (sourceId: string, active: boolean) => {
    // Edge animation can be implemented via CSS classes or state if needed
    // For now, edges are animated via status updates on nodes
  };

  const startExecution = async () => {
    setMode('executing');
    setIsPaused(false);
    
    setNodes(prev => prev.map(n => ({ 
        ...n, 
        data: { ...n.data, status: 'pending', logs: [], isExpanded: false } 
    })));
    setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));

    if (isConnected && socket) {
        socket.send(JSON.stringify({ type: 'START_WORKFLOW' }));
        return; 
    }

    const executionIds = ['2', '3', '4', '5']; 
    const wait = (ms) => new Promise(r => setTimeout(r, ms));

    for (const nodeId of executionIds) {
        if (modeRef.current !== 'executing') return;

        while (isPausedRef.current) {
            await wait(100);
            if (modeRef.current !== 'executing') return;
        }

        updateNodeData(nodeId, { status: 'active', isExpanded: true }); 

        const logs = MOCK_DATA_STEPS[nodeId] || ["Processing...", "Validating...", "Done"];
        for (const log of logs) {
            while (isPausedRef.current) await wait(100);
            await wait(800);
            setNodes(prev => prev.map(n => {
                if (n.id === nodeId && n.type === 'agent') {
                    const agentData = n.data as AgentNodeData;
                    return { ...n, data: { ...agentData, logs: [...(agentData.logs || []), log] } };
                }
                return n;
            }));
        }

        updateNodeData(nodeId, { status: 'completed', isExpanded: false });
        animateEdgesFrom(nodeId, true);
        await wait(500);
    }

    setMode('results');
  };

  const resetWorkflow = () => {
    setMode('planning');
    setIsPaused(false);
    setNodes(initialNodesData);
    setEdges(initialEdgesData);
    // Sync reset with backend
    sendPlanToBackend(initialNodesData, initialEdgesData);
  };

  return (
    <div className="w-full h-screen font-sans flex flex-col bg-[#000000] text-white">
      {/* --- HEADER / CONTROL BAR (OHIF Style) --- */}
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
                 <button
                    onClick={startExecution}
                    className="flex items-center gap-2 px-4 py-1.5 bg-[#348cfd] hover:bg-[#599fff] text-white text-xs rounded font-medium transition-colors"
                 >
                    <Play size={14} fill="currentColor"/> Execute
                 </button>
             )}

             {mode === 'executing' && (
                 <>
                    <button
                        onClick={() => setIsPaused(!isPaused)}
                        className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs font-medium transition-colors border ${
                            isPaused 
                            ? 'bg-[#3b2d1c] border-[#705228] text-[#f8ce46]' 
                            : 'bg-[#151a1f] border-[#3c5d80] text-[#91b9cd] hover:border-[#91b9cd]'
                        }`}
                    >
                        {isPaused ? <Play size={14} /> : <Pause size={14} />}
                        {isPaused ? 'Resume' : 'Pause'}
                    </button>
                    
                    <button
                        onClick={resetWorkflow}
                        className="flex items-center gap-2 px-3 py-1.5 bg-[#151a1f] hover:bg-[#2c1a1f] text-[#91b9cd] hover:text-[#ff6b6b] border border-[#3c5d80] rounded text-xs transition-colors"
                    >
                        <RotateCcw size={14} /> Stop
                    </button>
                 </>
             )}

             {mode === 'results' && (
                 <button
                    onClick={resetWorkflow}
                    className="flex items-center gap-2 px-4 py-1.5 bg-[#6fe5b8] hover:bg-[#91eac8] text-black text-xs rounded font-medium transition-colors"
                 >
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

      {/* --- CANVAS --- */}
      <div className="flex-1 relative overflow-hidden">
        <MiniFlow
            nodes={nodesWithHandlers}
            edges={edges}
            onNodesChange={setNodes}
            nodeTypes={nodeTypes}
            edgeTypes={edgeTypes}
            readOnly={mode !== 'planning'}
        >
            {/* Context Legend (OHIF Style) */}
            <div className="absolute bottom-6 left-6 pointer-events-none">
                 <div className="bg-[#151a1f]/90 backdrop-blur p-3 rounded border border-[#3c5d80] text-[10px] space-y-2">
                    <div className="font-bold text-[#91b9cd] uppercase mb-1">
                        {mode === 'planning' ? 'Planning Tools' : 'Monitor Status'}
                    </div>
                    
                    {mode === 'planning' ? (
                        <>
                            <div className="flex items-center gap-2 text-[#dadada]">
                                <MousePointer2 size={10} /> Drag nodes
                            </div>
                            <div className="flex items-center gap-2 text-[#dadada]">
                                <Edit3 size={10} /> Edit / Delete
                            </div>
                        </>
                    ) : (
                        <>
                             <div className="flex items-center gap-2 text-[#dadada]">
                                <Activity size={10} className="text-[#5acce6]" /> Active
                            </div>
                             <div className="flex items-center gap-2 text-[#dadada]">
                                <CheckCircle2 size={10} className="text-[#6fe5b8]" /> Complete
                            </div>
                        </>
                    )}
                 </div>
            </div>
            
            {/* Results Overlay (OHIF Style) */}
            {mode === 'results' && (
                <div className="absolute inset-0 bg-black/70 backdrop-blur-[2px] z-50 flex items-center justify-center animate-in fade-in duration-500">
                    <div className="bg-[#151a1f] p-8 rounded border border-[#3c5d80] max-w-md text-center space-y-4 shadow-2xl">
                        <div className="w-12 h-12 bg-[#06422d] rounded-full flex items-center justify-center mx-auto mb-2 border border-[#6fe5b8]">
                            <CheckCircle2 size={24} className="text-[#6fe5b8]" />
                        </div>
                        <h2 className="text-xl font-bold text-white">Workflow Complete</h2>
                        <p className="text-[#91b9cd] text-sm">
                            DICOM transfer verified. SR Object #1.2.840... committed successfully.
                        </p>
                        <button 
                            onClick={resetWorkflow}
                            className="w-full py-2 bg-[#348cfd] text-white text-sm rounded hover:bg-[#599fff] transition-colors font-medium"
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