import React from 'react'

export function ProgressLog({
  data,
}: {
  data: { current: number; total: number; label?: string }
}) {
  const percent = Math.round((data.current / data.total) * 100)
  return (
    <div className="space-y-1">
      {data.label && (
        <div className="text-[10px] text-[#91b9cd] uppercase tracking-wide">
          {data.label}
        </div>
      )}

      <div className="flex items-center gap-2 min-w-0">
        <div className="flex-1 h-2 bg-[#1f2630] rounded overflow-hidden">
          <div
            className="h-full bg-[#5acce6] transition-all"
            style={{ width: `${percent}%` }}
          />
        </div>

        <span className="text-[10px] text-gray-400 w-[60px] text-right">
          {data.current}/{data.total}
        </span>
      </div>
    </div>
  )
}