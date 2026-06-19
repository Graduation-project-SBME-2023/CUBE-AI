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

interface FeaturePlotsProps {
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
  chartData,
  hiddenThresholds,
  toggleThreshold,
  hiddenPcaClasses,
  togglePcaClass,
  hiddenTsneClasses,
  toggleTsneClass,
}: FeaturePlotsProps) {
  if (chartData.cumulative?.length) {
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

  if (chartData.pca) {
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

  if (chartData.tsne) {
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
