import React from 'react';
import { Box, ChevronDown, ChevronUp, Database, Bot, Activity, FileText, Shield, User, Plus } from 'lucide-react';
import './AgentRegistry.css';

const AgentRegistry = ({ isOpen, onToggle }) => {
    const agents = [
        { id: 'a1', name: 'DICOM Fetcher', type: 'IO', icon: Database },
        { id: 'a2', name: 'Liver Seg v2', type: 'ML', icon: Bot },
        { id: 'a3', name: 'Lung Nodule Det', type: 'ML', icon: Activity },
        { id: 'a4', name: 'Report Gen', type: 'NLP', icon: FileText },
        { id: 'a5', name: 'Anonymizer', type: 'Util', icon: Shield },
        { id: 'a6', name: 'User Review', type: 'Human', icon: User },
    ];
    return (
        <div className={`w-agent-registry${isOpen ? ' open' : ' closed'}`}>
            <div 
                className="w-agent-registry-header"
                onClick={onToggle}
            >
                <div className="w-agent-registry-title">
                    <Box size={14} /> Agent Registry
                </div>
                {isOpen ? <ChevronDown size={16} className="w-agent-registry-chevron"/> : <ChevronUp size={16} className="w-agent-registry-chevron"/>}
            </div>
            {isOpen && (
                <div className="w-agent-registry-list">
                    {agents.map(agent => (
                        <div key={agent.id} className="w-agent-card">
                            <div className="w-agent-card-icon">
                                <agent.icon size={20} />
                            </div>
                            <span className="w-agent-card-name">{agent.name}</span>
                            <div className="w-agent-card-type">{agent.type}</div>
                        </div>
                    ))}
                    <div className="w-agent-card-add">
                        <Plus size={24} />
                    </div>
                </div>
            )}
        </div>
    );
};
export default AgentRegistry;
