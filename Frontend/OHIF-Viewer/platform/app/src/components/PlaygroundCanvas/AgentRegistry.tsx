import React from 'react';
import { 
    Database, Bot, Activity, FileText, Shield, User, 
    ChevronDown, ChevronUp 
} from 'lucide-react';

// --- RICH CONTENT CONFIGURATION ---
const AGENTS_REGISTRY = [
    { 
        id: 'a1', 
        title: 'DICOM Fetcher', 
        role: 'IO / Network', 
        icon: <Database size={16} className="text-[#5acce6]" />,
        capabilities: [
            "Query/Retrieve (C-FIND, C-MOVE)",
            "Parse DICOM Tags",
            "Anonymize on fly"
        ]
    },
    { 
        id: 'a2', 
        title: 'Liver Segmentation', 
        role: 'ML Model', 
        icon: <Bot size={16} className="text-[#6fe5b8]" />,
        capabilities: [
            "Organ Volumetry",
            "Couinaud Segments",
            "Lesion Detection"
        ]
    },
    { 
        id: 'a3', 
        title: 'Lung Nodule Det', 
        role: 'ML Model', 
        icon: <Activity size={16} className="text-[#f8ce46]" />,
        capabilities: [
            "Nodule Classification",
            "Diameter Measurement",
            "Growth Tracking"
        ]
    },
    { 
        id: 'a4', 
        title: 'Report Generator', 
        role: 'LLM / NLP', 
        icon: <FileText size={16} className="text-[#91b9cd]" />,
        capabilities: [
            "Summarize Findings",
            "Format Structured Report",
            "ICD-10 Coding"
        ]
    },
    { 
        id: 'a5', 
        title: 'De-identifier', 
        role: 'Utility', 
        icon: <Shield size={16} className="text-[#ff6b6b]" />,
        capabilities: [
            "Remove PHI",
            "Shift Dates",
            "Mask Pixel Data"
        ]
    },
    { 
        id: 'a6', 
        title: 'Radiologist Review', 
        role: 'Human In Loop', 
        icon: <User size={16} className="text-white" />,
        capabilities: [
            "Manual Verification",
            "Add Annotations",
            "Sign Off"
        ]
    },
];

const AgentRegistry = ({ isOpen, onToggle }) => {
    return (
        /* --- EXPANDABLE BOTTOM AGENT DECK --- */
        <div 
            className={`absolute bottom-0 left-0 right-0 z-30 transition-transform duration-300 ease-in-out 
            ${isOpen ? 'translate-y-0' : 'translate-y-[calc(100%-32px)]'}`}
        >
            {/* 1. Toggle Handle */}
            <div className="flex justify-center">
                <button 
                    onClick={onToggle}
                    className="bg-[#151a1f] border-t border-x border-[#3c5d80] rounded-t-lg px-6 py-1 h-8 text-[#91b9cd] hover:text-white transition-colors flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider shadow-lg cursor-pointer select-none"
                >
                    {isOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />} Available Agents
                </button>
            </div>

            {/* 2. Content Container */}
            <div className="bg-[#0b1015]/95 backdrop-blur-md border-t border-[#3c5d80] p-4 shadow-2xl h-64">
                <div className="flex gap-4 overflow-x-auto h-full pb-2 scrollbar-thin scrollbar-thumb-[#3c5d80] scrollbar-track-transparent px-4">
                    {AGENTS_REGISTRY.map((agent) => (
                        <div 
                            key={agent.id} 
                            className="shrink-0 w-64 bg-[#1f2d40]/50 border border-[#3c5d80]/50 rounded-lg p-3 hover:border-[#348cfd] hover:bg-[#1f2d40] transition-all group cursor-pointer flex flex-col"
                            title="Drag to canvas"
                            draggable
                            onDragStart={(e) => {
                                e.dataTransfer.setData('application/reactflow', agent.type || 'agent');
                                e.dataTransfer.setData('application/agentData', JSON.stringify(agent));
                            }}
                        >
                            <div className="flex items-center gap-3 mb-2 border-b border-[#3c5d80]/30 pb-2">
                                <div className="p-2 bg-[#151a1f] rounded border border-[#3c5d80]/50 group-hover:border-[#348cfd]/50 transition-colors">
                                    {agent.icon}
                                </div>
                                <div>
                                    <h3 className="text-sm font-bold text-white leading-tight">{agent.title}</h3>
                                    <span className="text-[10px] text-[#91b9cd] uppercase tracking-wider">{agent.role}</span>
                                </div>
                            </div>
                            <ul className="space-y-1 overflow-y-auto flex-1 scrollbar-thin scrollbar-thumb-[#3c5d80] pr-2">
                                {agent.capabilities.map((cap, idx) => (
                                    <li key={idx} className="text-[10px] text-[#dadada] flex items-start gap-1.5 leading-relaxed">
                                        <span className="mt-1.5 w-1 h-1 rounded-full bg-[#566a84] group-hover:bg-[#348cfd] shrink-0" />
                                        {cap}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
};

export default AgentRegistry;

            // {/* --- EXPANDABLE BOTTOM AGENT DECK --- */}
            // <div 
            //     className={`absolute bottom-0 left-0 right-0 z-30 transition-transform duration-300 ease-in-out 
            //     ${isDeckOpen ? 'translate-y-0' : 'translate-y-[calc(100%-32px)]'}`}
            // >
            //     {/* 1. Toggle Handle */}
            //     <div className="flex justify-center">
            //         <button 
            //             onClick={() => setIsDeckOpen(!isDeckOpen)}
            //             className="bg-[#151a1f] border-t border-x border-[#3c5d80] rounded-t-lg px-6 py-1 h-8 text-[#91b9cd] hover:text-white transition-colors flex items-center gap-2 text-[10px] font-bold uppercase tracking-wider shadow-lg cursor-pointer"
            //         >
            //            {isDeckOpen ? <ChevronDown size={14} /> : <ChevronUp size={14} />} Available Agents
            //         </button>
            //     </div>

            //     {/* 2. Content Container */}
            //     <div className="bg-[#0b1015]/95 backdrop-blur-md border-t border-[#3c5d80] p-4 shadow-2xl h-64">
            //         <div className="flex gap-4 overflow-x-auto h-full pb-2 scrollbar-thin scrollbar-thumb-[#3c5d80] scrollbar-track-transparent px-4">
            //             {AGENTS_REGISTRY.map((agent) => (
            //                 <div 
            //                     key={agent.id} 
            //                     className="shrink-0 w-64 bg-[#1f2d40]/50 border border-[#3c5d80]/50 rounded-lg p-3 hover:border-[#348cfd] hover:bg-[#1f2d40] transition-all group cursor-pointer flex flex-col"
            //                     title="Click to add (demo)"
            //                 >
            //                     <div className="flex items-center gap-3 mb-2 border-b border-[#3c5d80]/30 pb-2">
            //                         <div className="p-2 bg-[#151a1f] rounded border border-[#3c5d80]/50 group-hover:border-[#348cfd]/50 transition-colors">
            //                             {agent.icon}
            //                         </div>
            //                         <div>
            //                             <h3 className="text-sm font-bold text-white leading-tight">{agent.title}</h3>
            //                             <span className="text-[10px] text-[#91b9cd] uppercase tracking-wider">{agent.role}</span>
            //                         </div>
            //                     </div>
            //                     <ul className="space-y-1 overflow-y-auto flex-1 scrollbar-thin scrollbar-thumb-[#3c5d80] pr-2">
            //                         {agent.capabilities.map((cap, idx) => (
            //                             <li key={idx} className="text-[10px] text-[#dadada] flex items-start gap-1.5 leading-relaxed">
            //                                 <span className="mt-1 w-1 h-1 rounded-full bg-[#566a84] group-hover:bg-[#348cfd] shrink-0" />
            //                                 {cap}
            //                             </li>
            //                         ))}
            //                     </ul>
            //                 </div>
            //             ))}
            //         </div>
            //     </div>
            // </div>