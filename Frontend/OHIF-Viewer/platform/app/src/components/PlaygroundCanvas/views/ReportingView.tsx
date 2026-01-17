import React from 'react';
import { FileText, Download } from 'lucide-react';

export default function ReportingView() {
    return (
        <div className="w-full h-full flex flex-col items-center justify-center text-gray-600 space-y-4">
            <FileText size={64} className="text-[#3c5d80]" />
            <div className="text-center max-w-md">
                <h3 className="text-xl font-bold text-[#91b9cd]">Paper & Reporting</h3>
                <p className="text-xs text-gray-500 mt-2">
                    This module compiles findings from all previous steps into a structured medical report or academic paper draft, comparing results against the initial hypothesis.
                </p>
            </div>
            <button className="px-6 py-2 bg-[#348cfd] text-white rounded-full text-xs hover:bg-[#599fff] transition-colors flex items-center gap-2 shadow-lg">
                <Download size={14} /> Export PDF Draft
            </button>
        </div>
    );
}