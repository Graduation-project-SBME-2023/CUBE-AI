import React, { useCallback, useEffect } from "react";
import { ReactFlow, Background, Controls, useNodesState, useEdgesState, addEdge, MarkerType } from "@xyflow/react";
import { Play, RotateCcw, Terminal, Activity } from 'lucide-react';
import "@xyflow/react/dist/style.css";
import { AgentNode, StartNode } from "./customNodes";
import CustomEdge from "./CustomEdge";

const nodeTypes = {
  start: StartNode,
  agent: AgentNode,
};

const edgeTypes = {
  custom: CustomEdge,
};

function PlaygroundCanvas({
  initialNodes = [],
  initialEdges = [],
  editMode,
}) {
  const [nodes, setNodes, handleNodesChange] = useNodesState(
    initialNodes.map(n => ({
      ...n,
      data: {
        ...n.data,
        status: 'pending',
        logs: [],
        editable: editMode,
        onDelete: (id) => handleDeleteNode(id),
      }
    }))
  );
  const [edges, setEdges, handleEdgesChange] = useEdgesState(initialEdges);
  const [globalLogs, setGlobalLogs] = React.useState([]);
  const [isRunning, setIsRunning] = React.useState(false);
  const [currentNodeId, setCurrentNodeId] = React.useState(null);

  const onConnect = useCallback(
    (params) =>
      setEdges((eds) =>
        addEdge(
          {
            ...params,
            type: "custom",
            animated: true,
            markerEnd: { type: MarkerType.ArrowClosed, color: "#555" },
          },
          eds
        )
      ),
    [setEdges]
  );

  // Delete node handler
  const handleDeleteNode = useCallback((nodeId) => {
    setNodes((prevNodes) => prevNodes.filter(n => n.id !== nodeId));
    setEdges((prevEdges) => 
      prevEdges.filter(e => e.source !== nodeId && e.target !== nodeId)
    );
  }, [setNodes, setEdges]);

  // Logger Helper
  const addGlobalLog = useCallback((message, type = 'info') => {
    setGlobalLogs((prev) => [
      ...prev,
      { id: Date.now(), timestamp: new Date().toLocaleTimeString(), message, type },
    ]);
  }, []);

  // Update nodes when initialNodes change
  useEffect(() => {
    setNodes(initialNodes.map(n => ({
      ...n,
      data: {
        ...n.data,
        status: n.data?.status || 'pending',
        logs: n.data?.logs || [],
        editable: editMode,
        onDelete: (id) => handleDeleteNode(id),
      }
    })));
  }, [initialNodes, setNodes, editMode, handleDeleteNode]);

  useEffect(() => {
    setEdges(initialEdges);
  }, [initialEdges, setEdges]);

  // Workflow Execution Logic
  const runWorkflow = async () => {
    if (isRunning) return;
    setIsRunning(true);
    
    // Reset nodes
    setNodes(prev => prev.map(n => ({ 
      ...n, 
      data: { ...n.data, status: 'pending', logs: [] } 
    })));
    setGlobalLogs([]);
    
    addGlobalLog("🚀 Agent initialized. Starting workflow sequence...", 'system');

    const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));
    
    // Execute nodes in order (skip start node with id '0')
    const executionOrder = nodes.filter(n => n.id !== '0').map(n => n.id);

    for (const nodeId of executionOrder) {
      const currentNode = nodes.find(n => n.id === nodeId);
      if (!currentNode) continue;

      // Set Active
      setCurrentNodeId(nodeId);
      setNodes(prev => prev.map(n => 
        n.id === nodeId ? { ...n, data: { ...n.data, status: 'active' } } : n
      ));
      
      const nodeLabel = currentNode.data?.label || currentNode.id;
      addGlobalLog(`👉 STARTING STEP: ${nodeLabel}`, 'highlight');

      // Simulate work with logs
      const logCount = Math.floor(Math.random() * 3) + 2;
      for (let i = 0; i < logCount; i++) {
        await wait(Math.random() * 400 + 300);
        
        const logMsg = `Processing step ${i + 1}/${logCount}...`;
        setNodes(prev => prev.map(n => {
          if (n.id === nodeId) {
            return { 
              ...n, 
              data: { 
                ...n.data, 
                logs: [...(n.data?.logs || []), logMsg] 
              } 
            };
          }
          return n;
        }));

        addGlobalLog(`  > ${logMsg}`, 'detail');
      }
      
      await wait(300);

      // Set Completed
      setNodes(prev => prev.map(n => 
        n.id === nodeId ? { ...n, data: { ...n.data, status: 'completed' } } : n
      ));
      addGlobalLog(`✅ COMPLETED: ${nodeLabel}`, 'success');
      
      await wait(200);
    }

    addGlobalLog("🏁 Workflow finished successfully.", 'system');
    setCurrentNodeId(null);
    setIsRunning(false);
  };

  const reset = () => {
    setIsRunning(false);
    setCurrentNodeId(null);
    setNodes(prev => prev.map(n => ({ 
      ...n, 
      data: { ...n.data, status: 'pending', logs: [] } 
    })));
    setGlobalLogs([]);
    addGlobalLog("🔄 Workflow state reset.", 'system');
  };

  return (
    <div className="flex h-screen w-full bg-slate-50 font-sans text-slate-800">
      <div className="relative flex-1 h-full overflow-hidden border-r border-slate-200">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={handleNodesChange}
          onEdgesChange={handleEdgesChange}
          onConnect={onConnect}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          nodesDraggable={!editMode}
          fitView
        >
          <Background />
          <Controls />
        </ReactFlow>

        {/* Floating Controls */}
        <div className="absolute top-6 left-1/2 -translate-x-1/2 flex gap-3 bg-white p-2 rounded-xl shadow-lg border border-slate-100 z-10">
          <button
            onClick={runWorkflow}
            disabled={isRunning}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg font-medium transition-colors ${
              isRunning 
                ? 'bg-slate-100 text-slate-400 cursor-not-allowed' 
                : 'bg-blue-600 hover:bg-blue-700 text-white shadow-md shadow-blue-200'
            }`}
          >
            {isRunning ? <Activity size={18} className="animate-spin" /> : <Play size={18} />}
            {isRunning ? 'Agent Running...' : 'Start Workflow'}
          </button>

          <button
            onClick={reset}
            disabled={isRunning}
            className="flex items-center gap-2 px-4 py-2 bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 rounded-lg transition-colors"
          >
            <RotateCcw size={18} />
            Reset
          </button>
        </div>

        {/* Current Node Indicator */}
        {currentNodeId && (
          <div className="absolute bottom-6 left-6 bg-blue-50 border-l-4 border-blue-500 p-4 rounded-lg shadow-md">
            <div className="text-sm font-semibold text-blue-900">
              🔵 Currently Running:
            </div>
            <div className="text-lg font-bold text-blue-600 mt-1">
              {nodes.find(n => n.id === currentNodeId)?.data?.label || currentNodeId}
            </div>
          </div>
        )}
      </div>

      {/* Live Log Panel */}
      <div className="w-96 flex flex-col bg-slate-900 text-slate-300 shadow-xl z-20">
        <div className="p-4 border-b border-slate-800 bg-slate-950 flex items-center justify-between">
          <div className="flex items-center gap-2 text-sm font-semibold text-white">
            <Terminal size={16} className="text-blue-400" />
            Live Agent Logs
          </div>
          <div className="flex gap-1.5">
            <div className="w-2.5 h-2.5 rounded-full bg-red-500/20"></div>
            <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/20"></div>
            <div className="w-2.5 h-2.5 rounded-full bg-green-500/80 shadow-[0_0_8px_rgba(34,197,94,0.4)]"></div>
          </div>
        </div>
        
        <div className="flex-1 overflow-y-auto p-4 font-mono text-sm space-y-3 scrollbar-thin scrollbar-thumb-slate-700">
          {globalLogs.length === 0 && (
            <div className="text-slate-600 italic text-center mt-10">
              Waiting for workflow trigger...
            </div>
          )}
          
          {globalLogs.map((log) => (
            <div key={log.id} className="flex gap-3 animate-in fade-in slide-in-from-left-2 duration-300">
              <span className="text-slate-600 text-xs min-w-[60px] pt-0.5 select-none">{log.timestamp}</span>
              <span className={`${
                log.type === 'success' ? 'text-green-400 font-bold' :
                log.type === 'highlight' ? 'text-blue-300 font-bold' :
                log.type === 'detail' ? 'text-slate-400 pl-2 border-l border-slate-700' :
                'text-slate-300'
              }`}>
                {log.message}
              </span>
            </div>
          ))}
          <div className="h-4" /> 
        </div>

        <div className="p-2 bg-slate-950 border-t border-slate-800 text-[10px] text-slate-500 flex justify-between px-4">
          <span>STATUS: {isRunning ? 'EXECUTING' : 'IDLE'}</span>
          <span>NODES: {nodes.filter(n => n.id !== '0').length}</span>
        </div>
      </div>
    </div>
  );
}

export default PlaygroundCanvas;
