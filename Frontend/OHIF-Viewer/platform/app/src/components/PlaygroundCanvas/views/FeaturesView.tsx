import React from 'react';
import { Scan, BrainCircuit, List } from 'lucide-react';

export default function FeaturesView() {
    return (
        <div className="w-full h-full flex flex-col items-center justify-center text-gray-600 space-y-4">
            <div className="relative">
                <Scan size={64} className="text-[#3c5d80]" />
                <BrainCircuit size={32} className="text-[#5acce6] absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 animate-pulse" />
            </div>
            <div className="text-center max-w-md">
                <h3 className="text-xl font-bold text-[#91b9cd]">Feature Extraction Engine</h3>
                <p className="text-xs text-gray-500 mt-2">
                    This module defines Regions of Interest (ROI) and extracts quantitative radiomics features (shape, texture, intensity) for the modeling phase.
                </p>
            </div>
            <button className="px-6 py-2 border border-[#3c5d80] text-[#5acce6] rounded-full text-xs hover:bg-[#151a1f] transition-colors flex items-center gap-2">
                <List size={14} /> Configure Radiomics Class
            </button>
        </div>
    );
}