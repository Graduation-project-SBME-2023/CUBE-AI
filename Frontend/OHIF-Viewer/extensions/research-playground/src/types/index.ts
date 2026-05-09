import { FeatureImportanceData, FeaturesData } from '../components/PlaygroundCanvas/views/features/types';
import { ModelingData } from '../components/PlaygroundCanvas/views/modeling/types';
import { PopulationData, SegmentationData } from '../components/PlaygroundCanvas/views/dataCohort/types';

export interface ResearchViewState {
  literatureOutput: any;
  populationData: PopulationData | null;
  segmentationData: SegmentationData | null;
  featuresData: FeaturesData | null;
  importanceData: FeatureImportanceData | null;
  modelingData: ModelingData | null;
  reportData: any;
  latexData: any;
}

export interface ResearchRouteState {
  hypothesis?: string;
  sessionId?: string;
  session_id?: string;
  includeLitReview?: boolean;
  sendInitialMessage?: boolean;
  literatureReviewOutput?: any;
  literatureContext?: any;
}
