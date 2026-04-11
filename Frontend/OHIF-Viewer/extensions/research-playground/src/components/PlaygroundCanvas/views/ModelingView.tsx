import React from 'react';
import { BarChart3, TrendingUp, GitBranch, CheckCircle, FileImage, Database } from 'lucide-react';

const BarChart3Icon: any = BarChart3;
const TrendingUpIcon: any = TrendingUp;
const GitBranchIcon: any = GitBranch;
const CheckCircleIcon: any = CheckCircle;
const FileImageIcon: any = FileImage;
const DatabaseIcon: any = Database;

export interface ModelingData {
    status: string;
    experiment_name?: string;
    input_path?: string;
    output_dir?: string;
    final_model_path?: string;
    individual_model_paths?: Record<string, string>;
    model_test_predictions?: string;
    model_summary?: string;
    created_files?: string[] | null;
}

interface ModelingViewProps {
    modelingData?: ModelingData | null;
}

export default function ModelingView({ modelingData }: ModelingViewProps) {
    const hasData = modelingData && modelingData.status === 'success';

    if (!hasData) {
        return (
            <div className="w-full h-full flex flex-col items-center justify-center text-slate-400 space-y-5 bg-slate-900/50 rounded-2xl border border-slate-800/50 p-6">
                <div className="flex gap-4 opacity-70">
                    <BarChart3Icon size={48} className="text-slate-500" />
                    <TrendingUpIcon size={48} className="text-slate-500" />
                </div>
                <div className="text-center max-w-md space-y-2">
                    <h3 className="text-xl font-semibold tracking-tight text-slate-100">Modeling & Statistics</h3>
                    <p className="text-sm leading-relaxed text-slate-400 mt-2">
                        Visualizations for ROC curves, Loss/Accuracy training history, and statistical hypothesis testing (t-test, ANOVA, Chi-Square) will appear here.
                    </p>
                </div>
                <button className="px-6 py-2.5 border border-slate-600 text-emerald-300 rounded-lg text-sm hover:bg-slate-800/70 transition-all duration-200 shadow-sm hover:shadow-md flex items-center gap-2">
                    <GitBranchIcon size={14} /> Select Algorithm
                </button>
            </div>
        );
    }

    const modelEntries = Object.entries(modelingData.individual_model_paths || {});
    const plotFiles = (modelingData.created_files || []).filter(f => f.endsWith('.png') || f.endsWith('.jpg') || f.endsWith('.svg'));
    const csvFiles = (modelingData.created_files || []).filter(f => f.endsWith('.csv'));

    return (
        <div className="p-6 w-full h-full flex flex-col lg:flex-row gap-6 bg-slate-950 overflow-hidden rounded-2xl">
            {/* LEFT: Summary Panel */}
            <div className="w-full lg:w-80 flex flex-col gap-4 shrink-0">
                <div className="bg-slate-900/70 rounded-xl border border-slate-700 p-5 shadow-sm">
                    <h3 className="text-emerald-400 font-semibold uppercase tracking-wider text-xs flex items-center gap-2 mb-3">
                        <CheckCircleIcon size={14} /> Modeling Complete
                    </h3>
                    <div className="space-y-3">
                        <div>
                            <div className="text-slate-500 text-[11px] uppercase">Experiment</div>
                            <div className="text-sm font-medium text-slate-100">{modelingData.experiment_name || '—'}</div>
                        </div>
                        <div>
                            <div className="text-slate-500 text-[11px] uppercase">Models Trained</div>
                            <div className="text-2xl font-semibold text-slate-100">{modelEntries.length}</div>
                        </div>
                        {modelingData.model_summary && (
                            <div>
                                <div className="text-slate-500 text-[11px] uppercase">Summary CSV</div>
                                <div className="text-[11px] text-slate-400 font-mono truncate">{modelingData.model_summary}</div>
                            </div>
                        )}
                        {modelingData.final_model_path && (
                            <div>
                                <div className="text-slate-500 text-[11px] uppercase">Final Model</div>
                                <div className="text-[11px] text-slate-400 font-mono truncate">{modelingData.final_model_path}</div>
                            </div>
                        )}
                    </div>
                </div>

                {/* Individual Models */}
                {modelEntries.length > 0 && (
                    <div className="bg-slate-900/70 rounded-xl border border-slate-700 p-5 overflow-y-auto max-h-60 shadow-sm">
                        <h3 className="text-sky-300 font-semibold uppercase tracking-wider text-xs mb-2">Trained Models</h3>
                        <div className="space-y-2">
                            {modelEntries.map(([name, path]) => (
                                <div key={name} className="flex justify-between items-center text-xs p-2 bg-slate-950 rounded border border-slate-700/60">
                                    <span className="text-slate-100 font-medium">{name}</span>
                                    <span className="text-[10px] bg-cyan-300 text-cyan-950 font-semibold px-2 py-0.5 rounded">Saved</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>

            {/* RIGHT: Plots & Artifacts */}
            <div className="flex-1 bg-slate-900/60 rounded-xl border border-slate-700 p-6 flex flex-col min-h-0 shadow-sm">
                <h2 className="text-lg font-semibold text-slate-100 flex items-center gap-2 mb-4">
                    <BarChart3Icon size={18} className="text-cyan-400" /> Model Artifacts
                </h2>

                {/* Plots grid */}
                {plotFiles.length > 0 && (
                    <div className="mb-4">
                        <h3 className="text-sky-300 text-xs font-semibold uppercase mb-2 flex items-center gap-2">
                            <FileImageIcon size={12} /> Visualizations ({plotFiles.length})
                        </h3>
                        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3 overflow-y-auto max-h-64">
                            {plotFiles.map((file, i) => {
                                const name = file.split(/[/\\]/).pop() || file;
                                return (
                                    <div key={i} className="bg-slate-950 rounded-lg border border-slate-700/70 p-3 text-center transition-all duration-200 hover:border-cyan-500/60 hover:shadow-md">
                                        <div className="aspect-video bg-slate-900 rounded-md border border-slate-800 flex items-center justify-center mb-2">
                                            <FileImageIcon size={24} className="text-slate-500" />
                                        </div>
                                        <div className="text-[11px] text-slate-400 font-mono truncate">{name}</div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* CSV outputs */}
                {csvFiles.length > 0 && (
                    <div>
                        <h3 className="text-sky-300 text-xs font-semibold uppercase mb-2 flex items-center gap-2">
                            <DatabaseIcon size={12} /> Data Files ({csvFiles.length})
                        </h3>
                        <div className="space-y-2 overflow-y-auto max-h-40">
                            {csvFiles.map((file, i) => {
                                const name = file.split(/[/\\]/).pop() || file;
                                return (
                                    <div key={i} className="bg-slate-950 rounded-lg border border-slate-700/70 p-3 flex items-center justify-between transition-all duration-200 hover:border-emerald-500/60">
                                        <span className="text-xs text-slate-300 font-mono truncate">{name}</span>
                                        <span className="text-[10px] bg-emerald-300 text-emerald-950 font-semibold px-2 py-0.5 rounded">CSV</span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {plotFiles.length === 0 && csvFiles.length === 0 && (
                    <div className="flex-1 flex items-center justify-center text-slate-500 text-sm">
                        No artifacts generated yet.
                    </div>
                )}
            </div>
        </div>
    );
}