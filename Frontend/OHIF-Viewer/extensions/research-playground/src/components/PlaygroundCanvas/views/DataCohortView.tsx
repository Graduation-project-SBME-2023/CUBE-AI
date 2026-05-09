import React, { useEffect, useState } from 'react';
import DataCohortPanel from './dataCohort/DataCohortPanel';
import { AccumulatedPopulation, PopulationData, SegmentationData } from './dataCohort/types';

interface DataCohortViewProps {
  populationData?: PopulationData | null;
  segmentationData?: SegmentationData | null;
}

const INITIAL_ACCUMULATED_POPULATION: AccumulatedPopulation = {
  count: null,
  uids: [],
  inclusion: [],
  exclusion: [],
  modality: 'Awaiting...',
  summary: '',
  matchRate: null,
};

export default function DataCohortView({ populationData, segmentationData }: DataCohortViewProps) {
  const [accumulatedPop, setAccumulatedPop] = useState(INITIAL_ACCUMULATED_POPULATION);

  useEffect(() => {
    if (!populationData) {
      return;
    }

    setAccumulatedPop(prev => {
      const next = { ...prev };

      if (populationData.tool === 'write_inclusion_criteria') {
        if (populationData.inclusion_criteria) next.inclusion = populationData.inclusion_criteria;
        if (populationData.exclusion_criteria) next.exclusion = populationData.exclusion_criteria;
        if (populationData.modality) next.modality = populationData.modality;
      }

      if (populationData.tool === 'search_volumes_by_description') {
        if (populationData.count !== undefined) next.count = populationData.count;
        if (populationData.uid_list) next.uids = populationData.uid_list;
      }

      if (populationData.outputs?.metrics) {
        const metrics = populationData.outputs.metrics;
        if (metrics.cohort_size) next.count = metrics.cohort_size;
        if (metrics.match_rate) next.matchRate = metrics.match_rate;
        if (metrics.inclusion_criteria) next.inclusion = metrics.inclusion_criteria;
        if (metrics.exclusion_criteria) next.exclusion = metrics.exclusion_criteria;
        if (populationData.outputs.summary) next.summary = populationData.outputs.summary;
      }

      return next;
    });
  }, [populationData]);

  const hasPopData = populationData && populationData.status !== undefined;

  return (
    <DataCohortPanel
      accumulatedPop={accumulatedPop}
      segmentationData={segmentationData}
      hasPopData={Boolean(hasPopData)}
      populationTool={populationData?.tool}
    />
  );
}
