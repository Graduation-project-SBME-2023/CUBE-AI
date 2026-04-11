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
        <div className="w-full h-full relative bg-slate-950 rounded-2xl overflow-hidden border border-slate-800">
            <div className="absolute top-0 left-0 right-0 z-10 pointer-events-none bg-gradient-to-b from-slate-900/90 to-transparent px-4 py-2">
                <p className="text-[11px] uppercase tracking-widest text-slate-500 font-semibold">Planning Canvas</p>
            </div>
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