import React from 'react';
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { ImportanceBar } from './types';

interface ImportanceChartProps {
  bars: ImportanceBar[];
  hiddenFeatures: Set<string>;
  onToggle: (feature: string) => void;
  compact?: boolean;
}

function SeriesToggle({
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
      <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: active ? color : '#3c5d80' }} />
      {label}
    </button>
  );
}

export default function ImportanceChart({
  bars,
  hiddenFeatures,
  onToggle,
  compact = true,
}: ImportanceChartProps) {
  const visible = bars.filter(b => !hiddenFeatures.has(b.feature));
  const max = Math.max(...visible.map(b => b.importance ?? 0), 0);
  const toggleLimit = compact ? 8 : bars.length;
  const toggleBars = bars.slice(0, toggleLimit);

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-1.5">
        {toggleBars.map((b, i) => (
          <SeriesToggle
            key={b.feature}
            label={b.feature.length > 24 ? `...${b.feature.slice(-23)}` : b.feature}
            color={`rgba(90,204,230,${0.3 + (i / toggleBars.length) * 0.7})`}
            active={!hiddenFeatures.has(b.feature)}
            onClick={() => onToggle(b.feature)}
          />
        ))}
      </div>

      <ResponsiveContainer width="100%" height={Math.min(compact ? 360 : 700, visible.length * 22 + 40)}>
        <BarChart data={visible} layout="vertical" margin={{ left: 4, right: 24, top: 4, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1f2d40" horizontal={false} />
          <XAxis
            type="number"
            tick={{ fill: '#566a84', fontSize: 9 }}
            domain={[0, max * 1.05]}
            tickFormatter={v => v.toFixed(4)}
          />
          <YAxis
            type="category"
            dataKey="feature"
            width={180}
            tick={{ fill: '#91b9cd', fontSize: 9 }}
            tickFormatter={v => (v.length > 28 ? `${v.slice(0, 27)}...` : v)}
          />
          <Tooltip
            contentStyle={{ background: '#0b1015', border: '1px solid #1f2d40', borderRadius: 8, fontSize: 11 }}
            formatter={(v: number) => [v.toFixed(6), 'Importance']}
            labelStyle={{ color: '#5acce6' }}
          />
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
