import React, { useEffect, useState, useRef } from 'react';
import { User, AlertTriangle, FileText, Activity, AlertCircle, Info, CheckCircle2, Loader2 } from 'lucide-react';
import websocketService from '../../../services/websocket';

interface ScratchpadMessage {
  type: string;
  agent_id: string;
  role: string;
  content: string;
  timestamp: string;
  status?: string;
  level?: string;
  previous_status?: string;
}

const AgentAvatar = ({ role, type }: { role: string; type: string }) => {
  let bgColor = 'bg-blue-500';
  let Icon = User;

  if (type === 'agent_flag') {
    bgColor = 'bg-orange-500';
    Icon = AlertTriangle;
  } else if (type === 'agent_summary') {
    bgColor = 'bg-emerald-500';
    Icon = CheckCircle2;
  } else if (type === 'agent_status') {
    bgColor = 'bg-purple-500';
    Icon = Activity;
  } else {
    // Note or Registered
    bgColor = 'bg-blue-500';
    Icon = FileText;
  }

  return (
    <div className={`w-8 h-8 rounded-full flex items-center justify-center text-white ${bgColor} flex-shrink-0 shadow-md border border-white/10`}>
      <Icon size={14} />
    </div>
  );
};

const ChatBubble = ({ message }: { message: ScratchpadMessage }) => {
  const isFlag = message.type === 'agent_flag';
  const isSummary = message.type === 'agent_summary';
  const isStatus = message.type === 'agent_status';
  const isRegistration = message.type === 'agent_registered';

  let bubbleClass = 'bg-[#151a1f] border-[#3c5d80] text-gray-200'; // Note (Default)
  let indicator = null;

  if (isFlag) {
    if (message.level === 'critical' || message.level === 'error') {
      bubbleClass = 'bg-[#2a1313] border-red-500/50 text-red-100';
      indicator = <AlertCircle size={14} className="text-red-400 mr-1" />;
    } else if (message.level === 'warning') {
      bubbleClass = 'bg-[#2a2413] border-orange-500/50 text-orange-100';
      indicator = <AlertTriangle size={14} className="text-orange-400 mr-1" />;
    } else {
      bubbleClass = 'bg-[#132a2a] border-blue-500/50 text-blue-100';
      indicator = <Info size={14} className="text-blue-400 mr-1" />;
    }
  } else if (isSummary) {
    bubbleClass = 'bg-[#0f241a] border-emerald-500/50 text-emerald-100';
    indicator = <CheckCircle2 size={14} className="text-emerald-400 mr-1" />;
  } else if (isStatus) {
    bubbleClass = 'bg-[#1a132a] border-purple-500/50 text-purple-100';
    indicator = <Activity size={14} className="text-purple-400 mr-1" />;
  } else if (isRegistration) {
    bubbleClass = 'bg-[#13202a] border-cyan-500/50 text-cyan-100';
  }

  const timeString = new Date(message.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  return (
    <div className={`flex gap-3 mb-4 w-full animate-in fade-in slide-in-from-bottom-2 duration-300`}>
      <AgentAvatar role={message.role} type={message.type} />
      <div className={`flex-1 max-w-[85%] rounded-xl p-3 shadow-md border ${bubbleClass}`}>
        <div className="flex items-center justify-between mb-1.5 border-b border-white/10 pb-1.5">
          <div className="flex items-center gap-2">
            <span className="font-bold text-sm text-white">{message.agent_id}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-black/30 font-medium uppercase tracking-wider">{message.role}</span>
            {isStatus && message.previous_status && message.status && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-black/30 font-medium tracking-wider text-purple-300">
                    {message.previous_status} → {message.status}
                </span>
            )}
          </div>
          <span className="text-xs opacity-60 font-mono tracking-wider">{timeString}</span>
        </div>
        <div className="text-sm leading-relaxed whitespace-pre-wrap flex items-start mt-1">
          {indicator}
          <span className="flex-1">{message.content}</span>
        </div>
      </div>
    </div>
  );
};

export default function ExecutionView({ sessionId }: { sessionId: string | number | null }) {
  const [messages, setMessages] = useState<ScratchpadMessage[]>([]);
  const [isFinished, setIsFinished] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleWSMessage = (data: any) => {
      const { type, payload } = data;
      
      if (type === 'run_finished') {
        setIsFinished(true);
        return;
      }
      
      // Scratchpad updates are wrapped in scratchpad_update
      if (type === 'scratchpad_update') {
        const msg = payload as ScratchpadMessage;
        setMessages(prev => [...prev, msg]);
      }
      
      // Support granular types if they come directly
      if (['agent_registered', 'agent_note', 'agent_flag', 'agent_status', 'agent_summary'].includes(type)) {
        const msg = payload as ScratchpadMessage;
        // Avoid duplicates if both are sent
        msg.type = type;
        setMessages(prev => {
            // Very simple dedup based on timestamp and content length
            const isDup = prev.some(p => p.timestamp === msg.timestamp && p.content === msg.content);
            if (isDup) return prev;
            return [...prev, msg];
        });
      }
    };

    const unsubscribe = websocketService.subscribe(handleWSMessage);
    return () => unsubscribe();
  }, []);

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
