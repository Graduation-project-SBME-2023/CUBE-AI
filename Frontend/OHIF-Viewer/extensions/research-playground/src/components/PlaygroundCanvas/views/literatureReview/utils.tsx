import React from 'react';
import {
  Microscope,
  Tag,
  BookOpenText,
  TrendingUp,
  Newspaper,
  ShieldCheck,
  Database,
  FileSearch,
} from 'lucide-react';
import { LiteratureOutput, KBSection } from './types';

const MicroscopeIcon: any = Microscope;
const TagIcon: any = Tag;
const BookOpenTextIcon: any = BookOpenText;
const TrendingUpIcon: any = TrendingUp;
const NewspaperIcon: any = Newspaper;
const ShieldCheckIcon: any = ShieldCheck;
const DatabaseIcon: any = Database;
const FileSearchIcon: any = FileSearch;

export const parseOutput = (input?: LiteratureOutput | string): LiteratureOutput => {
  if (!input) return {};
  if (typeof input === 'string') {
    try {
      return JSON.parse(input);
    } catch {
      return { knowledge_base: input };
    }
  }
  return input;
};

export function parseKnowledgeBase(kb: string): KBSection[] {
  if (!kb) return [];

  // Split on section headers like ─── TITLE ───
  const sectionRegex = /─{3,}\s*(.+?)\s*─{3,}/g;
  const sections: KBSection[] = [];
  const matches: { title: string; index: number }[] = [];

  let match: RegExpExecArray | null;
  while ((match = sectionRegex.exec(kb)) !== null) {
    matches.push({ title: match[1].trim(), index: match.index + match[0].length });
  }

  for (let i = 0; i < matches.length; i++) {
    const start = matches[i].index;
    const end = i + 1 < matches.length
      ? kb.lastIndexOf('─', matches[i + 1].index - 1)
      : kb.length;
    const content = kb.slice(start, end).replace(/^[═─\s]+|[═─\s]+$/g, '').trim();
    if (content) {
      sections.push({ title: matches[i].title, content });
    }
  }

  // If no sections found, treat the whole thing as one section
  if (sections.length === 0 && kb.trim()) {
    sections.push({ title: 'Knowledge Base', content: kb.trim() });
  }

  return sections;
}

/**
 * Convert the knowledge_builder's custom text format (│ prefixed, [i] refs,
 * • bullets) into proper markdown for react-markdown rendering.
 */
export function sectionToMarkdown(raw: string): string {
  let md = raw
    // strip leading │ pipes used as visual borders
    .replace(/^│\s?/gm, '')
    // convert topic headers like "LIVER RADIOMICS — Recent Evidence" to ### headers
    .replace(/^([A-Z][A-Z &,/\-—()]+(?:\s—\s.+)?)$/gm, '\n### $1\n')
    // convert numbered refs like [1] Title  to **1.** **Title**
    .replace(/^\s*\[(\d+)\]\s*(.+)$/gm, '**$1.** **$2**')
    // convert arrows → to blockquote-style finding lines
    .replace(/^\s*→\s*(.+)$/gm, '> $1')
    // convert bullets • to markdown list items
    .replace(/^\s*•\s*/gm, '- ')
    // convert indented info lines (Journal (Year), Fields:, TLDR:)
    .replace(/^\s{4,}((?:Fields|TLDR|PMID):?\s*.+)$/gm, '  *$1*')
    .replace(/^\s{4,}(.+\(\d{4}\).*)$/gm, '  *$1*')
    // clean up excessive blank lines
    .replace(/\n{3,}/g, '\n\n');

  return md.trim();
}

/** Map section title keywords to icons */
export function getSectionIcon(title: string): React.ReactNode {
  const t = title.toLowerCase();
  if (t.includes('anatomy') || t.includes('pathology') || t.includes('clinical'))
    return <MicroscopeIcon size={14} />;
  if (t.includes('mesh') || t.includes('vocabulary') || t.includes('terminology'))
    return <TagIcon size={14} />;
  if (t.includes('state-of-the-art') || t.includes('evidence') || t.includes('review'))
    return <BookOpenTextIcon size={14} />;
  if (t.includes('method') || t.includes('trend'))
    return <TrendingUpIcon size={14} />;
  if (t.includes('journal') || t.includes('publication'))
    return <NewspaperIcon size={14} />;
  if (t.includes('guideline') || t.includes('practice'))
    return <ShieldCheckIcon size={14} />;
  if (t.includes('dataset') || t.includes('benchmark'))
    return <DatabaseIcon size={14} />;
  return <FileSearchIcon size={14} />;
}

/** Map section title to accent color */
export function getSectionColor(title: string): string {
  const t = title.toLowerCase();
  if (t.includes('anatomy') || t.includes('pathology')) return '#e879a5';
  if (t.includes('mesh') || t.includes('vocabulary')) return '#d4a843';
  if (t.includes('state-of-the-art') || t.includes('evidence')) return '#5acce6';
  if (t.includes('method') || t.includes('trend')) return '#a78bfa';
  if (t.includes('journal') || t.includes('publication')) return '#f59e42';
  if (t.includes('guideline')) return '#6fe5b8';
  if (t.includes('dataset') || t.includes('benchmark')) return '#60a5fa';
  return '#5acce6';
}

export const CATEGORY_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  Review: { bg: '#0d2847', text: '#5acce6', border: '#5acce630' },
  Trending: { bg: '#1a0d3d', text: '#a78bfa', border: '#a78bfa30' },
  Guideline: { bg: '#0d3320', text: '#6fe5b8', border: '#6fe5b830' },
  Dataset: { bg: '#1a2a0d', text: '#60a5fa', border: '#60a5fa30' },
};
