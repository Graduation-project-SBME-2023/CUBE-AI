"""Hooks of ``clinical_target_intent_agent`` (see ``toolkit/agents/loader.py``).

Its answer is the segmentation module's own ``ClinicalTargetIntentPlan``, so
the engine checks exactly what ``segmentation/planner.py`` parses. The
semantic checks that need the instruction itself stay in the planner.
"""

from __future__ import annotations

from ..._runtime import segmentation_contracts

output_contract = segmentation_contracts.ClinicalTargetIntentPlan
