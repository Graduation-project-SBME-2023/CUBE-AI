import React, { useState, useMemo } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import { Search } from 'lucide-react';
import { ImportanceBar } from './types';

interface ImportanceChartProps {
  bars: ImportanceBar[];
}

const KNOWN_CATEGORIES = [
  'original', 'wavelet', 'log', 'lbp', 'gradient', 'squareroot', 'exponential',
  'firstorder', 'shape', 'glcm', 'glrlm', 'glszm', 'gldm', 'ngtdm'
];

// ── [TASK 1] Custom tooltip: always renders the full, unabbreviated feature
function ImportanceTooltipContent({ active, payload }: any) {
  if (!active || !payload?.length) return null;
  const entry: ImportanceBar = payload[0]?.payload;
  if (!entry) return null;
  return (
    <div
      style={{
        background: '#0b1015',
        border: '1px solid #1f2d40',
        borderRadius: 8,
        padding: '8px 12px',
        fontSize: 11,
        maxWidth: 320,
      }}
    >
      <p style={{ color: '#5acce6', marginBottom: 4, wordBreak: 'break-all', lineHeight: 1.4 }}>
        {entry.feature}
      </p>
      <p style={{ color: '#91b9cd' }}>
        Importance:{' '}
        <span style={{ color: '#6fe5b8', fontFamily: 'monospace' }}>
          {entry.importance != null ? entry.importance.toFixed(6) : '—'}
        </span>
      </p>
    </div>
  );
}

export default function ImportanceChart({ bars }: ImportanceChartProps) {
  const [searchTerm, setSearchTerm] = useState('');
  const [hiddenCategories, setHiddenCategories] = useState<Set<string>>(new Set());

  const availableCategories = useMemo(() => {
    const found = new Set<string>();
    bars.forEach(b => {
      const lower = b.feature.toLowerCase();
      KNOWN_CATEGORIES.forEach(cat => {
        if (lower.includes(cat)) found.add(cat);
      });
    });
    return Array.from(found).sort();
  }, [bars]);

  const toggleCategory = (cat: string) => {
    setHiddenCategories(prev => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat); else next.add(cat);
      return next;
    });
  };

  const visible = useMemo(() => {
    let list = bars;
    if (hiddenCategories.size > 0) {
      list = list.filter(b => {
        const lower = b.feature.toLowerCase();
        let isHidden = false;
        hiddenCategories.forEach(cat => {
          if (lower.includes(cat)) isHidden = true;
        });
        return !isHidden;
      });
    }
    if (searchTerm) {
      const lower = searchTerm.toLowerCase();
      list = list.filter(b => b.feature.toLowerCase().includes(lower));
    }
    return list;
  }, [bars, hiddenCategories, searchTerm]);

  const max = Math.max(...visible.map(b => b.importance ?? 0), 0);

  return (
    <div className="flex flex-col gap-4 h-full min-h-0">
      {/* Search Input */}
      <div className="relative w-full max-w-md">
        <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#566a84]" />
        <input
          type="text"
          placeholder="Filter features (e.g., wavelet, glcm, shape)"
          value={searchTerm}
          onChange={(e) => setSearchTerm(e.target.value)}
          className="w-full rounded-lg border border-[#1f2d40] bg-[#151a1f] py-1.5 pl-9 pr-3 text-xs text-gray-100 placeholder:text-[#566a84] focus:border-[#5acce6] focus:outline-none"
        />
      </div>

      <div className="flex flex-wrap gap-2 max-h-28 overflow-y-auto ohif-scrollbar [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar]:w-1.5 hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">
        {availableCategories.map(cat => {
          const isActive = !hiddenCategories.has(cat);
          return (
            <button
              key={cat}
              onClick={() => toggleCategory(cat)}
              className="flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1 text-[10px] font-medium transition-all capitalize"
              style={{
                borderColor: isActive ? '#5acce6' : '#3c5d80',
                background: isActive ? 'rgba(90,204,230,0.12)' : 'transparent',
                color: isActive ? '#5acce6' : '#91b9cd',
                opacity: isActive ? 1 : 0.6,
                textDecoration: isActive ? 'none' : 'line-through',
              }}
            >
              <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: isActive ? '#5acce6' : '#3c5d80' }} />
              {cat}
            </button>
          );
        })}
      </div>

      <div className="flex-1 overflow-y-auto min-h-0 ohif-scrollbar [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar]:w-1.5 hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6] pr-2">
        <div style={{ width: '100%', minHeight: 300, height: visible.length * 22 + 40 }}>
          {visible.length === 0 ? (
            <div className="flex h-full items-center justify-center text-sm text-[#566a84]">
              No features match your filter.
            </div>
          ) : (
            <ResponsiveContainer>
              <BarChart
                data={visible}
                layout="vertical"
                margin={{ top: 10, right: 30, left: 280, bottom: 5 }}
              >
                <CartesianGrid strokeDasharray="3 3" horizontal={false} stroke="#1f2d40" />
                <XAxis type="number" stroke="#566a84" fontSize={10} tickFormatter={v => v.toFixed(4)} domain={[0, max * 1.05]} />
                <YAxis
                  type="category"
                  dataKey="feature"
                  stroke="#566a84"
                  fontSize={10}
                  width={270}
                  tick={props => {
                    const { x, y, payload } = props;
                    const val = payload.value;
                    const short = val.length > 55 ? `${val.slice(0, 53)}...` : val;
                    return (
                      <g transform={`translate(${x},${y})`}>
                        <text x={0} y={0} dy={4} textAnchor="end" fill="#566a84" fontSize={10}>
                          {short}
                        </text>
                      </g>
                    );
                  }}
                />
                <Tooltip content={<ImportanceTooltipContent />} cursor={{ fill: '#1f2d40', opacity: 0.5 }} />
                <Bar dataKey="importance" radius={[0, 4, 4, 0]}>
                  {visible.map((b, i) => {
                    const intensity = max > 0 ? (b.importance ?? 0) / max : 0;
                    return <Cell key={i} fill={`rgba(90,204,230,${0.2 + intensity * 0.8})`} />;
                  })}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>
    </div>
  );
}
