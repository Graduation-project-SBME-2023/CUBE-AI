import React, { useState } from "react";
import PlaygroundCanvas from "../../components/PlaygroundCanvas";

const initialNodes = [
    {
        id: 'start',
        position: { x: 0, y: -200},
        type: 'start',
        data: { label: '_Start_' }
    },
    {
        id: 'n1',
        position: { x: 0, y: 0 },
        data: { name: 'Population Agent', role: 'Helper' },
        type: 'agent',
    },
    {
        id: 'n2',
        position: { x: 100, y: 100 },
        data: { name: 'Modeling Agent', role: 'Modeler' },
        type: 'agent',
    },
];

const initialEdges = [
    {
        id: 'n1-n2',
        source: 'n1',
        target: 'n2',
        label: 'Included studies',
        type: 'step'
    },
    {
        id: 'start',
        source: 'start',
        target: 'n1',
    }
]

function AgentsPlayground() {
    const [nodes, setNodes] = useState(initialNodes);
    const [edges, setEdges] = useState(initialEdges);

    // fetch from orchestrator and update edges and nodes here using state
    React.useEffect(() => {
        const timer = setTimeout(() => {
            setNodes((prev) => [
                ...prev,
                {
                    id: "new-agent",
                    position: {x: 200, y: 200 },
                    data: { name: "New Agent", role: "Simulates data fetching from backend" },
                    type: "agent",
                },
            ]);
        }, 3000);

        return () => clearTimeout(timer);
    }, []);

    return (
        <PlaygroundCanvas
            initialEdges={edges}
            initialNodes={nodes}
            onNodesChange={setNodes}
            onEdgesChange={setEdges}
        />
    )
}

export default AgentsPlayground;