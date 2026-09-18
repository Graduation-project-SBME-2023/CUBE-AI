/**
 * Application API paths (relative to BACKEND_API) that serve generated
 * downloads. Feature tables are rows in PostgreSQL, not workspace files.
 */
export type ExportPaths = Record<string, string>;

export interface FeaturesData {
  status: string;
  agent_run_id?: string;
  radiomics_rows?: { created?: number; reused?: number; total?: number };
  exports?: ExportPaths;
  num_subjects?: number;
  /** Patients that actually had at least one feature extracted (subset of num_subjects). */
  num_subjects_with_features?: number;
  /** Total patient-organ pairs that could not be extracted (audit count). */
  missing_patient_organs?: number;
  /** Subject IDs that were found in images/masks but had no matching target label. */
  unlabeled_subject_ids?: string[];
  num_features?: Record<string, number>;
  mask_labels?: number[];
  processing_time_seconds?: number;
}

export interface FeatureSetSummary {
  set_key: string;
  feature_set_id: string;
  feature_count?: number;
  frozen_at?: string | null;
  label_supervised_on_full_data?: boolean;
}

export interface ImportanceBar {
  feature: string;
  importance: number | null;
}

export interface CumulativePt {
  n_features: number;
  cumulative_pct: number;
}

export interface ScatterPt {
  x: number;
  y: number;
  target: string;
}

export interface PcaData {
  points: ScatterPt[];
  explained_variance: number[];
  n_features: number;
}

export interface TsneData {
  points: ScatterPt[];
  n_features: number;
}

export interface CorrelationData {
  labels: string[];
  matrix: (number | null)[][];
}

export interface ChartData {
  importance_bars?: ImportanceBar[];
  cumulative?: CumulativePt[];
  correlation?: CorrelationData | null;
  pca?: PcaData | null;
  tsne?: TsneData | null;
  task_type?: string;
  method?: string;
}

export interface FeatureImportanceData {
  status: string;
  method?: string;
  feature_count?: number;
  feature_sets?: FeatureSetSummary[];
  exports?: ExportPaths;
  label_supervised_on_full_data?: boolean;
  chart_data?: ChartData | null;
}
