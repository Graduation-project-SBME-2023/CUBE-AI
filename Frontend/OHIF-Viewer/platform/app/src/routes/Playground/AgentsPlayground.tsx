import React, { useState, useRef, useEffect, memo } from 'react';
import MiniFlow from './MiniFlow';
import AgentRegistry from './AgentRegistry';
import ChatSidebar from './ChatSidebar';
import websocketService from './WebSocketService';
import { Play, Pause, RotateCcw, MessageSquare, CheckCircle2, Activity, CheckCircle2 as CheckIcon, Circle, Edit3, Trash2, ChevronUp, ChevronDown, FileText } from 'lucide-react';

// --- Node and Edge Components ---
const CustomEdge = ({ sourceX, sourceY, targetX, targetY, data }) => {
  const isAnimated = data?.animated;
  const cY = (sourceY + targetY) / 2;
  const path = `M${sourceX},${sourceY} C${sourceX},${cY} ${targetX},${cY} ${targetX},${targetY}`;
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

const StartNode = memo(({ data }) => (
  <div className="px-6 py-3 shadow-lg rounded-full bg-[#151a1f] border-2 border-[#5acce6] min-w-[120px] text-center relative z-10">
    <div className="font-bold text-[#5acce6] select-none uppercase tracking-wider text-xs">{data.title}</div>
    <div className="absolute left-1/2 bottom-0 translate-y-1/2 -translate-x-1/2 w-2 h-2 bg-[#5acce6] rounded-full" />
  </div>
));

const AgentNode = memo(({ data, id }) => {
  const { title, description, status = 'pending', logs = [], isExpanded, onDelete, onEdit, onToggleExpand } = data;
  const [isHovered, setIsHovered] = useState(false);
  let borderColor = 'border-[#3c5d80]';
  let bgColor = 'bg-[#151a1f]';
  let textColor = 'text-white';
  let Icon = Circle;
  let iconColor = 'text-[#566a84]'; 
  let shadow = 'shadow-none';
  if (status === 'active') {
    borderColor = 'border-[#5acce6]';
    bgColor = 'bg-[#090c29]'; 
    textColor = 'text-[#5acce6]';
    Icon = Activity;
    iconColor = 'text-[#5acce6]';
    shadow = 'shadow-[0_0_20px_rgba(90,204,230,0.2)]';
  } else if (status === 'completed') {
    borderColor = 'border-[#6fe5b8]';
    bgColor = 'bg-[#151a1f]';
    textColor = 'text-[#6fe5b8]';
    Icon = CheckIcon;
    iconColor = 'text-[#6fe5b8]';
  }
  const handleNodeClick = (e) => {
    if (data.mode !== 'planning' && (status === 'active' || status === 'completed')) {
      if (typeof data.onToggleExpand === 'function') data.onToggleExpand(id);
    }
  };
  return (
    <div
      className="relative group w-[280px]"
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={handleNodeClick}
    >
      <div className="absolute left-1/2 top-0 -translate-y-1/2 -translate-x-1/2 w-2 h-2 bg-[#566a84] rounded-full border border-black z-10" />
      <div className={`
          relative flex flex-col justify-center rounded-lg border 
          transition-all duration-300 overflow-hidden
          ${borderColor} ${bgColor} ${shadow}
          ${data.mode !== 'planning' && (status === 'active' || status === 'completed') ? 'cursor-pointer hover:bg-[#1f2630]' : ''}
        `}
      >
        <div className="h-[60px] flex items-center justify-between px-4">
          <div className="flex items-center gap-3">
            <Icon className={`${iconColor} ${status === 'active' ? 'animate-spin' : ''}`} size={18} />
            <div className="flex flex-col">
                <span className={`font-semibold text-sm ${textColor} select-none`}>{title}</span>
                <span className="text-[10px] text-[#566a84] truncate w-[160px]">{description}</span>
            </div>
          </div>
          {data.mode === 'planning' && (
            <div className={`flex gap-1 transition-opacity ${isHovered ? 'opacity-100' : 'opacity-0'}`}>
              <button onClick={(e) => { e.stopPropagation(); if (typeof onEdit === 'function') onEdit(id); }} className="p-1 hover:bg-[#000] rounded text-[#91b9cd]"><Edit3 size={14}/></button>
              <button onClick={(e) => { e.stopPropagation(); if (typeof onDelete === 'function') onDelete(id); }} className="p-1 hover:bg-[#000] rounded text-red-400"><Trash2 size={14}/></button>
            </div>
          )}
          {data.mode !== 'planning' && (status === 'active' || status === 'completed') && (
            <div className="text-[#566a84]">
               {isExpanded ? <ChevronUp size={14}/> : <ChevronDown size={14}/>}
            </div>
          )}
        </div>
        {isExpanded && data.mode !== 'planning' && (
          <div className="border-t border-[#3c5d80] bg-[#000000] p-3 text-xs font-mono space-y-1 animate-in slide-in-from-top-2 duration-200">
            <div className="flex items-center gap-2 text-[#91b9cd] mb-2 uppercase tracking-wider font-bold text-[10px]">
              <FileText size={10} /> Live Logs
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
      <div className="absolute left-1/2 bottom-0 translate-y-1/2 -translate-x-1/2 w-2 h-2 bg-[#566a84] rounded-full border border-black z-10" />
    </div>
  );
});

const initialNodesData = [
  { id: '1', type: 'start', position: { x: 400, y: 50 }, data: { title: 'Start', description: 'Entry point' } },
  { id: '2', type: 'agent', position: { x: 320, y: 180 }, data: { title: 'Retrieve Study', description: 'Fetch DICOM metadata', status: 'pending', logs: [] } },
  { id: '3', type: 'agent', position: { x: 320, y: 320 }, data: { title: 'Segmentation', description: 'Run ML Segmentation Model', status: 'pending', logs: [] } },
  { id: '4', type: 'agent', position: { x: 320, y: 460 }, data: { title: 'Measurements', description: 'Calculate ROI metrics', status: 'pending', logs: [] } },
  { id: '5', type: 'agent', position: { x: 320, y: 600 }, data: { title: 'Save Report', description: 'Commit SR to PACS', status: 'pending', logs: [] } },
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

export default function AgentsPlayground() {
  const [mode, setMode] = useState('planning'); 
  const [nodes, setNodes] = useState(initialNodesData);
  const [edges, setEdges] = useState(initialEdgesData);
  const [isPaused, setIsPaused] = useState(false);
  const [isConnected, setIsConnected] = useState(false);
  const [isChatOpen, setIsChatOpen] = useState(false);
  const [isRegistryOpen, setIsRegistryOpen] = useState(true);
  const isPausedRef = useRef(isPaused);
  const modeRef = useRef(mode);
  useEffect(() => { isPausedRef.current = isPaused; }, [isPaused]);
  useEffect(() => { modeRef.current = mode; }, [mode]);
  useEffect(() => {
    websocketService.connect();
    const handleWSMessage = (data) => {
        const { type, payload } = data;
        if (type === 'msg') return;
        if (type === 'node' || type === 'NODE_UPDATE') {
            const targetId = data.nodeId || data.id || payload?.nodeId;
            updateNodeData(targetId, payload || data.data);
        }
        if (type === 'plan' || type === 'PLAN_UPDATE') {
            updatePlan(payload || data.data || data);
        }
    };
    const handleStatusChange = (status) => setIsConnected(status);
    const unsubscribeMsg = websocketService.subscribe(handleWSMessage);
    const unsubscribeStatus = websocketService.onStatusChange(handleStatusChange);
    return () => { unsubscribeMsg(); unsubscribeStatus(); };
  }, []);
  const updateNodeData = (id, patch) => {
    setNodes(prev => prev.map(n => n.id === id ? { ...n, data: { ...n.data, ...patch } } : n));
  };
  const updatePlan = (planPayload) => {
    if (planPayload.nodes) {
        setNodes(planPayload.nodes);
        if (planPayload.edges) setEdges(planPayload.edges);
        if (planPayload.mode) setMode(planPayload.mode);
    }
  };
  const startExecution = async () => {
    setMode('executing');
    setIsPaused(false);
    setNodes(prev => prev.map(n => ({ ...n, data: { ...n.data, status: 'pending', logs: [], isExpanded: false } })));
    setEdges(prev => prev.map(e => ({ ...e, data: { animated: false } })));
    if (websocketService.isConnected()) {
        websocketService.sendMessage({ type: 'START_WORKFLOW' });
        return; 
    }
    const executionIds = ['2', '3', '4', '5']; 
    const wait = (ms) => new Promise(r => setTimeout(r, ms));
    for (const nodeId of executionIds) {
        if (modeRef.current === 'planning') return;
        while (isPausedRef.current) { await wait(100); if (modeRef.current === 'planning') return; }
        updateNodeData(nodeId, { status: 'active', isExpanded: true }); 
        const logs = MOCK_DATA_STEPS[nodeId] || ["Processing...", "Validating...", "Done"];
        for (const log of logs) {
            while (isPausedRef.current) await wait(100);
            await wait(800);
            setNodes(prev => prev.map(n => {
                if (n.id === nodeId) {
                    return { ...n, data: { ...n.data, logs: [...(n.data.logs || []), log] } };
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
  const animateEdgesFrom = (sourceId, active) => {
    setEdges(prev => prev.map(e => e.source === sourceId ? { ...e, data: { ...e.data, animated: active } } : e));
  };
  const resetWorkflow = () => {
    setMode('planning');
    setIsPaused(false);
    setNodes(initialNodesData);
    setEdges(initialEdgesData);
    if(websocketService.isConnected()) websocketService.sendMessage({ type: 'RESET_WORKFLOW' });
  };
  return (
    <div className="w-full h-screen font-sans flex flex-col bg-[#000000] text-white overflow-hidden">
      <div className="h-12 bg-[#151a1f] border-b border-[#000000] flex items-center justify-between px-4 z-20 shrink-0">
        <div className="flex items-center gap-4">
            <h1 className="font-bold text-[#91b9cd] text-sm tracking-wide">CUBE WORKSPACE</h1>
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
             <button 
                onClick={() => setIsChatOpen(!isChatOpen)}
                className={`ml-4 p-1.5 rounded border transition-colors ${isChatOpen ? 'bg-[#3c5d80] text-white border-[#5acce6]' : 'bg-[#151a1f] text-[#566a84] border-[#3c5d80] hover:text-white'}`}
                title="Toggle Assistant"
             >
                 <MessageSquare size={16} />
             </button>
        </div>
      </div>
      <div className="flex-1 flex overflow-hidden">
          <div className="flex-1 flex flex-col min-w-0">
              <div className="flex-1 relative overflow-hidden">
                <MiniFlow
                    nodes={nodes}
                    edges={edges}
                    onNodesChange={setNodes}
                    nodeTypes={nodeTypes}
                    edgeTypes={edgeTypes}
                    readOnly={mode !== 'planning'}
                >
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
              <AgentRegistry isOpen={isRegistryOpen} onToggle={() => setIsRegistryOpen(!isRegistryOpen)} />
          </div>
          {isChatOpen && (
              <ChatSidebar isOpen={isChatOpen} onClose={() => setIsChatOpen(false)} />
          )}
      </div>
    </div>
  );
}
