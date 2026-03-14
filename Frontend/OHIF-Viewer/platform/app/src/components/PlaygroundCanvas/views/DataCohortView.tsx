import React from 'react';
import { Users, Filter, Database, AlertTriangle, Check, Activity, Layers } from 'lucide-react';

const CheckIcon: any = Check;
const AlertTriangleIcon: any = AlertTriangle;
const FilterIcon: any = Filter;
const DatabaseIcon: any = Database;
const ActivityIcon: any = Activity;
const LayersIcon: any = Layers;

interface PopulationData {
    agent_id?: string;
    status?: string;
    outputs?: {
        metrics?: {
            cohort_size?: number;
            match_rate?: number;
            inclusion_criteria?: string[];
            exclusion_criteria?: string[];
        };
        summary?: string;
        files?: Record<string, string>;
    };
    tool?: string;
    data?: any;
}

interface SegmentationData {
    agent_id?: string;
    status?: string;
    outputs?: {
        metrics?: {
            segmentation_method?: string;
            volumes_segmented?: number;
            roi_description?: string;
        };
        summary?: string;
        files?: Record<string, string>;
    };
    tool?: string;
    data?: any;
}

interface DataCohortViewProps {
    populationData?: PopulationData | null;
    segmentationData?: SegmentationData | null;
}

export default function DataCohortView({ populationData, segmentationData }: DataCohortViewProps) {
    const popMetrics = populationData?.outputs?.metrics;
    const segMetrics = segmentationData?.outputs?.metrics;
    const cohortSize = popMetrics?.cohort_size ?? null;
    const matchRate = popMetrics?.match_rate ?? null;
    const inclusionCriteria = popMetrics?.inclusion_criteria ?? [];
    const exclusionCriteria = popMetrics?.exclusion_criteria ?? [];
    const volumesSegmented = segMetrics?.volumes_segmented ?? null;
    const segMethod = segMetrics?.segmentation_method ?? null;
    const roiDesc = segMetrics?.roi_description ?? null;

    const hasPopData = populationData && populationData.status !== undefined;
    const hasSegData = segmentationData && segmentationData.status !== undefined;
    return (
        <div className="p-6 w-full h-full flex gap-6 bg-[#000000] overflow-hidden">
            {/* LEFT: Criteria Panel */}
            <div className="w-80 flex flex-col gap-4 shrink-0">
                <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4 flex flex-col gap-4">
                    <h3 className="text-[#5acce6] font-bold uppercase tracking-wider text-xs flex items-center gap-2">
                        <FilterIcon size={14} /> Study Criteria
                    </h3>
                    
                    {/* Inclusion Box */}
                    <div className="space-y-2">
                        <span className="text-[#6fe5b8] text-[10px] font-bold uppercase tracking-wide bg-[#6fe5b8]/10 px-2 py-0.5 rounded border border-[#6fe5b8]/20 inline-block">
                            Inclusion
                        </span>
                        <div className="bg-[#0b1015] rounded p-3 border border-[#3c5d80]/30 text-xs text-gray-300 space-y-1">
                            {inclusionCriteria.length > 0 ? (
                                inclusionCriteria.map((c, i) => (
                                    <div key={i} className="flex items-center gap-2"><CheckIcon size={10} className="text-[#6fe5b8]"/> {c}</div>
                                ))
                            ) : (
                                <>
                                    <div className="flex items-center gap-2"><CheckIcon size={10} className="text-[#6fe5b8]"/> Modality: CT Chest</div>
                                    <div className="flex items-center gap-2"><CheckIcon size={10} className="text-[#6fe5b8]"/> Contrast: Enhanced</div>
                                    <div className="flex items-center gap-2"><CheckIcon size={10} className="text-[#6fe5b8]"/> Age: {'>'} 45 years</div>
                                </>
                            )}
                        </div>
                    </div>

                    {/* Exclusion Box */}
                    <div className="space-y-2">
                        <span className="text-[#ff6b6b] text-[10px] font-bold uppercase tracking-wide bg-[#ff6b6b]/10 px-2 py-0.5 rounded border border-[#ff6b6b]/20 inline-block">
                            Exclusion
                        </span>
                        <div className="bg-[#0b1015] rounded p-3 border border-[#3c5d80]/30 text-xs text-gray-300 space-y-1">
                            {exclusionCriteria.length > 0 ? (
                                exclusionCriteria.map((c, i) => (
                                    <div key={i} className="flex items-center gap-2"><AlertTriangleIcon size={10} className="text-[#ff6b6b]"/> {c}</div>
                                ))
                            ) : (
                                <>
                                    <div className="flex items-center gap-2"><AlertTriangleIcon size={10} className="text-[#ff6b6b]"/> Motion Artifacts {'>'} 10%</div>
                                    <div className="flex items-center gap-2"><AlertTriangleIcon size={10} className="text-[#ff6b6b]"/> Prior Resection</div>
                                </>
                            )}
                        </div>
                    </div>
                </div>

                {/* Cohort Summary */}
                <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4">
                    <h3 className="text-[#91b9cd] font-bold uppercase tracking-wider text-xs mb-2">Cohort Summary</h3>
                    <div className="text-3xl font-bold text-white mb-1">
                        {cohortSize ?? '—'} <span className="text-sm font-normal text-gray-500">patients</span>
                    </div>
                    {matchRate !== null && (
                        <>
                            <div className="h-2 w-full bg-[#0b1015] rounded-full overflow-hidden">
                                <div className="h-full bg-[#5acce6]" style={{ width: `${matchRate}%` }}></div>
                            </div>
                            <div className="text-[10px] text-gray-500 mt-1 text-right">{matchRate}% Match Rate</div>
                        </>
                    )}
                    {populationData?.outputs?.summary && (
                        <p className="text-xs text-gray-400 mt-2">{populationData.outputs.summary}</p>
                    )}
                </div>

                {/* Segmentation Summary */}
                {hasSegData && (
                    <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4">
                        <h3 className="text-[#5acce6] font-bold uppercase tracking-wider text-xs mb-2 flex items-center gap-2">
                            <LayersIcon size={14} /> Segmentation
                        </h3>
                        {segMethod && <div className="text-xs text-gray-300 mb-1"><span className="text-gray-500">Method:</span> {segMethod}</div>}
                        {volumesSegmented !== null && (
                            <div className="text-2xl font-bold text-white">{volumesSegmented} <span className="text-sm font-normal text-gray-500">volumes</span></div>
                        )}
                        {roiDesc && <div className="text-xs text-gray-400 mt-1">{roiDesc}</div>}
                        {segmentationData?.outputs?.summary && (
                            <p className="text-xs text-gray-400 mt-2">{segmentationData.outputs.summary}</p>
                        )}
                    </div>
                )}
            </div>

            {/* RIGHT: Data Grid */}
            <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-6 flex flex-col min-h-0">
                <div className="flex justify-between items-center mb-6">
                    <div>
                        <h2 className="text-lg font-bold text-white flex items-center gap-2">
                            <DatabaseIcon size={18} className="text-[#5acce6]" /> Data Acquisition
                        </h2>
                        <p className="text-xs text-gray-400">
                            {hasPopData
                                ? `Population status: ${populationData?.status ?? 'pending'}`
                                : 'Reviewing PACS query results for Study UID: 1.2.840.113...'}
                        </p>
                    </div>
                    <div className="flex gap-2">
                        <button className="px-3 py-1.5 bg-[#1f2d40] text-[#91b9cd] border border-[#3c5d80] rounded text-xs hover:text-white transition-colors">Query PACS</button>
                        <button className="px-3 py-1.5 bg-[#348cfd] text-white rounded text-xs font-medium hover:bg-[#599fff] transition-colors shadow-lg">Run QA Check</button>
                    </div>
                </div>

                {/* Mock Grid */}
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 overflow-y-auto pr-2">
                    {Array.from({ length: volumesSegmented || 12 }).map((_, i) => (
                        <div key={i} className="aspect-square bg-black rounded border border-[#3c5d80]/30 hover:border-[#5acce6] transition-all cursor-pointer relative group overflow-hidden">
                            <div className="absolute inset-0 flex items-center justify-center opacity-20 group-hover:opacity-40 transition-opacity bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-gray-700 via-gray-900 to-black">
                                <span className="text-[10px] font-mono text-gray-500">DICOM / {i + 100}</span>
                            </div>
                            <div className="absolute top-2 right-2">
                                {i % 4 === 0 ? (
                                    <div className="bg-[#ff6b6b] text-black text-[8px] font-bold px-1.5 py-0.5 rounded">FAIL</div>
                                ) : (
                                    <div className="bg-[#6fe5b8] text-black text-[8px] font-bold px-1.5 py-0.5 rounded">PASS</div>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}