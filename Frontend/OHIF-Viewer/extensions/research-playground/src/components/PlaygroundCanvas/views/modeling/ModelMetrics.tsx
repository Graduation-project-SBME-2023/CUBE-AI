import React from 'react';
import { CheckCircle, Database, Layers } from 'lucide-react';
import { ModelingData } from './types';

interface ModelMetricsProps {
  modelingData: ModelingData;
  modelCount: number;
}

export default function ModelMetrics({ modelingData, modelCount }: ModelMetricsProps) {
  return (
    <div className="flex flex-wrap items-center gap-3">
      <div className="flex items-center gap-2 rounded-lg border border-[#1f2d40] bg-[#0b1015] px-3 py-2">
        <CheckCircle size={13} className="text-[#6fe5b8]" />
        <span className="text-[11px] text-[#91b9cd]">{modelingData.experiment_name || 'Experiment'}</span>
      </div>
      <div className="flex items-center gap-2 rounded-lg border border-[#1f2d40] bg-[#0b1015] px-3 py-2">
        <Layers size={13} className="text-[#5acce6]" />
        <span className="text-[11px] text-[#91b9cd]">{modelCount} model{modelCount !== 1 ? 's' : ''} trained</span>
      </div>
      {modelingData.chart_data?.fold ? (
        <div className="flex items-center gap-2 rounded-lg border border-[#1f2d40] bg-[#0b1015] px-3 py-2">
          <Database size={13} className="text-[#a78bfa]" />
          <span className="text-[11px] text-[#91b9cd]">{modelingData.chart_data.fold}-fold CV</span>
        </div>
      ) : null}
    </div>
  );
}
