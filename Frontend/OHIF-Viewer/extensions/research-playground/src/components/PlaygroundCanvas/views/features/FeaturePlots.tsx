import React, { useMemo, useState } from 'react';
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
  if (v == null) return '#1f2d40'; // Faded text for sparse cells
  return Math.abs(v) > 0.5 ? '#fff' : '#91b9cd';
}

function CorrelationHeatmap({ data }: { data: NonNullable<ChartData['correlation']> }) {
  const [sparsityThreshold, setSparsityThreshold] = useState(0.6);
  const { labels, matrix } = data;
  const cellSize = Math.max(12, Math.min(48, Math.floor(500 / labels.length)));

  return (
    <div className="flex flex-col h-full gap-4">
      <div className="flex items-center gap-4">
        <label className="text-[10px] uppercase tracking-wide text-[#3c5d80]">
          Sparsity Threshold:
        </label>
        <input
          type="range"
          min={0.0}
          max={0.95}
          step={0.05}
          value={sparsityThreshold}
          onChange={e => setSparsityThreshold(parseFloat(e.target.value))}
          className="h-1 w-32 cursor-pointer accent-[#a78bfa]"
        />
        <span className="font-mono text-[10px] text-[#566a84]">
          {sparsityThreshold.toFixed(2)}
        </span>
        <span className="text-[9px] text-[#3c5d80] italic ml-2">
          (Hides correlations weaker than {sparsityThreshold.toFixed(2)})
        </span>
      </div>

      <div className="ohif-scrollbar [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar]:w-2 [&::-webkit-scrollbar]:h-2 hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]" style={{ height: '600px', overflow: 'auto' }}>
        <div className="flex flex-col items-center min-w-full pb-8">
          <div>
            <div style={{ display: 'flex', marginLeft: 140 + 8 }}>
            {labels.map((l, i) => (
              <div
                key={i}
                style={{ width: cellSize, flexShrink: 0, height: 90, position: 'relative' }}
              >
                <div
                  className="absolute bottom-2 left-1/2 origin-bottom-left -translate-x-1/2 -rotate-45 font-mono text-[9px] text-[#91b9cd] whitespace-nowrap"
                  title={l}
                >
                  {l.length > 25 ? l.slice(0, 23) + '…' : l}
                </div>
              </div>
            ))}
          </div>
          {matrix.map((row, ri) => (
            <div key={ri} style={{ display: 'flex', alignItems: 'center', marginBottom: 2 }}>
              <div
                style={{ width: 140, flexShrink: 0, marginRight: 8 }}
                className="pr-1 text-right font-mono text-[9px] text-[#91b9cd] truncate"
                title={labels[ri]}
              >
                {labels[ri]}
              </div>
              {row.map((val, ci) => {
                const isSparse = Math.abs(val) < sparsityThreshold;
                const displayVal = isSparse ? null : val;
                return (
                  <div
                    key={ci}
                    style={{
                      width: cellSize,
                      height: cellSize,
                      flexShrink: 0,
                      background: corrBg(displayVal),
                      color: corrText(displayVal),
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: 9,
                      fontFamily: 'monospace',
                      borderRadius: 2,
                      marginRight: 2,
                      cursor: 'default',
                      transition: 'background 0.2s, color 0.2s'
                    }}
                    title={val != null ? `${labels[ri]} × ${labels[ci]}: ${val.toFixed(3)}` : '—'}
                  >
                    {displayVal != null && cellSize >= 36 ? displayVal.toFixed(2) : ''}
                  </div>
                );
              })}
            </div>
          ))}
          <div className="mt-3 flex items-center gap-2 px-1 pb-4 w-64">
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
  scatterOpacity?: number;
  selectedIndex?: number | null;
  onSelectIndex?: (idx: number | null) => void;
}

const THRESHOLDS = [
  { pct: 80, color: '#6fe5b8', label: '80%' },
  { pct: 90, color: '#5acce6', label: '90%' },
  { pct: 95, color: '#a78bfa', label: '95%' },
  { pct: 99, color: '#f59e0b', label: '99%' },
];

const TARGET_PALETTE = ['#5acce6', '#6fe5b8', '#a78bfa', '#f59e0b', '#f472b6', '#34d399', '#fb923c', '#60a5fa'];
const targetColor = (target: string, all: string[]) =>
  TARGET_PALETTE[all.indexOf(target) % TARGET_PALETTE.length] ?? TARGET_PALETTE[0];

function TogglePill({
  label,
  color,
  active,
  onClick,
}: {
  label: string;
  color: string;
  active: boolean;
  onClick: () => void;
}) {
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
  opacity,
  selectedIndex,
  onSelectIndex,
}: {
  points: Array<{ x: number; y: number; target: string; originalIndex: number }>;
  xLabel: string;
  yLabel: string;
  hiddenClasses: Set<string>;
  onToggle: (value: string) => void;
  opacity: number;
  selectedIndex?: number | null;
  onSelectIndex?: (idx: number | null) => void;
}) {
  const classes = [...new Set(points.map(p => p.target))].sort();
  const grouped = classes.map(t => ({
    target: t,
    color: targetColor(t, classes),
    data: hiddenClasses.has(t) ? [] : points.filter(p => p.target === t),
  }));

  const renderDot = (props: any) => {
    const { cx, cy, fill, payload } = props;
    const isSelected = selectedIndex != null && payload.originalIndex === selectedIndex;
    const isFaded = selectedIndex != null && !isSelected;
    
    return (
      <circle
        cx={cx}
        cy={cy}
        r={isSelected ? 6 : 4}
        fill={fill}
        fillOpacity={isFaded ? opacity * 0.2 : opacity}
        stroke={isSelected ? '#fff' : 'none'}
        strokeWidth={isSelected ? 2 : 0}
        style={{ cursor: onSelectIndex ? 'pointer' : 'default', transition: 'all 0.2s ease-in-out' }}
        onClick={(e) => {
          if (onSelectIndex) {
            e.stopPropagation();
            onSelectIndex(isSelected ? null : payload.originalIndex);
          }
        }}
      />
    );
  };

  return (
    <div onClick={() => onSelectIndex && onSelectIndex(null)}>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        {classes.map(c => (
          <TogglePill
            key={c}
            label={`Class ${c}`}
            color={targetColor(c, classes)}
            active={!hiddenClasses.has(c)}
            onClick={(e) => { e.stopPropagation(); onToggle(c); }}
          />
        ))}
        {selectedIndex != null && (
           <span className="text-[10px] text-[#5acce6] ml-4 bg-[#5acce6]/10 px-2 py-0.5 rounded border border-[#5acce6]/20">
             Patient {selectedIndex + 1} selected
           </span>
        )}
      </div>
      <ResponsiveContainer width="100%" height={320}>
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
            labelFormatter={() => ''}
          />
          {grouped.map(g => (
            <Scatter
              key={g.target}
              name={`Class ${g.target}`}
              data={g.data}
              fill={g.color}
              shape={renderDot}
            />
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
  scatterOpacity = 0.75,
  selectedIndex,
  onSelectIndex,
}: FeaturePlotsProps) {
  if (activeTab === 'cumulative') {
    if (!chartData.cumulative?.length)
      return <p className="text-xs text-[#3c5d80]">No cumulative data available.</p>;
    return (
      <div>
        <div className="mb-3 flex flex-wrap gap-1.5">
          {THRESHOLDS.map(t => (
            <TogglePill
              key={t.pct}
              label={t.label}
              color={t.color}
              active={!hiddenThresholds.has(t.pct)}
              onClick={() => toggleThreshold(t.pct)}
            />
          ))}
        </div>
        <ResponsiveContainer width="100%" height={320}>
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
    if (!chartData.correlation)
      return <p className="text-xs text-[#3c5d80]">No correlation data available.</p>;
    return <CorrelationHeatmap data={chartData.correlation} />;
  }

  if (activeTab === 'pca') {
    if (!chartData.pca)
      return <p className="text-xs text-[#3c5d80]">No PCA data available.</p>;
    const pointsWithIdx = chartData.pca.points.map((p, i) => ({ ...p, originalIndex: i }));
    return (
      <ScatterByClass
        points={pointsWithIdx}
        xLabel="PC1"
        yLabel="PC2"
        hiddenClasses={hiddenPcaClasses}
        onToggle={togglePcaClass}
        opacity={scatterOpacity}
        selectedIndex={selectedIndex}
        onSelectIndex={onSelectIndex}
      />
    );
  }

  if (activeTab === 'tsne') {
    if (!chartData.tsne)
      return <p className="text-xs text-[#3c5d80]">No t-SNE data available.</p>;
    const pointsWithIdx = chartData.tsne.points.map((p, i) => ({ ...p, originalIndex: i }));
    return (
      <ScatterByClass
        points={pointsWithIdx}
        xLabel="D1"
        yLabel="D2"
        hiddenClasses={hiddenTsneClasses}
        onToggle={toggleTsneClass}
        opacity={scatterOpacity}
        selectedIndex={selectedIndex}
        onSelectIndex={onSelectIndex}
      />
    );
  }

  return null;
}

