import { useCallback, useMemo } from 'react';
import { useResearchContext } from '../context/ResearchContext';

export function useResearchData() {
  const { data, setData } = useResearchContext();

  const setLiteratureOutput = useCallback(
    (payload: any) =>
      setData(prev =>
        prev.literatureOutput === payload ? prev : { ...prev, literatureOutput: payload }
      ),
    [setData]
  );

  const setPopulationData = useCallback(
    (payload: any) =>
      setData(prev => (prev.populationData === payload ? prev : { ...prev, populationData: payload })),
    [setData]
  );

  const setSegmentationData = useCallback(
    (payload: any) =>
      setData(prev =>
        prev.segmentationData === payload ? prev : { ...prev, segmentationData: payload }
      ),
    [setData]
  );

  const setFeaturesData = useCallback(
    (payload: any) =>
      setData(prev => (prev.featuresData === payload ? prev : { ...prev, featuresData: payload })),
    [setData]
  );

  const setImportanceData = useCallback(
    (payload: any) =>
      setData(prev =>
        prev.importanceData === payload ? prev : { ...prev, importanceData: payload }
      ),
    [setData]
  );

  const setModelingData = useCallback(
    (payload: any) =>
      setData(prev => ({
        ...prev,
        modelingData: { ...(prev.modelingData ?? {}), ...payload },
      })),
    [setData]
  );

  const setReportData = useCallback(
    (payload: any) =>
      setData(prev => (prev.reportData === payload ? prev : { ...prev, reportData: payload })),
    [setData]
  );

  const setLatexData = useCallback(
    (payload: any) =>
      setData(prev => (prev.latexData === payload ? prev : { ...prev, latexData: payload })),
    [setData]
  );

  return useMemo(
    () => ({
      data,
      setData,
      setLiteratureOutput,
      setPopulationData,
      setSegmentationData,
      setFeaturesData,
      setImportanceData,
      setModelingData,
      setReportData,
      setLatexData,
    }),
    [
      data,
      setData,
      setFeaturesData,
      setImportanceData,
      setLatexData,
      setLiteratureOutput,
      setModelingData,
      setPopulationData,
      setReportData,
      setSegmentationData,
    ]
  );
}
