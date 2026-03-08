import React from 'react';
import { Scan, BrainCircuit, List, CheckCircle, FileSpreadsheet, Clock, BarChart3 } from 'lucide-react';

const ScanIcon: any = Scan;
const BrainCircuitIcon: any = BrainCircuit;
const ListIcon: any = List;
const CheckCircleIcon: any = CheckCircle;
const FileSpreadsheetIcon: any = FileSpreadsheet;
const ClockIcon: any = Clock;
const BarChart3Icon: any = BarChart3;

export interface FeaturesData {
    status: string;
    csv_paths?: Record<string, string>;
    num_subjects?: number;
    num_features?: Record<string, number>;
    mask_labels?: number[];
    processing_time_seconds?: number;
}

export interface FeatureImportanceData {
    status: string;
    method?: string;
    feature_count?: number;
    result_files?: Record<string, string>;
}

interface FeaturesViewProps {
    featuresData?: FeaturesData | null;
    importanceData?: FeatureImportanceData | null;
}

export default function FeaturesView({ featuresData, importanceData }: FeaturesViewProps) {
    const hasData = featuresData && featuresData.status === 'success';
    const hasImportance = importanceData && importanceData.status === 'success';

    if (!hasData) {
        return (
            <div className="w-full h-full flex flex-col items-center justify-center text-gray-600 space-y-4">
                <div className="relative">
                    <ScanIcon size={64} className="text-[#3c5d80]" />
                    <BrainCircuitIcon size={32} className="text-[#5acce6] absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 animate-pulse" />
                </div>
                <div className="text-center max-w-md">
                    <h3 className="text-xl font-bold text-[#91b9cd]">Feature Extraction Engine</h3>
                    <p className="text-xs text-gray-500 mt-2">
                        This module defines Regions of Interest (ROI) and extracts quantitative radiomics features (shape, texture, intensity) for the modeling phase.
                    </p>
                </div>
                <button className="px-6 py-2 border border-[#3c5d80] text-[#5acce6] rounded-full text-xs hover:bg-[#151a1f] transition-colors flex items-center gap-2">
                    <ListIcon size={14} /> Configure Radiomics Class
                </button>
            </div>
        );
    }

    const csvEntries = Object.entries(featuresData.csv_paths || {});
    const featureEntries = Object.entries(featuresData.num_features || {});
    const totalFeatures = featureEntries.reduce((sum, [, count]) => sum + count, 0);

    return (
        <div className="p-6 w-full h-full flex gap-6 bg-[#000000] overflow-hidden">
            {/* LEFT: Summary Panel */}
            <div className="w-80 flex flex-col gap-4 shrink-0">
                <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4">
                    <h3 className="text-[#5acce6] font-bold uppercase tracking-wider text-xs flex items-center gap-2 mb-3">
                        <CheckCircleIcon size={14} /> Extraction Complete
                    </h3>
                    <div className="space-y-3">
                        <div>
                            <div className="text-gray-500 text-[10px] uppercase">Subjects</div>
                            <div className="text-2xl font-bold text-white">{featuresData.num_subjects ?? '—'}</div>
                        </div>
                        <div>
                            <div className="text-gray-500 text-[10px] uppercase">Total Features</div>
                            <div className="text-2xl font-bold text-white">{totalFeatures}</div>
                        </div>
                        <div>
                            <div className="text-gray-500 text-[10px] uppercase">Labels</div>
                            <div className="text-sm text-gray-300">{(featuresData.mask_labels || []).join(', ') || '—'}</div>
                        </div>
                        {featuresData.processing_time_seconds != null && (
                            <div className="flex items-center gap-2 text-xs text-gray-400">
                                <ClockIcon size={12} /> {featuresData.processing_time_seconds.toFixed(1)}s
                            </div>
                        )}
                    </div>
                </div>

                {/* Per-label feature counts */}
                {featureEntries.length > 0 && (
                    <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4">
                        <h3 className="text-[#91b9cd] font-bold uppercase tracking-wider text-xs mb-2">Features by Label</h3>
                        <div className="space-y-2">
                            {featureEntries.map(([label, count]) => (
                                <div key={label} className="flex justify-between items-center text-xs">
                                    <span className="text-gray-400">Label {label}</span>
                                    <span className="text-white font-mono">{count}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* Feature Importance Summary */}
                {hasImportance && (
                    <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4">
                        <h3 className="text-[#6fe5b8] font-bold uppercase tracking-wider text-xs flex items-center gap-2 mb-2">
                            <BarChart3Icon size={14} /> Importance Analysis
                        </h3>
                        <div className="space-y-1 text-xs">
                            <div className="flex justify-between"><span className="text-gray-400">Method</span><span className="text-white">{importanceData.method}</span></div>
                            <div className="flex justify-between"><span className="text-gray-400">Features Analyzed</span><span className="text-white">{importanceData.feature_count}</span></div>
                            {importanceData.result_files && (
                                <div className="mt-2 text-gray-500 text-[10px]">
                                    {Object.keys(importanceData.result_files).length} result files generated
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>

            {/* RIGHT: CSV outputs list */}
            <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-6 flex flex-col min-h-0">
                <h2 className="text-lg font-bold text-white flex items-center gap-2 mb-4">
                    <FileSpreadsheetIcon size={18} className="text-[#5acce6]" /> Extracted Feature Files
                </h2>
                <div className="space-y-3 overflow-y-auto pr-2">
                    {csvEntries.map(([label, path]) => (
                        <div key={label} className="bg-[#0b1015] rounded-lg border border-[#3c5d80]/30 p-4 flex items-center justify-between">
                            <div>
                                <div className="text-sm font-medium text-white">{label}</div>
                                <div className="text-[10px] text-gray-500 font-mono truncate max-w-md">{path}</div>
                            </div>
                            <span className="text-[8px] bg-[#6fe5b8] text-black font-bold px-2 py-0.5 rounded uppercase">Ready</span>
                        </div>
                    ))}
                    {csvEntries.length === 0 && (
                        <div className="text-center text-gray-500 text-xs py-8">No CSV files generated yet.</div>
                    )}
                </div>
            </div>
        </div>
    );
}