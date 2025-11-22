import React, { useEffect, useState, useCallback} from "react";
import PlaygroundCanvas from "../../components/PlaygroundCanvas";
import { MarkerType } from "@xyflow/react";

// mock for testing
const mockResponse = {
  steps: [
    { id: "s1", title: "Population query", description: "Collect raw data from multiple sources including surveys and APIs." },
    { id: "s2", title: "imaging protocols", description: "Remove duplicates and handle missing values." },
    { id: "s3", title: "segmentation ", description: "Train ML models to predict outcomes based on clean data." },
    { id: "s4", title: "Radiomics Feature", description: "Evaluate model accuracy and performance." },
    { id: "s5", title: "modelling", description: "Train ML models to predict outcomes based on clean data."},
    { id: "s6", title: "Validation", description: "Evaluate model accuracy and performance."}

  ],
};

// from json to nodes & edges
const buildGraph = (data, handleDeleteNode, editMode) => {
  if (!data?.steps || data.steps.length === 0) return { nodes: [], edges: [] };

  const startNode = {
    id: "start",
    type: "start",
    position: { x: -100, y: 100 },
    data: { label: "Start" },
  };

  const stepNodes = data.steps.map((step, index) => ({
    id: step.id,
    position: { x: index * 220, y: 100 },
    type: "agent",
    data: {
      name: step.title,
      description: step.description,
      onDelete: handleDeleteNode, 
      editable: editMode,  
    },
  }));

  const stepEdges = data.steps.slice(0, -1).map((step, index) => ({
    id: `${step.id}-${data.steps[index + 1].id}`,
    source: step.id,
    target: data.steps[index + 1].id,
    type: "custom",
    markerEnd: { type: MarkerType.ArrowClosed },
  }));

  const startEdge = {
    id: `start-${data.steps[0].id}`,
    source: "start",
    target: data.steps[0].id,
    type: "custom",
    markerEnd: { type: MarkerType.ArrowClosed },
  };

  return { nodes: [startNode, ...stepNodes], edges: [startEdge, ...stepEdges] };
};

function AgentsPlayground() {
  const [nodes, setNodes] = useState([]);
  const [edges, setEdges] = useState([]);
  const [originalData, setOriginalData] = useState(null);
  const [editMode, setEditMode] = useState(false);
  const [loading, setLoading] = useState(true);


  const fetchPlan = async () => {
    setLoading(true);
    try {
      await new Promise((resolve) => setTimeout(resolve, 500));
      const response = mockResponse; // Replace with: await fetch("/api/plan").then(res => res.json());
      setOriginalData(response);
      const { nodes, edges } = buildGraph(response, handleDeleteNode, editMode);
      setNodes(nodes);
      setEdges(edges);
    } catch (err) {
      console.error("Error fetching plan:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPlan();
  }, []);

  const handleDeleteNode = useCallback((nodeId) => {
    setOriginalData((prevData) => {
      if (!prevData) return prevData;
      
      const updatedSteps = prevData.steps.filter((step) => step.id !== nodeId);
      const newData = { steps: updatedSteps };
      
      return newData; // Just return the new data, don't rebuild here
    });
  }, []);

  // Add a separate useEffect to rebuild the graph when originalData changes
  useEffect(() => {
    if (originalData) {
      const { nodes: newNodes, edges: newEdges } = buildGraph(
        originalData,
        handleDeleteNode,
        editMode
      );
      setNodes(newNodes);
      setEdges(newEdges);
    }
  }, [originalData, editMode, handleDeleteNode]);


  // 
  const handleCancelEdit = () => {
    if (originalData) {
      const { nodes, edges } = buildGraph(originalData, handleDeleteNode, false);
      setNodes(nodes);
      setEdges(edges);
    }
    setEditMode(false);
  };
  const handleToggleEdit = () => {
    setEditMode(true);
    if (originalData) {
      // Call buildGraph outside setState for cleaner execution
      const { nodes, edges } = buildGraph(originalData, handleDeleteNode, true);
      setNodes(nodes);
      setEdges(edges);
    }
  };
  // Approved (send to backend → get updated plan) , i think if approved the planner should start working
  const handleApprove = async () => {
    try {
      await new Promise((resolve) => setTimeout(resolve, 800));
      
      // Use the current originalData (which already reflects deletions)
      // Rebuild graph with editMode = false to remove X buttons
      const { nodes: newNodes, edges: newEdges } = buildGraph(
        originalData,
        handleDeleteNode,
        false  // This removes the X buttons
      );
      
      setNodes(newNodes);
      setEdges(newEdges);
      setEditMode(false);
      
      console.log("Plan approved!");
    } catch (err) {
      console.error(err);
      alert("Failed to approve plan");
    }
  };


  if (loading) return <div className="p-10 text-lg">⏳ Loading plan...</div>;

  return (
    <div style={{ position: "relative", width: "100vw", height: "100vh" }}>
      {/* control Buttons <TODO: need better css and alignment in the page> */}
      <div
        style={{
          position: "absolute",
          top: 20,
          left: 20,
          zIndex: 10,
          display: "flex",
          gap: "10px",
        }}
      >
        {!editMode ? (
          <button
            onClick={handleToggleEdit}
            className="bg-yellow-500 text-white px-3 py-2 rounded-xl hover:bg-yellow-600"
          >
             Edit Plan
          </button>
        ) : (
          <button
            onClick={handleCancelEdit}
            className="bg-gray-500 text-white px-3 py-2 rounded-xl hover:bg-gray-600"
          >
            Cancel Edit
          </button>
        )}
        <button
          onClick={handleApprove}
          className="bg-green-600 text-white px-3 py-2 rounded-xl hover:bg-green-700"
        >
          Approve Plan
        </button>
      </div>
      <PlaygroundCanvas
        initialEdges={edges}
        initialNodes={nodes}
        onNodesChange={setNodes}
        onEdgesChange={setEdges}
        editMode={editMode} 
        onDelete={handleDeleteNode}
      />
    </div>
  );
}

export default AgentsPlayground;
