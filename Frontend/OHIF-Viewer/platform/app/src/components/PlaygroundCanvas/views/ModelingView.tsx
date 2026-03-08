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
            <div className="w-full h-full flex flex-col items-center justify-center text-gray-600 space-y-4">
                <div className="flex gap-4 opacity-50">
                    <BarChart3Icon size={48} className="text-[#3c5d80]" />
                    <TrendingUpIcon size={48} className="text-[#3c5d80]" />
                </div>
                <div className="text-center max-w-md">
                    <h3 className="text-xl font-bold text-[#91b9cd]">Modeling & Statistics</h3>
                    <p className="text-xs text-gray-500 mt-2">
                        Visualizations for ROC curves, Loss/Accuracy training history, and statistical hypothesis testing (t-test, ANOVA, Chi-Square) will appear here.
                    </p>
                </div>
                <button className="px-6 py-2 border border-[#3c5d80] text-[#6fe5b8] rounded-full text-xs hover:bg-[#151a1f] transition-colors flex items-center gap-2">
                    <GitBranchIcon size={14} /> Select Algorithm
                </button>
            </div>
        );
    }

    const modelEntries = Object.entries(modelingData.individual_model_paths || {});
    const plotFiles = (modelingData.created_files || []).filter(f => f.endsWith('.png') || f.endsWith('.jpg') || f.endsWith('.svg'));
    const csvFiles = (modelingData.created_files || []).filter(f => f.endsWith('.csv'));

    return (
        <div className="p-6 w-full h-full flex gap-6 bg-[#000000] overflow-hidden">
            {/* LEFT: Summary Panel */}
            <div className="w-80 flex flex-col gap-4 shrink-0">
                <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4">
                    <h3 className="text-[#6fe5b8] font-bold uppercase tracking-wider text-xs flex items-center gap-2 mb-3">
                        <CheckCircleIcon size={14} /> Modeling Complete
                    </h3>
                    <div className="space-y-3">
                        <div>
                            <div className="text-gray-500 text-[10px] uppercase">Experiment</div>
                            <div className="text-sm font-medium text-white">{modelingData.experiment_name || '—'}</div>
                        </div>
                        <div>
                            <div className="text-gray-500 text-[10px] uppercase">Models Trained</div>
                            <div className="text-2xl font-bold text-white">{modelEntries.length}</div>
                        </div>
                        {modelingData.model_summary && (
                            <div>
                                <div className="text-gray-500 text-[10px] uppercase">Summary CSV</div>
                                <div className="text-[10px] text-gray-400 font-mono truncate">{modelingData.model_summary}</div>
                            </div>
                        )}
                        {modelingData.final_model_path && (
                            <div>
                                <div className="text-gray-500 text-[10px] uppercase">Final Model</div>
                                <div className="text-[10px] text-gray-400 font-mono truncate">{modelingData.final_model_path}</div>
                            </div>
                        )}
                    </div>
                </div>

                {/* Individual Models */}
                {modelEntries.length > 0 && (
                    <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4 overflow-y-auto max-h-60">
                        <h3 className="text-[#91b9cd] font-bold uppercase tracking-wider text-xs mb-2">Trained Models</h3>
                        <div className="space-y-2">
                            {modelEntries.map(([name, path]) => (
                                <div key={name} className="flex justify-between items-center text-xs p-2 bg-[#0b1015] rounded border border-[#3c5d80]/20">
                                    <span className="text-white font-medium">{name}</span>
                                    <span className="text-[8px] bg-[#5acce6] text-black font-bold px-1.5 py-0.5 rounded">Saved</span>
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>

            {/* RIGHT: Plots & Artifacts */}
            <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-6 flex flex-col min-h-0">
                <h2 className="text-lg font-bold text-white flex items-center gap-2 mb-4">
                    <BarChart3Icon size={18} className="text-[#5acce6]" /> Model Artifacts
                </h2>

                {/* Plots grid */}
                {plotFiles.length > 0 && (
                    <div className="mb-4">
                        <h3 className="text-[#91b9cd] text-xs font-bold uppercase mb-2 flex items-center gap-2">
                            <FileImageIcon size={12} /> Visualizations ({plotFiles.length})
                        </h3>
                        <div className="grid grid-cols-2 lg:grid-cols-3 gap-3 overflow-y-auto max-h-64">
                            {plotFiles.map((file, i) => {
                                const name = file.split(/[/\\]/).pop() || file;
                                return (
                                    <div key={i} className="bg-[#0b1015] rounded-lg border border-[#3c5d80]/30 p-3 text-center">
                                        <div className="aspect-video bg-black/50 rounded flex items-center justify-center mb-2">
                                            <FileImageIcon size={24} className="text-[#3c5d80]" />
                                        </div>
                                        <div className="text-[10px] text-gray-400 font-mono truncate">{name}</div>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {/* CSV outputs */}
                {csvFiles.length > 0 && (
                    <div>
                        <h3 className="text-[#91b9cd] text-xs font-bold uppercase mb-2 flex items-center gap-2">
                            <DatabaseIcon size={12} /> Data Files ({csvFiles.length})
                        </h3>
                        <div className="space-y-2 overflow-y-auto max-h-40">
                            {csvFiles.map((file, i) => {
                                const name = file.split(/[/\\]/).pop() || file;
                                return (
                                    <div key={i} className="bg-[#0b1015] rounded-lg border border-[#3c5d80]/30 p-3 flex items-center justify-between">
                                        <span className="text-xs text-gray-300 font-mono truncate">{name}</span>
                                        <span className="text-[8px] bg-[#6fe5b8] text-black font-bold px-2 py-0.5 rounded">CSV</span>
                                    </div>
                                );
                            })}
                        </div>
                    </div>
                )}

                {plotFiles.length === 0 && csvFiles.length === 0 && (
                    <div className="flex-1 flex items-center justify-center text-gray-500 text-xs">
                        No artifacts generated yet.
                    </div>
                )}
            </div>
        </div>
    );
}