"""Hooks of ``mask_resolution_agent`` (see ``toolkit/agents/loader.py``).

Its answer is the segmentation module's own ``TargetCandidateDecisionPlan``,
so the engine checks exactly what ``segmentation/planner.py`` parses. The
checks against the locked targets and the offered candidates
(``validate_candidate_decision_references``) need the planner's inputs and
stay in the planner.
"""

from __future__ import annotations

from ..._runtime import segmentation_contracts

output_contract = segmentation_contracts.TargetCandidateDecisionPlan
