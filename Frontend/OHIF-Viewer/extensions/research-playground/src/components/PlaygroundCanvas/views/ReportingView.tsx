import React, { useEffect, useMemo, useState } from 'react';
import { FileText, Download, CheckCircle, FileOutput, AlertCircle, Loader2 } from 'lucide-react';
import { compileLatexReport } from '../../../services/api';
import { ReportingViewProps, CompileStatus } from './reporting/types';
import { extractLatexText, handlePdfDownload } from './reporting/utils';
import { EmptyReportingState } from './reporting/Shared';
import { AGENTS_API } from '../../../config';

const FileTextIcon: any = FileText;
const DownloadIcon: any = Download;
const CheckCircleIcon: any = CheckCircle;
const FileOutputIcon: any = FileOutput;
const AlertCircleIcon: any = AlertCircle;
const Loader2Icon: any = Loader2;

export default function ReportingView({ reportData, latexData, sessionId }: ReportingViewProps) {
    const hasReport = reportData && reportData.status === 'success';
    const latexText = useMemo(() => extractLatexText(latexData), [latexData]);
    const hasLatex = !!latexText || !!latexData?.latex_pdf || !!latexData?.pdf_path;
    const [compileStatus, setCompileStatus] = useState<CompileStatus>('idle');
    const [compileError, setCompileError] = useState<string>('');
    const [compiledPdfUrl, setCompiledPdfUrl] = useState<string>('');
    const [editableLatex, setEditableLatex] = useState<string>(latexText || '');
    const [outputFilename, setOutputFilename] = useState<string>(latexData?.output_filename || 'final_report.pdf');

    const sessionForCompile = sessionId || latexData?.sessionId || reportData?.sessionId;
    
    let pdfUrl = compiledPdfUrl || latexData?.latex_pdf || latexData?.pdf_path;
    const backendPdfPath = latexData?.outputs?.files?.manuscript_pdf;
    if (!pdfUrl && backendPdfPath && sessionForCompile) {
        const filename = backendPdfPath.split('/').pop() || 'research_abstract.pdf';
        pdfUrl = `${AGENTS_API}/files/workspace_${sessionForCompile}/reports/${filename}`;
    }

    const [pdfBlobUrl, setPdfBlobUrl] = useState<string>('');

    useEffect(() => {
        setEditableLatex(latexText || '');
    }, [latexText]);

    useEffect(() => {
        if (latexData?.output_filename) {
            setOutputFilename(latexData.output_filename);
        }
    }, [latexData?.output_filename]);

    useEffect(() => {
        if (!pdfUrl) {
            setPdfBlobUrl('');
            return;
        }
        
        let isMounted = true;
        fetch(pdfUrl)
            .then(res => {
                if (!res.ok) throw new Error('Network response was not ok');
                return res.blob();
            })
            .then(blob => {
                if (isMounted) {
                    const url = URL.createObjectURL(blob);
                    setPdfBlobUrl(url);
                }
            })
            .catch(err => {
                console.error("Failed to fetch PDF for inline preview", err);
                if (isMounted) setPdfBlobUrl(pdfUrl);
            });
            
        return () => {
            isMounted = false;
        };
    }, [pdfUrl]);

    const handleCompile = async () => {
        if (!sessionForCompile) {
            setCompileStatus('error');
            setCompileError('Missing session id for compile');
            return;
        }

        setCompileStatus('compiling');
        setCompileError('');

        try {
            const result = await compileLatexReport(
                sessionForCompile,
                editableLatex,
                outputFilename || 'final_report.pdf'
            );

            if (result?.status === 'success' && result?.pdf_url) {
                setCompiledPdfUrl(result.pdf_url);
                setCompileStatus('success');
                return;
            }

            setCompileStatus('error');
            setCompileError(result?.message || 'Compilation failed');
        } catch (error: any) {
            setCompileStatus('error');
            setCompileError(error?.message || 'Compilation request failed');
        }
    };

    // NO REPORT YET STATE
    if (!hasReport && !hasLatex) {
        return <EmptyReportingState />;
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
                        <button
                            onClick={handleCompile}
                            disabled={compileStatus === 'compiling'}
                            className="group px-4 py-2 bg-[#22c55e] hover:bg-[#34d66e] disabled:opacity-60 disabled:cursor-not-allowed text-white rounded-lg transition-all inline-flex items-center gap-2 font-semibold text-sm"
                        >
                            {compileStatus === 'compiling' ? (
                                <Loader2Icon size={16} className="animate-spin" />
                            ) : (
                                <FileOutputIcon size={16} />
                            )}
                            Compile PDF
                        </button>
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
                                onClick={() => handlePdfDownload(pdfUrl)}
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
                        <textarea
                            value={editableLatex}
                            onChange={(event) => setEditableLatex(event.target.value)}
                            spellCheck={false}
                            className="p-4 text-xs leading-6 text-[#d7e5f3] overflow-auto h-[520px] w-full bg-transparent resize-none outline-none whitespace-pre-wrap break-words"
                            placeholder="No LaTeX content received yet."
                        />
                    </div>

                    <div className="rounded-xl border border-[#3c5d80] bg-[#12181d] overflow-hidden flex flex-col">
                        <div className="px-4 py-3 border-b border-[#3c5d80] bg-[#171e25] text-[#91b9cd] text-sm font-semibold flex items-center gap-2">
                            <FileOutputIcon size={16} /> Compiled PDF Preview
                        </div>

                        {pdfBlobUrl ? (
                            <iframe src={pdfBlobUrl} className="w-full h-[520px] bg-[#0f1419]" title="PDF Preview">
                                <div className="p-4 text-sm text-gray-300">
                                    Embedded preview is unavailable in this browser. Use Download PDF.
                                </div>
                            </iframe>
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