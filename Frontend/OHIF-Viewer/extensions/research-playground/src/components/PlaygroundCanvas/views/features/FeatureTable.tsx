import React from 'react';
import { BarChart3, CheckCircle, Clock, FileSpreadsheet } from 'lucide-react';
import Collapsible from '../../../shared/Collapsible';
import { FeatureImportanceData, FeaturesData } from './types';

interface FeatureTableProps {
  featuresData?: FeaturesData | null;
  importanceData?: FeatureImportanceData | null;
}

export default function FeatureTable({ featuresData, importanceData }: FeatureTableProps) {
  const hasFeatures = featuresData?.status === 'success';
  const chartData = importanceData?.chart_data;
  const featureEntries = Object.entries(featuresData?.num_features || {});
  const csvEntries = Object.entries(featuresData?.csv_paths || {});
  const totalFeatures = featureEntries.reduce((sum, [, count]) => sum + count, 0);

  return (
    <div className="w-64 shrink-0 overflow-y-auto pr-0.5 flex flex-col gap-3">
      {hasFeatures && (
        <Collapsible
          title={
            <>
              <CheckCircle size={11} className="text-[#6fe5b8]" /> Extraction Summary
            </>
          }
        >
          <div className="grid grid-cols-2 gap-2 pt-1">
            <div className="rounded-lg border border-[#1f2d40] bg-[#0b1015] p-3">
              <div className="mb-1 text-[9px] uppercase text-[#566a84]">Subjects</div>
              <div className="text-lg font-semibold text-gray-100">{featuresData?.num_subjects ?? '—'}</div>
            </div>
            <div className="rounded-lg border border-[#1f2d40] bg-[#0b1015] p-3">
              <div className="mb-1 text-[9px] uppercase text-[#566a84]">Features</div>
              <div className="text-lg font-semibold text-gray-100">{totalFeatures}</div>
            </div>
          </div>

          {!!featuresData?.mask_labels?.length && (
            <div className="mt-2">
              <div className="mb-1 text-[9px] uppercase text-[#566a84]">Labels</div>
              <div className="flex flex-wrap gap-1">
                {featuresData.mask_labels.map(l => (
                  <span key={l} className="rounded bg-[#3c5d80]/30 px-2 py-0.5 font-mono text-[9px] text-[#5acce6]">
                    {l}
                  </span>
                ))}
              </div>
            </div>
          )}

          {featuresData?.processing_time_seconds != null && (
            <div className="mt-2 flex items-center gap-1.5 text-[10px] text-[#566a84]">
              <Clock size={10} /> {featuresData.processing_time_seconds.toFixed(1)}s
            </div>
          )}
        </Collapsible>
      )}

      {chartData && (
        <Collapsible
          title={
            <>
              <BarChart3 size={11} className="text-[#a78bfa]" /> Analysis Info
            </>
          }
        >
          <div className="space-y-2 pt-1 text-[11px]">
            <div className="flex justify-between">
              <span className="text-[#566a84]">Method</span>
              <span className="font-mono text-[#91b9cd]">{chartData.method ?? importanceData?.method}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#566a84]">Task</span>
              <span className="font-mono text-[#91b9cd]">{chartData.task_type}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#566a84]">Total Features</span>
              <span className="font-mono text-[#91b9cd]">{importanceData?.feature_count}</span>
            </div>
          </div>
        </Collapsible>
      )}

      {hasFeatures && csvEntries.length > 0 && (
        <Collapsible
          title={
            <>
              <FileSpreadsheet size={11} className="text-[#f59e0b]" /> Feature Files
            </>
          }
          defaultOpen={false}
        >
          <div className="flex flex-col gap-2 pt-1">
            {csvEntries.map(([label, path]) => (
              <div key={label} className="rounded-lg border border-[#1f2d40] bg-[#0b1015] p-2">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-medium text-[#91b9cd]">{label}</span>
                  <span className="rounded bg-[#6fe5b8]/20 px-1.5 py-0.5 text-[9px] font-semibold text-[#6fe5b8]">CSV</span>
                </div>
                <div className="mt-0.5 truncate font-mono text-[9px] text-[#3c5d80]">{path.split(/[/\\]/).pop()}</div>
              </div>
            ))}
          </div>
        </Collapsible>
      )}
    </div>
  );
}
