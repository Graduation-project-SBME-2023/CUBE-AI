export interface RegistryAgent {
  id: string;
  title: string;
  role: string;
  icon: 'database' | 'bot' | 'activity' | 'fileText' | 'shield' | 'user';
  capabilities: string[];
}

export const AGENTS_REGISTRY: RegistryAgent[] = [
  {
    id: 'a1',
    title: 'DICOM Fetcher',
    role: 'IO / Network',
    icon: 'database',
    capabilities: ['Query/Retrieve (C-FIND, C-MOVE)', 'Parse DICOM Tags', 'Anonymize on fly'],
  },
  {
    id: 'a2',
    title: 'Liver Segmentation',
    role: 'ML Model',
    icon: 'bot',
    capabilities: ['Organ Volumetry', 'Couinaud Segments', 'Lesion Detection'],
  },
  {
    id: 'a3',
    title: 'Lung Nodule Det',
    role: 'ML Model',
    icon: 'activity',
    capabilities: ['Nodule Classification', 'Diameter Measurement', 'Growth Tracking'],
  },
  {
    id: 'a4',
    title: 'Report Generator',
    role: 'LLM / NLP',
    icon: 'fileText',
    capabilities: ['Summarize Findings', 'Format Structured Report', 'ICD-10 Coding'],
  },
  {
    id: 'a5',
    title: 'De-identifier',
    role: 'Utility',
    icon: 'shield',
    capabilities: ['Remove PHI', 'Shift Dates', 'Mask Pixel Data'],
  },
  {
    id: 'a6',
    title: 'Radiologist Review',
    role: 'Human In Loop',
    icon: 'user',
    capabilities: ['Manual Verification', 'Add Annotations', 'Sign Off'],
  },
];
