import React from 'react';

interface StatusIndicatorProps {
  active?: boolean;
  label: string;
}

export default function StatusIndicator({ active = false, label }: StatusIndicatorProps) {
  return (
    <div className="flex items-center gap-2">
      <span className="relative flex h-1.5 w-1.5 shrink-0">
        {active ? (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-[#2eca8e] opacity-60" />
        ) : null}
        <span className={`relative inline-flex h-1.5 w-1.5 rounded-full ${active ? 'bg-[#2eca8e]' : 'bg-[#566a84]'}`} />
      </span>
      <span className="text-[11px] text-[#5a8aaa]">{label}</span>
    </div>
  );
}
