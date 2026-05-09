export const colors = {
  black: '#000000',
  surface: '#0b1015',
  surfaceAlt: '#151a1f',
  panel: '#1f2d40',
  border: '#3c5d80',
  muted: '#566a84',
  text: '#dadada',
  textSoft: '#91b9cd',
  accent: '#5acce6',
  accentStrong: '#348cfd',
  success: '#6fe5b8',
  warning: '#f8ce46',
  danger: '#ff6b6b',
} as const;

export type AppColor = keyof typeof colors;
