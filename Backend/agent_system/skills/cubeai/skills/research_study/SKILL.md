---
name: research_study
description: Turning a research idea into a PICO hypothesis, and the usual shape of a radiomics study graph.
---
# Designing a radiomics research study

Guidance, not a script: adapt it to what the user actually asks.

## 1. Get a complete PICO hypothesis first

A study is driven by a structured hypothesis:

- **population**: who, with age range, condition, imaging modality and setting;
- **intervention**: the method, technically precise (feature types, region of
  interest, classifier family);
- **comparison**: a gold standard or conventional alternative (radiologist
  reading, clinical biomarkers, histopathology);
- **outcome**: quantitative metrics (AUC-ROC, sensitivity, specificity, PPV,
  NPV) and what counts as success;
- **hypothesis**: one falsifiable statement of the core claim;
- **question**: the research question in plain language;
- **clinical_context** and **relationship_type** (`diagnostic_accuracy`,
  `predictive`, `prognostic`, `comparative`).

When the idea is vague, ask about what is missing rather than reject it: the
region of interest, the modality, the clinical question (detection,
classification, prognosis, treatment response), the population. Refine only
what the user corrects. When the user asks you to assume, choose reasonable
clinical defaults and say which ones you chose.

The region of interest is the question most often left open. When the user
names a disease and an organ but not whether features come from the whole
organ, the lesion, or both, ask; do not guess (see the `segmentation_choices`
skill for the rules).

## 2. The usual study graph

Each step is a node; each node's inputs come from the outputs of earlier
nodes, by type, so a graph that is missing a producer is refused by the
Validator rather than run.

| Node | Takes | Gives |
|---|---|---|
| `literature_review_agent` (optional) | the PICO | `LiteratureContext` |
| `study_population_planner_agent` | the hypothesis | `Cohort` (frozen) |
| `segmentation_agent` | `Cohort` | `MaskSet` (reviewed, or accepted automatically) |
| `feature_extraction_agent` | `Cohort`, `MaskSet` | `FeatureTable[]`, `FeatureImportance` |
| `run_modeling` | `FeatureTable[]` | `ModelingOutput` |
| `result_reporter_agent` | `ModelingOutput` | `ResultSummary` |
| `latex_reporter_agent` | `ResultSummary`, `LiteratureContext` (optional) | `ReportDocument` |

Common variations, decided from the study, never from a fixed rule:

- the literature review first, when the report should cite prior work or the
  study design is uncertain;
- no segmentation when reviewed masks already exist for the cohort;
- only modeling and reporting when frozen feature sets already exist;
- only the reporters when the user wants a report on recorded results.

A step waits for a person only when the user asked for it: the doctor
approves the cohort, the radiologist reviews the masks (say so in that node's
task), or the user releases a node submitted with `approval: before` or
`approval: after`. Tell the user when their action is what the study waits
for.

## 3. What to put in each node's task

Each node gets the study-specific instructions it needs, not a generic
template: what to do for this hypothesis, which methods the literature
supports and why, parameters that matter, pitfalls to watch, and what success
looks like. Restate the locked region of interest wherever segmentation or
feature extraction is concerned.
