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
        <div className="p-6 w-full h-full flex flex-col xl:flex-row gap-6 bg-slate-950 overflow-hidden rounded-2xl">
            {/* LEFT: Criteria Panel */}
            <div className="w-full xl:w-80 flex flex-col gap-4 shrink-0">
                <div className="bg-slate-900/70 rounded-xl border border-slate-700 p-5 flex flex-col gap-4 shadow-sm">
                    <h3 className="text-cyan-400 font-semibold uppercase tracking-wider text-xs flex items-center gap-2">
                        <FilterIcon size={14} /> Study Criteria
                    </h3>
                    
                    {/* Inclusion Box */}
                    <div className="space-y-2">
                        <span className="text-emerald-300 text-[11px] font-semibold uppercase tracking-wide bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/20 inline-block">
                            Inclusion
                        </span>
                        <div className="bg-slate-950 rounded p-3 border border-slate-700/70 text-sm text-slate-300 space-y-1.5 leading-relaxed">
                            {inclusionCriteria.length > 0 ? (
                                inclusionCriteria.map((c, i) => (
                                    <div key={i} className="flex items-center gap-2"><CheckIcon size={11} className="text-emerald-400"/> {c}</div>
                                ))
                            ) : (
                                <>
                                    <div className="flex items-center gap-2"><CheckIcon size={11} className="text-emerald-400"/> Modality: CT Chest</div>
                                    <div className="flex items-center gap-2"><CheckIcon size={11} className="text-emerald-400"/> Contrast: Enhanced</div>
                                    <div className="flex items-center gap-2"><CheckIcon size={11} className="text-emerald-400"/> Age: {'>'} 45 years</div>
                                </>
                            )}
                        </div>
                    </div>

                    {/* Exclusion Box */}
                    <div className="space-y-2">
                        <span className="text-rose-300 text-[11px] font-semibold uppercase tracking-wide bg-rose-500/10 px-2 py-0.5 rounded border border-rose-500/20 inline-block">
                            Exclusion
                        </span>
                        <div className="bg-slate-950 rounded p-3 border border-slate-700/70 text-sm text-slate-300 space-y-1.5 leading-relaxed">
                            {exclusionCriteria.length > 0 ? (
                                exclusionCriteria.map((c, i) => (
                                    <div key={i} className="flex items-center gap-2"><AlertTriangleIcon size={11} className="text-rose-400"/> {c}</div>
                                ))
                            ) : (
                                <>
                                    <div className="flex items-center gap-2"><AlertTriangleIcon size={11} className="text-rose-400"/> Motion Artifacts {'>'} 10%</div>
                                    <div className="flex items-center gap-2"><AlertTriangleIcon size={11} className="text-rose-400"/> Prior Resection</div>
                                </>
                            )}
                        </div>
                    </div>
                </div>

                {/* Cohort Summary */}
                <div className="bg-slate-900/70 rounded-xl border border-slate-700 p-5 shadow-sm">
                    <h3 className="text-sky-300 font-semibold uppercase tracking-wider text-xs mb-2">Cohort Summary</h3>
                    <div className="text-3xl font-semibold text-slate-100 mb-1">
                        {cohortSize ?? '—'} <span className="text-sm font-normal text-slate-500">patients</span>
                    </div>
                    {matchRate !== null && (
                        <>
                            <div className="h-2 w-full bg-slate-950 rounded-full overflow-hidden border border-slate-700/60">
                                <div className="h-full bg-cyan-400" style={{ width: `${matchRate}%` }}></div>
                            </div>
                            <div className="text-[11px] text-slate-500 mt-1 text-right">{matchRate}% Match Rate</div>
                        </>
                    )}
                    {populationData?.outputs?.summary && (
                        <p className="text-sm text-slate-400 mt-2 leading-relaxed">{populationData.outputs.summary}</p>
                    )}
                </div>

                {/* Segmentation Summary */}
                {hasSegData && (
                    <div className="bg-slate-900/70 rounded-xl border border-slate-700 p-5 shadow-sm">
                        <h3 className="text-cyan-400 font-semibold uppercase tracking-wider text-xs mb-2 flex items-center gap-2">
                            <LayersIcon size={14} /> Segmentation
                        </h3>
                        {segMethod && <div className="text-sm text-slate-300 mb-1"><span className="text-slate-500">Method:</span> {segMethod}</div>}
                        {volumesSegmented !== null && (
                            <div className="text-2xl font-semibold text-slate-100">{volumesSegmented} <span className="text-sm font-normal text-slate-500">volumes</span></div>
                        )}
                        {roiDesc && <div className="text-sm text-slate-400 mt-1 leading-relaxed">{roiDesc}</div>}
                        {segmentationData?.outputs?.summary && (
                            <p className="text-sm text-slate-400 mt-2 leading-relaxed">{segmentationData.outputs.summary}</p>
                        )}
                    </div>
                )}
            </div>

            {/* RIGHT: Data Grid */}
            <div className="flex-1 bg-slate-900/60 rounded-xl border border-slate-700 p-6 flex flex-col min-h-0 shadow-sm">
                <div className="flex justify-between items-center mb-6">
                    <div>
                        <h2 className="text-lg font-semibold text-slate-100 flex items-center gap-2">
                            <DatabaseIcon size={18} className="text-cyan-400" /> Data Acquisition
                        </h2>
                        <p className="text-sm text-slate-400 mt-1">
                            {hasPopData
                                ? `Population status: ${populationData?.status ?? 'pending'}`
                                : 'Reviewing PACS query results for Study UID: 1.2.840.113...'}
                        </p>
                    </div>
                    <div className="flex gap-2">
                        <button className="px-3 py-1.5 bg-slate-800 text-sky-300 border border-slate-600 rounded-lg text-xs hover:text-slate-100 transition-all duration-200">Query PACS</button>
                        <button className="px-3 py-1.5 bg-blue-600 text-white rounded-lg text-xs font-medium hover:bg-blue-500 transition-all duration-200 shadow-lg shadow-blue-500/20">Run QA Check</button>
                    </div>
                </div>

                {/* Mock Grid */}
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 overflow-y-auto pr-2">
                    {Array.from({ length: volumesSegmented || 12 }).map((_, i) => (
                        <div key={i} className="aspect-square bg-slate-950 rounded-lg border border-slate-700/70 hover:border-cyan-500/70 transition-all duration-200 cursor-pointer relative group overflow-hidden hover:shadow-md">
                            <div className="absolute inset-0 flex items-center justify-center opacity-25 group-hover:opacity-45 transition-opacity bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-slate-700 via-slate-900 to-black">
                                <span className="text-[11px] font-mono text-slate-500">DICOM / {i + 100}</span>
                            </div>
                            <div className="absolute top-2 right-2">
                                {i % 4 === 0 ? (
                                    <div className="bg-rose-400 text-rose-950 text-[10px] font-semibold px-1.5 py-0.5 rounded">FAIL</div>
                                ) : (
                                    <div className="bg-emerald-300 text-emerald-950 text-[10px] font-semibold px-1.5 py-0.5 rounded">PASS</div>
                                )}
                            </div>
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}