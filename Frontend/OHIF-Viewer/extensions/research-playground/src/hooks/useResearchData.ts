import { useResearchContext } from '../context/ResearchContext';

export function useResearchData() {
  const { data, setData } = useResearchContext();

  const setLiteratureOutput = (payload: any) => setData(prev => ({ ...prev, literatureOutput: payload }));
  const setPopulationData = (payload: any) => setData(prev => ({ ...prev, populationData: payload }));
  const setSegmentationData = (payload: any) => setData(prev => ({ ...prev, segmentationData: payload }));
  const setFeaturesData = (payload: any) => setData(prev => ({ ...prev, featuresData: payload }));
  const setImportanceData = (payload: any) => setData(prev => ({ ...prev, importanceData: payload }));
  const setModelingData = (payload: any) => setData(prev => ({ ...prev, modelingData: payload }));
  const setReportData = (payload: any) => setData(prev => ({ ...prev, reportData: payload }));
  const setLatexData = (payload: any) => setData(prev => ({ ...prev, latexData: payload }));

  return {
    data,
    setLiteratureOutput,
    setPopulationData,
    setSegmentationData,
    setFeaturesData,
    setImportanceData,
    setModelingData,
    setReportData,
    setLatexData,
  };
}
