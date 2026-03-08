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
            <div className="w-full h-full flex flex-col items-center justify-center text-gray-600 space-y-4">
                <FileTextIcon size={64} className="text-[#3c5d80]" />
                <div className="text-center max-w-md">
                    <h3 className="text-xl font-bold text-[#91b9cd]">Paper & Reporting</h3>
                    <p className="text-xs text-gray-500 mt-2">
                        This module compiles findings from all previous steps into a structured medical report or academic paper draft, comparing results against the initial hypothesis.
                    </p>
                </div>
                <button className="px-6 py-2 bg-[#348cfd] text-white rounded-full text-xs hover:bg-[#599fff] transition-colors flex items-center gap-2 shadow-lg">
                    <DownloadIcon size={14} /> Export PDF Draft
                </button>
            </div>
        );
    }

    return (
        <div className="p-6 w-full h-full flex gap-6 bg-[#000000] overflow-hidden">
            {/* LEFT: Status Panel */}
            <div className="w-80 flex flex-col gap-4 shrink-0">
                {hasReport && (
                    <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4">
                        <h3 className="text-[#5acce6] font-bold uppercase tracking-wider text-xs flex items-center gap-2 mb-3">
                            <CheckCircleIcon size={14} /> Results Report
                        </h3>
                        {reportData.summary && (
                            <p className="text-xs text-gray-300 leading-relaxed">{reportData.summary}</p>
                        )}
                    </div>
                )}

                {hasLatex && (
                    <div className="bg-[#151a1f] rounded-xl border border-[#3c5d80] p-4">
                        <h3 className="text-[#6fe5b8] font-bold uppercase tracking-wider text-xs flex items-center gap-2 mb-3">
                            <FileOutputIcon size={14} /> LaTeX / PDF
                        </h3>
                        <div className="space-y-2 text-xs">
                            {latexData.pdf_path && (
                                <div>
                                    <span className="text-gray-500">PDF:</span>
                                    <div className="text-gray-300 font-mono text-[10px] truncate">{latexData.pdf_path}</div>
                                </div>
                            )}
                            {latexData.tex_path && (
                                <div>
                                    <span className="text-gray-500">TeX:</span>
                                    <div className="text-gray-300 font-mono text-[10px] truncate">{latexData.tex_path}</div>
                                </div>
                            )}
                        </div>
                        <button className="mt-3 w-full px-4 py-2 bg-[#348cfd] text-white rounded text-xs font-medium hover:bg-[#599fff] transition-colors flex items-center justify-center gap-2 shadow-lg">
                            <DownloadIcon size={14} /> Download PDF
                        </button>
                    </div>
                )}
            </div>

            {/* RIGHT: Report Content */}
            <div className="flex-1 bg-[#151a1f] rounded-xl border border-[#3c5d80] p-6 flex flex-col min-h-0">
                <h2 className="text-lg font-bold text-white flex items-center gap-2 mb-4">
                    <FileTextIcon size={18} className="text-[#5acce6]" /> Paper Draft
                </h2>
                <div className="flex-1 overflow-y-auto pr-2 text-sm text-gray-300 leading-relaxed whitespace-pre-wrap">
                    {reportData?.summary || latexData?.outputs?.content || 'Report content will appear here after generation.'}
                </div>
            </div>
        </div>
    );
}