import React from 'react'

export function ProgressLog({
  data,
}: {
  data: { current: number; total: number; label?: string }
}) {
  const currentDisplay = Number.isInteger(data.current) ? data.current : Math.floor(data.current);
  const percent = data.total > 0 ? Math.min(100, Math.max(0, (data.current / data.total) * 100)) : 0;

  // Clean header label: remove any trailing elapsed time, UID details, or volume counters to keep header clean
  const cleanLabel = data.label
    ? data.label
        .replace(/\s*\(\d+s\s+elapsed.*?\)/i, '')
        .replace(/\s+volume\s+\d+\s+of\s+\d+/i, '')
        .replace(/\s+\d+\s+of\s+\d+\s+volumes/i, '')
    : undefined;

  return (
    <div className="space-y-1">
      {cleanLabel && (
        <div className="text-[10px] text-[#91b9cd] uppercase tracking-wide">
          {cleanLabel}
        </div>
      )}

      <div className="flex items-center gap-2 min-w-0">
        <div className="flex-1 h-2 bg-[#1f2630] rounded overflow-hidden">
          <div
            className="h-full bg-[#5acce6] transition-all duration-300 ease-out"
            style={{ width: `${percent}%` }}
          />
        </div>

        <span className="text-[10px] text-gray-400 whitespace-nowrap text-right font-mono min-w-[65px]">
          {currentDisplay} from {data.total}
        </span>
      </div>
    </div>
  )
}