import React, { useState } from 'react';
import { ChevronDown, ChevronRight, FlaskConical, CheckCircle } from 'lucide-react';
import { ModelingData } from './types';
import { MODEL_COLORS } from './utils';
import { BestMetricBadge } from './Shared';
import { ExperimentPanel } from './ExperimentPanel';

// ── Accordion row for a single experiment ──────────────────────────────────────

export function ExperimentAccordion({
    expKey, data, index, defaultOpen,
}: {
    expKey: string; data: ModelingData; index: number; defaultOpen: boolean;
}) {
    const [open, setOpen] = useState(defaultOpen);
    const chartData = data.chart_data;
    const modelCount = chartData ? Object.keys(chartData.per_model ?? {}).length : 0;
    const accentColor = MODEL_COLORS[index % MODEL_COLORS.length];

    return (
        <div className="rounded-xl border border-[#1f2d40] overflow-hidden bg-[#0b1015]">
            {/* Header / toggle button */}
            <button
                onClick={() => setOpen(v => !v)}
                className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-white/[0.02] transition-colors"
            >
                {/* Colored left accent */}
                <span className="w-1 h-6 rounded-full flex-shrink-0" style={{ background: accentColor }} />

                {/* Expand indicator */}
                <span className="text-[#3c5d80] flex-shrink-0">
                    {open
                        ? <ChevronDown size={13} />
                        : <ChevronRight size={13} />
                    }
                </span>

                {/* Icon */}
                <FlaskConical size={13} style={{ color: accentColor, flexShrink: 0 }} />

                {/* Name */}
                <span className="font-mono text-[12px] font-semibold text-[#91b9cd] flex-1 truncate">
                    {expKey}
                </span>

                {/* Right-side badges */}
                <div className="flex items-center gap-2 flex-shrink-0">
                    {modelCount > 0 && (
                        <span className="text-[10px] px-2 py-0.5 rounded border border-[#1f2d40] text-[#566a84] font-mono">
                            {modelCount} model{modelCount !== 1 ? 's' : ''}
                        </span>
                    )}
                    {chartData && <BestMetricBadge chartData={chartData} />}
                    {data.status === 'success' && (
                        <CheckCircle size={12} className="text-[#6fe5b8]" />
                    )}
                </div>
            </button>

            {/* Collapsible content */}
            {open && (
                <div className="border-t border-[#1f2d40]">
                    <ExperimentPanel expKey={expKey} data={data} />
                </div>
            )}
        </div>
    );
}