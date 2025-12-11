import React from 'react';

export interface StartNodeData {
  title: string;
  description: string;
}

export interface AgentNodeData {
  title: string;
  name?: string; // fallback for title
  description: string;
  status: 'pending' | 'active' | 'completed' | string;
  logs: string[];
  isExpanded: boolean;
  matched_module?: string;
  mode?: string;
  
  // Callbacks
  onDelete?: (id: string) => void;
  onEdit?: (id: string) => void;
  onToggleExpand?: (id: string) => void;
}

export interface FlowNode {
  id: string;
  type: 'start' | 'agent';
  position: { x: number; y: number };
  data: StartNodeData | AgentNodeData;
}

export interface FlowEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  sourceHandleX?: number;
  data?: {
    animated?: boolean;
    [key: string]: any;
  };
}

export interface StartNodeProps {
  data: StartNodeData;
  nodeWidth?: number;
  nodeHeight?: number;
}

export interface AgentNodeProps {
  data: AgentNodeData;
  id: string;
}

export interface MiniFlowProps {
  nodes: FlowNode[];
  edges: FlowEdge[];
  onNodesChange: (nodes: FlowNode[]) => void;
  nodeTypes: Record<string, React.ComponentType<any>>;
  edgeTypes: Record<string, React.ComponentType<any>>;
  children?: React.ReactNode;
  readOnly?: boolean;
}