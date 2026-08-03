import React, { useState, useMemo } from 'react';
import { X, Search, List, BarChart3, Database } from 'lucide-react';
import { FeatureImportanceData, FeaturesData } from './types';

interface FeatureDrawerProps {
  importanceData?: FeatureImportanceData | null;
  featuresData?: FeaturesData | null;
}

export default function FeatureDrawer({ importanceData, featuresData }: FeatureDrawerProps) {
  const [isOpen, setIsOpen] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');

  const features = importanceData?.chart_data?.importance_bars || [];
  
  const filteredFeatures = useMemo(() => {
    if (!searchTerm) return features;
    const lower = searchTerm.toLowerCase();
    return features.filter(f => f.feature.toLowerCase().includes(lower));
  }, [features, searchTerm]);

  const totalFeatures = useMemo(() => {
    if (!featuresData?.num_features) return 0;
    return Object.values(featuresData.num_features).reduce((sum, count) => sum + count, 0);
  }, [featuresData]);

  const totalFeaturesCount = importanceData?.feature_count || totalFeatures;

  return (
    <>
      {/* Floating Trigger Button */}
      <button
        onClick={() => setIsOpen(true)}
        className="absolute bottom-6 left-1/2 -translate-x-1/2 z-40 flex h-9 items-center justify-center gap-2 rounded-full bg-[#5acce6] px-4 text-[#000000] shadow-lg transition-transform hover:scale-105 active:scale-95 font-medium text-[11px]"
        title="Open Feature List"
      >
        <List size={14} />
        View Master Feature List
      </button>

      {/* Overlay */}
      {isOpen && (
        <div 
          className="fixed inset-0 z-50 bg-black/50 transition-opacity backdrop-blur-sm"
          onClick={() => setIsOpen(false)}
        />
      )}

      {/* Drawer */}
      <div 
        className={`fixed inset-y-0 right-0 z-50 w-[500px] max-w-full bg-[#0b1015] border-l border-[#1f2d40] shadow-2xl transition-transform duration-300 flex flex-col ${
          isOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        <div className="flex items-center justify-between border-b border-[#1f2d40] px-4 py-3 bg-[#151a1f]/80">
          <div className="flex items-center gap-2">
            <Database size={16} className="text-[#5acce6]" />
            <h2 className="text-sm font-semibold text-gray-100">Top Important Features</h2>
            <span className="rounded-full bg-[#1f2d40] px-2 py-0.5 text-[10px] text-[#91b9cd]" title="Visualized top features">
              {features.length}
            </span>
          </div>
          <button 
            onClick={() => setIsOpen(false)}
            className="rounded p-1 text-[#566a84] hover:bg-[#1f2d40] hover:text-white"
          >
            <X size={16} />
          </button>
        </div>

        <div className="shrink-0 p-4 pb-2 border-b border-[#1f2d40] flex flex-col">
          {/* Analysis Info Panel */}
          <div className="mb-6 rounded-lg border border-[#1f2d40] bg-[#151a1f]/50 p-4">
            <h3 className="mb-3 flex items-center gap-2 text-xs font-semibold tracking-wider text-[#566a84]">
              <BarChart3 size={14} />
              ANALYSIS INFO
            </h3>
            <div className="flex flex-col gap-2">
              <div className="flex justify-between items-center text-[11px]">
                <span className="text-[#566a84]">Method</span>
                <span className="font-mono text-[#5acce6]">{importanceData?.method ?? 'rf'}</span>
              </div>
              <div className="flex justify-between items-center text-[11px]">
                <span className="text-[#566a84]">Task</span>
                <span className="font-mono text-[#5acce6]">{importanceData?.chart_data?.task_type ?? '—'}</span>
              </div>
              {totalFeaturesCount > 0 && (
                <div className="flex justify-between items-center text-[11px]" title="Total raw features extracted before selection">
                  <span className="text-[#566a84]">Total Features</span>
                  <span className="font-mono text-[#5acce6]">{totalFeaturesCount}</span>
                </div>
              )}
              <div className="flex justify-between items-center text-[11px]" title="Number of top features sent to the dashboard for visualization (capped for browser performance)">
                <span className="text-[#566a84]">Visualized Features</span>
                <span className="font-mono text-[#5acce6]">Top {features.length}</span>
              </div>
            </div>
          </div>

          <div className="relative mb-4">
            <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[#566a84]" />
            <input
              type="text"
              placeholder="Search features (e.g., glcm, shape)..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="w-full rounded-lg border border-[#1f2d40] bg-[#151a1f] py-2 pl-9 pr-3 text-sm text-gray-100 placeholder:text-[#566a84] focus:border-[#5acce6] focus:outline-none"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto p-4 pt-2 ohif-scrollbar [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-[#3c5d80] [&::-webkit-scrollbar-track]:rounded-full [&::-webkit-scrollbar-track]:bg-[#0b1015] [&::-webkit-scrollbar]:w-2 hover:[&::-webkit-scrollbar-thumb]:bg-[#5acce6]">
          {filteredFeatures.length === 0 ? (
            <div className="py-8 text-center text-sm text-[#566a84]">
              No features match "{searchTerm}"
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              {filteredFeatures.map((f, i) => (
                <div key={i} className="flex items-center justify-between rounded px-2 py-1.5 hover:bg-[#151a1f] group">
                  <span className="font-mono text-xs text-[#91b9cd] truncate pr-4" title={f.feature}>
                    {f.feature}
                  </span>
                  <div className="flex items-center gap-2 shrink-0">
                    <div className="w-16 h-1.5 bg-[#1f2d40] rounded-full overflow-hidden">
                      <div 
                        className="h-full bg-[#5acce6]"
                        style={{ width: `${Math.max(2, (f.importance || 0) * 100)}%` }}
                      />
                    </div>
                    <span className="w-8 text-right font-mono text-[9px] text-[#566a84]">
                      {((f.importance || 0) * 100).toFixed(1)}%
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
