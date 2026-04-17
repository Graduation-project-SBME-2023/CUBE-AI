// Centralized OHIF-like theme tokens (self-contained, no external imports)

// =======================
// COLORS
// =======================
export const colors = {
  primary: '#0944b3',
  primaryLight: '#5acce6',
  primaryDark: '#090c29',

  accentBlue: '#1454D4',

  neutral900: '#000000',
  neutral800: '#0A163F',
  neutral700: '#0B1F54',

  muted: '#a0aec0',
  surface: '#090c29',

  success: '#48bb78',
  warning: '#ecc94b',
  error: '#f56565',

  white: '#ffffff',
};

// =======================
// GRADIENTS
// =======================
export const gradients = {
  primary: `linear-gradient(135deg, ${colors.primaryLight}, ${colors.primary})`,

  standard: `radial-gradient(
    circle at 30% 50%,
    rgba(52, 140, 253, 0.12),
    transparent 60%
  )`,

  agentic: `radial-gradient(
    circle at 70% 50%,
    rgba(90, 204, 230, 0.08),
    transparent 60%
  )`,

  surface: `linear-gradient(180deg, ${colors.primaryDark}, ${colors.surface})`,
};

// =======================
// RADII
// =======================
export const radii = {
  sm: '6px',
  md: '12px',
  lg: '16px',
  xl: '20px',
};

// =======================
// SHADOWS
// =======================
export const shadows = {
  sm: '0 1px 2px rgba(0,0,0,0.2)',
  md: '0 4px 10px rgba(0,0,0,0.25)',
  lg: '0 10px 25px rgba(0,0,0,0.3)',
  xl: '0 20px 40px rgba(0,0,0,0.35)',
};

// =======================
// SPACING (optional utility layer)
// =======================
export const spacing = {
  xs: '4px',
  sm: '8px',
  md: '12px',
  lg: '16px',
  xl: '24px',
  xxl: '32px',
};

// =======================
// FULL THEME OBJECT
// =======================
const theme = {
  colors,
  gradients,
  radii,
  shadows,
  spacing,
};

export default theme;