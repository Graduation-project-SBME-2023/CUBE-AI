import React from 'react';
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { ChartData } from './types';

// ─── Correlation heatmap utilities ────────────────────────────────────────────

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

function CorrelationHeatmap({ data }: { data: NonNullable<ChartData['correlation']> }) {
  const { labels, matrix } = data;
  const cellSize = Math.max(28, Math.min(48, Math.floor(480 / labels.length)));

  return (
    <div className="overflow-auto">
      <div style={{ display: 'inline-block' }}>
        <div style={{ display: 'flex', marginLeft: cellSize + 4 }}>
          {labels.map((l, i) => (
            <div
              key={i}
              style={{ width: cellSize, flexShrink: 0 }}
              className="text-center font-mono text-[8px] text-[#566a84] overflow-hidden"
              title={l}
            >
              {l.length > 6 ? l.slice(-5) : l}
            </div>
          ))}
        </div>
        {matrix.map((row, ri) => (
          <div key={ri} style={{ display: 'flex', alignItems: 'center', marginBottom: 2 }}>
            <div
              style={{ width: cellSize, flexShrink: 0, marginRight: 4 }}
              className="pr-1 text-right font-mono text-[8px] text-[#566a84] overflow-hidden"
              title={labels[ri]}
            >
              {labels[ri].length > 8 ? `...${labels[ri].slice(-7)}` : labels[ri]}
            </div>
            {row.map((val, ci) => (
              <div
                key={ci}
                style={{
                  width: cellSize,
                  height: cellSize,
                  flexShrink: 0,
                  background: corrBg(val),
                  color: corrText(val),
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 8,
                  fontFamily: 'monospace',
                  borderRadius: 2,
                  marginRight: 2,
                  cursor: 'default',
                }}
                title={val != null ? `${labels[ri]} × ${labels[ci]}: ${val.toFixed(3)}` : '—'}
              >
                {val != null && cellSize >= 36 ? val.toFixed(2) : ''}
              </div>
            ))}
          </div>
        ))}
        <div className="mt-3 flex items-center gap-2 px-1">
          <span className="text-[9px] text-[#ef4444]">−1</span>
          <div
            style={{
              flex: 1,
              height: 6,
              borderRadius: 3,
              background: 'linear-gradient(to right, rgba(239,68,68,0.9), #1f2d40, rgba(90,204,230,0.9))',
            }}
          />
          <span className="text-[9px] text-[#5acce6]">+1</span>
        </div>
      </div>
    </div>
  );
}

type TabId = 'importance' | 'cumulative' | 'correlation' | 'pca' | 'tsne';

interface FeaturePlotsProps {
  activeTab: TabId;
  chartData: ChartData;
  hiddenThresholds: Set<number>;
  toggleThreshold: (value: number) => void;
  hiddenPcaClasses: Set<string>;
  togglePcaClass: (value: string) => void;
  hiddenTsneClasses: Set<string>;
  toggleTsneClass: (value: string) => void;
}

const THRESHOLDS = [
  { pct: 80, color: '#6fe5b8', label: '80%' },
  { pct: 90, color: '#5acce6', label: '90%' },
  { pct: 95, color: '#a78bfa', label: '95%' },
  { pct: 99, color: '#f59e0b', label: '99%' },
];

const TARGET_PALETTE = ['#5acce6', '#6fe5b8', '#a78bfa', '#f59e0b', '#f472b6', '#34d399', '#fb923c', '#60a5fa'];
const targetColor = (target: string, all: string[]) => TARGET_PALETTE[all.indexOf(target) % TARGET_PALETTE.length] ?? TARGET_PALETTE[0];

function TogglePill({ label, color, active, onClick }: { label: string; color: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-medium transition-all"
      style={{
        borderColor: active ? color : '#1f2d40',
        background: active ? `${color}18` : 'transparent',
        color: active ? color : '#3c5d80',
        opacity: active ? 1 : 0.45,
        textDecoration: active ? 'none' : 'line-through',
      }}
    >
      <span className="h-2 w-2 rounded-full" style={{ background: active ? color : '#3c5d80' }} />
      {label}
    </button>
  );
}

function ScatterByClass({
  points,
  xLabel,
  yLabel,
  hiddenClasses,
  onToggle,
}: {
  points: Array<{ x: number; y: number; target: string }>;
  xLabel: string;
  yLabel: string;
  hiddenClasses: Set<string>;
  onToggle: (value: string) => void;
}) {
  const classes = [...new Set(points.map(p => p.target))].sort();
  const grouped = classes.map(t => ({
    target: t,
    color: targetColor(t, classes),
    data: hiddenClasses.has(t) ? [] : points.filter(p => p.target === t),
  }));

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {classes.map(c => (
          <TogglePill key={c} label={`Class ${c}`} color={targetColor(c, classes)} active={!hiddenClasses.has(c)} onClick={() => onToggle(c)} />
        ))}
      </div>
      <ResponsiveContainer width="100%" height={240}>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 24, left: 8 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2d40" />
          <XAxis
            type="number"
            dataKey="x"
            name={xLabel}
            tick={{ fill: '#566a84', fontSize: 9 }}
            label={{ value: xLabel, position: 'insideBottom', offset: -10, fill: '#566a84', fontSize: 10 }}
          />
          <YAxis
            type="number"
            dataKey="y"
            name={yLabel}
            tick={{ fill: '#566a84', fontSize: 9 }}
            label={{ value: yLabel, angle: -90, position: 'insideLeft', fill: '#566a84', fontSize: 10 }}
          />
          <Tooltip
            contentStyle={{ background: '#0b1015', border: '1px solid #1f2d40', borderRadius: 8, fontSize: 11 }}
            cursor={{ strokeDasharray: '3 3', stroke: '#3c5d80' }}
            formatter={(v: number, name) => [v.toFixed(3), name]}
          />
          {grouped.map(g => (
            <Scatter key={g.target} name={`Class ${g.target}`} data={g.data} fill={g.color} fillOpacity={0.75} r={3} />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  );
}

export default function FeaturePlots({
  activeTab,
  chartData,
  hiddenThresholds,
  toggleThreshold,
  hiddenPcaClasses,
  togglePcaClass,
  hiddenTsneClasses,
  toggleTsneClass,
}: FeaturePlotsProps) {
  // ── Switch strictly on the active tab — never fall through ────────────────
  if (activeTab === 'cumulative') {
    if (!chartData.cumulative?.length) return <p className="text-xs text-[#3c5d80]">No cumulative data available.</p>;
    return (
      <div>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {THRESHOLDS.map(t => (
            <TogglePill key={t.pct} label={t.label} color={t.color} active={!hiddenThresholds.has(t.pct)} onClick={() => toggleThreshold(t.pct)} />
          ))}
        </div>
        <ResponsiveContainer width="100%" height={220}>
          <LineChart data={chartData.cumulative} margin={{ top: 8, right: 40, bottom: 24, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1f2d40" />
            <XAxis
              dataKey="n_features"
              tick={{ fill: '#566a84', fontSize: 9 }}
              label={{ value: 'Number of features', position: 'insideBottom', offset: -10, fill: '#566a84', fontSize: 10 }}
            />
            <YAxis
              domain={[0, 100]}
              tick={{ fill: '#566a84', fontSize: 9 }}
              tickFormatter={v => `${v}%`}
              label={{ value: 'Cumulative %', angle: -90, position: 'insideLeft', fill: '#566a84', fontSize: 10 }}
            />
            <Tooltip
              contentStyle={{ background: '#0b1015', border: '1px solid #1f2d40', borderRadius: 8, fontSize: 11 }}
              formatter={(v: number) => [`${v.toFixed(1)}%`, 'Cumulative importance']}
              labelFormatter={v => `Top ${v} features`}
              labelStyle={{ color: '#5acce6' }}
            />
            {THRESHOLDS.map(t =>
              !hiddenThresholds.has(t.pct) ? (
                <ReferenceLine key={t.pct} y={t.pct} stroke={t.color} strokeDasharray="4 3" strokeWidth={1} />
              ) : null
            )}
            <Line dataKey="cumulative_pct" dot={false} stroke="#5acce6" strokeWidth={2} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (activeTab === 'correlation') {
    if (!chartData.correlation) return <p className="text-xs text-[#3c5d80]">No correlation data available.</p>;
    return <CorrelationHeatmap data={chartData.correlation} />;
  }

  if (activeTab === 'pca') {
    if (!chartData.pca) return <p className="text-xs text-[#3c5d80]">No PCA data available.</p>;
    return (
      <ScatterByClass
        points={chartData.pca.points}
        xLabel="PC1"
        yLabel="PC2"
        hiddenClasses={hiddenPcaClasses}
        onToggle={togglePcaClass}
      />
    );
  }

  if (activeTab === 'tsne') {
    if (!chartData.tsne) return <p className="text-xs text-[#3c5d80]">No t-SNE data available.</p>;
    return (
      <ScatterByClass
        points={chartData.tsne.points}
        xLabel="D1"
        yLabel="D2"
        hiddenClasses={hiddenTsneClasses}
        onToggle={toggleTsneClass}
      />
    );
  }

  return null;
}
