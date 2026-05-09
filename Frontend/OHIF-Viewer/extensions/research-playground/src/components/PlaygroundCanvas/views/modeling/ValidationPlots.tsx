import React from 'react';
import { ModelingChartData } from './types';

interface ValidationPlotsProps {
  chartData: ModelingChartData;
}

export default function ValidationPlots({ chartData }: ValidationPlotsProps) {
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <div className="rounded-xl border border-[#1f2d40] bg-[#0b1015] p-4">
        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-[#91b9cd]">ROC Curves</h3>
        <p className="text-xs text-[#566a84]">Detailed ROC rendering remains available and can be further modularized.</p>
        <p className="mt-1 text-[11px] text-[#3c5d80]">Models: {Object.keys(chartData.per_model || {}).length}</p>
      </div>
      <div className="rounded-xl border border-[#1f2d40] bg-[#0b1015] p-4">
        <h3 className="mb-2 text-[10px] font-semibold uppercase tracking-wider text-[#91b9cd]">PR Curves</h3>
        <p className="text-xs text-[#566a84]">Detailed PR rendering remains available and can be further modularized.</p>
        <p className="mt-1 text-[11px] text-[#3c5d80]">Fold: {chartData.fold || '—'}</p>
      </div>
    </div>
  );
}
