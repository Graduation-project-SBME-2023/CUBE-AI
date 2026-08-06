import React, { useState } from 'react';
import { Target, Cpu, ChevronDown, ChevronUp } from 'lucide-react';

export function CollapsibleSection({
    title,
    icon,
    defaultOpen = true,
    children,
    accent = '#5acce6',
}: {
    title: string;
    icon: React.ReactNode;
    defaultOpen?: boolean;
    children: React.ReactNode;
    accent?: string;
}) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="bg-[#151a1f] rounded-xl border border-[#1f2d40] overflow-hidden">
            <button
                onClick={() => setOpen(o => !o)}
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/[0.025] transition-colors"
            >
                <span
                    className="flex items-center gap-2 text-[10px] uppercase font-semibold tracking-wider"
                    style={{ color: accent }}
                >
                    {icon} {title}
                </span>
                {open
                    ? <ChevronUp size={13} className="text-[#566a84]" />
                    : <ChevronDown size={13} className="text-[#566a84]" />}
            </button>
            {open && <div className="px-4 pb-4">{children}</div>}
        </div>
    );
}

export function InfoPanel({
    targetRoi,
    selectedModel,
}: {
    targetRoi: string;
    selectedModel: string;
}) {
    return (
        <div className="w-full shrink-0">
            <CollapsibleSection title="Segmentation Info" icon={<Target size={11} />} accent="#5acce6">
                <div className="flex flex-row gap-12 pt-1">
                    <div>
                        <p className="text-[#566a84] text-[10px] uppercase flex items-center gap-1 mb-0.5">
                            <Target size={11} /> Target ROI
                        </p>
                        <div className="text-base font-bold text-[#5acce6]">{targetRoi}</div>
                    </div>
                    <div>
                        <p className="text-[#566a84] text-[10px] uppercase flex items-center gap-1 mb-0.5">
                            <Cpu size={11} /> Selected Model
                        </p>
                        <div className="text-base font-bold text-white">{selectedModel}</div>
                    </div>
                </div>
            </CollapsibleSection>
        </div>
    );
}
