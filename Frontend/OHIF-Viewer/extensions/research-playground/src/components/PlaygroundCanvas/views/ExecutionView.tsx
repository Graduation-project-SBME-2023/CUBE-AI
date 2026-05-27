import React, { useEffect, useRef } from 'react';
import { Activity, Loader2 } from 'lucide-react';
import { ChatBubble } from './execution/Shared';
import { useExecutionMessages } from './execution/utils';

export default function ExecutionView({ sessionId }: { sessionId: string | number | null }) {
  const { messages, isFinished } = useExecutionMessages();
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  return (
    <div className="w-full h-full relative overflow-hidden bg-[#000000] flex flex-col">
      <div className="absolute top-0 left-0 right-0 z-10 pointer-events-none bg-gradient-to-b from-[#0a1520]/90 to-transparent px-4 py-2">
        <p className="text-[11px] uppercase tracking-widest text-[#5a8aaa] font-semibold">Live Agent Execution</p>
      </div>
      
      <div className="flex-1 overflow-y-auto px-6 pb-20 pt-12 relative" ref={scrollRef}>
        <div className="max-w-4xl mx-auto">
          {messages.length === 0 ? (
            <div className="h-full mt-32 flex flex-col items-center justify-center text-center opacity-50">
              <Activity size={48} className="text-[#3c5d80] mb-4 animate-pulse" />
              <h3 className="text-[#91b9cd] font-semibold tracking-wider uppercase mb-1">Waiting for transmission</h3>
              <p className="text-sm text-[#566a84]">Agent group chat will appear here once agents begin executing tasks</p>
            </div>
          ) : (
            <div className="space-y-2">
              {messages.map((msg, i) => (
                <ChatBubble key={`${msg.timestamp}-${i}`} message={msg} />
              ))}
              {!isFinished && (
                <div className="flex items-center gap-3 text-[#5a8aaa] font-medium text-sm animate-pulse pt-4">
                  <Loader2 size={16} className="animate-spin" />
                  <span>Agents are evaluating and executing...</span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
