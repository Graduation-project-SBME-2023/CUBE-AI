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
    const [lastCompiledAt, setLastCompiledAt] = useState<number>(0);
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
        if (!latexData) {
            setCompiledPdfUrl('');
            setCompileStatus('idle');
            setCompileError('');
        }
    }, [latexText, latexData]);

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
        
        // Append timestamp for cache busting when re-compiling the exact same URL
        const fetchUrl = lastCompiledAt ? `${pdfUrl}${pdfUrl.includes('?') ? '&' : '?'}t=${lastCompiledAt}` : pdfUrl;

        fetch(fetchUrl)
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
    }, [pdfUrl, lastCompiledAt]);

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
                setLastCompiledAt(Date.now());
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
        <div className="w-full h-full bg-[#06090e] p-6 flex flex-col gap-6 overflow-y-auto [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-[#1f2d40] [&::-webkit-scrollbar-thumb]:rounded-full hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]/50 transition-colors">
            <div className="w-full max-w-7xl mx-auto bg-[#0b1015]/80 rounded-3xl border border-white/5 overflow-hidden flex flex-col shadow-lg shadow-black/40 backdrop-blur-md">
                
                {/* Header */}
                <div className="flex flex-wrap items-center justify-between gap-4 px-8 py-5 border-b border-white/5 bg-white/[0.01]">
                    <div className="flex items-center gap-4">
                        <div className="w-10 h-10 rounded-xl bg-[#6fe5b8]/10 flex items-center justify-center border border-[#6fe5b8]/20 shadow-[0_0_15px_rgba(111,229,184,0.1)]">
                            <CheckCircleIcon size={20} className="text-[#6fe5b8]" />
                        </div>
                        <div>
                            <h2 className="text-sm font-bold text-white tracking-wide">
                                Reporting Output
                            </h2>
                            <p className="text-[11px] text-[#566a84] mt-0.5 uppercase tracking-widest font-semibold">
                                Manuscript Compilation
                            </p>
                        </div>
                    </div>

                    <div className="flex items-center gap-3">
                        <button
                            onClick={handleCompile}
                            disabled={compileStatus === 'compiling'}
                            className="group px-5 py-2.5 bg-[#6fe5b8]/10 hover:bg-[#6fe5b8]/20 border border-[#6fe5b8]/20 disabled:opacity-50 disabled:cursor-not-allowed text-[#6fe5b8] rounded-xl transition-all inline-flex items-center gap-2 font-bold text-[11px] uppercase tracking-widest shadow-sm hover:shadow-[#6fe5b8]/10"
                        >
                            {compileStatus === 'compiling' ? (
                                <Loader2Icon size={14} className="animate-spin" />
                            ) : (
                                <FileOutputIcon size={14} />
                            )}
                            Compile PDF
                        </button>
                        {compileStatus === 'compiling' && (
                            <span className="text-[11px] text-[#5acce6] inline-flex items-center gap-1.5 font-medium uppercase tracking-wider">
                                <Loader2Icon size={12} className="animate-spin" /> Compiling...
                            </span>
                        )}
                        {compileStatus === 'error' && (
                            <span className="text-[11px] text-[#f5a2a2] inline-flex items-center gap-1.5 font-medium uppercase tracking-wider">
                                <AlertCircleIcon size={12} /> Compile failed
                            </span>
                        )}
                        {pdfUrl && (
                            <button
                                onClick={() => handlePdfDownload(pdfUrl)}
                                className="group px-5 py-2.5 bg-[#5acce6]/10 hover:bg-[#5acce6]/20 border border-[#5acce6]/20 text-[#5acce6] rounded-xl transition-all inline-flex items-center gap-2 font-bold text-[11px] uppercase tracking-widest shadow-sm hover:shadow-[#5acce6]/10"
                            >
                                <DownloadIcon size={14} className="group-hover:animate-bounce" />
                                Download PDF
                            </button>
                        )}
                    </div>
                </div>

                <div className="grid grid-cols-1 xl:grid-cols-2 gap-6 p-6 md:p-8 bg-transparent">
                    {/* LaTeX Source Pane */}
                    <div className="rounded-2xl border border-white/5 bg-[#06090e]/60 overflow-hidden shadow-inner flex flex-col h-[600px] transition-colors hover:border-white/10">
                        <div className="px-5 py-3 border-b border-white/5 bg-white/[0.02] flex items-center gap-2">
                            <FileTextIcon size={14} className="text-[#5acce6]" /> 
                            <span className="text-[#5acce6] text-[11px] font-bold uppercase tracking-widest">LaTeX Source</span>
                        </div>
                        <div className="flex-1 overflow-hidden">
                            <textarea
                                value={editableLatex}
                                onChange={(event) => setEditableLatex(event.target.value)}
                                spellCheck={false}
                                className="p-5 text-[12px] leading-relaxed text-[#91b9cd] h-full w-full bg-transparent resize-none outline-none whitespace-pre-wrap break-words [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:bg-[#1f2d40] [&::-webkit-scrollbar-thumb]:rounded-full hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]/50 transition-colors"
                                placeholder="No LaTeX content received yet."
                            />
                        </div>
                    </div>

                    {/* PDF Preview Pane */}
                    <div className="rounded-2xl border border-white/5 bg-[#06090e]/60 overflow-hidden flex flex-col shadow-inner h-[600px] transition-colors hover:border-white/10">
                        <div className="px-5 py-3 border-b border-white/5 bg-white/[0.02] flex items-center gap-2">
                            <FileOutputIcon size={14} className="text-[#5acce6]" /> 
                            <span className="text-[#5acce6] text-[11px] font-bold uppercase tracking-widest">Compiled PDF Preview</span>
                        </div>
                        
                        <div className="flex-1 bg-white relative">
                            {pdfBlobUrl ? (
                                <iframe src={pdfBlobUrl} className="w-full h-full border-none" title="PDF Preview">
                                    <div className="p-5 text-[12px] text-gray-500 bg-[#06090e] h-full flex items-center justify-center">
                                        Embedded preview is unavailable in this browser. Use Download PDF.
                                    </div>
                                </iframe>
                            ) : (
                                <div className="h-full bg-[#06090e] flex items-center justify-center text-[12px] text-slate-500 p-6 text-center font-medium">
                                    {compileStatus === 'compiling'
                                        ? 'Compiling LaTeX into PDF...'
                                        : compileStatus === 'error'
                                            ? <span className="text-[#f5a2a2]">Compilation failed. {compileError}</span>
                                            : 'PDF preview is not available yet.'}
                                </div>
                            )}
                        </div>
                    </div>
                </div>

                {/* Footer */}
                <div className="px-8 py-5 border-t border-white/5 bg-white/[0.01] text-slate-400 text-[11px] font-medium flex items-center gap-2">
                    <span className="w-1.5 h-1.5 rounded-full bg-[#5acce6] animate-pulse"></span>
                    {reportData?.summary || 'The generated LaTeX is displayed on the left. The backend compiler output is shown as a PDF preview on the right.'}
                </div>
            </div>
        </div>
    );
}