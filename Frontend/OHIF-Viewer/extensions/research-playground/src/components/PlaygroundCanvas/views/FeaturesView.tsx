import React, { useCallback, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  BarChart3,
  BrainCircuit,
  CheckCircle,
  Download,
  GitMerge,
  Layers,
  Scan,
  TrendingUp,
  Zap,
} from 'lucide-react';
import Tabs from '../../shared/Tabs';
import ChartCard from '../../shared/Charts/ChartCard';
import ImportanceChart from './features/ImportanceChart';
import FeaturePlots from './features/FeaturePlots';
import KPIHeader from './features/KPIHeader';
import DownloadDropdown from './features/DownloadDropdown';
import FeatureDrawer from './features/FeatureDrawer';
import { FeatureImportanceData, FeaturesData } from './features/types';

type TabId = 'importance' | 'cumulative' | 'correlation' | 'pca' | 'tsne';

const TAB_CONFIG: Array<{ id: TabId; label: string; icon: React.ReactNode }> = [
  { id: 'importance',  label: 'Importance',  icon: <BarChart3  size={11} /> },
  { id: 'cumulative',  label: 'Cumulative',  icon: <TrendingUp size={11} /> },
  { id: 'correlation', label: 'Correlation', icon: <GitMerge   size={11} /> },
  { id: 'pca',         label: 'PCA',         icon: <Layers     size={11} /> },
  { id: 'tsne',        label: 't-SNE',       icon: <Activity   size={11} /> },
];

const METHOD_LABELS: Record<string, string> = {
  rf:           'Random Forest',
  f_test:       'ANOVA',
  mutual_info:  'Mutual Info',
  rfe:          'RFE',
};

interface FeaturesViewProps {
  featuresData?: FeaturesData | null;
  importanceData?: FeatureImportanceData | null;
  isLoading?: boolean;
}

function MethodSelector({ activeMethod }: { activeMethod: string | undefined }) {
  const methods = ['rf', 'f_test', 'mutual_info', 'rfe'];
  return (
    <div className="mb-2 flex flex-wrap items-center gap-1.5">
      <span className="text-[9px] uppercase tracking-wide text-[#3c5d80]">Method:</span>
      {methods.map(m => {
        const isActive = m === (activeMethod ?? 'rf');
        return (
          <button
            key={m}
            disabled={!isActive}
            title={
              isActive
                ? `Active method: ${METHOD_LABELS[m]}`
                : `To use ${METHOD_LABELS[m]}: re-run the Feature Extraction agent with method="${m}" in the task context.`
            }
            className="rounded-full border px-2.5 py-1 text-[10px] font-medium transition-all"
            style={{
              borderColor: isActive ? '#5acce6' : '#3c5d80',
              background: isActive ? 'rgba(90,204,230,0.12)' : 'transparent',
              color: isActive ? '#5acce6' : '#91b9cd',
              cursor: isActive ? 'default' : 'not-allowed',
              opacity: isActive ? 1 : 0.8,
            }}
          >
            {METHOD_LABELS[m] ?? m}
          </button>
        );
      })}
    </div>
  );
}

function EmptyCharts({ isLoading }: { isLoading?: boolean }) {
  if (isLoading) {
    return (
      <div className="flex flex-1 flex-col p-6 animate-pulse">
        <div className="h-6 w-48 bg-[#1f2d40] rounded mb-4" />
        <div className="h-64 w-full bg-[#1f2d40] rounded-lg mb-4" />
        <div className="h-48 w-full bg-[#1f2d40] rounded-lg" />
      </div>
    );
  }
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-4 p-8 text-center">
      <div className="relative">
        <Scan size={48} className="text-[#1f2d40]" />
        <BrainCircuit
          size={22}
          className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 animate-pulse text-[#5acce6]"
        />
      </div>
      <div>
        <p className="text-sm font-medium text-[#566a84]">Feature analysis not yet complete</p>
        <p className="mt-1 text-xs text-[#3c5d80]">
          Charts will appear once the importance agent finishes.
        </p>
      </div>
    </div>
  );
}

export default function FeaturesView({ featuresData, importanceData, isLoading }: FeaturesViewProps) {
  const [activeTab, setActiveTab] = useState<TabId>('importance');
  const [hiddenThresholds, setHiddenThresholds] = useState<Set<number>>(new Set());
  const [hiddenPcaClasses, setHiddenPcaClasses] = useState<Set<string>>(new Set());
  const [hiddenTsneClasses, setHiddenTsneClasses] = useState<Set<string>>(new Set());
  
  // ── Shared brushing state (Task 5)
  const [selectedIndex, setSelectedIndex] = useState<number | null>(null);

  const [scatterOpacity, setScatterOpacity] = useState<number>(0.75);

  const chartData = importanceData?.chart_data;

  const available: Record<TabId, boolean> = {
    importance:  Boolean(chartData?.importance_bars?.length),
    cumulative:  Boolean(chartData?.cumulative?.length),
    correlation: Boolean(chartData?.correlation),
    pca:         Boolean(chartData?.pca),
    tsne:        Boolean(chartData?.tsne),
  };

  const hasCharts = Object.values(available).some(Boolean);
  const displayTab = available[activeTab]
    ? activeTab
    : (Object.keys(available) as TabId[]).find(t => available[t]) || 'importance';

  const tabs = TAB_CONFIG.map(tab => ({ ...tab, disabled: !available[tab.id] }));

  const toggleThreshold = useCallback((threshold: number) => {
    setHiddenThresholds(prev => {
      const next = new Set(prev);
      if (next.has(threshold)) next.delete(threshold); else next.add(threshold);
      return next;
    });
  }, []);

  const togglePcaClass = useCallback((value: string) => {
    setHiddenPcaClasses(prev => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value); else next.add(value);
      return next;
    });
  }, []);

  const toggleTsneClass = useCallback((value: string) => {
    setHiddenTsneClasses(prev => {
      const next = new Set(prev);
      if (next.has(value)) next.delete(value); else next.add(value);
      return next;
    });
  }, []);

  return (
    <div className="flex h-full w-full flex-col gap-4 overflow-hidden bg-[#000000] p-4 relative">
      
      {/* ── Top Header Row ── */}
      {featuresData && (
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <KPIHeader featuresData={featuresData} importanceData={importanceData} />
          </div>
          <div className="shrink-0 mt-1">
            <DownloadDropdown csvPaths={featuresData?.csv_paths} />
          </div>
        </div>
      )}

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {hasCharts && chartData ? (
          <>
            <MethodSelector activeMethod={chartData.method ?? importanceData?.method} />

            <Tabs<TabId>
              tabs={tabs}
              active={displayTab}
              onChange={setActiveTab}
              hint="Double-click to expand · click pills to toggle"
            />

            {(displayTab === 'pca' || displayTab === 'tsne') && (
              <div className="mb-2 flex items-center gap-2">
                <label
                  htmlFor="scatter-opacity"
                  className="text-[9px] uppercase tracking-wide text-[#3c5d80]"
                >
                  Point Opacity
                </label>
                <input
                  id="scatter-opacity"
                  type="range"
                  min={0.2}
                  max={1.0}
                  step={0.05}
                  value={scatterOpacity}
                  onChange={e => setScatterOpacity(parseFloat(e.target.value))}
                  className="h-1 w-28 cursor-pointer accent-[#5acce6]"
                />
                <span className="font-mono text-[9px] text-[#566a84]">
                  {Math.round(scatterOpacity * 100)}%
                </span>
              </div>
            )}

            <ChartCard title={`Feature analysis — ${displayTab}`}>
              {displayTab === 'importance' && chartData.importance_bars ? (
                <ImportanceChart
                  bars={chartData.importance_bars}
                />
              ) : (
                <FeaturePlots
                  activeTab={displayTab}
                  chartData={chartData}
                  hiddenThresholds={hiddenThresholds}
                  toggleThreshold={toggleThreshold}
                  hiddenPcaClasses={hiddenPcaClasses}
                  togglePcaClass={togglePcaClass}
                  hiddenTsneClasses={hiddenTsneClasses}
                  toggleTsneClass={toggleTsneClass}
                  scatterOpacity={scatterOpacity}
                  selectedIndex={selectedIndex}
                  onSelectIndex={setSelectedIndex}
                />
              )}
            </ChartCard>
          </>
        ) : (
          <div className="flex flex-1 flex-col rounded-xl border border-[#1f2d40] bg-[#0b1015]">
            <EmptyCharts isLoading={isLoading} />
          </div>
        )}
      </div>

      {hasCharts && <FeatureDrawer importanceData={importanceData} featuresData={featuresData} />}
    </div>
  );
}
