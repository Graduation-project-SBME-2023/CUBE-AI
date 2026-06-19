export interface ScratchpadMessage {
  type: string;
  agent_id: string;
  role: string;
  content: string;
  timestamp: string;
  status?: string;
  level?: string;
  previous_status?: string;
}
