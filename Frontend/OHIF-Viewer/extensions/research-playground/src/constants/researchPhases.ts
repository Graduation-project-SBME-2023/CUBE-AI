import { BarChart3, BookOpenText, Database, FileText, Layout, Scan, Users } from 'lucide-react';

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
    icon: Scan,
    description: 'Radiologists Approval of Segmentations',
  },
  {
    id: 'features',
    label: 'Feature Extraction',
    short_desc: 'ROI & Radiomics',
    icon: Scan,
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
