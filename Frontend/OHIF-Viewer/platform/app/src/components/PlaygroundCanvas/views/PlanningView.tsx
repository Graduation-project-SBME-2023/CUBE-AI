import React, { useState } from 'react';
// Assuming path to your existing component
import PlaygroundCanvas from '../PlaygroundCanvas'; 
import AgentRegistry from '../../../components/PlaygroundCanvas/AgentRegistry'; 

interface PlanningViewProps {
    initialHypothesis: string;
    sessionId?: string | number | null;
    onLiteratureOutput?: (data: any) => void;
}

export default function PlanningView({ initialHypothesis, sessionId, onLiteratureOutput }: PlanningViewProps) {
    const [isRegistryOpen, setIsRegistryOpen] = useState(true);

    console.log('[HypothesisFlow][PlanningView] Render', {
        hasHypothesis: Boolean(initialHypothesis),
        hypothesisLength: initialHypothesis?.length || 0,
        sessionId,
    });

    return (
        <div className="w-full h-full relative">
            {/* Reuse your existing Graph Engine */}
            <PlaygroundCanvas initialHypothesis={initialHypothesis} session_id={sessionId as any} onLiteratureOutput={onLiteratureOutput} />
            
            {/* Reuse your existing Registry
            <AgentRegistry 
                isOpen={isRegistryOpen} 
                onToggle={() => setIsRegistryOpen(!isRegistryOpen)} 
            /> */}
        </div>
    );
}