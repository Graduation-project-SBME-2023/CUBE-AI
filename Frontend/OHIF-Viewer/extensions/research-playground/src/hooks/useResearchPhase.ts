import { useMemo } from 'react';
import { RESEARCH_PHASES } from '../constants/researchPhases';

const DEFAULT_PHASE = 'planning';

export function useResearchPhase(phaseId?: string) {
  const isValidPhase = useMemo(
    () => Boolean(phaseId && RESEARCH_PHASES.some(phase => phase.id === phaseId)),
    [phaseId]
  );

  return {
    isValidPhase,
    activePhase: isValidPhase ? (phaseId as string) : DEFAULT_PHASE,
    defaultPhase: DEFAULT_PHASE,
    phases: RESEARCH_PHASES,
  };
}
