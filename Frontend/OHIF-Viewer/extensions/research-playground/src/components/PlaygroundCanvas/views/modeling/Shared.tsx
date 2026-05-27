import React, { useState, useEffect } from 'react';
import { MAXIMIZE_ICON_IMPORT } from 'lucide-react'; // I will just import the ones needed
import { Maximize2, X, CheckCircle, BarChart3, TrendingUp } from 'lucide-react';
import { ChartData, ModelingData } from './types';
import { pct } from './utils';

// ── Expand modal ───────────────────────────────────────────────────────────────

export function ExpandModal({ title, onClose, children }: {
    title: string; onClose: () => void; children: React.ReactNode;
}) {
    useEffect(() => {
        const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', handler);
        return () => window.removeEventListener('keydown', handler);
    }, [onClose]);

    return (
        <div
            className="fixed inset-0 z-[200] flex items-center justify-center"
            style={{ background: 'rgba(0,0,0,0.78)' }}
            onClick={e => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="bg-[#0b1015] border border-[#1f2d40] rounded-2xl shadow-2xl flex flex-col"
                style={{ width: 'min(90vw, 1100px)', maxHeight: '88vh' }}>
                <div className="flex items-center justify-between px-5 py-3 border-b border-[#1f2d40] shrink-0">
                    <span className="text-[11px] font-semibold uppercase tracking-wider text-[#91b9cd]">{title}</span>
                    <button onClick={onClose}
                        className="p-1.5 rounded-lg border border-[#1f2d40] text-[#566a84] hover:text-white hover:border-[#3c5d80] transition-colors">
                        <X size={13} />
                    </button>
                </div>
                <div className="flex-1 overflow-y-auto p-5">{children}</div>
            </div>
        </div>
    );
}

// ── Section wrapper with double-click expand ───────────────────────────────────

export function Section({ title, icon, children, className = '', expandContent }: {
    title: string; icon: React.ReactNode; children: React.ReactNode;
    className?: string; expandContent?: React.ReactNode;
}) {
    const [expanded, setExpanded] = useState(false);
    const open = () => expandContent && setExpanded(true);

    return (
        <>
            <div
                className={`bg-[#0b1015] rounded-xl border border-[#1f2d40] overflow-hidden ${className}`}
                onDoubleClick={open}
            >
                <div className="flex items-center justify-between px-4 py-3 border-b border-[#1f2d40] bg-[#151a1f]/60">
                    <div className="flex items-center gap-2">
                        <span className="text-[#5acce6]">{icon}</span>
                        <span className="text-[10px] font-semibold uppercase tracking-wider text-[#91b9cd]">{title}</span>
                    </div>
                    {expandContent && (
                        <button onClick={e => { e.stopPropagation(); open(); }}
                            className="p-1 rounded border border-[#1f2d40] text-[#3c5d80] hover:text-[#5acce6] hover:border-[#3c5d80] transition-colors"
                            title="Expand (or double-click)">
                            <Maximize2 size={11} />
                        </button>
                    )}
                </div>
                <div className="p-4">{children}</div>
            </div>
            {expanded && expandContent && (
                <ExpandModal title={title} onClose={() => setExpanded(false)}>
                    {expandContent}
                </ExpandModal>
            )}
        </>
    );
}

// ── Series toggle pill ─────────────────────────────────────────────────────────

export function SeriesToggle({ label, color, active, onClick }: {
    label: string; color: string; active: boolean; onClick: () => void;
}) {
    return (
        <button onClick={onClick}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[10px] font-medium border transition-all"
            style={{
                borderColor: active ? color : '#1f2d40',
                background: active ? `${color}18` : 'transparent',
                color: active ? color : '#3c5d80',
                opacity: active ? 1 : 0.45,
                textDecoration: active ? 'none' : 'line-through',
            }}>
            <span className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ background: active ? color : '#3c5d80' }} />
            {label}
        </button>
    );
}

// ── Comparison table ───────────────────────────────────────────────────────────

export function ComparisonTable({ table }: { table: Array<Record<string, any>> }) {
    if (!table.length) return null;
    const cols = Object.keys(table[0]);
    const metricCols = cols.filter(c => !['Model', 'TT (Sec)'].includes(c));
    return (
        <div className="overflow-x-auto">
            <table className="w-full text-xs border-collapse">
                <thead>
                    <tr className="border-b border-[#1f2d40]">
                        <th className="text-left py-2 px-3 text-[#5acce6] font-semibold uppercase tracking-wider text-[10px]">Model</th>
                        {metricCols.map(c => (
                            <th key={c} className="text-right py-2 px-2 text-[#566a84] uppercase tracking-wider text-[10px] whitespace-nowrap">{c}</th>
                        ))}
                    </tr>
                </thead>
                <tbody>
                    {table.map((row, i) => (
                        <tr key={i} className={`border-b border-[#1f2d40]/50 hover:bg-white/[0.02] transition-colors ${i === 0 ? 'bg-[#5acce6]/5' : ''}`}>
                            <td className="py-2 px-3 font-mono text-[#91b9cd] whitespace-nowrap">{row['Model'] ?? `Model ${i + 1}`}</td>
                            {metricCols.map(c => (
                                <td key={c} className="text-right py-2 px-2 font-mono">
                                    <span className={i === 0 ? 'text-[#5acce6] font-semibold' : 'text-[#566a84]'}>
                                        {typeof row[c] === 'number' ? pct(row[c]) : (row[c] ?? '—')}
                                    </span>
                                </td>
                            ))}
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

// ── Empty state ───────────────────────────────────────────────────────────────
export function EmptyState() {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center space-y-4 p-6 text-center">
      <div className="flex gap-4 opacity-40">
        <BarChart3 size={48} className="text-[#5acce6]" />
        <TrendingUp size={48} className="text-[#6fe5b8]" />
      </div>
      <div>
        <h3 className="text-xl font-semibold text-slate-100">Modeling & Statistics</h3>
        <p className="mt-2 max-w-md text-sm text-[#566a84]">
          ROC curves, PR curves, confusion matrices, and per-model metric comparisons will appear here once training completes.
        </p>
      </div>
    </div>
  );
}

// ── Best metric badge (used in accordion header) ───────────────────────────────

export function BestMetricBadge({ chartData }: { chartData: ChartData }) {
    const best = React.useMemo(() => {
        let bestVal: number | null = null;
        let bestModel = '';
        Object.entries(chartData.per_model).forEach(([, m]) => {
            const acc = m.holdout_metrics?.Accuracy ?? m.holdout_metrics?.accuracy ?? null;
            if (acc != null && (bestVal == null || acc > bestVal)) {
                bestVal = acc;
                bestModel = m.label;
            }
        });
        return { val: bestVal, model: bestModel };
    }, [chartData]);

    if (best.val == null) return null;

    return (
        <span className="flex items-center gap-1.5 text-[10px] font-mono px-2 py-0.5 rounded border border-[#6fe5b8]/30 bg-[#6fe5b8]/8 text-[#6fe5b8]">
            <CheckCircle size={9} />
            Best: {pct(best.val)} ({best.model})
        </span>
    );
}