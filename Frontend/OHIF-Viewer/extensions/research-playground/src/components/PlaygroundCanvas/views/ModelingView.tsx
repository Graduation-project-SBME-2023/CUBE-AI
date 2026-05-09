import React from 'react';
import { BarChart3, TrendingUp } from 'lucide-react';
import ModelMetrics from './modeling/ModelMetrics';
import ResultsTable from './modeling/ResultsTable';
import ValidationPlots from './modeling/ValidationPlots';
import { ModelingData } from './modeling/types';

interface ModelingViewProps {
  modelingData?: ModelingData | null;
}

function EmptyState() {
  return (
    <div className="flex h-full w-full flex-col items-center justify-center space-y-4 p-6 text-center">
      <div className="flex gap-4 opacity-40">
        <BarChart3 size={48} className="text-[#5acce6]" />
        <TrendingUp size={48} className="text-[#6fe5b8]" />
      </div>
      <div>
        <h3 className="text-xl font-semibold text-slate-100">Modeling & Statistics</h3>
        <p className="mt-2 max-w-md text-sm text-[#566a84]">
          ROC curves, PR curves, confusion matrices, and per-model metric comparisons will appear here once training completes.
        </p>
      </div>
    </div>
  );
}

export default function ModelingView({ modelingData }: ModelingViewProps) {
  const hasData = modelingData?.status === 'success';
  const chartData = modelingData?.chart_data;

  if (!hasData || !chartData) {
    return <EmptyState />;
  }

  const modelCount = Object.keys(chartData.per_model || {}).length;

  return (
    <div className="h-full w-full space-y-4 overflow-y-auto bg-[#000000] p-5">
      <ModelMetrics modelingData={modelingData} modelCount={modelCount} />
      <ResultsTable table={chartData.comparison_table || []} />
      <ValidationPlots chartData={chartData} />
    </div>
  );
}
