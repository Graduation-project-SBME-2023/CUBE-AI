import React, { useState } from 'react';
import { FlaskConical, MessageSquare } from 'lucide-react';
import PlaygroundCanvas from '../PlaygroundCanvas/PlaygroundCanvas';
import AgentRegistry from '../PlaygroundCanvas/AgentRegistry';
import ChatSidebar from '../PlaygroundCanvas/ChatSidebar';

interface PlaygroundContainerProps {
  hypothesis: string;
  sessionId: string | null;
  sendInitialMessage: boolean;
  includeLitReview: boolean;
}

export default function PlaygroundContainer({
  hypothesis,
  sessionId,
  sendInitialMessage,
  includeLitReview,
}: PlaygroundContainerProps) {
  const [isChatOpen, setIsChatOpen] = useState(true);
  const [isRegistryOpen, setIsRegistryOpen] = useState(true);

  return (
    <div className="flex h-screen w-full flex-col overflow-hidden bg-black font-sans text-white">
      <div className="z-20 flex h-14 shrink-0 items-center justify-between border-b border-[#1f2d40] bg-[#0b1015] px-4">
        <div className="flex items-center gap-6">
          <div className="flex items-center gap-3">
            <span className="text-xs font-bold tracking-widest text-[#5acce6]">CUBE</span>
            <span className="text-xs text-gray-500">/</span>
            <span className="text-xs font-medium text-gray-300">Workspace</span>
          </div>
          {hypothesis && (
            <>
              <div className="h-6 w-px bg-[#1f2d40]" />
              <div className="flex items-center gap-3 text-xs">
                <span className="flex items-center gap-2 font-bold uppercase tracking-wider text-[#5acce6]">
                  <FlaskConical size={14} /> Hypothesis:
                </span>
                <span className="max-w-xl truncate font-mono text-gray-300 opacity-90" title={hypothesis}>
                  {hypothesis}
                </span>
              </div>
            </>
          )}
        </div>

        <button
          onClick={() => setIsChatOpen(!isChatOpen)}
          className={`flex items-center gap-2 rounded border p-2 text-[10px] font-medium uppercase tracking-wider transition-colors ${
            isChatOpen
              ? 'border-[#5acce6] bg-[#3c5d80] text-white'
              : 'border-[#3c5d80] bg-[#151a1f] text-[#566a84] hover:text-white'
          }`}
        >
          <MessageSquare size={14} /> Assistant
        </button>
      </div>

      <div className="relative flex flex-1 overflow-hidden">
        <div className="relative min-w-0 flex-1">
          <PlaygroundCanvas
            initialHypothesis={hypothesis}
            session_id={sessionId || undefined}
            includeLitReview={includeLitReview}
          />
          <AgentRegistry isOpen={isRegistryOpen} onToggle={() => setIsRegistryOpen(!isRegistryOpen)} />
        </div>

        {isChatOpen && (
          <div className="animate-in slide-in-from-right relative z-30 border-l border-[#3c5d80] bg-[#0b1015] shadow-2xl duration-300">
            <ChatSidebar
              isOpen={isChatOpen}
              onClose={() => setIsChatOpen(false)}
              initialUserMessage={hypothesis}
              externalSessionId={sessionId}
              sendInitialMessage={sendInitialMessage}
            />
          </div>
        )}
      </div>
    </div>
  );
}
