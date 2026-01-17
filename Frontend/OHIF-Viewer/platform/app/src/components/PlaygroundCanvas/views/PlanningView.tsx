import React, { useState } from 'react';
// Assuming path to your existing component
import PlaygroundCanvas from '../PlaygroundCanvas'; 
import AgentRegistry from '../../../components/PlaygroundCanvas/AgentRegistry'; 

interface PlanningViewProps {
    initialHypothesis: string;
}

export default function PlanningView({ initialHypothesis }: PlanningViewProps) {
    const [isRegistryOpen, setIsRegistryOpen] = useState(true);

    return (
        <div className="w-full h-full relative">
            {/* Reuse your existing Graph Engine */}
            <PlaygroundCanvas initialHypothesis={initialHypothesis} />
            
            {/* Reuse your existing Registry */}
            <AgentRegistry 
                isOpen={isRegistryOpen} 
                onToggle={() => setIsRegistryOpen(!isRegistryOpen)} 
            />
        </div>
    );
}