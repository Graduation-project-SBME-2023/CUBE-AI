import React, { useState, useMemo, useCallback, useEffect } from 'react';
import {
    BarChart3, TrendingUp, GitBranch, CheckCircle,
    Database, Activity, Target, Layers, X, Maximize2,
    ChevronDown, ChevronRight, FlaskConical,
} from 'lucide-react';
import {
    LineChart, Line, BarChart, Bar, XAxis, YAxis, CartesianGrid,
    Tooltip, Legend, ResponsiveContainer, Cell, RadarChart,
    PolarGrid, PolarAngleAxis, Radar,
} from 'recharts';

// ── Types ──────────────────────────────────────────────────────────────────────

interface RocCurve  { fpr: number[]; tpr: number[]; }
interface PrCurve   { precision: number[]; recall: number[]; }

interface PerModelData {
    label: string;
    cv_metrics: Record<string, number | null>;
    holdout_metrics: Record<string, number | null>;
    confusion_matrix: number[][] | null;
    confusion_matrix_labels: string[] | null;
    roc_curve: RocCurve | null;
    pr_curve: PrCurve | null;
}

interface ChartData {
    comparison_table: Array<Record<string, any>>;
    per_model: Record<string, PerModelData>;
    fold: number;
}

export interface ModelingData {
    status: string;
    experiment_name?: string;
    output_dir?: string;
    final_model_path?: string;
    individual_model_paths?: Record<string, string>;
    model_test_predictions?: string;
    model_summary?: string;
    created_files?: string[] | null;
    chart_data?: ChartData | null;
}

interface ModelingViewProps {
    /** Keyed by experiment_name (or a fallback key). Never overwritten — new experiments are added. */
    modelingData?: Record<string, ModelingData> | null;
}

// ── Palette ────────────────────────────────────────────────────────────────────

const MODEL_COLORS = [
    '#5acce6', '#6fe5b8', '#a78bfa', '#f59e0b',
    '#f472b6', '#34d399', '#fb923c', '#60a5fa',
];
const METRIC_KEYS = ['Accuracy', 'AUC', 'F1', 'Precision', 'Recall'];

const fmt = (v: number | null | undefined, d = 3) => v == null ? '—' : v.toFixed(d);
const pct = (v: number | null | undefined) => v == null ? '—' : `${(v * 100).toFixed(1)}%`;

// ── Expand modal ───────────────────────────────────────────────────────────────

function ExpandModal({ title, onClose, children }: {
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

function Section({ title, icon, children, className = '', expandContent }: {
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

function SeriesToggle({ label, color, active, onClick }: {
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

// ── Model comparison bar chart ─────────────────────────────────────────────────

function ModelComparisonBars({ data, metric, hidden, onToggle }: {
    data: ChartData; metric: string;
    hidden: Set<string>; onToggle: (key: string) => void;
}) {
    const allRows = useMemo(() =>
        Object.entries(data.per_model).map(([key, m], i) => ({
            key, name: m.label || key,
            color: MODEL_COLORS[i % MODEL_COLORS.length],
        })), [data]);

    const rows = useMemo(() =>
        allRows.filter(r => !hidden.has(r.key)).map(r => {
            const m = data.per_model[r.key];
            return { ...r, cv: m.cv_metrics?.[metric] ?? null, holdout: m.holdout_metrics?.[metric] ?? null };
        }), [allRows, data, metric, hidden]);

    return (
        <div>
            <div className="flex gap-1.5 mb-3 flex-wrap">
                {allRows.map(r => (
                    <SeriesToggle key={r.key} label={r.name} color={r.color}
                        active={!hidden.has(r.key)} onClick={() => onToggle(r.key)} />
                ))}
            </div>
            <ResponsiveContainer width="100%" height={Math.max(100, rows.length * 56)}>
                <BarChart data={rows} layout="vertical" margin={{ left: 8, right: 24, top: 4, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2d40" horizontal={false} />
                    <XAxis type="number" domain={[0, 1]} tickFormatter={v => `${(v * 100).toFixed(0)}%`}
                        tick={{ fill: '#566a84', fontSize: 10 }} />
                    <YAxis type="category" dataKey="name" width={130} tick={{ fill: '#91b9cd', fontSize: 10 }} />
                    <Tooltip formatter={(v: number) => `${(v * 100).toFixed(2)}%`}
                        contentStyle={{ background: '#0b1015', border: '1px solid #1f2d40', borderRadius: 8 }}
                        labelStyle={{ color: '#5acce6' }} />
                    <Legend wrapperStyle={{ fontSize: 10, color: '#566a84' }} />
                    <Bar dataKey="cv" name={`CV ${metric}`} radius={[0, 4, 4, 0]}>
                        {rows.map((r, i) => <Cell key={i} fill={r.color} fillOpacity={0.4} />)}
                    </Bar>
                    <Bar dataKey="holdout" name={`Holdout ${metric}`} radius={[0, 4, 4, 0]}>
                        {rows.map((r, i) => <Cell key={i} fill={r.color} />)}
                    </Bar>
                </BarChart>
            </ResponsiveContainer>
        </div>
    );
}

// ── ROC chart ──────────────────────────────────────────────────────────────────

function RocChart({ data, hidden, onToggle, height = 260 }: {
    data: ChartData; hidden: Set<string>;
    onToggle: (key: string) => void; height?: number;
}) {
    const series = useMemo(() =>
        Object.entries(data.per_model)
            .filter(([, m]) => m.roc_curve != null)
            .map(([key, m], i) => ({
                key, label: m.label || key,
                color: MODEL_COLORS[i % MODEL_COLORS.length],
                points: m.roc_curve!.fpr.map((fpr, j) => ({
                    fpr: +fpr.toFixed(4),
                    tpr: +(m.roc_curve!.tpr[j] ?? 0).toFixed(4),
                })),
                auc: m.holdout_metrics?.auc ?? m.cv_metrics?.AUC,
            })), [data]);

    if (!series.length) return (
        <div className="flex items-center justify-center h-40 text-[#3c5d80] text-xs">
            ROC curves appear after holdout evaluation
        </div>
    );

    return (
        <div>
            <div className="flex gap-1.5 mb-3 flex-wrap">
                {series.map(s => (
                    <SeriesToggle key={s.key}
                        label={`${s.label}${s.auc != null ? ` (AUC ${fmt(s.auc)})` : ''}`}
                        color={s.color} active={!hidden.has(s.key)} onClick={() => onToggle(s.key)} />
                ))}
            </div>
            <ResponsiveContainer width="100%" height={height}>
                <LineChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2d40" />
                    <XAxis dataKey="fpr" type="number" domain={[0, 1]}
                        label={{ value: 'FPR', position: 'insideBottom', offset: -10, fill: '#566a84', fontSize: 10 }}
                        tick={{ fill: '#566a84', fontSize: 9 }} />
                    <YAxis domain={[0, 1]}
                        label={{ value: 'TPR', angle: -90, position: 'insideLeft', fill: '#566a84', fontSize: 10 }}
                        tick={{ fill: '#566a84', fontSize: 9 }} />
                    <Tooltip contentStyle={{ background: '#0b1015', border: '1px solid #1f2d40', borderRadius: 8, fontSize: 11 }}
                        labelStyle={{ color: '#5acce6' }} formatter={(v: number, name) => [v.toFixed(4), name]} />
                    <Line data={[{ fpr: 0, tpr: 0 }, { fpr: 1, tpr: 1 }]} dataKey="tpr"
                        dot={false} strokeDasharray="4 4" stroke="#2a3a4a" strokeWidth={1}
                        name="Random" legendType="none" />
                    {series.map(s => !hidden.has(s.key) && (
                        <Line key={s.key} data={s.points} dataKey="tpr" dot={false}
                            stroke={s.color} strokeWidth={2} name={s.label} />
                    ))}
                </LineChart>
            </ResponsiveContainer>
        </div>
    );
}

// ── PR chart ───────────────────────────────────────────────────────────────────

function PrChart({ data, hidden, onToggle, height = 220 }: {
    data: ChartData; hidden: Set<string>;
    onToggle: (key: string) => void; height?: number;
}) {
    const series = useMemo(() =>
        Object.entries(data.per_model)
            .filter(([, m]) => m.pr_curve != null)
            .map(([key, m], i) => ({
                key, label: m.label || key,
                color: MODEL_COLORS[i % MODEL_COLORS.length],
                points: m.pr_curve!.recall.map((recall, j) => ({
                    recall: +recall.toFixed(4),
                    precision: +(m.pr_curve!.precision[j] ?? 0).toFixed(4),
                })),
            })), [data]);

    if (!series.length) return (
        <div className="flex items-center justify-center h-40 text-[#3c5d80] text-xs">
            PR curves appear after holdout evaluation
        </div>
    );

    return (
        <div>
            <div className="flex gap-1.5 mb-3 flex-wrap">
                {series.map(s => (
                    <SeriesToggle key={s.key} label={s.label} color={s.color}
                        active={!hidden.has(s.key)} onClick={() => onToggle(s.key)} />
                ))}
            </div>
            <ResponsiveContainer width="100%" height={height}>
                <LineChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2d40" />
                    <XAxis dataKey="recall" type="number" domain={[0, 1]}
                        label={{ value: 'Recall', position: 'insideBottom', offset: -10, fill: '#566a84', fontSize: 10 }}
                        tick={{ fill: '#566a84', fontSize: 9 }} />
                    <YAxis domain={[0, 1]}
                        label={{ value: 'Precision', angle: -90, position: 'insideLeft', fill: '#566a84', fontSize: 10 }}
                        tick={{ fill: '#566a84', fontSize: 9 }} />
                    <Tooltip contentStyle={{ background: '#0b1015', border: '1px solid #1f2d40', borderRadius: 8, fontSize: 11 }}
                        formatter={(v: number, name) => [v.toFixed(4), name]} />
                    {series.map(s => !hidden.has(s.key) && (
                        <Line key={s.key} data={s.points} dataKey="precision" dot={false}
                            stroke={s.color} strokeWidth={2} name={s.label} />
                    ))}
                </LineChart>
            </ResponsiveContainer>
        </div>
    );
}

// ── Confusion matrix ───────────────────────────────────────────────────────────

function ConfusionMatrix({ matrix, labels }: { matrix: number[][]; labels: string[] }) {
    const max = Math.max(...matrix.flat());

    const cellTheme = (val: number, ri: number, ci: number) => {
        const intensity = max > 0 ? val / max : 0;
        const isCorrect = ri === ci;
        if (isCorrect) {
            const bg = `rgba(111,229,184,${0.12 + intensity * 0.82})`;
            const color = intensity > 0.35 ? '#051a0f' : '#6fe5b8';
            return { bg, color };
        } else {
            const bg = `rgba(239,68,68,${0.05 + intensity * 0.65})`;
            const color = intensity > 0.5 ? '#fca5a5' : '#566a84';
            return { bg, color };
        }
    };

    return (
        <div className="flex flex-col items-center gap-1">
            <div className="text-[9px] text-[#566a84] uppercase mb-1">Predicted →</div>
            <div style={{ display: 'grid', gridTemplateColumns: `auto repeat(${labels.length}, 1fr)`, gap: 2 }}>
                <div />
                {labels.map(l => (
                    <div key={l} className="text-[9px] text-[#5acce6] text-center font-mono truncate px-1">{l}</div>
                ))}
                {matrix.map((row, ri) => (
                    <React.Fragment key={ri}>
                        <div className="text-[9px] text-[#566a84] font-mono flex items-center pr-2">{labels[ri]}</div>
                        {row.map((val, ci) => {
                            const { bg, color } = cellTheme(val, ri, ci);
                            return (
                                <div key={ci}
                                    className="flex items-center justify-center rounded text-[10px] font-mono font-bold transition-all"
                                    style={{ minWidth: 36, minHeight: 36, background: bg, color }}>
                                    {val}
                                </div>
                            );
                        })}
                    </React.Fragment>
                ))}
            </div>
            <div className="text-[9px] text-[#566a84] mt-1">↑ Actual</div>
        </div>
    );
}

// ── Radar chart ────────────────────────────────────────────────────────────────

function MetricRadar({ data, hidden, onToggle, height = 220 }: {
    data: ChartData; hidden: Set<string>;
    onToggle: (key: string) => void; height?: number;
}) {
    const models = useMemo(() =>
        Object.entries(data.per_model).map(([k, m], i) => ({
            key: k, label: m.label || k, color: MODEL_COLORS[i % MODEL_COLORS.length],
        })), [data]);

    const radarData = useMemo(() =>
        METRIC_KEYS.map(metric => {
            const entry: Record<string, any> = { metric };
            Object.entries(data.per_model).forEach(([key, m]) => {
                if (!hidden.has(key)) {
                    const v = m.cv_metrics?.[metric];
                    entry[m.label || key] = v != null ? +(v * 100).toFixed(1) : null;
                }
            });
            return entry;
        }), [data, hidden]);

    return (
        <div>
            <div className="flex gap-1.5 mb-3 flex-wrap">
                {models.map(m => (
                    <SeriesToggle key={m.key} label={m.label} color={m.color}
                        active={!hidden.has(m.key)} onClick={() => onToggle(m.key)} />
                ))}
            </div>
            <ResponsiveContainer width="100%" height={height}>
                <RadarChart data={radarData}>
                    <PolarGrid stroke="#1f2d40" />
                    <PolarAngleAxis dataKey="metric" tick={{ fill: '#91b9cd', fontSize: 10 }} />
                    <Tooltip contentStyle={{ background: '#0b1015', border: '1px solid #1f2d40', borderRadius: 8, fontSize: 11 }}
                        formatter={(v: number) => `${v.toFixed(1)}%`} />
                    {models.map(m => !hidden.has(m.key) && (
                        <Radar key={m.key} dataKey={m.label} stroke={m.color}
                            fill={m.color} fillOpacity={0.08} dot={{ r: 3, fill: m.color }} />
                    ))}
                </RadarChart>
            </ResponsiveContainer>
        </div>
    );
}

// ── Comparison table ───────────────────────────────────────────────────────────

function ComparisonTable({ table }: { table: Array<Record<string, any>> }) {
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
function EmptyState() {
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
// ── Best metric badge (used in accordion header) ───────────────────────────────

function BestMetricBadge({ chartData }: { chartData: ChartData }) {
    // Find the best holdout accuracy across all models in this experiment
    const best = useMemo(() => {
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

// ── Per-experiment content panel ───────────────────────────────────────────────

function ExperimentPanel({ expKey, data }: { expKey: string; data: ModelingData }) {
    const [selectedModel, setSelectedModel] = useState<string | null>(null);
    const [activeMetric, setActiveMetric] = useState('Accuracy');
    const [hidden, setHidden] = useState<Record<string, Set<string>>>({});
    const chartData = data.chart_data;
    const hasCharts = !!(chartData && Object.keys(chartData.per_model ?? {}).length);
    const modelKeys = hasCharts ? Object.keys(chartData!.per_model) : [];
    const activeKey = selectedModel ?? modelKeys[0] ?? null;
    const activeModel = activeKey && hasCharts ? chartData!.per_model[activeKey] : null;

    const MetricBar = () => (
        <div className="flex gap-2 flex-wrap">
            {METRIC_KEYS.map(m => (
                <button key={m} onClick={() => setActiveMetric(m)}
                    className={`px-3 py-1 rounded text-[10px] font-medium uppercase tracking-wider border transition-colors ${
                        activeMetric === m
                            ? 'bg-[#5acce6]/20 text-[#5acce6] border-[#5acce6]/60'
                            : 'text-[#566a84] border-[#1f2d40] hover:text-[#91b9cd]'
                    }`}>
                    {m}
                </button>
            ))}
        </div>
    );

    if (!hasCharts) {
        return (
            <div className="px-4 pb-4 pt-2 text-[#3c5d80] text-xs italic">
                No chart data available for this experiment yet.
            </div>
        );
    }

    return (
        <div className="px-4 pb-5 pt-3 space-y-4">
            {/* Meta info badges */}
            <div className="flex items-center gap-2 flex-wrap">
                <div className="flex items-center gap-2 bg-[#0b1015] border border-[#1f2d40] rounded-lg px-3 py-1.5">
                    <Layers size={11} className="text-[#5acce6]" />
                    <span className="text-[10px] text-[#91b9cd]">{modelKeys.length} model{modelKeys.length !== 1 ? 's' : ''}</span>
                </div>
                {chartData?.fold && (
                    <div className="flex items-center gap-2 bg-[#0b1015] border border-[#1f2d40] rounded-lg px-3 py-1.5">
                        <Database size={11} className="text-[#a78bfa]" />
                        <span className="text-[10px] text-[#91b9cd]">{chartData.fold}-fold CV</span>
                    </div>
                )}
                <span className="text-[10px] text-[#3c5d80] ml-auto">Double-click any chart to expand · click pills to toggle series</span>
            </div>

            {/* Comparison table */}
            {chartData.comparison_table?.length > 0 && (
                <Section title="Model Comparison (Cross-Validation)" icon={<BarChart3 size={13} />}
                    expandContent={<ComparisonTable table={chartData.comparison_table} />}>
                    <ComparisonTable table={chartData.comparison_table} />
                </Section>
            )}

            {/* Metric bars + Radar */}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <Section title="Metric Comparison" icon={<Activity size={13} />}
                    expandContent={
                        <div className="space-y-4">
                            <MetricBar />
                            <ModelComparisonBars data={chartData} metric={activeMetric}
                                hidden={h('bars')} onToggle={k => toggle('bars', k)} />
                        </div>
                    }>
                    <div className="space-y-3">
                        <MetricBar />
                        <ModelComparisonBars data={chartData} metric={activeMetric}
                            hidden={h('bars')} onToggle={k => toggle('bars', k)} />
                    </div>
                </Section>

                <Section title="CV Metric Radar" icon={<Target size={13} />}
                    expandContent={
                        <MetricRadar data={chartData} hidden={h('radar')}
                            onToggle={k => toggle('radar', k)} height={380} />
                    }>
                    <MetricRadar data={chartData} hidden={h('radar')}
                        onToggle={k => toggle('radar', k)} />
                </Section>
            </div>

            {/* ROC + PR */}
            <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                <Section title="ROC Curves (Holdout)" icon={<TrendingUp size={13} />}
                    expandContent={
                        <RocChart data={chartData} hidden={h('roc')}
                            onToggle={k => toggle('roc', k)} height={420} />
                    }>
                    <RocChart data={chartData} hidden={h('roc')}
                        onToggle={k => toggle('roc', k)} />
                </Section>

                <Section title="Precision-Recall Curves (Holdout)" icon={<Activity size={13} />}
                    expandContent={
                        <PrChart data={chartData} hidden={h('pr')}
                            onToggle={k => toggle('pr', k)} height={420} />
                    }>
                    <PrChart data={chartData} hidden={h('pr')}
                        onToggle={k => toggle('pr', k)} />
                </Section>
            </div>

            {/* Confusion matrices */}
            {modelKeys.length > 0 && (
                <Section title="Confusion Matrices" icon={<Target size={13} />}
                    expandContent={activeModel?.confusion_matrix && (
                        <div className="flex flex-col lg:flex-row gap-8 items-start">
                            <ConfusionMatrix
                                matrix={activeModel.confusion_matrix}
                                labels={activeModel.confusion_matrix_labels ?? activeModel.confusion_matrix.map((_, i) => String(i))}
                            />
                            {activeModel.holdout_metrics && (
                                <div className="flex-1 grid grid-cols-2 sm:grid-cols-3 gap-3">
                                    {Object.entries(activeModel.holdout_metrics)
                                        .filter(([k]) => !['confusion_matrix', 'confusion_matrix_labels'].includes(k))
                                        .map(([k, v]) => (
                                            <div key={k} className="bg-[#151a1f] rounded-lg border border-[#1f2d40] p-3">
                                                <div className="text-[9px] text-[#566a84] uppercase tracking-wider mb-1">{k}</div>
                                                <div className="text-lg font-mono font-semibold text-[#5acce6]">
                                                    {typeof v === 'number' ? (v <= 1 && v >= 0 ? pct(v) : v.toFixed(3)) : '—'}
                                                </div>
                                            </div>
                                        ))}
                                </div>
                            )}
                        </div>
                    )}>
                    <div className="flex gap-2 mb-4 flex-wrap">
                        {modelKeys.map((key, i) => (
                            <button key={key} onClick={() => setSelectedModel(key)}
                                className={`px-3 py-1.5 rounded-lg text-[10px] font-medium border transition-colors flex items-center gap-1.5 ${
                                    activeKey === key
                                        ? 'border-[#5acce6]/60 text-[#5acce6] bg-[#5acce6]/10'
                                        : 'border-[#1f2d40] text-[#566a84] hover:text-[#91b9cd]'
                                }`}>
                                <span className="w-2 h-2 rounded-full"
                                    style={{ background: MODEL_COLORS[i % MODEL_COLORS.length] }} />
                                {chartData.per_model[key].label || key}
                            </button>
                        ))}
                    </div>
                    {activeModel?.confusion_matrix ? (
                        <div className="flex flex-col lg:flex-row gap-6 items-start">
                            <ConfusionMatrix
                                matrix={activeModel.confusion_matrix}
                                labels={activeModel.confusion_matrix_labels ?? activeModel.confusion_matrix.map((_, i) => String(i))}
                            />
                            {activeModel.holdout_metrics && Object.keys(activeModel.holdout_metrics).length > 0 && (
                                <div className="flex-1 grid grid-cols-2 sm:grid-cols-3 gap-3">
                                    {Object.entries(activeModel.holdout_metrics)
                                        .filter(([k]) => !['confusion_matrix', 'confusion_matrix_labels'].includes(k))
                                        .map(([k, v]) => (
                                            <div key={k} className="bg-[#151a1f] rounded-lg border border-[#1f2d40] p-3">
                                                <div className="text-[9px] text-[#566a84] uppercase tracking-wider mb-1">{k}</div>
                                                <div className="text-lg font-mono font-semibold text-[#5acce6]">
                                                    {typeof v === 'number' ? (v <= 1 && v >= 0 ? pct(v) : v.toFixed(3)) : '—'}
                                                </div>
                                            </div>
                                        ))}
                                </div>
                            )}
                        </div>
                    ) : (
                        <div className="text-[#3c5d80] text-xs text-center py-6">
                            Confusion matrix available after holdout evaluation
                        </div>
                    )}
                </Section>
            )}
        </div>
    );
}

// ── Accordion row for a single experiment ──────────────────────────────────────

function ExperimentAccordion({
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

// ── Main ───────────────────────────────────────────────────────────────────────

export default function ModelingView({ modelingData }: ModelingViewProps) {
    const experiments = modelingData ?? {};
    const expEntries = Object.entries(experiments);

    if (expEntries.length === 0) return <EmptyState />;

    return (
        <div className="w-full h-full overflow-y-auto bg-[#000000] p-5 space-y-3">
            {/* Header */}
            <div className="flex items-center gap-3 flex-wrap mb-1">
                <div className="flex items-center gap-2 bg-[#0b1015] border border-[#1f2d40] rounded-lg px-3 py-2">
                    <FlaskConical size={13} className="text-[#5acce6]" />
                    <span className="text-[11px] text-[#91b9cd]">
                        {expEntries.length} experiment{expEntries.length !== 1 ? 's' : ''}
                    </span>
                </div>
                <span className="text-[10px] text-[#3c5d80] ml-auto">
                    Each experiment is collapsible · newest runs appear at the bottom
                </span>
            </div>

            {/* One accordion per experiment */}
            {expEntries.map(([key, data], i) => (
                <ExperimentAccordion
                    key={key}
                    expKey={key}
                    data={data}
                    index={i}
                    // Auto-open the most recent (last) experiment
                    defaultOpen={i === expEntries.length - 1}
                />
            ))}
        </div>
    );
}
