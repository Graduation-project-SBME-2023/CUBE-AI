import React, { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';

interface CollapsibleProps {
  title: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
  action?: React.ReactNode;
}

export default function Collapsible({
  title,
  children,
  defaultOpen = true,
  action,
}: CollapsibleProps) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="overflow-hidden rounded-xl border border-[#1f2d40] bg-[#151a1f]">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex w-full items-center justify-between px-4 py-3 hover:bg-white/[0.025]"
      >
        <span className="flex items-center gap-2 text-left text-[10px] font-semibold uppercase tracking-wider text-[#91b9cd]">
          {title}
        </span>
        <span className="flex items-center gap-2">
          {action}
          {open ? <ChevronUp size={13} className="text-[#566a84]" /> : <ChevronDown size={13} className="text-[#566a84]" />}
        </span>
      </button>
      {open ? <div className="px-4 pb-4">{children}</div> : null}
    </div>
  );
}
