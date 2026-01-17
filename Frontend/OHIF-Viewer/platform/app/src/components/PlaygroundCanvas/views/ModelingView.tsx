import React from 'react';
import { BarChart3, TrendingUp, GitBranch } from 'lucide-react';

export default function ModelingView() {
    return (
        <div className="w-full h-full flex flex-col items-center justify-center text-gray-600 space-y-4">
            <div className="flex gap-4 opacity-50">
                <BarChart3 size={48} className="text-[#3c5d80]" />
                <TrendingUp size={48} className="text-[#3c5d80]" />
            </div>
            <div className="text-center max-w-md">
                <h3 className="text-xl font-bold text-[#91b9cd]">Modeling & Statistics</h3>
                <p className="text-xs text-gray-500 mt-2">
                    Visualizations for ROC curves, Loss/Accuracy training history, and statistical hypothesis testing (t-test, ANOVA, Chi-Square) will appear here.
                </p>
            </div>
            <button className="px-6 py-2 border border-[#3c5d80] text-[#6fe5b8] rounded-full text-xs hover:bg-[#151a1f] transition-colors flex items-center gap-2">
                <GitBranch size={14} /> Select Algorithm
            </button>
        </div>
    );
}