import React from 'react';
import { FeaturesData, FeatureImportanceData } from './types';
import { AlertTriangle, CheckCircle, Clock } from 'lucide-react';

interface KPIHeaderProps {
  featuresData?: FeaturesData | null;
  importanceData?: FeatureImportanceData | null;
}

export default function KPIHeader({ featuresData, importanceData }: KPIHeaderProps) {
  if (!featuresData || featuresData.status !== 'success') return null;

  const total = featuresData.num_subjects ?? 0;
  const extracted = featuresData.num_subjects_with_features ?? total;
  const failedOrgans = featuresData.missing_patient_organs ?? 0;
  
  const failRate = total > 0 ? (total - extracted) / total : 0;
  const isCritical = failRate > 0.2;

  const featureEntries = Object.entries(featuresData.num_features || {});
  const totalFeatures = featureEntries.reduce((sum, [, count]) => sum + count, 0);

  const method = importanceData?.chart_data?.method ?? importanceData?.method ?? '—';
  const task = importanceData?.chart_data?.task_type ?? '—';

  return (
    <div className={`flex flex-wrap items-center justify-between gap-4 rounded-xl border p-3 ${isCritical ? 'border-[#ef4444]/40 bg-[#ef4444]/5' : 'border-[#1f2d40] bg-[#0b1015]'}`}>
      <div className="flex items-center gap-4 flex-wrap">
        {/* Status Icon */}
        {isCritical ? (
          <AlertTriangle size={18} className="shrink-0 text-[#ef4444]" />
        ) : (
          <CheckCircle size={18} className="shrink-0 text-[#6fe5b8]" />
        )}

        <div className="flex flex-col">
          <span className="text-[10px] uppercase text-[#566a84]">Method / Task</span>
          <span className="font-mono text-[12px] font-semibold text-[#5acce6]">{method} / {task}</span>
        </div>

        <div className="h-6 w-px bg-[#1f2d40] hidden sm:block" />

        <div className="flex flex-col">
          <span className="text-[10px] uppercase text-[#566a84]">Subjects</span>
          <span className="font-mono text-[12px] font-semibold text-[#91b9cd]">{extracted} / {total}</span>
        </div>

        <div className="h-6 w-px bg-[#1f2d40] hidden sm:block" />

        <div className="flex flex-col">
          <span className="text-[10px] uppercase text-[#566a84]">Features</span>
          <span className="font-mono text-[12px] font-semibold text-[#91b9cd]">{totalFeatures}</span>
        </div>
        
        {failedOrgans > 0 && (
          <>
            <div className="h-6 w-px bg-[#1f2d40] hidden sm:block" />
            <div className="flex flex-col">
              <span className="text-[10px] uppercase text-[#566a84]">Missing Pairs</span>
              <span className={`font-mono text-[12px] font-semibold ${isCritical ? 'text-[#ef4444]' : 'text-[#f59e0b]'}`}>{failedOrgans}</span>
            </div>
          </>
        )}

        {featuresData.mask_labels && featuresData.mask_labels.length > 0 && (
          <>
            <div className="h-6 w-px bg-[#1f2d40] hidden sm:block" />
            <div className="flex flex-col">
              <span className="text-[10px] uppercase text-[#566a84]">Labels</span>
              <div className="flex gap-1 mt-0.5">
                {featuresData.mask_labels.map(l => (
                  <span key={l} className="rounded bg-[#3c5d80]/30 px-1.5 py-0.5 font-mono text-[9px] text-[#5acce6]">
                    {l}
                  </span>
                ))}
              </div>
            </div>
          </>
        )}
      </div>

      {featuresData.processing_time_seconds != null && (
         <div className="flex items-center gap-1.5 text-[10px] text-[#566a84]">
           <Clock size={12} /> {featuresData.processing_time_seconds.toFixed(1)}s
         </div>
      )}
    </div>
  );
}
