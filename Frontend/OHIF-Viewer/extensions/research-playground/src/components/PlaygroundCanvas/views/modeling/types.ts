export interface RocCurve {
  fpr: number[];
  tpr: number[];
}

export interface PrCurve {
  precision: number[];
  recall: number[];
}

export interface PerModelData {
  label: string;
  cv_metrics: Record<string, number | null>;
  holdout_metrics: Record<string, number | null>;
  confusion_matrix: number[][] | null;
  confusion_matrix_labels: string[] | null;
  roc_curve: RocCurve | null;
  pr_curve: PrCurve | null;
}

export interface ModelingChartData {
  comparison_table: Array<Record<string, any>>;
  per_model: Record<string, PerModelData>;
  fold: number;
}

export interface ModelingData {
  status: string;
  experiment_name?: string;
  output_dir?: string;
  final_model_path?: string;
  individual_model_paths?: Record<string, string>;
  model_test_predictions?: string;
  model_summary?: string;
  created_files?: string[] | null;
  chart_data?: ModelingChartData | null;
}
