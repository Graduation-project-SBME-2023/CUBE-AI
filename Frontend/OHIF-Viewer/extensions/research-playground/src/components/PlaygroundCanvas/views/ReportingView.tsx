import React, { useEffect, useMemo, useState } from 'react';
import { FileText, Download, CheckCircle, FileOutput, AlertCircle, Loader2 } from 'lucide-react';
import { compileLatexReport } from '../../../services/api';

const FileTextIcon: any = FileText;
const DownloadIcon: any = Download;
const CheckCircleIcon: any = CheckCircle;
const FileOutputIcon: any = FileOutput;
const AlertCircleIcon: any = AlertCircle;
const Loader2Icon: any = Loader2;

export interface ReportingViewProps {
    reportData?: any;
    latexData?: any;
    sessionId?: string;
}

function extractLatexText(latexData?: any) {
    if (!latexData) return '';
    return (
        latexData?.latex_text ||
        latexData?.latex ||
        latexData?.outputs?.metrics?.latex_source ||
        latexData?.outputs?.metrics?.latex ||
        latexData?.outputs?.summary ||
        latexData?.data?.latex_text ||
        latexData?.data?.latex ||
        latexData?.outputs?.latex_text ||
        ''
    );
}

export default function ReportingView({ reportData, latexData, sessionId }: ReportingViewProps) {
    const hasReport = reportData && reportData.status === 'success';
    const latexText = useMemo(() => extractLatexText(latexData), [latexData]);
    const hasLatex = !!latexText || !!latexData?.latex_pdf || !!latexData?.pdf_path;
    const [compileStatus, setCompileStatus] = useState<'idle' | 'compiling' | 'success' | 'error'>('idle');
    const [compileError, setCompileError] = useState<string>('');
    const [compiledPdfUrl, setCompiledPdfUrl] = useState<string>('');

    const sessionForCompile = sessionId || latexData?.sessionId || reportData?.sessionId;
    const pdfUrl = compiledPdfUrl || latexData?.latex_pdf || latexData?.pdf_path;

    useEffect(() => {
        if (!latexText || !sessionForCompile) {
            return;
        }

        let cancelled = false;

        const runCompile = async () => {
            setCompileStatus('compiling');
            setCompileError('');

            try {
                const result = await compileLatexReport(
                    sessionForCompile,
                    latexText,
                    latexData?.output_filename || 'final_report.pdf'
                );

                if (cancelled) return;

                if (result?.status === 'success' && result?.pdf_url) {
                    setCompiledPdfUrl(result.pdf_url);
                    setCompileStatus('success');
                    return;
                }

                setCompileStatus('error');
                setCompileError(result?.message || 'Compilation failed');
            } catch (error: any) {
                if (cancelled) return;
                setCompileStatus('error');
                setCompileError(error?.message || 'Compilation request failed');
            }
        };

        runCompile();

        return () => {
            cancelled = true;
        };
    }, [latexData?.output_filename, latexText, sessionForCompile]);

    const handleDownload = () => {
        if (!pdfUrl) return;
        const link = document.createElement('a');
        link.href = pdfUrl;
        link.download = 'Clinical_Hypothesis_Report.pdf';
        document.body.appendChild(link);
        link.click();
        link.remove();
    };

    // NO REPORT YET STATE
    if (!hasReport && !hasLatex) {
        return (
            <div className="w-full h-full flex flex-col items-center justify-center text-gray-600 space-y-4 bg-[#000000]">
                <FileTextIcon size={64} className="text-[#3c5d80]" />
                <h3 className="text-xl font-bold text-[#91b9cd]">Paper & Reporting</h3>
                <p className="text-sm">Run the orchestrator to generate a document.</p>
            </div>
        );
    }

    return (
        <div className="w-full h-full bg-[#000000] p-6 flex flex-col gap-4 overflow-y-auto">
            <div className="w-full bg-[#151a1f] rounded-2xl border border-[#3c5d80] overflow-hidden flex flex-col shadow-2xl">
                <div className="flex items-center justify-between gap-3 px-6 py-4 border-b border-[#3c5d80] bg-[#1a2128]">
                    <div className="flex items-center gap-3">
                    <CheckCircleIcon size={24} className="text-[#6fe5b8]" />
                    <h2 className="text-lg font-bold text-white tracking-wide">
                            Reporting Output
                    </h2>
                    </div>

                    <div className="flex items-center gap-3">
                        {compileStatus === 'compiling' && (
                            <span className="text-xs text-[#8ebce9] inline-flex items-center gap-1">
                                <Loader2Icon size={14} className="animate-spin" /> Compiling PDF
                            </span>
                        )}
                        {compileStatus === 'error' && (
                            <span className="text-xs text-[#f5a2a2] inline-flex items-center gap-1">
                                <AlertCircleIcon size={14} /> Compile failed
                            </span>
                        )}
                        {pdfUrl && (
                            <button
                                onClick={handleDownload}
                                className="group px-4 py-2 bg-[#348cfd] hover:bg-[#599fff] text-white rounded-lg transition-all inline-flex items-center gap-2 font-semibold text-sm"
                            >
                                <DownloadIcon size={16} className="group-hover:animate-bounce" />
                                Download PDF
                            </button>
                        )}
                    </div>
                </div>

                <div className="grid grid-cols-1 xl:grid-cols-2 gap-4 p-4 bg-[#1e252b]">
                    <div className="rounded-xl border border-[#3c5d80] bg-[#12181d] overflow-hidden">
                        <div className="px-4 py-3 border-b border-[#3c5d80] bg-[#171e25] text-[#91b9cd] text-sm font-semibold flex items-center gap-2">
                            <FileTextIcon size={16} /> LaTeX Source
                        </div>
                        <pre className="p-4 text-xs leading-6 text-[#d7e5f3] overflow-auto h-[520px] whitespace-pre-wrap break-words">
                            {latexText || 'No LaTeX content received yet.'}
                        </pre>
                    </div>

                    <div className="rounded-xl border border-[#3c5d80] bg-[#12181d] overflow-hidden flex flex-col">
                        <div className="px-4 py-3 border-b border-[#3c5d80] bg-[#171e25] text-[#91b9cd] text-sm font-semibold flex items-center gap-2">
                            <FileOutputIcon size={16} /> Compiled PDF Preview
                        </div>

                        {pdfUrl ? (
                            <object data={pdfUrl} type="application/pdf" className="w-full h-[520px] bg-[#0f1419]">
                                <div className="p-4 text-sm text-gray-300">
                                    Embedded preview is unavailable in this browser. Use Download PDF.
                                </div>
                            </object>
                        ) : (
                            <div className="h-[520px] flex items-center justify-center text-sm text-gray-400 p-4 text-center">
                                {compileStatus === 'compiling'
                                    ? 'Compiling LaTeX into PDF...'
                                    : compileStatus === 'error'
                                        ? `Compilation failed. ${compileError}`
                                        : 'PDF is not available yet.'}
                            </div>
                        )}
                    </div>
                </div>

                <div className="px-6 py-4 border-t border-[#3c5d80] bg-[#1a2128] text-gray-300 text-sm">
                    {reportData?.summary || 'The generated LaTeX is displayed on the left. The backend compiler output is shown as PDF on the right.'}
                </div>
            </div>
        </div>
    );
}