// Centralized OHIF-like theme tokens
export const colors = {
  primary: '#5acce6', // OHIF cyan accent (unified)
  primaryDark: '#023a7d',
  accentBlue: '#5acce6',
  neutral900: '#000000',
  neutral800: '#151a1f',
  neutral700: '#1f2d40',
  muted: '#566a84',
  surface: '#090c29',
};

export const gradients = {
  // Standard uses a slightly stronger blue highlight
  standard: 'radial-gradient(circle at 30% 50%, rgba(52, 140, 253, 0.12), transparent 60%)',
  // Agentic uses the OHIF cyan at a lighter alpha for subtlety
  agentic: 'radial-gradient(circle at 70% 50%, rgba(90, 204, 230, 0.08), transparent 60%)',
};

export const radii = {
  sm: '6px',
  md: '12px',
  lg: '16px',
};

export default {
  colors,
  gradients,
  radii,
};