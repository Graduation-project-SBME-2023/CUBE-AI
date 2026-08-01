import React, { useEffect, useMemo, useState } from 'react';
import { AlertCircle, Edit3, MousePointer2, ZoomIn } from 'lucide-react';
import MiniFlow from './miniflow';
import StartNode from './nodes/StartNode';
import AgentNode from './nodes/AgentNode';
import CustomEdge from './edges/CustomEdges';
import { FlowEdge, FlowNode } from './types';

const nodeTypes = { start: StartNode, agent: AgentNode };
const edgeTypes = { custom: CustomEdge };

interface PromptState {
  type: string;
  message: string;
}

interface FlowEngineWrapperProps {
  mode: 'planning' | 'executing' | 'results';
  nodes: FlowNode[];
  edges: FlowEdge[];
  fitViewTrigger?: number;
  pendingPrompt: PromptState | null;
  onNodesChange: (nodes: FlowNode[]) => void;
  onDrop: (event: React.DragEvent) => void;
  onDragOver: (event: React.DragEvent) => void;
  onPromptSubmit: (payload: string) => void;
  onPromptClose: () => void;
  onReset: () => void;
}

export default function FlowEngineWrapper({
  mode,
  nodes,
  edges,
  fitViewTrigger,
  pendingPrompt,
  onNodesChange,
  onDrop,
  onDragOver,
  onPromptSubmit,
  onPromptClose,
  onReset,
}: FlowEngineWrapperProps) {
  const [promptExpanded, setPromptExpanded] = useState(true);

  const nodesWithHandlers = useMemo(() => nodes, [nodes]);

  useEffect(() => {
    if (!pendingPrompt) {
      return;
    }
    setPromptExpanded(true);
  }, [pendingPrompt]);

  return (
    <div className="relative flex-1 overflow-hidden" onDrop={onDrop} onDragOver={onDragOver}>
      <MiniFlow
        nodes={nodesWithHandlers}
        edges={edges}
        fitViewTrigger={fitViewTrigger}
        onNodesChange={onNodesChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        readOnly={mode !== 'planning'}
      >


        {pendingPrompt && (
          <div className="animate-in slide-in-from-bottom-2 pointer-events-auto absolute bottom-6 right-6 z-50 duration-200">
            <div className="w-72 overflow-hidden rounded-lg border border-[#5acce6]/60 bg-[#151a1f] shadow-[0_0_20px_rgba(90,204,230,0.15)]">
              <button
                onClick={() => setPromptExpanded(prev => !prev)}
                className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-[#1f2630]"
              >
                <AlertCircle size={14} className="shrink-0 animate-pulse text-[#f8ce46]" />
                <span className="flex-1 text-xs font-bold text-white">
                  {pendingPrompt.type === 'hypothesis_approval'
                    ? 'Approve Hypothesis?'
                    : 'Run Literature Review?'}
                </span>
              </button>

              {promptExpanded && (
                <div className="space-y-3 border-t border-[#3c5d80]/50 px-3 pb-3 pt-2">
                  {pendingPrompt.message && <p className="text-[11px] leading-relaxed text-[#91b9cd]">{pendingPrompt.message}</p>}
                  <div className="flex gap-2">
                    {pendingPrompt.type === 'hypothesis_approval' ? (
                      <>
                        <button
                          onClick={() => {
                            onPromptSubmit('APPROVE');
                            onPromptClose();
                          }}
                          className="flex-1 rounded bg-[#6fe5b8] py-1.5 text-xs font-bold text-black hover:bg-[#91eac8]"
                        >
                          Approve
                        </button>
                        <button
                          onClick={() => {
                            const feedback = window.prompt('Describe your changes:');
                            if (feedback) {
                               onPromptSubmit(feedback);
                            }
                            onPromptClose();
                          }}
                          className="flex-1 rounded border border-[#3c5d80] bg-[#1f2d40] py-1.5 text-xs font-medium text-[#91b9cd] hover:bg-[#2a3a4f]"
                        >
                          Revise
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          onClick={() => {
                            onPromptSubmit('YES');
                            onPromptClose();
                          }}
                          className="flex-1 rounded bg-[#348cfd] py-1.5 text-xs font-bold text-white hover:bg-[#599fff]"
                        >
                          Yes, Build Knowledge
                        </button>
                        <button
                          onClick={() => {
                            onPromptSubmit('NO');
                            onPromptClose();
                          }}
                          className="flex-1 rounded border border-[#3c5d80] bg-[#1f2d40] py-1.5 text-xs font-medium text-[#91b9cd] hover:bg-[#2a3a4f]"
                        >
                          Skip
                        </button>
                      </>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}


      </MiniFlow>
    </div>
  );
}
