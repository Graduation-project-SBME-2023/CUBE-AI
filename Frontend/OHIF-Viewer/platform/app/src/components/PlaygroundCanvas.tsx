import React, { useCallback, useEffect } from 'react';
import { useParams } from 'react-router';
import { ReactFlow, Background, Controls, useNodesState, useEdgesState, addEdge } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { AgentNode, StartNode } from './customNodes';

const nodeTypes = {
    start: StartNode,
    agent: AgentNode,
}

function PlaygroundCanvas({ initialNodes = [], initialEdges = [], onNodesChange, onEdgesChange}) {
    const { chat_id } = useParams();
    
    const [nodes, setNodes, handleNodesChange] = useNodesState(initialNodes);
    const [edges, setEdges, handleEdgesChange] = useEdgesState(initialEdges);

    const onConnect = useCallback(
        (params) => setEdges((eds) => addEdge({ ...params, animated: true}, eds)),
        [setEdges]
    )

    // Sync back to parent when nodes/edges change
    useEffect(() => {
        if (onNodesChange) onNodesChange(nodes);
    }, [nodes]);

    useEffect(() => {
        if (onEdgesChange) onEdgesChange(edges);
    }, [edges]);

    // Sync from patient to child when parent fetches nodes/edges
    useEffect(() => {
        setNodes(initialNodes);
    }, [initialNodes, setNodes]);

    useEffect(() => {
        setEdges(initialEdges);
    }, [initialEdges, setEdges]);

    return (
        <div style={{ width: '100vw', height: '100vh' }}>
            <ReactFlow 
                nodes={nodes}
                edges={edges}
                onNodesChange={handleNodesChange}
                onEdgesChange={handleEdgesChange}
                onConnect={onConnect}
                nodeTypes={nodeTypes}
                nodesDraggable={true}
                fitView
            >
                <Background />
                <Controls />
            </ReactFlow>
        </div>
    )
}

export default PlaygroundCanvas;
