import React, { useState, useRef, useEffect, memo } from 'react';
import websocketService from '../../utils/WebsocketService';
import {
  Play,
  Pause,
  RotateCcw,
  MessageSquare,
  FlaskConical,
  CheckCircle2,
  Activity,
  CheckCircle2 as CheckIcon,
  Circle,
  Edit3,
  Trash2,
  ChevronUp,
  ChevronDown,
  FileText,
} from 'lucide-react';
import { useLocation } from 'react-router-dom';

// Components
import PlaygroundCanvas from '../../components/PlaygroundCanvas/PlaygroundCanvas';
import AgentRegistry from '../../components/PlaygroundCanvas/AgentRegistry';
import ChatSidebar from '../../components/PlaygroundCanvas/ChatSidebar';

// Icon aliases for lucide-react
const PlayIcon: any = Play;
const PauseIcon: any = Pause;
const RotateCcwIcon: any = RotateCcw;
const MessageSquareIcon: any = MessageSquare;
const CheckCircle2Icon: any = CheckCircle2;
const ActivityIcon: any = Activity;
const CheckIconAlias: any = CheckIcon;
const CircleIcon: any = Circle;
const Edit3Icon: any = Edit3;
const Trash2Icon: any = Trash2;
const ChevronUpIcon: any = ChevronUp;
const ChevronDownIcon: any = ChevronDown;
const FileTextIcon: any = FileText;

export default function AgentsPlayground() {
  // --- APP LEVEL STATE ---
  const location = useLocation();
  const [isChatOpen, setIsChatOpen] = useState(true);

  // 1. Add state for the Registry Deck
  const [isRegistryOpen, setIsRegistryOpen] = useState(true);

  const hypothesis = location.state?.hypothesis || '';
  const sessionId = location.state?.sessionId || null;
  const sendInitialMessage = location.state?.sendInitialMessage || false;

  return (
    <div className="flex h-screen w-full flex-col overflow-hidden bg-[#000000] font-sans text-white">
      {/* --- THE ONLY HEADER --- */}
      <div className="z-20 flex h-14 shrink-0 items-center justify-between border-b border-[#1f2d40] bg-[#0b1015] px-4">
        <div className="flex items-center gap-6">
          {/* Breadcrumbs */}
          <div className="flex items-center gap-3">
            <span className="text-xs font-bold tracking-widest text-[#5acce6]">CUBE</span>
            <span className="text-xs text-gray-500">/</span>
            <span className="text-xs font-medium text-gray-300">Workspace</span>
          </div>
          {/* Hypothesis Context */}
          {hypothesis && (
            <>
              <div className="h-6 w-px bg-[#1f2d40]" />
              <div className="flex items-center gap-3 text-xs">
                <span className="flex items-center gap-2 font-bold uppercase tracking-wider text-[#5acce6]">
                  <FlaskConical size={14} /> Hypothesis:
                </span>
                <span
                  className="max-w-xl truncate font-mono text-gray-300 opacity-90"
                  title={hypothesis}
                >
                  {hypothesis}
                </span>
              </div>
            </>
          )}
        </div>

        {/* Right Actions */}
        <button
          onClick={() => setIsChatOpen(!isChatOpen)}
          className={`flex items-center gap-2 rounded border p-2 text-[10px] font-medium uppercase tracking-wider transition-colors
                    ${isChatOpen ? 'border-[#5acce6] bg-[#3c5d80] text-white' : 'border-[#3c5d80] bg-[#151a1f] text-[#566a84] hover:text-white'}`}
        >
          <MessageSquare size={14} /> Assistant
        </button>
      </div>

      {/* --- MAIN WORKSPACE --- */}
      <div className="relative flex flex-1 overflow-hidden">
        {/* A. The Workflow Canvas */}
        <div className="relative min-w-0 flex-1">
          <PlaygroundCanvas
            initialHypothesis={hypothesis}
            session_id={sessionId}
          />

          {/* B. Agent Registry (Floating Deck) */}
          {/* FIX: Connect state here */}
          <AgentRegistry
            isOpen={isRegistryOpen}
            onToggle={() => setIsRegistryOpen(!isRegistryOpen)}
          />
        </div>

        {/* C. Chat Sidebar */}
        {isChatOpen && (
          <div className="animate-in slide-in-from-right relative z-30 w-[350px] border-l border-[#3c5d80] bg-[#0b1015] shadow-2xl duration-300">
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
