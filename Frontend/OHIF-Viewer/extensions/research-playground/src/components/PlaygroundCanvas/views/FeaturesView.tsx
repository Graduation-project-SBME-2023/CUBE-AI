import React, { useState, useMemo, useCallback, useEffect } from 'react';
import {
    Scan, BrainCircuit, BarChart3, TrendingUp,
    GitMerge, Layers, CheckCircle, Clock, FileSpreadsheet,
    ChevronDown, ChevronUp, Activity, X, Maximize2
} from 'lucide-react';
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
    ResponsiveContainer, Cell, LineChart, Line, ScatterChart,
    Scatter, ReferenceLine,
} from 'recharts';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface FeaturesData {
    status: string;
    csv_paths?: Record<string, string>;
    num_subjects?: number;
    num_features?: Record<string, number>;
    mask_labels?: number[];
    processing_time_seconds?: number;
}

interface ImportanceBar  { feature: string; importance: number | null; }
interface CumulativePt   { n_features: number; cumulative_pct: number; }
interface ScatterPt      { x: number; y: number; target: string; }
interface PcaData        { points: ScatterPt[]; explained_variance: number[]; n_features: number; }
interface TsneData       { points: ScatterPt[]; n_features: number; }
interface CorrelationData{ labels: string[]; matrix: (number | null)[][]; }

export interface ChartData {
    importance_bars?: ImportanceBar[];
    cumulative?: CumulativePt[];
    correlation?: CorrelationData | null;
    pca?: PcaData | null;
    tsne?: TsneData | null;
    task_type?: string;
    method?: string;
}

export interface FeatureImportanceData {
    status: string;
    method?: string;
    feature_count?: number;
    result_files?: Record<string, string>;
    chart_data?: ChartData | null;
}

// ─── Palette ──────────────────────────────────────────────────────────────────

const TARGET_PALETTE = [
    '#5acce6', '#6fe5b8', '#a78bfa', '#f59e0b',
    '#f472b6', '#34d399', '#fb923c', '#60a5fa',
];

function targetColor(target: string, all: string[]): string {
    return TARGET_PALETTE[all.indexOf(target) % TARGET_PALETTE.length] ?? TARGET_PALETTE[0];
}

function corrBg(v: number | null): string {
    if (v == null) return '#0b1015';
    const abs = Math.abs(v);
    return v > 0
        ? `rgba(90,204,230,${0.08 + abs * 0.85})`
        : `rgba(239,68,68,${0.08 + abs * 0.85})`;
}

function corrText(v: number | null): string {
    if (v == null) return '#3c5d80';
    return Math.abs(v) > 0.5 ? '#fff' : '#91b9cd';
}

// ─── Expand modal ─────────────────────────────────────────────────────────────

function ExpandModal({ title, onClose, children }: {
    title: string; onClose: () => void; children: React.ReactNode;
}) {
    useEffect(() => {
        const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
        window.addEventListener('keydown', h);
        return () => window.removeEventListener('keydown', h);
    }, [onClose]);

    return (
        <div className="fixed inset-0 z-[200] flex items-center justify-center"
            style={{ background: 'rgba(0,0,0,0.78)' }}
            onClick={e => { if (e.target === e.currentTarget) onClose(); }}>
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

// ─── Series toggle pill ───────────────────────────────────────────────────────

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

// ─── Collapsible section (metadata panel) ─────────────────────────────────────

function CollapsibleSection({
    title, icon, defaultOpen = true, children, accent = '#5acce6',
}: {
    title: string; icon: React.ReactNode; defaultOpen?: boolean;
    children: React.ReactNode; accent?: string;
}) {
    const [open, setOpen] = useState(defaultOpen);
    return (
        <div className="bg-[#151a1f] rounded-xl border border-[#1f2d40] overflow-hidden">
            <button onClick={() => setOpen(o => !o)}
                className="w-full flex items-center justify-between px-4 py-3 hover:bg-white/[0.025] transition-colors">
                <span className="flex items-center gap-2 text-[10px] uppercase font-semibold tracking-wider" style={{ color: accent }}>
                    {icon} {title}
                </span>
                {open ? <ChevronUp size={13} className="text-[#566a84]" /> : <ChevronDown size={13} className="text-[#566a84]" />}
            </button>
            {open && <div className="px-4 pb-4">{children}</div>}
        </div>
    );
}

// ─── Chart panel wrapper (with expand) ───────────────────────────────────────

function ChartPanel({ title, expandContent, children }: {
    title: string; expandContent?: React.ReactNode; children: React.ReactNode;
}) {
    const [expanded, setExpanded] = useState(false);
    return (
        <>
            <div className="flex-1 overflow-y-auto bg-[#0b1015] rounded-xl border border-[#1f2d40]"
                onDoubleClick={() => expandContent && setExpanded(true)}>
                <div className="flex items-center justify-between px-4 py-2 border-b border-[#1f2d40] bg-[#151a1f]/60">
                    <span className="text-[10px] text-[#566a84]">{title}</span>
                    {expandContent && (
                        <button onClick={e => { e.stopPropagation(); setExpanded(true); }}
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

// ─── Feature importance bar chart ─────────────────────────────────────────────

function ImportanceBars({ bars, hiddenFeatures, onToggle, compact = true }: {
    bars: ImportanceBar[];
    hiddenFeatures: Set<string>;
    onToggle: (f: string) => void;
    compact?: boolean;
}) {
    const visible = bars.filter(b => !hiddenFeatures.has(b.feature));
    const max = Math.max(...visible.map(b => b.importance ?? 0), 0);

    // Show top-N toggles to avoid overflow — all visible in expanded modal
    const TOGGLE_LIMIT = compact ? 8 : bars.length;
    const toggleBars = bars.slice(0, TOGGLE_LIMIT);

    return (
        <div>
            <div className="flex gap-1.5 mb-3 flex-wrap">
                {toggleBars.map((b, i) => (
                    <SeriesToggle key={b.feature}
                        label={b.feature.length > 24 ? '…' + b.feature.slice(-23) : b.feature}
                        color={`rgba(90,204,230,${0.3 + (i / toggleBars.length) * 0.7})`}
                        active={!hiddenFeatures.has(b.feature)}
                        onClick={() => onToggle(b.feature)} />
                ))}
                {bars.length > TOGGLE_LIMIT && (
                    <span className="text-[9px] text-[#3c5d80] self-center">+{bars.length - TOGGLE_LIMIT} more</span>
                )}
            </div>
            <ResponsiveContainer width="100%" height={Math.min(compact ? 360 : 700, visible.length * 22 + 40)}>
                <BarChart data={visible} layout="vertical" margin={{ left: 4, right: 24, top: 4, bottom: 4 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2d40" horizontal={false} />
                    <XAxis type="number" tick={{ fill: '#566a84', fontSize: 9 }}
                        domain={[0, max * 1.05]} tickFormatter={v => v.toFixed(4)} />
                    <YAxis type="category" dataKey="feature" width={180} tick={{ fill: '#91b9cd', fontSize: 9 }}
                        tickFormatter={v => v.length > 28 ? v.slice(0, 27) + '…' : v} />
                    <Tooltip contentStyle={{ background: '#0b1015', border: '1px solid #1f2d40', borderRadius: 8, fontSize: 11 }}
                        formatter={(v: number) => [v.toFixed(6), 'Importance']} labelStyle={{ color: '#5acce6' }} />
                    <Bar dataKey="importance" radius={[0, 4, 4, 0]}>
                        {visible.map((b, i) => {
                            const intensity = max > 0 ? (b.importance ?? 0) / max : 0;
                            return <Cell key={i} fill={`rgba(90,204,230,${0.2 + intensity * 0.8})`} />;
                        })}
                    </Bar>
                </BarChart>
            </ResponsiveContainer>
        </div>
    );
}

// ─── Cumulative importance curve ──────────────────────────────────────────────

const THRESHOLDS = [
    { pct: 80, color: '#6fe5b8', label: '80%' },
    { pct: 90, color: '#5acce6', label: '90%' },
    { pct: 95, color: '#a78bfa', label: '95%' },
    { pct: 99, color: '#f59e0b', label: '99%' },
];

function CumulativeCurve({ data, hiddenThresholds, onToggle, height = 220 }: {
    data: CumulativePt[];
    hiddenThresholds: Set<number>;
    onToggle: (pct: number) => void;
    height?: number;
}) {
    if (!data.length) return null;
    return (
        <div>
            <div className="flex gap-1.5 mb-3 flex-wrap">
                {THRESHOLDS.map(t => (
                    <SeriesToggle key={t.pct} label={t.label} color={t.color}
                        active={!hiddenThresholds.has(t.pct)} onClick={() => onToggle(t.pct)} />
                ))}
            </div>
            <ResponsiveContainer width="100%" height={height}>
                <LineChart data={data} margin={{ top: 8, right: 40, bottom: 24, left: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2d40" />
                    <XAxis dataKey="n_features" tick={{ fill: '#566a84', fontSize: 9 }}
                        label={{ value: 'Number of features', position: 'insideBottom', offset: -10, fill: '#566a84', fontSize: 10 }} />
                    <YAxis domain={[0, 100]} tick={{ fill: '#566a84', fontSize: 9 }}
                        tickFormatter={v => `${v}%`}
                        label={{ value: 'Cumulative %', angle: -90, position: 'insideLeft', fill: '#566a84', fontSize: 10 }} />
                    <Tooltip contentStyle={{ background: '#0b1015', border: '1px solid #1f2d40', borderRadius: 8, fontSize: 11 }}
                        formatter={(v: number) => [`${v.toFixed(1)}%`, 'Cumulative importance']}
                        labelFormatter={v => `Top ${v} features`} labelStyle={{ color: '#5acce6' }} />
                    {THRESHOLDS.map(t => !hiddenThresholds.has(t.pct) && (
                        <ReferenceLine key={t.pct} y={t.pct} stroke={t.color}
                            strokeDasharray="4 3" strokeWidth={1}
                            label={{ value: t.label, position: 'right', fill: t.color, fontSize: 9 }} />
                    ))}
                    <Line dataKey="cumulative_pct" dot={false} stroke="#5acce6" strokeWidth={2} />
                </LineChart>
            </ResponsiveContainer>
        </div>
    );
}

// ─── Correlation heatmap ──────────────────────────────────────────────────────

function CorrelationHeatmap({ data }: { data: CorrelationData }) {
    const { labels, matrix } = data;
    const cellSize = Math.max(28, Math.min(48, Math.floor(480 / labels.length)));

    return (
        <div className="overflow-auto">
            <div style={{ display: 'inline-block' }}>
                <div style={{ display: 'flex', marginLeft: cellSize + 4 }}>
                    {labels.map((l, i) => (
                        <div key={i} style={{ width: cellSize, flexShrink: 0 }}
                            className="text-[8px] text-[#566a84] text-center font-mono overflow-hidden"
                            title={l}>
                            {l.length > 6 ? l.slice(-5) : l}
                        </div>
                    ))}
                </div>
                {matrix.map((row, ri) => (
                    <div key={ri} style={{ display: 'flex', alignItems: 'center', marginBottom: 2 }}>
                        <div style={{ width: cellSize, flexShrink: 0, marginRight: 4 }}
                            className="text-[8px] text-[#566a84] font-mono text-right pr-1 overflow-hidden"
                            title={labels[ri]}>
                            {labels[ri].length > 8 ? '…' + labels[ri].slice(-7) : labels[ri]}
                        </div>
                        {row.map((val, ci) => (
                            <div key={ci}
                                style={{
                                    width: cellSize, height: cellSize, flexShrink: 0,
                                    background: corrBg(val), color: corrText(val),
                                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                                    fontSize: 8, fontFamily: 'monospace', borderRadius: 2,
                                    marginRight: 2, cursor: 'default',
                                }}
                                title={val != null ? `${labels[ri]} × ${labels[ci]}: ${val.toFixed(3)}` : '—'}>
                                {val != null && cellSize >= 36 ? val.toFixed(2) : ''}
                            </div>
                        ))}
                    </div>
                ))}
                <div className="flex items-center gap-2 mt-3 px-1">
                    <span className="text-[9px] text-[#ef4444]">−1</span>
                    <div style={{
                        flex: 1, height: 6, borderRadius: 3,
                        background: 'linear-gradient(to right, rgba(239,68,68,0.9), #1f2d40, rgba(90,204,230,0.9))',
                    }} />
                    <span className="text-[9px] text-[#5acce6]">+1</span>
                </div>
            </div>
        </div>
    );
}

// ─── Scatter plot (PCA / t-SNE) ───────────────────────────────────────────────

function ScatterPlot({ points, xLabel, yLabel, hiddenClasses, onToggle }: {
    points: ScatterPt[]; xLabel: string; yLabel: string;
    hiddenClasses: Set<string>; onToggle: (cls: string) => void;
}) {
    const allTargets = useMemo(() => [...new Set(points.map(p => p.target))].sort(), [points]);
    const grouped = useMemo(() =>
        allTargets.map(t => ({
            target: t,
            color: targetColor(t, allTargets),
            data: hiddenClasses.has(t) ? [] : points.filter(p => p.target === t),
        })), [points, allTargets, hiddenClasses]);

    return (
        <div>
            <div className="flex gap-1.5 mb-3 flex-wrap">
                {allTargets.map(t => (
                    <SeriesToggle key={t} label={`Class ${t}`}
                        color={targetColor(t, allTargets)}
                        active={!hiddenClasses.has(t)} onClick={() => onToggle(t)} />
                ))}
            </div>
            <ResponsiveContainer width="100%" height={240}>
                <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#1f2d40" />
                    <XAxis type="number" dataKey="x" name={xLabel} tick={{ fill: '#566a84', fontSize: 9 }}
                        label={{ value: xLabel, position: 'insideBottom', offset: -10, fill: '#566a84', fontSize: 10 }} />
                    <YAxis type="number" dataKey="y" name={yLabel} tick={{ fill: '#566a84', fontSize: 9 }}
                        label={{ value: yLabel, angle: -90, position: 'insideLeft', fill: '#566a84', fontSize: 10 }} />
                    <Tooltip contentStyle={{ background: '#0b1015', border: '1px solid #1f2d40', borderRadius: 8, fontSize: 11 }}
                        cursor={{ strokeDasharray: '3 3', stroke: '#3c5d80' }}
                        formatter={(v: number, name) => [v.toFixed(3), name]} />
                    {grouped.map(g => (
                        <Scatter key={g.target} name={`Class ${g.target}`} data={g.data}
                            fill={g.color} fillOpacity={0.75} r={3} />
                    ))}
                </ScatterChart>
            </ResponsiveContainer>
        </div>
    );
}

// ─── Metadata panel ───────────────────────────────────────────────────────────

function MetaPanel({ featuresData, importanceData }: {
    featuresData?: FeaturesData | null;
    importanceData?: FeatureImportanceData | null;
}) {
    const hasFeatures   = featuresData?.status === 'success';
    const hasImportance = importanceData?.status === 'success';
    const cd = importanceData?.chart_data;

    const featureEntries = Object.entries(featuresData?.num_features || {});
    const totalFeatures  = featureEntries.reduce((s, [, c]) => s + c, 0);
    const csvEntries     = Object.entries(featuresData?.csv_paths || {});

    return (
        <div className="w-64 flex flex-col gap-3 shrink-0 overflow-y-auto pr-0.5">
            {hasFeatures && (
                <CollapsibleSection title="Extraction Summary" icon={<CheckCircle size={11} />} accent="#6fe5b8">
                    <div className="grid grid-cols-2 gap-2 pt-1">
                        <div className="bg-[#0b1015] rounded-lg p-3 border border-[#1f2d40]">
                            <div className="text-[9px] text-[#566a84] uppercase mb-1">Subjects</div>
                            <div className="text-lg font-semibold text-gray-100">{featuresData?.num_subjects ?? '—'}</div>
                        </div>
                        <div className="bg-[#0b1015] rounded-lg p-3 border border-[#1f2d40]">
                            <div className="text-[9px] text-[#566a84] uppercase mb-1">Features</div>
                            <div className="text-lg font-semibold text-gray-100">{totalFeatures}</div>
                        </div>
                    </div>
                    {!!featuresData?.mask_labels?.length && (
                        <div className="mt-2">
                            <div className="text-[9px] text-[#566a84] uppercase mb-1">Labels</div>
                            <div className="flex flex-wrap gap-1">
                                {featuresData.mask_labels.map(l => (
                                    <span key={l} className="text-[9px] bg-[#3c5d80]/30 text-[#5acce6] px-2 py-0.5 rounded font-mono">{l}</span>
                                ))}
                            </div>
                        </div>
                    )}
                    {featuresData?.processing_time_seconds != null && (
                        <div className="flex items-center gap-1.5 text-[10px] text-[#566a84] mt-2">
                            <Clock size={10} /> {featuresData.processing_time_seconds.toFixed(1)}s
                        </div>
                    )}
                    {featureEntries.length > 0 && (
                        <div className="mt-2 space-y-1">
                            {featureEntries.map(([label, count]) => (
                                <div key={label} className="flex justify-between text-[10px]">
                                    <span className="text-[#566a84]">Label {label}</span>
                                    <span className="text-[#91b9cd] font-mono">{count}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </CollapsibleSection>
            )}

            {hasImportance && cd && (
                <CollapsibleSection title="Analysis Info" icon={<BarChart3 size={11} />} accent="#a78bfa">
                    <div className="space-y-2 pt-1 text-[11px]">
                        <div className="flex justify-between">
                            <span className="text-[#566a84]">Method</span>
                            <span className="text-[#91b9cd] font-mono">{cd.method ?? importanceData.method}</span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-[#566a84]">Task</span>
                            <span className="text-[#91b9cd] font-mono">{cd.task_type}</span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-[#566a84]">Total Features</span>
                            <span className="text-[#91b9cd] font-mono">{importanceData.feature_count}</span>
                        </div>
                    </div>
                </CollapsibleSection>
            )}

            {hasFeatures && csvEntries.length > 0 && (
                <CollapsibleSection title="Feature Files" icon={<FileSpreadsheet size={11} />}
                    defaultOpen={false} accent="#f59e0b">
                    <div className="flex flex-col gap-2 pt-1">
                        {csvEntries.map(([label, path]) => (
                            <div key={label} className="bg-[#0b1015] rounded-lg border border-[#1f2d40] p-2 hover:border-[#5acce6]/40 transition-colors">
                                <div className="flex justify-between items-center">
                                    <span className="text-[10px] font-medium text-[#91b9cd]">{label}</span>
                                    <span className="text-[9px] bg-[#6fe5b8]/20 text-[#6fe5b8] font-semibold px-1.5 py-0.5 rounded">CSV</span>
                                </div>
                                <div className="text-[9px] text-[#3c5d80] font-mono truncate mt-0.5">{path.split(/[/\\]/).pop()}</div>
                            </div>
                        ))}
                    </div>
                </CollapsibleSection>
            )}
        </div>
    );
}

// ─── Empty placeholder ────────────────────────────────────────────────────────

function EmptyCharts() {
    return (
        <div className="flex-1 flex flex-col items-center justify-center gap-4 text-center p-8">
            <div className="relative">
                <Scan size={48} className="text-[#1f2d40]" />
                <BrainCircuit size={22} className="text-[#5acce6] absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 animate-pulse" />
            </div>
            <div>
                <p className="text-[#566a84] text-sm font-medium">Feature analysis not yet complete</p>
                <p className="text-[#3c5d80] text-xs mt-1">Charts will appear once the importance agent finishes.</p>
            </div>
        </div>
    );
}

// ─── Tab definitions ──────────────────────────────────────────────────────────

type TabId = 'importance' | 'cumulative' | 'correlation' | 'pca' | 'tsne';

const TABS: { id: TabId; label: string; icon: React.ReactNode }[] = [
    { id: 'importance',  label: 'Importance',   icon: <BarChart3 size={11} /> },
    { id: 'cumulative',  label: 'Cumulative',   icon: <TrendingUp size={11} /> },
    { id: 'correlation', label: 'Correlation',  icon: <GitMerge size={11} /> },
    { id: 'pca',         label: 'PCA',          icon: <Layers size={11} /> },
    { id: 'tsne',        label: 't-SNE',        icon: <Activity size={11} /> },
];

// ─── Main export ──────────────────────────────────────────────────────────────

interface FeaturesViewProps {
    featuresData?: FeaturesData | null;
    importanceData?: FeatureImportanceData | null;
}

export default function FeaturesView({ featuresData, importanceData }: FeaturesViewProps) {
    const [activeTab, setActiveTab] = useState<TabId>('importance');

    // Per-chart hidden series/classes
    const [hiddenFeatures,    setHiddenFeatures]    = useState<Set<string>>(new Set());
    const [hiddenThresholds,  setHiddenThresholds]  = useState<Set<number>>(new Set());
    const [hiddenPcaClasses,  setHiddenPcaClasses]  = useState<Set<string>>(new Set());
    const [hiddenTsneClasses, setHiddenTsneClasses] = useState<Set<string>>(new Set());

    const toggleFeature    = useCallback((f: string) =>
        setHiddenFeatures(prev => { const s = new Set(prev); s.has(f) ? s.delete(f) : s.add(f); return s; }), []);
    const toggleThreshold  = useCallback((p: number) =>
        setHiddenThresholds(prev => { const s = new Set(prev); s.has(p) ? s.delete(p) : s.add(p); return s; }), []);
    const togglePcaClass   = useCallback((c: string) =>
        setHiddenPcaClasses(prev => { const s = new Set(prev); s.has(c) ? s.delete(c) : s.add(c); return s; }), []);
    const toggleTsneClass  = useCallback((c: string) =>
        setHiddenTsneClasses(prev => { const s = new Set(prev); s.has(c) ? s.delete(c) : s.add(c); return s; }), []);

    const cd = importanceData?.chart_data;
    const hasCharts = !!(cd && (
        cd.importance_bars?.length ||
        cd.cumulative?.length ||
        cd.correlation ||
        cd.pca ||
        cd.tsne
    ));

    const available: Record<TabId, boolean> = {
        importance:  !!(cd?.importance_bars?.length),
        cumulative:  !!(cd?.cumulative?.length),
        correlation: !!(cd?.correlation),
        pca:         !!(cd?.pca),
        tsne:        !!(cd?.tsne),
    };
    const displayTab = available[activeTab]
        ? activeTab
        : ((Object.keys(available) as TabId[]).find(t => available[t]) ?? 'importance');

    return (
        <div className="p-4 w-full h-full flex flex-col gap-4 bg-[#000000] overflow-hidden">
            <div className="flex gap-4 flex-1 min-h-0">

                {/* ── MAIN charts area ── */}
                <div className="flex-1 flex flex-col min-h-0 min-w-0">
                    {hasCharts && cd ? (
                        <>
                            {/* Tab bar */}
                            <div className="flex items-center gap-1 mb-3 flex-wrap shrink-0">
                                {TABS.map(tab => (
                                    <button key={tab.id}
                                        onClick={() => available[tab.id] && setActiveTab(tab.id)}
                                        disabled={!available[tab.id]}
                                        className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-medium border transition-colors ${
                                            displayTab === tab.id
                                                ? 'bg-[#5acce6]/15 text-[#5acce6] border-[#5acce6]/50'
                                                : available[tab.id]
                                                    ? 'text-[#566a84] border-[#1f2d40] hover:text-[#91b9cd]'
                                                    : 'text-[#2a3a4a] border-[#1a2530] cursor-not-allowed opacity-40'
                                        }`}>
                                        {tab.icon} {tab.label}
                                    </button>
                                ))}
                                <span className="text-[9px] text-[#3c5d80] ml-auto">
                                    Double-click to expand · click pills to toggle
                                </span>
                            </div>

                            {/* Active tab content */}
                            {displayTab === 'importance' && cd.importance_bars?.length && (
                                <ChartPanel
                                    title={`Top ${cd.importance_bars.length} features — method: ${cd.method ?? '—'}`}
                                    expandContent={
                                        <ImportanceBars bars={cd.importance_bars}
                                            hiddenFeatures={hiddenFeatures} onToggle={toggleFeature}
                                            compact={false} />
                                    }>
                                    <ImportanceBars bars={cd.importance_bars}
                                        hiddenFeatures={hiddenFeatures} onToggle={toggleFeature} />
                                </ChartPanel>
                            )}

                            {displayTab === 'cumulative' && cd.cumulative?.length && (
                                <ChartPanel
                                    title="Cumulative importance — features needed to explain X% of variance"
                                    expandContent={
                                        <CumulativeCurve data={cd.cumulative}
                                            hiddenThresholds={hiddenThresholds}
                                            onToggle={toggleThreshold} height={400} />
                                    }>
                                    <CumulativeCurve data={cd.cumulative}
                                        hiddenThresholds={hiddenThresholds}
                                        onToggle={toggleThreshold} />
                                </ChartPanel>
                            )}

                            {displayTab === 'correlation' && cd.correlation && (
                                <ChartPanel
                                    title="Pairwise correlation — top 20 features (teal = positive, red = negative)"
                                    expandContent={<CorrelationHeatmap data={cd.correlation} />}>
                                    <CorrelationHeatmap data={cd.correlation} />
                                </ChartPanel>
                            )}

                            {displayTab === 'pca' && cd.pca && (
                                <ChartPanel
                                    title={`PCA — top ${cd.pca.n_features} features (PC1 ${(cd.pca.explained_variance[0]*100).toFixed(1)}%, PC2 ${(cd.pca.explained_variance[1]*100).toFixed(1)}%)`}
                                    expandContent={
                                        <ScatterPlot points={cd.pca.points} xLabel="PC1" yLabel="PC2"
                                            hiddenClasses={hiddenPcaClasses} onToggle={togglePcaClass} />
                                    }>
                                    <ScatterPlot points={cd.pca.points} xLabel="PC1" yLabel="PC2"
                                        hiddenClasses={hiddenPcaClasses} onToggle={togglePcaClass} />
                                </ChartPanel>
                            )}

                            {displayTab === 'tsne' && cd.tsne && (
                                <ChartPanel
                                    title={`t-SNE — top ${cd.tsne.n_features} features`}
                                    expandContent={
                                        <ScatterPlot points={cd.tsne.points} xLabel="D1" yLabel="D2"
                                            hiddenClasses={hiddenTsneClasses} onToggle={toggleTsneClass} />
                                    }>
                                    <ScatterPlot points={cd.tsne.points} xLabel="D1" yLabel="D2"
                                        hiddenClasses={hiddenTsneClasses} onToggle={toggleTsneClass} />
                                </ChartPanel>
                            )}
                        </>
                    ) : (
                        <div className="flex-1 bg-[#0b1015] rounded-xl border border-[#1f2d40] flex flex-col">
                            <EmptyCharts />
                        </div>
                    )}
                </div>

                {/* ── RIGHT metadata panel ── */}
                <MetaPanel featuresData={featuresData} importanceData={importanceData} />
            </div>
        </div>
    );
}