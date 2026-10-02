// `node_status` carries the per-node execution status. Before Phase 6.1 the
// same event was called `pipeline_agent_status`; sessions recorded before the
// rename still replay it from session_events, whose historical rows are never
// rewritten, so a replay consumer accepts both names.
const LEGACY_NODE_STATUS_EVENT_TYPES = ['pipeline_agent_status', 'Pipeline_agent_status'];

export const NODE_STATUS_EVENT_TYPE = 'node_status';

export function isNodeStatusEvent(type: unknown): boolean {
  return type === NODE_STATUS_EVENT_TYPE || LEGACY_NODE_STATUS_EVENT_TYPES.includes(String(type));
}
