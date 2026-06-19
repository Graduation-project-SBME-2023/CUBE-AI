export interface PopulationData {
  agent_id?: string;
  status?: string;
  outputs?: {
    metrics?: {
      cohort_size?: number;
      match_rate?: number;
      inclusion_criteria?: string[];
      exclusion_criteria?: string[];
    };
    summary?: string;
    files?: Record<string, string>;
  };
  tool?: string;
  count?: number;
  uid_list?: string[];
  inclusion_criteria?: string[];
  exclusion_criteria?: string[];
  modality?: string;
  data?: any;
}

export interface SegmentationData {
  agent_id?: string;
  status?: string;
  outputs?: {
    metrics?: {
      segmentation_method?: string;
      volumes_segmented?: number;
      roi_description?: string;
    };
    summary?: string;
    files?: Record<string, string>;
  };
  tool?: string;
  data?: any;
}

export interface AccumulatedPopulation {
  count: number | null;
  uids: string[];
  inclusion: string[];
  exclusion: string[];
  modality: string;
  summary: string;
  matchRate: number | null;
}
