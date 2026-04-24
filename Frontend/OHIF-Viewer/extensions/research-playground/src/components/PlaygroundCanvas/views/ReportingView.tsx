import React from 'react';
import { FileText, Download, CheckCircle, FileOutput } from 'lucide-react';

const FileTextIcon: any = FileText;
const DownloadIcon: any = Download;
const CheckCircleIcon: any = CheckCircle;
const FileOutputIcon: any = FileOutput;

export interface ReportingViewProps {
    reportData?: any;
    latexData?: any;
}

export default function ReportingView({ reportData, latexData }: ReportingViewProps) {
    const hasReport = reportData && reportData.status === 'success';
    const hasLatex = latexData && (latexData.status === 'success' || latexData.latex_pdf);
    
    // The raw URL for downloading
    const pdfUrl = latexData?.latex_pdf || latexData?.pdf_path;

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

    // SUCCESS STATE
    return (
        <div className="w-full h-full bg-[#000000] p-6 flex flex-col items-center justify-center overflow-y-auto">
            
            {/* Main Card */}
            <div className="w-full max-w-xl bg-[#151a1f] rounded-2xl border border-[#3c5d80] overflow-hidden flex flex-col shadow-2xl">
                
                {/* Header */}
                <div className="flex items-center gap-3 px-6 py-4 border-b border-[#3c5d80] bg-[#1a2128]">
                    <CheckCircleIcon size={24} className="text-[#6fe5b8]" />
                    <h2 className="text-lg font-bold text-white tracking-wide">
                        Report Ready for Download
                    </h2>
                </div>

                {/* Body Area */}
                <div className="p-10 flex flex-col items-center text-center space-y-6 bg-[#1e252b]">
                    
                    {/* Big Icon */}
                    <div className="p-6 bg-[#151a1f] rounded-full border border-[#3c5d80] shadow-inner relative">
                        <FileOutputIcon size={56} className="text-[#348cfd]" />
                        {/* Decorative ping effect behind the icon */}
                        <div className="absolute inset-0 rounded-full border border-[#348cfd] animate-ping opacity-20"></div>
                    </div>
                    
                    {/* Text Content */}
                    <div className="space-y-2">
                        <h3 className="text-2xl font-semibold text-gray-100">
                            Clinical Hypothesis Document
                        </h3>
                        <p className="text-gray-400 max-w-sm mx-auto leading-relaxed text-sm">
                            {reportData?.summary || "Your data has been successfully compiled into a finalized PDF report. Click below to save it to your device."}
                        </p>
                    </div>

                    {/* Action Button */}
                    {pdfUrl && (
                        <div className="pt-6 w-full flex justify-center">
                            <button
                                onClick={handleDownload}
                                className="group relative px-8 py-4 w-full sm:w-3/4 bg-[#348cfd] hover:bg-[#599fff] text-white rounded-xl transition-all flex items-center justify-center gap-3 font-bold text-lg shadow-[0_0_20px_rgba(52,140,253,0.3)] hover:shadow-[0_0_30px_rgba(52,140,253,0.5)] transform hover:-translate-y-1"
                            >
                                <DownloadIcon size={24} className="group-hover:animate-bounce" />
                                <span>Download PDF</span>
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}