import React from 'react';
import { Layers } from 'lucide-react';
import { AccumulatedPopulation, SegmentationData } from './types';

interface CohortSummaryProps {
  accumulatedPop: AccumulatedPopulation;
  segmentationData?: SegmentationData | null;
}

export default function CohortSummary({ accumulatedPop, segmentationData }: CohortSummaryProps) {
  const segMetrics = segmentationData?.outputs?.metrics;
  const hasSegData = segmentationData && segmentationData.status !== undefined;

  return (
    <>
      <div className="rounded-xl border border-[#3c5d80] bg-[#151a1f] p-4">
        <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[#91b9cd]">Cohort Summary</h3>
        <div className="mb-1 text-3xl font-bold text-white">
          {accumulatedPop.count ?? '—'} <span className="text-sm font-normal text-gray-500">patients</span>
        </div>
        {accumulatedPop.matchRate !== null && (
          <>
            <div className="mt-2 h-2 w-full overflow-hidden rounded-full bg-[#0b1015]">
              <div className="h-full bg-[#5acce6] transition-all duration-1000" style={{ width: `${accumulatedPop.matchRate}%` }} />
            </div>
            <div className="mt-1 text-right text-[10px] text-gray-500">{accumulatedPop.matchRate}% Match Rate</div>
          </>
        )}
        {accumulatedPop.summary && (
          <p className="mt-3 rounded border border-[#1f2d40] bg-[#0b1015] p-2 text-xs leading-relaxed text-[#5acce6]">
            {accumulatedPop.summary}
          </p>
        )}
      </div>

      {hasSegData && (
        <div className="rounded-xl border border-[#3c5d80] bg-[#151a1f] p-4">
          <h3 className="mb-2 flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-[#5acce6]">
            <Layers size={14} /> Segmentation
          </h3>
          {segMetrics?.segmentation_method && (
            <div className="mb-1 text-xs text-gray-300">
              <span className="text-gray-500">Method:</span> {segMetrics.segmentation_method}
            </div>
          )}
          {segMetrics?.volumes_segmented != null && (
            <div className="text-2xl font-bold text-white">
              {segMetrics.volumes_segmented} <span className="text-sm font-normal text-gray-500">volumes</span>
            </div>
          )}
          {segMetrics?.roi_description && <div className="mt-1 text-xs text-gray-400">{segMetrics.roi_description}</div>}
        </div>
      )}
    </>
  );
}
