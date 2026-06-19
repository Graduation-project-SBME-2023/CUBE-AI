import React, { useCallback, useState } from 'react';
import { Activity, BarChart3, GitMerge, Layers, Scan, TrendingUp } from 'lucide-react';
import Tabs from '../../shared/Tabs';
import ChartCard from '../../shared/Charts/ChartCard';
import ImportanceChart from './features/ImportanceChart';
import FeaturePlots from './features/FeaturePlots';
import FeatureTable from './features/FeatureTable';
import { FeatureImportanceData, FeaturesData } from './features/types';

type TabId = 'importance' | 'cumulative' | 'correlation' | 'pca' | 'tsne';

const TAB_CONFIG: Array<{ id: TabId; label: string; icon: React.ReactNode }> = [
  { id: 'importance', label: 'Importance', icon: <BarChart3 size={11} /> },
  { id: 'cumulative', label: 'Cumulative', icon: <TrendingUp size={11} /> },
  { id: 'correlation', label: 'Correlation', icon: <GitMerge size={11} /> },
  { id: 'pca', label: 'PCA', icon: <Layers size={11} /> },
  { id: 'tsne', label: 't-SNE', icon: <Activity size={11} /> },
];

interface FeaturesViewProps {
  featuresData?: FeaturesData | null;
  importanceData?: FeatureImportanceData | null;
}

function EmptyCharts() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="relative">
        <Scan size={48} className="text-[#1f2d40]" />
        <BarChart3 size={22} className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 animate-pulse text-[#5acce6]" />
      </div>
      <div>
        <p className="text-sm font-medium text-[#566a84]">Feature analysis not yet complete</p>
        <p className="mt-1 text-xs text-[#3c5d80]">Charts will appear once the importance agent finishes.</p>
      </div>
    </div>
  );
}

export default function FeaturesView({ featuresData, importanceData }: FeaturesViewProps) {
  const [activeTab, setActiveTab] = useState<TabId>('importance');
  const [hiddenFeatures, setHiddenFeatures] = useState<Set<string>>(new Set());
  const [hiddenThresholds, setHiddenThresholds] = useState<Set<number>>(new Set());
  const [hiddenPcaClasses, setHiddenPcaClasses] = useState<Set<string>>(new Set());
  const [hiddenTsneClasses, setHiddenTsneClasses] = useState<Set<string>>(new Set());

  const chartData = importanceData?.chart_data;
  const available: Record<TabId, boolean> = {
    importance: Boolean(chartData?.importance_bars?.length),
    cumulative: Boolean(chartData?.cumulative?.length),
    correlation: Boolean(chartData?.correlation),
    pca: Boolean(chartData?.pca),
    tsne: Boolean(chartData?.tsne),
  };

  const hasCharts = Object.values(available).some(Boolean);
  const displayTab = available[activeTab]
    ? activeTab
    : (Object.keys(available) as TabId[]).find(t => available[t]) || 'importance';

  const tabs = TAB_CONFIG.map(tab => ({ ...tab, disabled: !available[tab.id] }));

  const toggleFeature = useCallback((feature: string) => {
    setHiddenFeatures(prev => {
      const next = new Set(prev);
      if (next.has(feature)) next.delete(feature);
      else next.add(feature);
      return next;
    });
  }, []);

  const toggleThreshold = useCallback((threshold: number) => {
    setHiddenThresholds(prev => {
      const next = new Set(prev);
      if (next.has(threshold)) next.delete(threshold);
      else next.add(threshold);
      return next;
    });
  }, []);

  const togglePcaClass = useCallback((value: string) => {
    setHiddenPcaClasses(prev => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }, []);

  const toggleTsneClass = useCallback((value: string) => {
    setHiddenTsneClasses(prev => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value);
      else next.add(value);
      return next;
    });
  }, []);

  return (
    <div className="flex h-full w-full flex-col gap-4 overflow-hidden bg-[#000000] p-4">
      <div className="flex min-h-0 flex-1 gap-4">
        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {hasCharts && chartData ? (
            <>
              <Tabs<TabId>
                tabs={tabs}
                active={displayTab}
                onChange={setActiveTab}
                hint="Double-click to expand · click pills to toggle"
              />

              <ChartCard title={`Feature analysis — ${displayTab}`}>
                {displayTab === 'importance' && chartData.importance_bars ? (
                  <ImportanceChart bars={chartData.importance_bars} hiddenFeatures={hiddenFeatures} onToggle={toggleFeature} />
                ) : (
                  <FeaturePlots
                    chartData={chartData}
                    hiddenThresholds={hiddenThresholds}
                    toggleThreshold={toggleThreshold}
                    hiddenPcaClasses={hiddenPcaClasses}
                    togglePcaClass={togglePcaClass}
                    hiddenTsneClasses={hiddenTsneClasses}
                    toggleTsneClass={toggleTsneClass}
                  />
                )}
              </ChartCard>
            </>
          ) : (
            <div className="flex flex-1 flex-col rounded-xl border border-[#1f2d40] bg-[#0b1015]">
              <EmptyCharts />
            </div>
          )}
        </div>

        <FeatureTable featuresData={featuresData} importanceData={importanceData} />
      </div>
    </div>
  );
}
