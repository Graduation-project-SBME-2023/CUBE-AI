---
name: cohort_building
description: Briefing the population step - criteria, labelling modes, cohort size, and the doctor's approval.
applies_to: [main, composer]
---
# Building the study cohort

The population step (`study_population_planner_agent`) turns the hypothesis
into inclusion and exclusion criteria, finds the matching volumes in the
archive, labels them, and waits for the doctor to approve the cohort. Its
output, the approved `Cohort`, is what every later step measures.

## What its task should say

- The hypothesis and the population of the PICO, with the modality, the body
  region, the age range and the conditions that matter.
- Any criterion the user stated explicitly (scan phase, slice thickness,
  exclusions such as prior surgery or non-diagnostic quality). The agent
  writes its criteria before it looks at the archive, so criteria given
  afterwards cannot be biased by what happens to be available; give them up
  front.
- How the label is defined (for example malignant versus benign, with
  histopathology as the reference), when the user said so.

## Labelling modes

The doctor chooses the mode in the Population view before the step starts:

- **automatic** (the default): an LLM adjudicator reads each patient's
  clinical report and proposes a label with a confidence; the doctor reviews
  them at approval.
- **manual**: the doctor labels every volume; no label is extracted
  automatically.

Either way, only volumes with a label take part in the analysis.

## Cohort size

- Radiomics classification is fragile on small cohorts: the literature
  suggests about 50 patients per class (Lambin et al., 2017, the Radiomics
  Quality Score). Smaller cohorts can still be studied, but tell the user the
  results will be exploratory and the selection may have no held-out rows.
- Under about 30 labelled volumes, modeling restricts itself to simple
  estimators, and feature extraction should prefer fewer feature classes.
- When the archive has no matching volume, the step does not invent one: the
  doctor can select volumes by hand at the approval step. Tell the user.

## The doctor's approval

The node waits, holding no resources, until the doctor approves the cohort in
the Population view. The doctor may add or remove volumes and change labels;
the approved cohort is the only one later steps use. When a study waits on
this approval, say so to the user rather than report the step as stuck.
