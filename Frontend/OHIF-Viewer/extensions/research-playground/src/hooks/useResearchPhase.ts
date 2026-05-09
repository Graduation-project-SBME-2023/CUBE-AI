import { useMemo } from 'react';
import { RESEARCH_PHASES } from '../constants/researchPhases';

const DEFAULT_PHASE = 'planning';

interface UseResearchPhaseOptions {
  includeLiterature?: boolean;
}

export function useResearchPhase(phaseId?: string, options?: UseResearchPhaseOptions) {
  const includeLiterature = options?.includeLiterature ?? true;
  const phases = useMemo(
    () => RESEARCH_PHASES.filter(phase => includeLiterature || phase.id !== 'literature'),
    [includeLiterature]
  );

  const isValidPhase = useMemo(
    () => Boolean(phaseId && phases.some(phase => phase.id === phaseId)),
    [phaseId, phases]
  );

  return {
    isValidPhase,
    activePhase: isValidPhase ? (phaseId as string) : DEFAULT_PHASE,
    defaultPhase: DEFAULT_PHASE,
    phases,
  };
}
