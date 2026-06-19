import React from 'react';

interface ResultsTableProps {
  table: Array<Record<string, any>>;
}

const pct = (value: number | null | undefined) =>
  value == null ? '—' : `${(value * 100).toFixed(1)}%`;

export default function ResultsTable({ table }: ResultsTableProps) {
  if (!table.length) {
    return (
      <div className="rounded-xl border border-[#1f2d40] bg-[#0b1015] p-4 text-xs text-[#3c5d80]">
        Comparison table unavailable.
      </div>
    );
  }

  const cols = Object.keys(table[0]);
  const metricCols = cols.filter(c => !['Model', 'TT (Sec)'].includes(c));

  return (
    <div className="overflow-x-auto rounded-xl border border-[#1f2d40] bg-[#0b1015]">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr className="border-b border-[#1f2d40]">
            <th className="px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-[#5acce6]">
              Model
            </th>
            {metricCols.map(c => (
              <th key={c} className="whitespace-nowrap px-2 py-2 text-right text-[10px] uppercase tracking-wider text-[#566a84]">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.map((row, i) => (
            <tr
              key={i}
              className={`border-b border-[#1f2d40]/50 transition-colors hover:bg-white/[0.02] ${
                i === 0 ? 'bg-[#5acce6]/5' : ''
              }`}
            >
              <td className="whitespace-nowrap px-3 py-2 font-mono text-[#91b9cd]">
                {row.Model ?? `Model ${i + 1}`}
              </td>
              {metricCols.map(c => (
                <td key={c} className="px-2 py-2 text-right font-mono">
                  <span className={i === 0 ? 'font-semibold text-[#5acce6]' : 'text-[#566a84]'}>
                    {typeof row[c] === 'number' ? pct(row[c]) : row[c] ?? '—'}
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
