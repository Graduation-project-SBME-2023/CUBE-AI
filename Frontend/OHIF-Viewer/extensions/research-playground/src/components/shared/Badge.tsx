import React from 'react';

type BadgeTone = 'default' | 'info' | 'success' | 'warning' | 'danger';

interface BadgeProps {
  children: React.ReactNode;
  tone?: BadgeTone;
}

const toneClass: Record<BadgeTone, string> = {
  default: 'border-[#3c5d80] bg-[#1f2d40] text-[#91b9cd]',
  info: 'border-[#5acce6]/30 bg-[#0d2847] text-[#5acce6]',
  success: 'border-[#6fe5b8]/30 bg-[#0d3320] text-[#6fe5b8]',
  warning: 'border-[#f8ce46]/30 bg-[#3b2d1c] text-[#f8ce46]',
  danger: 'border-[#ff6b6b]/30 bg-[#2a1313] text-[#ff6b6b]',
};

export default function Badge({ children, tone = 'default' }: BadgeProps) {
  return (
    <span
      className={`inline-flex items-center rounded px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider border ${toneClass[tone]}`}
    >
      {children}
    </span>
  );
}
