import { BarChart3, BookOpenText, Database, FileText, Layout, Target, Activity, Users } from 'lucide-react';

export interface ResearchPhase {
  id: string;
  label: string;
  short_desc: string;
  icon: any;
  description: string;
}

export const RESEARCH_PHASES: ResearchPhase[] = [
  {
    id: 'planning',
    label: 'Study Planning',
    short_desc: 'Design & Hypothesis',
    icon: Layout,
    description: 'Define the research question, scope, and plan structure',
  },
  {
    id: 'execution',
    label: 'Agent Execution',
    short_desc: 'Live Agent Chat',
    icon: Users,
    description: 'Real-time agent group chat',
  },
  {
    id: 'literature',
    label: 'Literature Review',
    short_desc: 'Evidence Synthesis',
    icon: BookOpenText,
    description: 'Knowledge base, source map, and hypothesis context',
  },
  {
    id: 'data',
    label: 'Data & Population',
    short_desc: 'Cohort Selection',
    icon: Database,
    description: 'Inclusion Criteria, Cohort Selection & QA',
  },
  {
    id: 'segmentation',
    label: 'Segmentation QC',
    short_desc: 'Segmentation HITL',
    icon: Target,
    description: 'Radiologists Approval of Segmentations',
  },
  {
    id: 'features',
    label: 'Feature Extraction',
    short_desc: 'ROI & Radiomics',
    icon: Activity,
    description: 'ROI Definition, Radiomics & Preprocessing',
  },
  {
    id: 'modeling',
    label: 'Modeling & Stats',
    short_desc: 'Training & Testing',
    icon: BarChart3,
    description: 'Model Training, Validation & Statistical Tests',
  },
  {
    id: 'reporting',
    label: 'Paper Writing',
    short_desc: 'Results & Export',
    icon: FileText,
    description: 'Results Compilation & Literature Comparison',
  },
];

/**
 * Maps a backend agent / matched_module string to a sidebar phase ID.
 * Add new entries here whenever the backend introduces a new agent module.
 * Keys are intentionally lower-cased so callers should normalise before lookup.
 */
export const MODULE_TO_PHASE_ID: Record<string, string> = {
  // Literature Review
  literature_review_agent:             'literature',
  literature_review:                   'literature',
  lit_review:                          'literature',

  // Data & Population
  study_population_planner_agent:      'data',
  study_population_planner:            'data',
  population_agent:                    'data',
  data_population:                     'data',
  data:                                'data',

  // Segmentation
  segmentation_agent:                  'segmentation',
  segmentation_qc_agent:               'segmentation',
  segmentation:                        'segmentation',

  // Feature Extraction
  feature_extraction_agent:            'features',
  feature_extraction:                  'features',
  radiomics_agent:                     'features',
  features:                            'features',

  // Modeling
  modeling_agent:                      'modeling',
  model_training_agent:                'modeling',
  statistical_analysis_agent:          'modeling',
  modeling:                            'modeling',

  // Reporting / Paper Writing
  result_reporter_agent:               'reporting',
  latex_reporter_agent:                'reporting',
  report_agent:                        'reporting',
  reporting:                           'reporting',
  paper_writing:                       'reporting',
};

/**
 * Given a backend step (from pipeline_update / plan), returns the sidebar phase
 * ID it maps to, or null if unrecognised.
 */
export function resolveStepToPhaseId(step: {
  id?: string;
  matched_module?: string;
  module?: string;
  name?: string;
}): string | null {
  const candidates = [
    step.matched_module,
    step.module,
    step.id,
  ].filter(Boolean) as string[];

  for (const raw of candidates) {
    const key = raw.trim().toLowerCase();
    if (MODULE_TO_PHASE_ID[key]) return MODULE_TO_PHASE_ID[key];
  }

  return null;
}
