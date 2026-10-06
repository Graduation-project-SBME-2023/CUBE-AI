"""Population pack: the study population, from the inclusion criteria to the
doctor-approved cohort.

- ``inclusion_criteria`` — the criteria and modality, on the population cohort;
- ``unique_descriptions``, ``volume_search`` — finding the candidate volumes;
- ``validate_anatomy`` (rules in ``anatomy``) — the candidates in the
  hypothesis' body region become the cohort's members;
- ``save_population`` — the agent's own selection as the membership;
- ``cohort_labels`` (``clinical_records``, ``label_adjudicator``) — LLM labels
  from the clinical reports;
- ``cohort_approval`` — ``generate_targets``, the Human-in-the-Loop gate.
"""
