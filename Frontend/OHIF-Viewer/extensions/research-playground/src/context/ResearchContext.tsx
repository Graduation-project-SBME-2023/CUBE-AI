import React, { createContext, useContext, useMemo, useState } from 'react';
import { ResearchViewState } from '../types';

interface ResearchContextValue {
  data: ResearchViewState;
  setData: React.Dispatch<React.SetStateAction<ResearchViewState>>;
}

const DEFAULT_STATE: ResearchViewState = {
  literatureOutput: null,
  populationData: null,
  segmentationData: null,
  featuresData: null,
  importanceData: null,
  modelingData: null,
  reportData: null,
  latexData: null,
};

const ResearchContext = createContext<ResearchContextValue | null>(null);

export function ResearchProvider({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<ResearchViewState>(DEFAULT_STATE);
  const value = useMemo(() => ({ data, setData }), [data]);
  return <ResearchContext.Provider value={value}>{children}</ResearchContext.Provider>;
}

export function useResearchContext() {
  const ctx = useContext(ResearchContext);
  if (!ctx) {
    throw new Error('useResearchContext must be used within ResearchProvider');
  }
  return ctx;
}
