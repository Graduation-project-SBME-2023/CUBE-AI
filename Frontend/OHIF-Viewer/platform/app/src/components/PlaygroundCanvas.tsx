import React, { useCallback, useEffect, useState } from "react";
import { ReactFlow, Background, Controls, useNodesState, useEdgesState, addEdge, MarkerType } from "@xyflow/react";
import { Play, Pause, RotateCcw } from "lucide-react";
import "@xyflow/react/dist/style.css";
import { AgentNode, StartNode } from "./customNodes";
import CustomEdge from "./CustomEdge";

// The flow will be: We have three modes (planning mode - executing mode - results mode)
// In planning mode: step represent a node and each step has an id and title and description appears when hovering over it, the steps are shown and user can delete or edit them if he needs (editting will be just rewriting the description - discussion with the chatbot)
// Once planning finished the user now can start execution, each step had an id and title and description, the description still shown when hovering
// while execution each step will have status and logs-messages, the current running step show loading glowing border and loading icon 
// when we click on the current step it shows the logs-messages in the node (it is treated as we expanded it) max the last 5 log messages appears
// once the step is completed its color turns into green and check appears then the following node starts
// the user has the ability to pause while running and continue later.

const nodeTypes = {
  start: StartNode,
  agent: AgentNode,
};

const edgeTypes = {
  custom: CustomEdge,
};

// Mode: 'planning' | 'executing' | 'results'
function PlaygroundCanvas({
  initialNodes = [],
  initialEdges = [],
  editMode = true,
  mode = 'planning', // planning, executing, results
  onModeChange,
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
        onEdit: (id, description) => handleEditNode(id, description),
      }
    }))
  );
  const [edges, setEdges, handleEdgesChange] = useEdgesState(initialEdges);
  const [currentMode, setCurrentMode] = useState(mode);
  const [isExecuting, setIsExecuting] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [currentNodeId, setCurrentNodeId] = useState(null);
  const [expandedNodeId, setExpandedNodeId] = useState(null);

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

  // Edit node handler
  const handleEditNode = useCallback((nodeId, description) => {
    setNodes((prevNodes) =>
      prevNodes.map((n) =>
        n.id === nodeId
          ? { ...n, data: { ...n.data, description } }
          : n
      )
    );
  }, [setNodes]);

  // Update nodes when initialNodes change
  useEffect(() => {
    setNodes(initialNodes.map(n => ({
      ...n,
      data: {
        ...n.data,
        status: n.data?.status || 'pending',
        logs: n.data?.logs || [],
        editable: editMode && currentMode === 'planning',
        onDelete: (id) => handleDeleteNode(id),
        onEdit: (id, description) => handleEditNode(id, description),
      }
    })));
  }, [initialNodes, setNodes, editMode, currentMode, handleDeleteNode, handleEditNode]);

  useEffect(() => {
    setEdges(initialEdges);
  }, [initialEdges, setEdges]);

  // Update mode
  useEffect(() => {
    setCurrentMode(mode);
  }, [mode]);

  // Execution workflow
  const startExecution = async () => {
    if (isExecuting) return;
    setIsExecuting(true);
    setCurrentMode('executing');
    
    const executionOrder = nodes.filter(n => n.id !== '0').map(n => n.id);
    const wait = (ms) => new Promise(resolve => setTimeout(resolve, ms));

    for (const nodeId of executionOrder) {
      if (!isExecuting && !isPaused) break; // Stop if execution ended
      
      // Wait if paused
      while (isPaused && isExecuting) {
        await wait(500);
      }

      setCurrentNodeId(nodeId);
      setExpandedNodeId(nodeId);

      // Set Active
      setNodes(prev => prev.map(n => 
        n.id === nodeId ? { ...n, data: { ...n.data, status: 'active' } } : n
      ));

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
      }
      
      await wait(300);

      // Set Completed
      setNodes(prev => prev.map(n => 
        n.id === nodeId ? { ...n, data: { ...n.data, status: 'completed' } } : n
      ));
      
      await wait(200);
    }

    setCurrentNodeId(null);
    setIsExecuting(false);
    setCurrentMode('results');
  };

  const pauseExecution = () => {
    setIsPaused(!isPaused);
  };

  const resetExecution = () => {
    setIsExecuting(false);
    setIsPaused(false);
    setCurrentNodeId(null);
    setExpandedNodeId(null);
    setCurrentMode('planning');
    setNodes(prev => prev.map(n => ({ 
      ...n, 
      data: { ...n.data, status: 'pending', logs: [] } 
    })));
  };

  return (
    <div style={{ width: "100vw", height: "100vh", position: 'relative' }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onConnect={onConnect}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={currentMode === 'planning'}
        fitView
      >
        <Background />
        <Controls />
      </ReactFlow>

      {/* Control Panel */}
      <div 
        style={{
          position: 'absolute',
          top: 20,
          left: 20,
          background: 'white',
          padding: '16px',
          borderRadius: '8px',
          boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
          zIndex: 100,
          display: 'flex',
          gap: '8px',
          alignItems: 'center',
        }}
      >
        <span style={{ fontWeight: 'bold', marginRight: '16px', textTransform: 'capitalize' }}>
          Mode: {currentMode}
        </span>

        {currentMode === 'planning' && (
          <button
            onClick={startExecution}
            style={{
              background: '#3b82f6',
              color: 'white',
              padding: '8px 16px',
              borderRadius: '4px',
              border: 'none',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <Play size={16} /> Start Execution
          </button>
        )}

        {currentMode === 'executing' && (
          <>
            <button
              onClick={pauseExecution}
              style={{
                background: isPaused ? '#f59e0b' : '#ef4444',
                color: 'white',
                padding: '8px 16px',
                borderRadius: '4px',
                border: 'none',
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
              }}
            >
              <Pause size={16} /> {isPaused ? 'Resume' : 'Pause'}
            </button>
          </>
        )}

        {(currentMode === 'executing' || currentMode === 'results') && (
          <button
            onClick={resetExecution}
            style={{
              background: '#6b7280',
              color: 'white',
              padding: '8px 16px',
              borderRadius: '4px',
              border: 'none',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            <RotateCcw size={16} /> Reset
          </button>
        )}
      </div>

      {/* Current Step Indicator */}
      {currentMode === 'executing' && currentNodeId && (
        <div
          style={{
            position: 'absolute',
            bottom: 20,
            left: 20,
            background: '#eff6ff',
            border: '2px solid #3b82f6',
            padding: '12px 16px',
            borderRadius: '8px',
            zIndex: 100,
          }}
        >
          <div style={{ fontSize: '12px', color: '#1e40af', fontWeight: 'bold' }}>
            🔵 Currently Executing:
          </div>
          <div style={{ fontSize: '14px', color: '#1d4ed8', fontWeight: 'bold', marginTop: '4px' }}>
            {nodes.find(n => n.id === currentNodeId)?.data?.name || currentNodeId}
          </div>
        </div>
      )}

      {/* Status Legend */}
      {currentMode !== 'planning' && (
        <div
          style={{
            position: 'absolute',
            bottom: 20,
            right: 20,
            background: 'white',
            padding: '12px 16px',
            borderRadius: '8px',
            boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
            zIndex: 100,
            fontSize: '12px',
          }}
        >
          <div style={{ marginBottom: '8px', fontWeight: 'bold' }}>Status Legend:</div>
          <div style={{ display: 'flex', gap: '16px', flexDirection: 'column' }}>
            <div>⚪ Pending</div>
            <div>🔵 Active (Running)</div>
            <div>✅ Completed</div>
          </div>
        </div>
      )}
    </div>
  );
}

export default PlaygroundCanvas;
