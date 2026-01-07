import React, { useState } from 'react';
import { useLocation } from 'react-router-dom';
import { MessageSquare } from 'lucide-react';

// Components
import PlaygroundCanvas from '../../components/PlaygroundCanvas'; // Importing the unified engine
import AgentRegistry from '../../components/PlaygroundCanvas/AgentRegistry'; // Your updated deck component
import ChatSidebar from '../../components/PlaygroundCanvas/ChatSidebar';

export default function AgentsPlayground() {
    // --- APP LEVEL STATE ---
    const location = useLocation();
    const [isChatOpen, setIsChatOpen] = useState(false);
    
    // Retrieve hypothesis passed from previous navigation (e.g., Search Page)
    const hypothesis = location.state?.hypothesis || '';

    return (
        <div className="w-full h-screen font-sans flex flex-col bg-[#000000] text-white overflow-hidden">
            
            {/* 1. TOP HEADER (App Context) */}
            <div className="h-10 bg-[#0b1015] border-b border-[#1f2d40] flex items-center justify-between px-4 z-20 shrink-0">
                <div className="flex items-center gap-3">
                    <span className="font-bold text-[#5acce6] text-xs tracking-widest">CUBE</span>
                    <span className="text-gray-500 text-xs">/</span>
                    <span className="text-gray-300 text-xs font-medium">Workspace</span>
                </div>
                
                <div className="flex items-center gap-3">
                    {/* Chat Toggle Button */}
                    <button 
                        onClick={() => setIsChatOpen(!isChatOpen)}
                        className={`p-1.5 rounded border transition-colors flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider
                        ${isChatOpen ? 'bg-[#3c5d80] text-white border-[#5acce6]' : 'bg-[#151a1f] text-[#566a84] border-[#3c5d80] hover:text-white'}`}
                    >
                        <MessageSquare size={14} /> Assistant
                    </button>
                </div>
            </div>

            {/* 2. HYPOTHESIS BANNER (Context for the session) */}
            {hypothesis && (
                <div className="bg-[#1f2d40]/50 text-[#91b9cd] px-4 py-2 text-xs border-b border-[#3c5d80] flex items-center gap-2 shrink-0 backdrop-blur-sm z-10">
                    <span className="font-bold text-[#5acce6] uppercase tracking-wider">Hypothesis:</span>
                    <span className="font-mono text-white opacity-90 truncate">{hypothesis}</span>
                </div>
            )}

            {/* 3. MAIN WORKSPACE */}
            <div className="flex-1 flex overflow-hidden relative">
                
                {/* A. The Workflow Canvas (The Engine) */}
                <div className="flex-1 min-w-0 relative">
                    <PlaygroundCanvas 
                        initialHypothesis={hypothesis} 
                    />
                    
                    {/* B. Agent Registry (Floating Deck) */}
                    <AgentRegistry 
                        isOpen={true} // It manages its own state or you can pass props
                        onToggle={() => {}} // Optional if using internal state
                    />
                </div>

                {/* C. Chat Sidebar (Overlay/Slide-in) */}
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