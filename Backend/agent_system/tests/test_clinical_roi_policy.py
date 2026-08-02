"""Focused ROI-policy tests with no imaging-runtime dependencies."""

import unittest

from pydantic import ValidationError

from agent_system.agents.clinical_roi_policy import (
    HypothesisValidationResult,
    ROI_IDENTITY_POLICY,
)
from agent_system.agents.execution_agents.segmentation.clinical_target_policy import (
    CLINICAL_TARGET_POLICY,
)
from agent_system.agents.planning_agents.context_compiler import (
    _build_segmentation_context,
    compile_task_messages,
)
from agent_system.models.plans import ClinicalHypothesis, StudyPlan, StudyStep


class ClinicalRoiPolicyTests(unittest.TestCase):
    def test_valid_hypothesis_requires_locked_roi(self):
        with self.assertRaisesRegex(ValidationError, "locked ROI interpretation"):
            HypothesisValidationResult(
                valid=True,
                feedback="",
                extracted={"population": "Adults"},
            )

    def test_ambiguous_hypothesis_requests_clarification(self):
        result = HypothesisValidationResult(
            valid=False,
            feedback=(
                "Specify whether features come from the whole organ, a lesion, "
                "or both."
            ),
        )
        self.assertFalse(result.valid)
        self.assertIsNone(result.roi_interpretation)

    def test_shared_policy_encodes_organ_lesion_and_both_precedence(self):
        self.assertIn(ROI_IDENTITY_POLICY, CLINICAL_TARGET_POLICY)
        self.assertIn(
            "one whole-organ target: `spleen`, pathology `null`",
            ROI_IDENTITY_POLICY,
        )
        self.assertIn(
            "one pathological target: `spleen lesion`",
            ROI_IDENTITY_POLICY,
        )
        self.assertIn(
            "`spleen` and `spleen lesion`",
            ROI_IDENTITY_POLICY,
        )
        self.assertNotIn(
            "Radiomics can classify cancerous spleens",
            CLINICAL_TARGET_POLICY,
        )

    def test_both_supplied_hypotheses_preserve_whole_spleen_wording(self):
        questions = [
            (
                "In adult patients undergoing contrast-enhanced abdominal CT "
                "for suspected splenic pathology, do CT radiomic features "
                "extracted from the spleen improve classification of healthy "
                "versus diseased spleens compared with clinical parameters?"
            ),
            (
                "In adult patients undergoing contrast-enhanced abdominal CT "
                "for cancerous splenic pathology, do CT radiomic features "
                "extracted from the spleen improve classification of healthy "
                "versus diseased spleens compared with clinical parameters?"
            ),
        ]
        for question in questions:
            with self.subTest(question=question):
                hypothesis = ClinicalHypothesis(
                    title="Whole-spleen radiomics",
                    population="Adult CT patients",
                    intervention="CT radiomic features extracted from the spleen",
                    comparison="Standard clinical parameters",
                    outcome="Healthy versus diseased spleen classification",
                    hypothesis=question,
                    question=question,
                )
                _context, instructions = _build_segmentation_context(
                    {},
                    "",
                    hypothesis,
                )
                self.assertIn(
                    "CT radiomic features extracted from the spleen",
                    instructions,
                )
                self.assertIn(ROI_IDENTITY_POLICY, instructions)
                self.assertNotIn(
                    "the segmentation target is the PATHOLOGY",
                    instructions,
                )

    def test_locked_whole_spleen_roi_precedes_generated_instructions(self):
        hypothesis = ClinicalHypothesis(
            title="Whole-spleen radiomics",
            intervention="Features extracted from the spleen",
            outcome="Healthy versus diseased classification",
        )
        plan = StudyPlan(
            steps=[
                StudyStep(
                    title="Segment ROI",
                    description="Prepare the radiomics ROI.",
                    module="segmentation_agent",
                )
            ]
        )
        task = compile_task_messages(
            hypothesis,
            plan,
            {},
            "",
            pipeline_instructions={
                "segmentation_agent": "Incorrectly add a spleen lesion."
            },
            locked_roi_interpretation={
                "scope": "whole_organ",
                "labels": ["spleen"],
                "rationale": "Features are explicitly extracted from the spleen.",
            },
        )[0]
        self.assertLess(
            task.instructions.index("## Locked ROI Interpretation"),
            task.instructions.index("## Study-Specific Instructions"),
        )
        self.assertIn('"scope": "whole_organ"', task.instructions)
        self.assertIn('"spleen"', task.instructions)


if __name__ == "__main__":
    unittest.main()
