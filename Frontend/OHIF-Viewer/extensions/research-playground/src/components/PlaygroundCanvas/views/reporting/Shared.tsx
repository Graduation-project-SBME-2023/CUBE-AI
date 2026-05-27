import React from 'react';
import { FileText } from 'lucide-react';

const FileTextIcon: any = FileText;

export function EmptyReportingState() {
    return (
        <div className="w-full h-full flex flex-col items-center justify-center text-gray-600 space-y-4 bg-[#000000]">
            <FileTextIcon size={64} className="text-[#3c5d80]" />
            <h3 className="text-xl font-bold text-[#91b9cd]">Paper & Reporting</h3>
            <p className="text-sm">Run the orchestrator to generate a document.</p>
        </div>
    );
}
