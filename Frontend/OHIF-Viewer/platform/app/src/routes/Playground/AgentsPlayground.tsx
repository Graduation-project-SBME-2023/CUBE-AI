import React, { useState, useRef, useEffect, memo } from 'react';
import websocketService from '../../utils/WebsocketService';
import { Play, Pause, RotateCcw, MessageSquare, FlaskConical, CheckCircle2, Activity, CheckCircle2 as CheckIcon, Circle, Edit3, Trash2, ChevronUp, ChevronDown, FileText } from 'lucide-react';
import Button from '../../components/ui/Button';

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
import { useLocation } from 'react-router-dom';

// Components
import PlaygroundCanvas from '../../components/PlaygroundCanvas'; 
import AgentRegistry from '../../components/PlaygroundCanvas/AgentRegistry'; 
import ChatSidebar from '../../components/PlaygroundCanvas/ChatSidebar';

export default function AgentsPlayground() {
    // --- APP LEVEL STATE ---
    const location = useLocation();
    const [isChatOpen, setIsChatOpen] = useState(false);
    
    // 1. Add state for the Registry Deck
    const [isRegistryOpen, setIsRegistryOpen] = useState(true);
    
    const hypothesis = location.state?.hypothesis || '';

    return (
        <div className="w-full h-screen font-sans flex flex-col bg-[#000000] text-white overflow-hidden">
            
            {/* --- THE ONLY HEADER --- */}
            <div className="h-14 bg-[#0b1015] border-b border-[#1f2d40] flex items-center justify-between px-4 z-20 shrink-0">
                <div className="flex items-center gap-6">
                    {/* Breadcrumbs */}
                    <div className="flex items-center gap-3">
                        <span className="font-bold text-[#5acce6] text-xs tracking-widest">CUBE</span>
                        <span className="text-gray-500 text-xs">/</span>
                        <span className="text-gray-300 text-xs font-medium">Workspace</span>
                    </div>

                    {/* Hypothesis Context */}
                    {hypothesis && (
                        <>
                            <div className="h-6 w-px bg-[#1f2d40]" />
                            <div className="flex items-center gap-3 text-xs">
                                <span className="text-[#5acce6] uppercase tracking-wider font-bold flex items-center gap-2">
                                    <FlaskConical size={14} /> Hypothesis:
                                </span>
                                <span className="text-gray-300 font-mono max-w-xl truncate opacity-90" title={hypothesis}>
                                    {hypothesis}
                                </span>
                            </div>
                        </>
                    )}
                </div>
                
                {/* Right Actions */}
                <button 
                    onClick={() => setIsChatOpen(!isChatOpen)}
                    className={`p-2 rounded border transition-colors flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider
                    ${isChatOpen ? 'bg-[#3c5d80] text-white border-[#5acce6]' : 'bg-[#151a1f] text-[#566a84] border-[#3c5d80] hover:text-white'}`}
                >
                    <MessageSquare size={14} /> Assistant
                </button>
            </div>

            {/* --- MAIN WORKSPACE --- */}
            <div className="flex-1 flex overflow-hidden relative">
                
                {/* A. The Workflow Canvas */}
                <div className="flex-1 min-w-0 relative">
                    <PlaygroundCanvas 
                        initialHypothesis={hypothesis} 
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
                    <div className="w-[350px] border-l border-[#3c5d80] bg-[#0b1015] z-30 shadow-2xl animate-in slide-in-from-right duration-300 relative">
                        <ChatSidebar 
                            isOpen={isChatOpen} 
                            onClose={() => setIsChatOpen(false)} 
                            initialUserMessage={hypothesis} 
                        />
                    </div>
                )}
            </div>
        </div>
    );
}