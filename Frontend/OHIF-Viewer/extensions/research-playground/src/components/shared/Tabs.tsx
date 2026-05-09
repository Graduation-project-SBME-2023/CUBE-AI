import React from 'react';

export interface TabItem<T extends string> {
  id: T;
  label: string;
  icon?: React.ReactNode;
  disabled?: boolean;
}

interface TabsProps<T extends string> {
  tabs: TabItem<T>[];
  active: T;
  onChange: (id: T) => void;
  hint?: string;
}

export default function Tabs<T extends string>({ tabs, active, onChange, hint }: TabsProps<T>) {
  return (
    <div className="mb-3 flex items-center gap-1 shrink-0 flex-wrap">
      {tabs.map(tab => {
        const isActive = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            onClick={() => !tab.disabled && onChange(tab.id)}
            disabled={tab.disabled}
            className={`flex items-center gap-1.5 rounded-lg border px-3 py-1.5 text-[10px] font-medium transition-colors ${
              isActive
                ? 'border-[#5acce6]/50 bg-[#5acce6]/15 text-[#5acce6]'
                : tab.disabled
                  ? 'cursor-not-allowed border-[#1a2530] text-[#2a3a4a] opacity-40'
                  : 'border-[#1f2d40] text-[#566a84] hover:text-[#91b9cd]'
            }`}
          >
            {tab.icon}
            {tab.label}
          </button>
        );
      })}
      {hint ? <span className="ml-auto text-[9px] text-[#3c5d80]">{hint}</span> : null}
    </div>
  );
}
