import React, { useCallback, useEffect } from "react";
import { ReactFlow, Background, Controls, useNodesState, useEdgesState, addEdge, MarkerType } from "@xyflow/react";
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
  const [nodes, setNodes, handleNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, handleEdgesChange] = useEdgesState(initialEdges);

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

  // propagate editMode and delete handler into node data
    useEffect(() => {
    setNodes(initialNodes);
    }, [initialNodes, setNodes]);

    useEffect(() => {
    setEdges(initialEdges);
    }, [initialEdges, setEdges]);

//   useEffect(() => {
//     if (onNodesChange) onNodesChange(nodes);
//   }, [nodes]);

//   useEffect(() => {
//     if (onEdgesChange) onEdgesChange(edges);
//   }, [edges]);

  useEffect(() => {
    setNodes(initialNodes);
  }, [initialNodes]);

  useEffect(() => {
    setEdges(initialEdges);
  }, [initialEdges]);

  return (
    <div style={{ width: "100vw", height: "100vh" }}>
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
    </div>
  );
}

export default PlaygroundCanvas;
