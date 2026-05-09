import React from 'react';
import { Activity, AlertTriangle, Check, Database, Filter, Layers } from 'lucide-react';
import CohortSummary from './CohortSummary';
import DataPreview from './DataPreview';
import { AccumulatedPopulation, SegmentationData } from './types';

const CheckIcon: any = Check;
const AlertTriangleIcon: any = AlertTriangle;
const FilterIcon: any = Filter;
const DatabaseIcon: any = Database;
const ActivityIcon: any = Activity;

interface DataCohortPanelProps {
  accumulatedPop: AccumulatedPopulation;
  segmentationData?: SegmentationData | null;
  hasPopData: boolean;
  populationTool?: string;
}

export default function DataCohortPanel({
  accumulatedPop,
  segmentationData,
  hasPopData,
  populationTool,
}: DataCohortPanelProps) {
  return (
    <div className="flex h-full w-full gap-6 overflow-hidden bg-[#000000] p-6">
      <div className="flex w-80 shrink-0 flex-col gap-4 overflow-y-auto pb-4 pr-1 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar]:w-2 hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">
        <div className="flex flex-col gap-4 rounded-xl border border-[#3c5d80] bg-[#151a1f] p-4">
          <h3 className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-[#5acce6]">
            <FilterIcon size={14} /> Study Criteria
          </h3>

          {accumulatedPop.modality && accumulatedPop.modality !== 'Awaiting...' && (
            <div className="inline-block w-max rounded border border-[#3c5d80] bg-[#3c5d80]/20 px-2 py-1 text-xs text-[#91b9cd]">
              Target: <span className="font-bold text-white">{accumulatedPop.modality}</span>
            </div>
          )}

          <div className="space-y-2">
            <span className="inline-block rounded border border-[#6fe5b8]/20 bg-[#6fe5b8]/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-[#6fe5b8]">
              Inclusion
            </span>
            <div className="min-h-[60px] max-h-[200px] space-y-1 overflow-y-auto rounded border border-[#3c5d80]/30 bg-[#0b1015] p-3 text-xs text-gray-300 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar]:w-2 hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">
              {accumulatedPop.inclusion.length > 0 ? (
                accumulatedPop.inclusion.map((c, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <CheckIcon size={12} className="mt-0.5 shrink-0 text-[#6fe5b8]" />
                    <span>{c}</span>
                  </div>
                ))
              ) : (
                <div className="italic text-gray-600">Waiting for agent to define criteria...</div>
              )}
            </div>
          </div>

          <div className="space-y-2">
            <span className="inline-block rounded border border-[#ff6b6b]/20 bg-[#ff6b6b]/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wide text-[#ff6b6b]">
              Exclusion
            </span>
            <div className="min-h-[60px] max-h-[200px] space-y-1 overflow-y-auto rounded border border-[#3c5d80]/30 bg-[#0b1015] p-3 text-xs text-gray-300 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar]:w-2 hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">
              {accumulatedPop.exclusion.length > 0 ? (
                accumulatedPop.exclusion.map((c, i) => (
                  <div key={i} className="flex items-start gap-2">
                    <AlertTriangleIcon size={12} className="mt-0.5 shrink-0 text-[#ff6b6b]" />
                    <span>{c}</span>
                  </div>
                ))
              ) : (
                <div className="italic text-gray-600">Waiting for exclusion logic...</div>
              )}
            </div>
          </div>
        </div>

        <CohortSummary accumulatedPop={accumulatedPop} segmentationData={segmentationData} />
      </div>

      <div className="flex min-h-0 flex-1 flex-col rounded-xl border border-[#3c5d80] bg-[#151a1f] p-6">
        <div className="mb-6 flex items-center justify-between">
          <div>
            <h2 className="flex items-center gap-2 text-lg font-bold text-white">
              <DatabaseIcon size={18} className="text-[#5acce6]" /> Data Acquisition
            </h2>
            <p className="mt-1 text-xs text-gray-400">
              {hasPopData
                ? `Agent Activity: Processing ${populationTool?.replace(/_/g, ' ') || 'finalizing'}...`
                : 'Waiting for Population Agent to initiate DB search...'}
            </p>
          </div>
          <div className="flex gap-2 font-mono text-xs">
            {accumulatedPop.uids.length > 0 && (
              <span className="flex items-center gap-2 rounded border border-[#234d4d] bg-[#152e2e] px-3 py-1.5 text-[#5ce6b0] shadow-[0_0_10px_rgba(92,230,176,0.2)]">
                <ActivityIcon size={12} className="animate-pulse" /> Cohort Secured & Loading Previews
              </span>
            )}
          </div>
        </div>

        <DataPreview uids={accumulatedPop.uids} />
      </div>
    </div>
  );
}
