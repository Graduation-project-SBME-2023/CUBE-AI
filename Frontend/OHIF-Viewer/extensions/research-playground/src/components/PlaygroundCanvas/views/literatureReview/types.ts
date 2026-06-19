export type HypothesisDetails = {
  title?: string;
  question?: string;
  hypothesis?: string;
  population?: string;
  intervention?: string;
  comparison?: string;
  outcome?: string;
};

export type Paper = {
  rank?: number;
  title?: string;
  authors?: string;
  year?: string | number;
  journal?: string;
  abstract?: string;
  source?: string;
  url?: string;
  category?: string;
  topic?: string;
  citation_count?: number;
  fields_of_study?: string[];
};

export type LiteratureOutput = {
  knowledge_base?: string;
  sources_guide?: string;
  hypothesis?: HypothesisDetails;
  papers?: Paper[];
  report?: string;
};

export interface LiteratureReviewViewProps {
  literatureOutput?: LiteratureOutput | string;
}

export interface KBSection {
  title: string;
  content: string;
}
