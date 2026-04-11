import React from 'react';
import clsx from 'clsx';
import theme from '../../theme';

export type SystemVariant = 'standard' | 'agentic';

interface CardProps {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  ctaText?: string;
  onClick?: () => void;
  variant?: SystemVariant;
  className?: string;
}

export default function Card({ title, description, icon, ctaText, onClick, variant = 'standard', className }: CardProps) {
  const gradientStyle = variant === 'agentic' ? theme.gradients.agentic : theme.gradients.standard;

  return (
    <div
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      className={clsx('relative p-6 rounded-2xl border transition-all duration-300 text-left flex flex-col gap-4 shadow-xl cursor-pointer', className)}
      style={{ background: theme.colors.neutral900, borderColor: '#3c5d80' }}
    >
      <div style={{ position: 'absolute', inset: 0, background: gradientStyle, opacity: 0.08, pointerEvents: 'none', borderRadius: theme.radii.md }} />

      <div className="flex items-start gap-4 z-10">
        <div className="w-12 h-12 rounded-xl flex items-center justify-center bg-[#0f1724]">
          {icon}
        </div>
        <div className="flex-1">
          <div className="text-lg font-bold text-white">{title}</div>
          {description && <div className="text-sm text-[#91b9cd] mt-1">{description}</div>}
        </div>
      </div>

      {ctaText && (
        <div className="mt-auto z-10">
          <div className="text-xs font-bold uppercase tracking-wider text-[#91b9cd]">{ctaText}</div>
        </div>
      )}
    </div>
  );
}
