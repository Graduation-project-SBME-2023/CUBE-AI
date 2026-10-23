---
name: cohort_building
description: Building the study cohort - criteria, labelling modes, cohort size, and when the doctor approves it.
---
# Building the study cohort

The population step (`study_population_planner_agent`) turns the hypothesis
into inclusion and exclusion criteria, finds the matching volumes in the
archive, labels them, and freezes the cohort, after the doctor's approval
when the user asked for one. Its output, the frozen `Cohort`, is what every
later step measures.

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
  clinical report and proposes a label with a confidence; the doctor can
  review them when the user asked to approve the cohort.
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
  doctor can select volumes by hand when the user asked to approve the
  cohort. Tell the user.

## The doctor's approval (only when the user asks for it)

The doctor approves the cohort only when the user asked for it: the node
instructions then say so, and the agent asks with the cohort form
(`ask_user`). The node waits, holding no resources, until the doctor
approves the cohort in the Population view; the doctor may add or remove
volumes and change labels. Otherwise the cohort is frozen automatically.
Either way, the frozen cohort is the only one later steps use. When a study
waits on this approval, say so to the user rather than report the step as
stuck.
