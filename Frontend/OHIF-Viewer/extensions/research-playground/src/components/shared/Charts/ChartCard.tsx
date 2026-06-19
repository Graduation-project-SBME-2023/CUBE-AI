import React from 'react';

interface ChartCardProps {
  title: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}

export default function ChartCard({ title, actions, children }: ChartCardProps) {
  return (
    <div className="flex-1 overflow-y-auto rounded-xl border border-[#1f2d40] bg-[#0b1015]">
      <div className="flex items-center justify-between border-b border-[#1f2d40] bg-[#151a1f]/60 px-4 py-2">
        <span className="text-[10px] text-[#566a84]">{title}</span>
        {actions}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}
