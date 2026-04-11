import React from 'react';
import { FileText, Download, CheckCircle, FileOutput } from 'lucide-react';

const FileTextIcon: any = FileText;
const DownloadIcon: any = Download;
const CheckCircleIcon: any = CheckCircle;
const FileOutputIcon: any = FileOutput;

export interface ReportData {
    status: string;
    summary?: string;
    outputs?: Record<string, any>;
}

export interface LatexData {
    status: string;
    pdf_path?: string;
    tex_path?: string;
    outputs?: Record<string, any>;
}

interface ReportingViewProps {
    reportData?: ReportData | null;
    latexData?: LatexData | null;
}

export default function ReportingView({ reportData, latexData }: ReportingViewProps) {
    const hasReport = reportData && reportData.status === 'success';
    const hasLatex = latexData && latexData.status === 'success';

    if (!hasReport && !hasLatex) {
        return (
            <div className="w-full h-full flex flex-col items-center justify-center text-slate-400 space-y-5 bg-slate-900/50 rounded-2xl border border-slate-800/50">
                <div className="p-4 bg-slate-800 rounded-full shadow-inner">
                    <FileTextIcon size={48} className="text-slate-500" />
                </div>
                <div className="text-center max-w-sm">
                    <h3 className="text-xl font-semibold text-slate-200 tracking-tight">Paper & Reporting</h3>
                    <p className="text-sm text-slate-400 mt-2 leading-relaxed">
                        This module compiles findings from all previous steps into a structured medical report or academic paper draft.
                    </p>
                </div>
                <button className="mt-4 px-6 py-2.5 bg-blue-600 text-white font-medium rounded-lg text-sm hover:bg-blue-500 focus:ring-4 focus:ring-blue-500/20 transition-all duration-200 flex items-center gap-2 shadow-lg shadow-blue-500/20">
                    <DownloadIcon size={16} /> Export PDF Draft
                </button>
            </div>
        );
    }

    return (
        <div className="p-6 w-full h-full flex flex-col lg:flex-row gap-6 bg-[#0B1120] overflow-hidden rounded-2xl">
            {/* LEFT: Status Panel */}
            <div className="w-full lg:w-80 flex flex-col gap-4 shrink-0">
                {hasReport && (
                    <div className="bg-slate-800/50 backdrop-blur-sm rounded-xl border border-slate-700/50 p-5 shadow-sm transition-all hover:border-slate-600/50 hover:bg-slate-800/80">
                        <h3 className="text-cyan-400 font-semibold uppercase tracking-wider text-xs flex items-center gap-2 mb-3">
                            <CheckCircleIcon size={16} /> Results Report
                        </h3>
                        {reportData.summary && (
                            <p className="text-sm text-slate-300 leading-relaxed">{reportData.summary}</p>
                        )}
                    </div>
                )}

                {hasLatex && (
                    <div className="bg-slate-800/50 backdrop-blur-sm rounded-xl border border-slate-700/50 p-5 shadow-sm transition-all hover:border-slate-600/50 hover:bg-slate-800/80">
                        <h3 className="text-emerald-400 font-semibold uppercase tracking-wider text-xs flex items-center gap-2 mb-4">
                            <FileOutputIcon size={16} /> LaTeX / PDF Ready
                        </h3>
                        <div className="space-y-3 text-sm mb-5">
                            {latexData.pdf_path && (
                                <div className="bg-slate-900/50 rounded-lg p-3 border border-slate-800">
                                    <span className="text-slate-500 text-xs font-medium uppercase tracking-wider block mb-1">PDF Path</span>
                                    <div className="text-slate-300 font-mono text-xs truncate" title={latexData.pdf_path}>{latexData.pdf_path}</div>
                                </div>
                            )}
                            {latexData.tex_path && (
                                <div className="bg-slate-900/50 rounded-lg p-3 border border-slate-800">
                                    <span className="text-slate-500 text-xs font-medium uppercase tracking-wider block mb-1">TeX Path</span>
                                    <div className="text-slate-300 font-mono text-xs truncate" title={latexData.tex_path}>{latexData.tex_path}</div>
                                </div>
                            )}
                        </div>
                        <button className="w-full px-4 py-2.5 bg-blue-600 hover:bg-blue-500 text-white rounded-lg text-sm font-medium transition-all duration-200 flex items-center justify-center gap-2 shadow-lg shadow-blue-500/20 focus:outline-none focus:ring-4 focus:ring-blue-500/30">
                            <DownloadIcon size={16} /> Download Final PDF
                        </button>
                    </div>
                )}
            </div>

            {/* RIGHT: Document Preview */}
            <div className="flex-1 bg-slate-800/40 backdrop-blur-md rounded-xl border border-slate-700/50 p-6 shadow-xl flex flex-col min-h-0 relative">
                <div className="absolute top-0 left-0 w-full h-1 bg-gradient-to-r from-blue-500 to-cyan-400 rounded-t-xl opacity-80"></div>
                <h2 className="text-xl font-semibold text-white flex items-center gap-3 mb-6 pb-4 border-b border-slate-700/50">
                    <div className="p-2 bg-slate-800 rounded-lg border border-slate-700">
                        <FileTextIcon size={20} className="text-cyan-400" />
                    </div>
                    Paper Draft Preview
                </h2>
                <div className="flex-1 overflow-y-auto pr-4 text-sm text-slate-300 leading-7 whitespace-pre-wrap font-serif">
                    {reportData?.summary || latexData?.outputs?.content || (
                        <span className="text-slate-500 italic">Report content will appear here after generation completes.</span>
                    )}
                </div>
            </div>
        </div>
    );
}