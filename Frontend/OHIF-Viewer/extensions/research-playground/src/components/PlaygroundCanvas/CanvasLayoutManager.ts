import { FlowEdge, FlowNode } from './types';

export const CENTER_LINE = 400;
export const START_W = 340;
export const AGENT_W = 280;
export const DEFAULT_GAP = 120;
export const MIN_GAP = 48;
export const MAX_GAP = 180;
export const START_H_ESTIMATE = 80;
export const AGENT_H_ESTIMATE = 84;

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));

export const getDynamicLayout = (agentCount: number) => {
  const viewportWidth =
    typeof window !== 'undefined' ? Math.max(window.innerWidth, 1200) : 1600;

  const usableWidth = Math.max(1000, viewportWidth - 420);
  const gaps = Math.max(1, agentCount);
  const nodeWidthTotal = START_W + agentCount * AGENT_W;
  const computedGap = (usableWidth - nodeWidthTotal) / gaps;
  const gap = clamp(Number.isFinite(computedGap) ? computedGap : DEFAULT_GAP, MIN_GAP, MAX_GAP);
  const rowWidth = nodeWidthTotal + gap * gaps;
  const startX = Math.max(80, (usableWidth - rowWidth) / 2 + 60);

  return {
    gap,
    startX,
    startY: CENTER_LINE - START_H_ESTIMATE / 2,
    agentY: CENTER_LINE - AGENT_H_ESTIMATE / 2,
  };
};

const INITIAL_LAYOUT = getDynamicLayout(0);

export const createInitialNodes = (initialHypothesis?: string): FlowNode[] => [
  {
    id: '1',
    type: 'start',
    position: { x: INITIAL_LAYOUT.startX, y: INITIAL_LAYOUT.startY },
    data: {
      title: 'Research Hypothesis',
      description: initialHypothesis || 'Entry point',
    },
  },
];

export const INITIAL_EDGES: FlowEdge[] = [];
