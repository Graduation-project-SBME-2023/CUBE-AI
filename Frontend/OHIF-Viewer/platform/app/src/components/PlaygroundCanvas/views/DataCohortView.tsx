import React, { useState, useEffect } from 'react';
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
    // Side-channel properties we added
    tool?: string;
    count?: number;
    uid_list?: string[];
    inclusion_criteria?: string[]; // <--- ADDED
    exclusion_criteria?: string[]; // <--- ADDED
    modality?: string;
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
    
    // --- 1. LOCAL ACCUMULATOR STATE ---
    // Because the backend sends updates tool-by-tool, we merge them here so the UI doesn't flicker.
    const [accumulatedPop, setAccumulatedPop] = useState({
        count: null as number | null,
        uids: [] as string[],
        inclusion: [] as string[],
        exclusion: [] as string[],
        modality: "Awaiting...",
        summary: "",
        matchRate: null as number | null
    });

    useEffect(() => {
        if (!populationData) return;

        setAccumulatedPop(prev => {
            const next = { ...prev };

            // A. Catch output from the write_inclusion_criteria tool
            if (populationData.tool === 'write_inclusion_criteria') {
                if (populationData.inclusion_criteria) next.inclusion = populationData.inclusion_criteria;
                if (populationData.exclusion_criteria) next.exclusion = populationData.exclusion_criteria;
                if (populationData.modality) next.modality = populationData.modality;
            }

            // B. Catch output from the secure search tool (The side-channel!)
            if (populationData.tool === 'search_volumes_by_description') {
                if (populationData.count !== undefined) next.count = populationData.count;
                if (populationData.uid_list) next.uids = populationData.uid_list;
            }

            // C. Catch final output from the standardized write_agent_output tool
            if (populationData.outputs?.metrics) {
                const metrics = populationData.outputs.metrics;
                if (metrics.cohort_size) next.count = metrics.cohort_size;
                if (metrics.match_rate) next.matchRate = metrics.match_rate;
                if (metrics.inclusion_criteria) next.inclusion = metrics.inclusion_criteria;
                if (metrics.exclusion_criteria) next.exclusion = metrics.exclusion_criteria;
                if (populationData.outputs.summary) next.summary = populationData.outputs.summary;
            }

            return next;
        });
    }, [populationData]);

    const segMetrics = segmentationData?.outputs?.metrics;
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
                    
                    {/* Dynamic Modality Tag */}
                    {accumulatedPop.modality && accumulatedPop.modality !== "Awaiting..." && (
                        <div className="text-xs bg-[#3c5d80]/20 border border-[#3c5d80] text-[#91b9cd] px-2 py-1 rounded inline-block w-max">
                            Target: <span className="font-bold text-white">{accumulatedPop.modality}</span>
                        </div>
                    )}
                    
                    {/* Inclusion Box */}
                    <div className="space-y-2">
                        <span className="text-[#6fe5b8] text-[10px] font-bold uppercase tracking-wide bg-[#6fe5b8]/10 px-2 py-0.5 rounded border border-[#6fe5b8]/20 inline-block">
                            Inclusion
                        </span>
                        <div className="bg-[#0b1015] rounded p-3 border border-[#3c5d80]/30 text-xs text-gray-300 space-y-1 min-h-[60px]">
                            {accumulatedPop.inclusion.length > 0 ? (
                                accumulatedPop.inclusion.map((c, i) => (
                                    <div key={i} className="flex items-start gap-2">
                                        <CheckIcon size={12} className="text-[#6fe5b8] mt-0.5 shrink-0"/> 
                                        <span>{c}</span>
                                    </div>
                                ))
                            ) : (
                                <div className="text-gray-600 italic">Waiting for agent to define criteria...</div>
                            )}
                        </div>
                    </div>

                    {/* Exclusion Box */}
                    <div className="space-y-2">
                        <span className="text-[#ff6b6b] text-[10px] font-bold uppercase tracking-wide bg-[#ff6b6b]/10 px-2 py-0.5 rounded border border-[#ff6b6b]/20 inline-block">
                            Exclusion
                        </span>
                        <div className="bg-[#0b1015] rounded p-3 border border-[#3c5d80]/30 text-xs text-gray-300 space-y-1 min-h-[60px]">
                            {accumulatedPop.exclusion.length > 0 ? (
                                accumulatedPop.exclusion.map((c, i) => (
                                    <div key={i} className="flex items-start gap-2">
                                        <AlertTriangleIcon size={12} className="text-[#ff6b6b] mt-0.5 shrink-0"/> 
                                        <span>{c}</span>
                                    </div>
                                ))
                            ) : (
                                <div className="text-gray-600 italic">Waiting for exclusion logic...</div>
                            )}
                        </div>
                    </div>
                </div>

                {/* Cohort Summary */}
                <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4">
                    <h3 className="text-[#91b9cd] font-bold uppercase tracking-wider text-xs mb-2">Cohort Summary</h3>
                    <div className="text-3xl font-bold text-white mb-1 transition-all">
                        {accumulatedPop.count ?? '—'} <span className="text-sm font-normal text-gray-500">patients</span>
                    </div>
                    {accumulatedPop.matchRate !== null && (
                        <>
                            <div className="h-2 w-full bg-[#0b1015] rounded-full overflow-hidden mt-2">
                                <div className="h-full bg-[#5acce6] transition-all duration-1000" style={{ width: `${accumulatedPop.matchRate}%` }}></div>
                            </div>
                            <div className="text-[10px] text-gray-500 mt-1 text-right">{accumulatedPop.matchRate}% Match Rate</div>
                        </>
                    )}
                    {accumulatedPop.summary && (
                        <p className="text-xs text-[#5acce6] mt-3 p-2 bg-[#0b1015] border border-[#1f2d40] rounded leading-relaxed">
                            {accumulatedPop.summary}
                        </p>
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
                        <p className="text-xs text-gray-400 mt-1">
                            {hasPopData 
                                ? `Agent Activity: Processing ${populationData?.tool?.replace(/_/g, ' ') || 'finalizing'}...` 
                                : 'Waiting for Population Agent to initiate DB search...'}
                        </p>
                    </div>
                    <div className="flex gap-2 text-xs font-mono">
                        {accumulatedPop.uids.length > 0 && (
                            <span className="px-3 py-1.5 bg-[#152e2e] text-[#5ce6b0] border border-[#234d4d] rounded flex items-center gap-2 shadow-[0_0_10px_rgba(92,230,176,0.2)]">
                                <ActivityIcon size={12} className="animate-pulse" /> Cohort Secured & Loading Previews
                            </span>
                        )}
                    </div>
                </div>

                {/* Dynamic Data Grid */}
                <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-3 overflow-y-auto pr-2 pb-4">
                    {/* Render Real UIDs if we have them */}
                    {accumulatedPop.uids.length > 0 ? (
                        accumulatedPop.uids.map((uid, i) => (
                            <div key={uid} className="aspect-square bg-black rounded border border-[#3c5d80]/50 hover:border-[#5acce6] transition-all relative group overflow-hidden flex flex-col">
                                
                                {/* Image / GIF Container */}
                                <div className="flex-1 relative bg-[#05080a] overflow-hidden">
                                    {/* This points to your backend API which fetches/generates the GIF from Orthanc. 
                                        Adjust the port/URL to match your actual backend API.
                                    */}
                                    <img 
                                        src={`http://localhost:8000/volumes/${uid}/preview.gif`} 
                                        alt={`Volume ${uid}`}
                                        className="w-full h-full object-cover opacity-50 group-hover:opacity-90 transition-opacity duration-300"
                                        onError={(e) => {
                                            // Fallback if the GIF hasn't been generated yet or fails
                                            (e.target as HTMLImageElement).style.display = 'none';
                                            (e.target as HTMLImageElement).nextElementSibling?.classList.remove('hidden');
                                        }}
                                    />
                                    
                                    {/* Fallback Icon if Image Fails */}
                                    <div className="hidden absolute inset-0 flex items-center justify-center bg-[radial-gradient(ellipse_at_center,_var(--tw-gradient-stops))] from-[#1f2d40] via-[#0b1015] to-black">
                                        <DatabaseIcon size={24} className="text-[#3c5d80]" />
                                    </div>

                                    {/* Status Badge */}
                                    <div className="absolute top-2 right-2 z-10">
                                        <div className="bg-[#152e2e]/90 backdrop-blur text-[#5ce6b0] border border-[#5ce6b0]/30 text-[8px] font-bold px-1.5 py-0.5 rounded uppercase shadow-lg">
                                            READY
                                        </div>
                                    </div>
                                </div>

                                {/* UID Footer */}
                                <div className="h-7 bg-[#0b1015] border-t border-[#1f2d40] flex items-center justify-center px-2 shrink-0 group-hover:bg-[#151a1f] transition-colors">
                                    <span className="text-[10px] font-mono text-[#91b9cd] truncate w-full text-center" title={uid}>
                                        ...{uid.slice(-12)}
                                    </span>
                                </div>

                            </div>
                        ))
                    ) : (
                        /* Fallback Mock Skeleton Loading State */
                        Array.from({ length: 12 }).map((_, i) => (
                            <div key={`skeleton-${i}`} className="aspect-square bg-[#0b1015] rounded border border-[#1f2d40] relative overflow-hidden animate-pulse">
                                <div className="absolute inset-0 flex items-center justify-center">
                                    <span className="text-[10px] font-mono text-gray-700">Awaiting Data</span>
                                </div>
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>
    );
}