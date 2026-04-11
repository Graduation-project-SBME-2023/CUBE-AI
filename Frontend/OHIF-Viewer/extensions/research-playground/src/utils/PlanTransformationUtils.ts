/**
 * Shared utilities for transforming plan data to flow nodes and edges
 * Used by both PlaygroundCanvas and AgentsPlayground components
 */

export interface FlowNode {
  id: string;
  type: 'start' | 'agent';
  position: { x: number; y: number };
  data: any;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  type: string;
}

export interface GraphTransformResult {
  nodes: FlowNode[];
  edges: FlowEdge[];
}

/**
 * Transform backend plan structure to flow nodes and edges
 * Supports both formats:
 * - Format 1: {hypothesis_id, study_goal, steps: [{name, description, status, matched_module}]}
 * - Format 2: {steps: [{id, name, description}]}
 */
export const transformPlanToFlow = (planData: any): GraphTransformResult => {
  if (!planData?.steps || planData.steps.length === 0) {
    return { nodes: [], edges: [] };
  }

  const nodes: FlowNode[] = [
    {
      id: 'start',
      type: 'start',
      position: { x: 50, y: 50 },
      data: {
        title: 'Start',
        description: planData.study_goal || 'Study workflow'
      }
    }
  ];

  // Create step nodes with alternating vertical positions
  planData.steps.forEach((step: any, index: number) => {
    const baseX = 50;
    const spacingX = 100;
    const baseY = 180;
    const offsetY = 40;
    const y = baseY + (index % 2 === 0 ? -offsetY : offsetY);

    nodes.push({
      id: String(index + 1),
      type: 'agent',
      position: { x: baseX + index * spacingX, y },
      data: {
        title: step.name || `Step ${index + 1}`,
        description: step.description || '',
        status: step.status || 'pending',
        logs: [],
        isExpanded: false,
        matched_module: step.matched_module
      }
    });
  });

  // Create sequential edges
  const edges: FlowEdge[] = [];
  for (let i = 0; i < nodes.length - 1; i++) {
    edges.push({
      id: `e${nodes[i].id}-${nodes[i + 1].id}`,
      source: nodes[i].id,
      target: nodes[i + 1].id,
      type: 'custom'
    });
  }

  return { nodes, edges };
};

/**
 * Transform plan to flow with custom handlers for AgentsPlayground style
 * Uses step IDs instead of indices for node IDs
 */
export const transformPlanToFlowLegacy = (
  planData: any,
  handleDeleteNode?: (id: string) => void,
  editMode: boolean = false
): GraphTransformResult => {
  if (!planData?.steps || planData.steps.length === 0) {
    return { nodes: [], edges: [] };
  }

  const startNode: FlowNode = {
    id: 'start',
    type: 'start',
    position: { x: -100, y: 100 },
    data: { title: 'Start' }
  };

  const stepNodes: FlowNode[] = planData.steps.map((step: any, index: number) => ({
    id: step.id,
    position: { x: index * 220, y: 100 },
    type: 'agent',
    data: {
      name: step.name,
      description: step.description,
      onDelete: handleDeleteNode,
      editable: editMode
    }
  }));

  const stepEdges: FlowEdge[] = planData.steps.slice(0, -1).map((step: any, index: number) => ({
    id: `${step.id}-${planData.steps[index + 1].id}`,
    source: step.id,
    target: planData.steps[index + 1].id,
    type: 'custom'
  }));

  const startEdge: FlowEdge = {
    id: `start-${planData.steps[0].id}`,
    source: 'start',
    target: planData.steps[0].id,
    type: 'custom'
  };

  return { nodes: [startNode, ...stepNodes], edges: [startEdge, ...stepEdges] };
};
