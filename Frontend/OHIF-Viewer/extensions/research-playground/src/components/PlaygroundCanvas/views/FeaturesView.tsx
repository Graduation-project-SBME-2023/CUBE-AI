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
            <div className="w-full h-full flex flex-col items-center justify-center text-slate-400 space-y-5 bg-slate-900/50 rounded-2xl border border-slate-800/50 p-6">
                <div className="relative">
                    <ScanIcon size={64} className="text-slate-500" />
                    <BrainCircuitIcon size={30} className="text-cyan-400 absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 animate-pulse" />
                </div>
                <div className="text-center max-w-md space-y-2">
                    <h3 className="text-xl font-semibold tracking-tight text-slate-100">Feature Extraction Engine</h3>
                    <p className="text-sm leading-relaxed text-slate-400">
                        This module defines Regions of Interest (ROI) and extracts quantitative radiomics features (shape, texture, intensity) for the modeling phase.
                    </p>
                </div>
                <button className="px-6 py-2.5 border border-slate-600 text-cyan-300 rounded-lg text-sm hover:bg-slate-800/70 transition-all duration-200 shadow-sm hover:shadow-md flex items-center gap-2">
                    <ListIcon size={14} /> Configure Radiomics Class
                </button>
            </div>
        );
    }

    const csvEntries = Object.entries(featuresData.csv_paths || {});
    const featureEntries = Object.entries(featuresData.num_features || {});
    const totalFeatures = featureEntries.reduce((sum, [, count]) => sum + count, 0);

    return (
        <div className="p-6 w-full h-full flex flex-col lg:flex-row gap-6 bg-slate-950 overflow-hidden rounded-2xl">
            {/* LEFT: Summary Panel */}
            <div className="w-full lg:w-80 flex flex-col gap-4 shrink-0">
                <div className="bg-slate-900/70 rounded-xl border border-slate-700 p-5 shadow-sm">
                    <h3 className="text-cyan-400 font-semibold uppercase tracking-wider text-xs flex items-center gap-2 mb-3">
                        <CheckCircleIcon size={14} /> Extraction Complete
                    </h3>
                    <div className="space-y-3">
                        <div>
                            <div className="text-slate-500 text-[11px] uppercase">Subjects</div>
                            <div className="text-2xl font-semibold text-slate-100">{featuresData.num_subjects ?? '—'}</div>
                        </div>
                        <div>
                            <div className="text-slate-500 text-[11px] uppercase">Total Features</div>
                            <div className="text-2xl font-semibold text-slate-100">{totalFeatures}</div>
                        </div>
                        <div>
                            <div className="text-slate-500 text-[11px] uppercase">Labels</div>
                            <div className="text-sm text-slate-300">{(featuresData.mask_labels || []).join(', ') || '—'}</div>
                        </div>
                        {featuresData.processing_time_seconds != null && (
                            <div className="flex items-center gap-2 text-xs text-slate-400">
                                <ClockIcon size={12} /> {featuresData.processing_time_seconds.toFixed(1)}s
                            </div>
                        )}
                    </div>
                </div>

                {/* Per-label feature counts */}
                {featureEntries.length > 0 && (
                    <div className="bg-slate-900/70 rounded-xl border border-slate-700 p-5 shadow-sm">
                        <h3 className="text-sky-300 font-semibold uppercase tracking-wider text-xs mb-2">Features by Label</h3>
                        <div className="space-y-2">
                            {featureEntries.map(([label, count]) => (
                                <div key={label} className="flex justify-between items-center text-xs py-1">
                                    <span className="text-slate-400">Label {label}</span>
                                    <span className="text-slate-100 font-mono">{count}</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                {/* Feature Importance Summary */}
                {hasImportance && (
                    <div className="bg-slate-900/70 rounded-xl border border-slate-700 p-5 shadow-sm">
                        <h3 className="text-emerald-400 font-semibold uppercase tracking-wider text-xs flex items-center gap-2 mb-2">
                            <BarChart3Icon size={14} /> Importance Analysis
                        </h3>
                        <div className="space-y-1 text-xs">
                            <div className="flex justify-between"><span className="text-slate-400">Method</span><span className="text-slate-100">{importanceData.method}</span></div>
                            <div className="flex justify-between"><span className="text-slate-400">Features Analyzed</span><span className="text-slate-100">{importanceData.feature_count}</span></div>
                            {importanceData.result_files && (
                                <div className="mt-2 text-slate-500 text-[11px]">
                                    {Object.keys(importanceData.result_files).length} result files generated
                                </div>
                            )}
                        </div>
                    </div>
                )}
            </div>

            {/* RIGHT: CSV outputs list */}
            <div className="flex-1 bg-slate-900/60 rounded-xl border border-slate-700 p-6 flex flex-col min-h-0 shadow-sm">
                <h2 className="text-lg font-semibold text-slate-100 flex items-center gap-2 mb-4">
                    <FileSpreadsheetIcon size={18} className="text-cyan-400" /> Extracted Feature Files
                </h2>
                <div className="space-y-3 overflow-y-auto pr-2">
                    {csvEntries.map(([label, path]) => (
                        <div key={label} className="bg-slate-950 rounded-lg border border-slate-700/70 p-4 flex items-center justify-between transition-all duration-200 hover:border-cyan-500/60 hover:shadow-md">
                            <div>
                                <div className="text-sm font-medium text-slate-100">{label}</div>
                                <div className="text-[11px] text-slate-500 font-mono truncate max-w-md">{path}</div>
                            </div>
                            <span className="text-[10px] bg-emerald-300 text-emerald-950 font-semibold px-2 py-0.5 rounded uppercase">Ready</span>
                        </div>
                    ))}
                    {csvEntries.length === 0 && (
                        <div className="text-center text-slate-500 text-sm py-8">No CSV files generated yet.</div>
                    )}
                </div>
            </div>
        </div>
    );
}