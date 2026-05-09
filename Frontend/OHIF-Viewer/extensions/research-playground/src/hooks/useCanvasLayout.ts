import { useMemo } from 'react';
import { getDynamicLayout } from '../components/PlaygroundCanvas/CanvasLayoutManager';

export function useCanvasLayout(agentCount: number) {
  return useMemo(() => getDynamicLayout(agentCount), [agentCount]);
}
