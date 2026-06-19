import React, { useState, useEffect } from 'react';
// Assuming path to your existing component
import PlaygroundCanvas from '../PlaygroundCanvas'; 
import AgentRegistry from '../../../components/PlaygroundCanvas/AgentRegistry'; 
import { getResearchNotificationService } from '../../../utils/ResearchNotificationService';

interface PlanningViewProps {
    initialHypothesis: string;
    sessionId?: string | number | null;
    includeLitReview?: boolean;
    onLiteratureOutput?: (data: any) => void;
}

export default function PlanningView({ initialHypothesis, sessionId, includeLitReview = true, onLiteratureOutput }: PlanningViewProps) {
    const [isRegistryOpen, setIsRegistryOpen] = useState(true);

    useEffect(() => {
        const service = getResearchNotificationService();
        const wasEnabled = service.isEnabled();
        service.setEnabled(false);

        return () => {
            service.setEnabled(wasEnabled);
        };
    }, []);

    return (
        <div className="w-full h-full relative overflow-hidden bg-[#0a1520]">
            <div className="absolute top-0 left-0 right-0 z-10 pointer-events-none bg-gradient-to-b from-[#0a1520]/90 to-transparent px-4 py-2">
                <p className="text-[11px] uppercase tracking-widest text-[#5a8aaa] font-semibold">Planning Canvas</p>
            </div>
            {/* Reuse your existing Graph Engine */}
            <PlaygroundCanvas initialHypothesis={initialHypothesis} session_id={sessionId as any} includeLitReview={includeLitReview} onLiteratureOutput={onLiteratureOutput} />
            
            {/* Reuse your existing Registry
            <AgentRegistry 
                isOpen={isRegistryOpen} 
                onToggle={() => setIsRegistryOpen(!isRegistryOpen)} 
            /> */}
        </div>
    );
}
