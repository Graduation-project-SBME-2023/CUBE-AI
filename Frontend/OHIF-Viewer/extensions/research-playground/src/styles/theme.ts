import { colors } from './colors';

export const gradients = {
  standard: 'radial-gradient(circle at 30% 50%, rgba(52, 140, 253, 0.12), transparent 60%)',
  agentic: 'radial-gradient(circle at 70% 50%, rgba(90, 204, 230, 0.08), transparent 60%)',
  hero: 'radial-gradient(circle at 50% 0%, rgba(111, 229, 184, 0.1), transparent 60%)',
} as const;

export const radii = {
  sm: '6px',
  md: '12px',
  lg: '16px',
  xl: '20px',
} as const;

export const theme = {
  colors,
  gradients,
  radii,
};

export default theme;
