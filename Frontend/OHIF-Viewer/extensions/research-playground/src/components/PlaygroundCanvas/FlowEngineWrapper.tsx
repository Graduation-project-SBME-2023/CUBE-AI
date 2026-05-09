import React, { useEffect, useMemo, useState } from 'react';
import { AlertCircle, CheckCircle2, Edit3, MousePointer2, ZoomIn } from 'lucide-react';
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
        onNodesChange={onNodesChange}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        readOnly={mode !== 'planning'}
      >
        <div className="pointer-events-none absolute bottom-24 left-6">
          <div className="space-y-2 rounded border border-[#3c5d80] bg-[#151a1f]/90 p-3 text-[10px] backdrop-blur">
            <div className="mb-1 font-bold uppercase text-[#91b9cd]">{mode === 'planning' ? 'Planning Tools' : 'Monitor Status'}</div>
            {mode === 'planning' ? (
              <>
                <div className="flex items-center gap-2 text-[#dadada]"><MousePointer2 size={10} /> Drag nodes & canvas</div>
                <div className="flex items-center gap-2 text-[#dadada]"><Edit3 size={10} /> Edit / Delete</div>
                <div className="flex items-center gap-2 text-[#dadada]"><ZoomIn size={10} /> Ctrl+Scroll to zoom</div>
              </>
            ) : (
              <>
                <div className="flex items-center gap-2 text-[#dadada]"><AlertCircle size={10} className="text-[#5acce6]" /> Active</div>
                <div className="flex items-center gap-2 text-[#dadada]"><CheckCircle2 size={10} className="text-[#6fe5b8]" /> Complete</div>
              </>
            )}
          </div>
        </div>

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
                    : pendingPrompt.type === 'plan_approval'
                      ? 'Approve Pipeline Plan?'
                      : 'Run Literature Review?'}
                </span>
              </button>

              {promptExpanded && (
                <div className="space-y-3 border-t border-[#3c5d80]/50 px-3 pb-3 pt-2">
                  {pendingPrompt.message && <p className="text-[11px] leading-relaxed text-[#91b9cd]">{pendingPrompt.message}</p>}
                  <div className="flex gap-2">
                    {pendingPrompt.type === 'hypothesis_approval' || pendingPrompt.type === 'plan_approval' ? (
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

        {mode === 'results' && (
          <div className="animate-in fade-in absolute inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-[2px] duration-500">
            <div className="max-w-md space-y-4 rounded border border-[#3c5d80] bg-[#151a1f] p-8 text-center shadow-2xl">
              <div className="mx-auto mb-2 flex h-12 w-12 items-center justify-center rounded-full border border-[#6fe5b8] bg-[#06422d]">
                <CheckCircle2 size={24} className="text-[#6fe5b8]" />
              </div>
              <h2 className="text-xl font-bold text-white">Workflow Complete</h2>
              <button
                onClick={onReset}
                className="w-full rounded bg-[#348cfd] py-2 text-sm font-medium text-white transition-colors hover:bg-[#599fff]"
              >
                Return to Worklist
              </button>
            </div>
          </div>
        )}
      </MiniFlow>
    </div>
  );
}
