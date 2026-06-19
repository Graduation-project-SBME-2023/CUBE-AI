import React from 'react';

export interface StartNodeData {
  title: string;
  description: string;
  // PICO hypothesis fields
  population?: string;
  intervention?: string;
  comparison?: string;
  outcome?: string;
  hypothesis?: string;
  question?: string;
  clinical_context?: string;
  state?: string;
}

export interface AgentNodeData {
  title: string;
  name?: string; // fallback for title
  description: string;
  status: 'pending' | 'active' | 'completed' | string;
  logs: ToolLog[];
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
  fitViewTrigger?: number;
}

export type EventType =
  | 'tool_started'
  | 'tool_progress'
  | 'tool_log'
  | 'tool_finished'
  | 'run_started'
  | 'run_finished'
  | 'error'

export type ContentType =
  | 'markdown'
  | 'progress'
  | 'text'

  export interface MarkdownPayload {
  markdown: string
}

export interface ProgressPayload {
  current: number
  total: number
  label?: string
}

export interface TextPayload {
  message: string
}

export type ToolContent =
  | {
      content_type: 'markdown'
      content: MarkdownPayload
    }
  | {
      content_type: 'progress'
      content: ProgressPayload
    }
  | {
      content_type: 'text'
      content: TextPayload
    }

export type ToolEvent = ToolContent & {
  event_type: EventType
  source: string
  timestamp: string
}

export interface ToolLog {
  source: string
  started_at: string
  last_updated_at: string
  events: ToolEvent[]
  status: 'active' | 'completed' | 'error'
}
