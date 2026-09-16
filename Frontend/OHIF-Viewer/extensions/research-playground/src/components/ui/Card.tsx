import React from 'react';
import clsx from 'clsx';
import theme from './theme';

export type SystemVariant = 'standard' | 'agentic';

const STATUS_STYLES: Record<string, { dot: string; text: string; bg: string; label: string }> = {
  planning: { dot: 'bg-amber-400', text: 'text-amber-300', bg: 'bg-amber-400/10', label: 'Planning' },
  ready: { dot: 'bg-sky-400', text: 'text-sky-300', bg: 'bg-sky-400/10', label: 'Ready' },
  running: { dot: 'bg-[#5acce6]', text: 'text-[#5acce6]', bg: 'bg-[#5acce6]/10', label: 'Running' },
  pausing: { dot: 'bg-amber-400', text: 'text-amber-300', bg: 'bg-amber-400/10', label: 'Pausing' },
  paused: { dot: 'bg-amber-400', text: 'text-amber-300', bg: 'bg-amber-400/10', label: 'Paused' },
  completed: { dot: 'bg-emerald-400', text: 'text-emerald-300', bg: 'bg-emerald-400/10', label: 'Completed' },
  failed: { dot: 'bg-red-400', text: 'text-red-300', bg: 'bg-red-400/10', label: 'Failed' },
  archived: { dot: 'bg-[#566a84]', text: 'text-[#91b9cd]', bg: 'bg-[#566a84]/10', label: 'Archived' },
};

const ANIMATED_STATUSES = new Set(['running', 'pausing']);

interface CardProps {
  title: string;
  description?: string;
  icon?: React.ReactNode;
  ctaText?: string;
  /** Session status (e.g. "planning", "running") rendered as a colored pill. */
  status?: string;
  /** Small trailing text shown next to the status pill, e.g. "Edited 2 hours ago". */
  metaText?: string;
  onClick?: () => void;
  variant?: SystemVariant;
  className?: string;
}

export default function Card({
  title,
  description,
  icon,
  ctaText,
  status,
  metaText,
  onClick,
  variant = 'standard',
  className,
}: CardProps) {
  const statusKey = (status || '').toLowerCase();
  const statusStyle = status ? STATUS_STYLES[statusKey] : undefined;
  const isAnimated = ANIMATED_STATUSES.has(statusKey);
  const gradientStyle =
    variant === 'agentic'
      ? theme.gradients.agentic
      : theme.gradients.standard;

  return (
    <div
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      tabIndex={onClick ? 0 : undefined}
      className={clsx(
        `
        relative
        p-6
        rounded-2xl
        border
        transition-all
        duration-300
        text-left
        flex
        flex-col
        gap-4
        shadow-xl
        cursor-pointer
        bg-[#000000]
        border-[#3c5d80]
        min-h-[190px]
        `,
        className
      )}
    >
      {/* Gradient Overlay */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: gradientStyle,
          opacity: 0.08,
          pointerEvents: 'none',
          borderRadius: theme.radii.md,
        }}
      />

      {/* Header */}
      <div className="flex items-start gap-4 z-10">
        <div className="w-12 h-12 rounded-xl flex items-center justify-center bg-[#0f1724]">
          {icon}
        </div>

        <div className="flex-1 pr-8">
          <div className="text-lg font-bold text-white">{title}</div>

          {description && (
            <div
              className="
                text-sm
                text-[#91b9cd]
                mt-1
                line-clamp-3
                min-h-[3rem]
              "
            >
              {description}
            </div>
          )}
        </div>
      </div>

      {/* Footer: status pill + meta text on one side, CTA on the other */}
      {(status || metaText || ctaText) && (
        <div className="mt-auto z-10 flex items-center justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            {statusStyle && (
              <span
                className={clsx(
                  'inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-bold uppercase tracking-wider shrink-0',
                  statusStyle.bg,
                  statusStyle.text
                )}
              >
                <span
                  className={clsx(
                    'h-1.5 w-1.5 rounded-full',
                    statusStyle.dot,
                    isAnimated && 'animate-pulse'
                  )}
                />
                {statusStyle.label}
              </span>
            )}
            {metaText && (
              <span className="text-xs text-[#566a84] truncate">{metaText}</span>
            )}
          </div>

          {ctaText && !status && (
            <div className="text-xs font-bold uppercase tracking-wider text-[#91b9cd] shrink-0">
              {ctaText}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
