---
name: reporting
description: Judging the hypothesis from the results and producing the manuscript - decisions, caveats, templates and citations.
---
# Reporting a study

Two steps report: `result_reporter_agent` judges the hypothesis from the
recorded modeling results (`ResultSummary`), then `latex_reporter_agent`
writes and compiles the manuscript (`ReportDocument`, with its PDF).

## The judgement 

The result reporter decides one of:

- **accepted**: the primary metric clearly supports the hypothesis on a
  held-out evaluation;
- **rejected**: it clearly does not;
- **inconclusive**: the evidence cannot decide, for example a very small
  cohort, metrics that are not a generalisation estimate, or an earlier step
  that failed. This is an honest outcome, not a failure.

Give its task the primary metric the study decided on (for example AUC-ROC)
and the threshold the PICO outcome set, when there is one.

## The manuscript

The LaTeX reporter chooses its template from what was recorded:

- an earlier step failed, the data is incomplete, or the cohort is under 15:
  `one_page_abstract`;
- a cohort of 30 or more, every step succeeded, the judgement is `accepted`
  and the best AUC is above 0.7: `ieee_research_paper`;
- otherwise: `short_study`.

It cites only the papers of the recorded literature review; without a review
the manuscript has no related-work section and no citations. Run the
literature review before it when the user wants a cited manuscript.

The manuscript is done only when its PDF compiled. A LaTeX error is fixed by
the reporter itself; a compilation service that is down is reported, not
worked around.

## Talking about results

When the user asks about results, read them (`get_modeling_results`, the
nodes' outputs) rather than recall them, quote the metric names with their
values, and repeat the caveats: optimistic metrics when the selection saw
every label, small cohorts, failed volumes.
