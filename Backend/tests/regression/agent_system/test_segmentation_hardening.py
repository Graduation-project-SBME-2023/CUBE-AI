import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from storage_paths import workspace_subpath

import httpx
import numpy as np
import SimpleITK as sitk
from pydicom.dataset import Dataset
from pydantic import ValidationError

from agent_system.agents.clinical_roi_policy import (
    HypothesisValidationResult,
    ROI_IDENTITY_POLICY,
)
from agent_system.agents.execution_agents.segmentation.contracts import (
    CachePromptCandidate,
    ClinicalTargetIntent,
    ClinicalTargetIntentPlan,
    RESOLVED_REASON_MAX_LENGTH,
    ResolvedTarget,
    TargetCandidateDecision,
    TargetCandidateDecisionPlan,
    TargetResolutionPlan,
    VoxTellPromptDecision,
    validate_candidate_references,
)
from agent_system.agents.execution_agents.segmentation.planner import (
    INTENT_SYSTEM_MESSAGE,
    _compose_resolution_reason,
    _compose_resolution_plan,
    run_planner,
)
from agent_system.agents.execution_agents.segmentation.module import SegmentationModule
from agent_system.agents.execution_agents.segmentation.pipelines.common import (
    SegmentationPipelineError,
    raise_for_segmentation_error,
)
from agent_system.agents.execution_agents.segmentation.services import (
    SegmentationService,
)
from agent_system.agents.planning_agents.context_compiler import (
    _build_segmentation_context,
    compile_task_messages,
)
from agent_system.models.plans import ClinicalHypothesis, StudyPlan, StudyStep
from agent_system.agents.execution_agents.segmentation.utils import (
    _validated_nrrd_array,
    align_and_binarize_mask,
    download_and_pair_volumes,
    parse_multipart_response,
)
from agent_system.services.resume_recovery import reset_interrupted_agent
from agent_system.services.pipeline_monitor import (
    PipelineMonitor,
    raise_for_fatal_tool_failure,
)
from agent_system.models.execution import AgentStatus


class ClinicalRoiPolicyTests(unittest.TestCase):
    def test_valid_hypothesis_requires_locked_roi_interpretation(self):
        with self.assertRaisesRegex(ValidationError, "locked ROI interpretation"):
            HypothesisValidationResult(
                valid=True,
                feedback="",
                extracted={"population": "Adults"},
            )

    def test_ambiguous_hypothesis_can_request_roi_clarification(self):
        result = HypothesisValidationResult(
            valid=False,
            feedback=(
                "Specify whether features come from the whole organ, a lesion, "
                "or both."
            ),
            extracted=None,
            roi_interpretation=None,
        )
        self.assertFalse(result.valid)
        self.assertIsNone(result.roi_interpretation)

    def test_whole_organ_lesion_and_both_scopes_are_representable(self):
        cases = [
            ("whole_organ", ["spleen"]),
            ("whole_lesion", ["spleen lesion"]),
            ("both", ["spleen", "spleen lesion"]),
        ]
        for scope, labels in cases:
            with self.subTest(scope=scope):
                result = HypothesisValidationResult(
                    valid=True,
                    feedback="",
                    extracted={
                        "population": "Adults",
                        "intervention": "CT radiomics",
                        "comparison": "Clinical parameters",
                        "outcome": "Classification",
                    },
                    roi_interpretation={
                        "scope": scope,
                        "labels": labels,
                        "rationale": "Explicit feature-extraction wording.",
                    },
                )
                self.assertEqual(result.roi_interpretation.labels, labels)

    def test_both_scope_requires_multiple_distinct_targets(self):
        with self.assertRaises(ValidationError):
            HypothesisValidationResult(
                valid=True,
                feedback="",
                extracted={"population": "Adults"},
                roi_interpretation={
                    "scope": "both",
                    "labels": ["spleen"],
                    "rationale": "Both were requested.",
                },
            )

    def test_shared_policy_is_authoritative_in_intent_prompt(self):
        self.assertIn(ROI_IDENTITY_POLICY, INTENT_SYSTEM_MESSAGE)

    def test_supplied_whole_spleen_hypotheses_keep_explicit_roi_context(self):
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

    def test_locked_roi_is_embedded_ahead_of_generated_pipeline_instructions(self):
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
                "segmentation_agent": (
                    'Run VoxTell with the prompt "segment spleen" and do not '
                    "add a spleen lesion."
                )
            },
            locked_roi_interpretation={
                "scope": "whole_organ",
                "labels": ["spleen"],
                "rationale": "Features are explicitly extracted from the spleen.",
            },
        )[0]
        locked_index = task.instructions.index("## Locked ROI Interpretation")
        generated_index = task.instructions.index("## Study-Specific Instructions")
        self.assertLess(locked_index, generated_index)
        self.assertIn('"scope": "whole_organ"', task.instructions)
        self.assertIn('"spleen"', task.instructions)
        self.assertIn('prompt "spleen"', task.instructions)
        self.assertNotIn('prompt "segment spleen"', task.instructions)


class SegmentationContractTests(unittest.TestCase):
    def test_voxtell_target_uses_compact_clinical_identity(self):
        intent = ClinicalTargetIntent(
            target_id="brain_tumor",
            canonical_label="brain tumor",
            anatomy="brain",
            pathology="tumor",
            scope="whole_lesion",
            evidence_span="brain tumor",
            intent_confidence=1.0,
            rationale="The enhancing tumor is the feature-extraction ROI.",
        )

        self.assertEqual(intent.canonical_label, "brain tumor")
        self.assertEqual(intent.pathology, "tumor")

        candidate = TargetCandidateDecisionPlan(
            chosen_path="interactive",
            decisions=[
                TargetCandidateDecision(
                    target_id="brain_tumor",
                    voxtell_prompt=VoxTellPromptDecision(
                        mode="generate",
                        prompt="brain tumor",
                    ),
                    match_confidence=1.0,
                    rationale="Generate the compact canonical target.",
                )
            ],
        )
        plan = _compose_resolution_plan(
            ClinicalTargetIntentPlan(targets=[intent]),
            candidate,
            "interactive",
            ["voxtell"],
        )

        self.assertEqual(plan.targets[0].voxtell_prompt.prompt, "brain tumor")

    def test_intent_prompt_defines_compact_voxtell_target(self):
        self.assertIn(
            '"canonical_label": "brain tumor"',
            INTENT_SYSTEM_MESSAGE,
        )
        self.assertIn('"anatomy": "brain"', INTENT_SYSTEM_MESSAGE)
        self.assertIn('"pathology": "tumor"', INTENT_SYSTEM_MESSAGE)

    def test_descriptive_voxtell_cache_prompt_cannot_replace_compact_target(self):
        plan = TargetResolutionPlan(
            chosen_path="interactive",
            selected_models=["voxtell"],
            targets=[
                ResolvedTarget(
                    target_id="brain_tumor",
                    canonical_label="brain tumor",
                    inference_prompt="brain tumor",
                    anatomy="brain",
                    pathology="tumor",
                    scope="whole_lesion",
                    voxtell_prompt=VoxTellPromptDecision(
                        mode="cache",
                        cache_candidate_id="cache_descriptive",
                    ),
                    intent_confidence=1.0,
                    match_confidence=1.0,
                    reason="The cache phrase is descriptive.",
                )
            ],
        )

        with self.assertRaisesRegex(ValueError, "canonical label"):
            validate_candidate_references(
                plan,
                mask_candidates=[],
                cache_candidates=[
                    CachePromptCandidate(
                        candidate_id="cache_descriptive",
                        prompt="enhancing tumor of brain",
                    )
                ],
            )

    @staticmethod
    def _resolution_inputs(intent_rationale: str, candidate_rationale: str):
        intent_plan = ClinicalTargetIntentPlan(
            targets=[
                ClinicalTargetIntent(
                    target_id="renal_tumor",
                    canonical_label="renal tumor",
                    anatomy="kidney",
                    pathology="tumor",
                    scope="whole_lesion",
                    evidence_span="renal tumor",
                    intent_confidence=1.0,
                    rationale=intent_rationale,
                )
            ]
        )
        candidate_plan = TargetCandidateDecisionPlan(
            chosen_path="interactive",
            decisions=[
                TargetCandidateDecision(
                    target_id="renal_tumor",
                    voxtell_prompt=VoxTellPromptDecision(
                        mode="generate",
                        prompt="renal tumor",
                    ),
                    match_confidence=0.99,
                    rationale=candidate_rationale,
                )
            ],
        )
        return intent_plan, candidate_plan

    def test_resolution_reason_preserves_short_rationales(self):
        intent_plan, candidate_plan = self._resolution_inputs(
            "Renal pathology is the requested ROI.",
            "The generated prompt is clinically equivalent.",
        )

        plan = _compose_resolution_plan(
            intent_plan,
            candidate_plan,
            "interactive",
            ["voxtell"],
        )

        self.assertEqual(
            plan.targets[0].reason,
            "Clinical intent: Renal pathology is the requested ROI. "
            "Candidate resolution: The generated prompt is clinically equivalent.",
        )

    def test_resolution_reason_preserves_two_maximum_length_rationales(self):
        intent_plan, candidate_plan = self._resolution_inputs(
            "I" * 1000,
            "C" * 1000,
        )

        plan = _compose_resolution_plan(
            intent_plan,
            candidate_plan,
            "interactive",
            ["voxtell"],
        )
        reason = plan.targets[0].reason
        expected = (
            f"Clinical intent: {'I' * 1000} "
            f"Candidate resolution: {'C' * 1000}"
        )

        self.assertLessEqual(len(reason), RESOLVED_REASON_MAX_LENGTH)
        self.assertEqual(reason, expected)

    def test_resolution_reason_bounds_unexpected_future_overflow(self):
        reason = _compose_resolution_reason("I" * 2048, "C" * 2048)

        self.assertLessEqual(len(reason), RESOLVED_REASON_MAX_LENGTH)
        self.assertTrue(reason.startswith("Clinical intent: "))
        self.assertIn(" Candidate resolution: ", reason)
        self.assertEqual(reason.count("..."), 2)

    def test_reuse_requires_confidence_of_at_least_point_six(self):
        prompt = VoxTellPromptDecision(mode="generate", prompt="liver")
        with self.assertRaises(ValidationError):
            TargetCandidateDecision(
                target_id="liver",
                accepted_mask_candidate_ids=["mask_1"],
                voxtell_prompt=prompt,
                selected_model="voxtell",
                match_confidence=0.59,
                rationale="Candidate looks similar.",
            )

        decision = TargetCandidateDecision(
            target_id="liver",
            accepted_mask_candidate_ids=["mask_1"],
            voxtell_prompt=prompt,
            selected_model="voxtell",
            match_confidence=0.6,
            rationale="Candidate is equivalent.",
        )
        self.assertEqual(decision.accepted_mask_candidate_ids, ["mask_1"])

    def test_reserved_pair_delimiter_is_rejected(self):
        with self.assertRaises(ValidationError):
            ClinicalTargetIntentPlan(
                targets=[
                    ClinicalTargetIntent(
                        target_id="left__lung",
                        canonical_label="left lung",
                        anatomy="lung",
                        laterality="left",
                        confidence=1.0,
                        rationale="Explicitly requested.",
                    )
                ]
            )

    def test_error_contract_preserves_safe_diagnostics(self):
        with self.assertRaises(SegmentationPipelineError) as context:
            raise_for_segmentation_error(
                {
                    "error": True,
                    "stage": "inference_response",
                    "message": "MONAI returned HTTP 503",
                    "model": "voxtell",
                    "prompt": "liver",
                    "retry_count": 3,
                    "status_code": 503,
                },
                "volume",
                "liver",
            )
        self.assertEqual(context.exception.as_dict()["retry_count"], 3)
        self.assertEqual(context.exception.as_dict()["status_code"], 503)


class SegmentationPlannerTests(unittest.IsolatedAsyncioTestCase):
    async def test_planner_uses_only_intent_and_candidate_agents(self):
        intent_agent = SimpleNamespace(
            run=AsyncMock(
                return_value=SimpleNamespace(
                    messages=[
                        SimpleNamespace(
                            content=json.dumps(
                                {
                                    "targets": [
                                        {
                                            "target_id": "spleen",
                                            "canonical_label": "spleen",
                                            "anatomy": "spleen",
                                            "pathology": None,
                                            "scope": "whole_organ",
                                            "laterality": "unspecified",
                                            "evidence_span": "whole spleen",
                                            "intent_confidence": 1.0,
                                            "rationale": "Whole-spleen ROI requested.",
                                        }
                                    ]
                                }
                            )
                        )
                    ]
                )
            )
        )
        candidate_agent = SimpleNamespace(
            run=AsyncMock(
                return_value=SimpleNamespace(
                    messages=[
                        SimpleNamespace(
                            content=json.dumps(
                                {
                                    "decisions": [
                                        {
                                            "target_id": "spleen",
                                            "accepted_mask_candidate_ids": [],
                                            "voxtell_prompt": {
                                                "mode": "generate",
                                                "cache_candidate_id": None,
                                                "prompt": "spleen",
                                            },
                                            "match_confidence": 1.0,
                                            "rationale": "Generate canonical prompt.",
                                        }
                                    ],
                                }
                            )
                        )
                    ]
                )
            )
        )

        with patch(
            "agent_system.agents.execution_agents.segmentation.planner."
            "get_enabled_segmentation_model_paths",
            return_value={"voxtell": "interactive"},
        ):
            outcome = await run_planner(
                intent_agent,
                candidate_agent,
                "Extract features from the whole spleen.",
            )

        self.assertEqual(outcome.plan.target_labels, ["spleen"])
        self.assertFalse(hasattr(outcome, "audit"))
        intent_agent.run.assert_awaited_once()
        candidate_agent.run.assert_awaited_once()


class SegmentationUtilityTests(unittest.TestCase):
    def test_short_explicit_instruction_is_preserved(self):
        instruction, roi_interpretation = SegmentationModule._build_instruction(
            {
                "plan": {
                    "steps": [
                        {
                            "module": "segmentation_agent",
                            "task_message": {
                                "instructions": "Left kidney lesion"
                            },
                        }
                    ]
                },
                "hypothesis": {"title": "Renal study"},
            }
        )
        self.assertEqual(instruction, "Left kidney lesion")
        self.assertIsNone(roi_interpretation)

    def test_persisted_voxtell_command_prompt_is_canonicalized(self):
        instruction, roi_interpretation = SegmentationModule._build_instruction(
            {
                "plan": {
                    "steps": [
                        {
                            "module": "segmentation_agent",
                            "task_message": {
                                "instructions": (
                                    'Run VoxTell with the prompt "segment spleen".'
                                )
                            },
                        }
                    ]
                },
                "hypothesis": {"title": "Spleen study"},
            }
        )
        self.assertEqual(
            instruction,
            'Run VoxTell with the prompt "spleen".',
        )
        self.assertIsNone(roi_interpretation)

    def test_quoted_multipart_boundary_is_supported(self):
        body = (
            b"--cube-boundary\r\n"
            b"Content-Disposition: form-data; name=\"result\"; filename=\"mask.nrrd\"\r\n"
            b"Content-Type: application/octet-stream\r\n\r\n"
            b"NRRD-DATA\r\n"
            b"--cube-boundary\r\n"
            b"Content-Disposition: form-data; name=\"params\"\r\n"
            b"Content-Type: application/json\r\n\r\n"
            b"{\"centroids\": {}}\r\n"
            b"--cube-boundary--\r\n"
        )
        nrrd_data, centroids = parse_multipart_response(
            {"content-type": 'multipart/form-data; boundary="cube-boundary"'},
            body,
        )
        self.assertEqual(nrrd_data, b"NRRD-DATA")
        self.assertEqual(centroids, {})

    def test_non_binary_or_floating_nrrd_masks_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "integer type"):
            _validated_nrrd_array(np.array([0.0, 1.0], dtype=np.float32))
        with self.assertRaisesRegex(ValueError, "must be binary"):
            _validated_nrrd_array(np.array([0, 1, 2], dtype=np.uint16))

    def test_mask_is_resampled_to_reference_geometry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            reference_path = temp / "volume.nii.gz"
            mask_path = temp / "mask.nii.gz"
            output_path = temp / "aligned.nii.gz"

            reference = sitk.Image([4, 4, 4], sitk.sitkInt16)
            reference.SetSpacing((1.0, 1.0, 1.0))
            sitk.WriteImage(reference, str(reference_path))

            mask_array = np.zeros((2, 2, 2), dtype=np.uint8)
            mask_array[0, 0, 0] = 255
            mask = sitk.GetImageFromArray(mask_array)
            mask.SetSpacing((2.0, 2.0, 2.0))
            sitk.WriteImage(mask, str(mask_path))

            result = align_and_binarize_mask(
                str(mask_path),
                str(reference_path),
                str(output_path),
            )
            aligned = sitk.ReadImage(str(output_path))
            values = set(np.unique(sitk.GetArrayFromImage(aligned)).tolist())

            self.assertTrue(result["geometry_adjusted"])
            self.assertEqual(aligned.GetSize(), reference.GetSize())
            self.assertEqual(aligned.GetSpacing(), reference.GetSpacing())
            self.assertLessEqual(values, {0, 1})

    def test_same_size_monai_mask_keeps_voxel_order_when_header_z_is_reversed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            reference_path = temp / "volume.nii.gz"
            mask_path = temp / "mask.nii.gz"
            output_path = temp / "aligned.nii.gz"

            reference = sitk.Image([4, 3, 5], sitk.sitkInt16)
            reference.SetOrigin((0.0, 0.0, 0.0))
            reference.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0))
            sitk.WriteImage(reference, str(reference_path))

            mask_array = np.zeros((5, 3, 4), dtype=np.uint8)
            mask_array[4, 1, 2] = 255
            mask = sitk.GetImageFromArray(mask_array)
            mask.SetOrigin((0.0, 0.0, 4.0))
            mask.SetDirection((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0))
            sitk.WriteImage(mask, str(mask_path))

            result = align_and_binarize_mask(
                str(mask_path),
                str(reference_path),
                str(output_path),
                preserve_index_order_if_same_size=True,
            )
            aligned = sitk.ReadImage(str(output_path))
            aligned_array = sitk.GetArrayFromImage(aligned)

            self.assertTrue(result["geometry_adjusted"])
            self.assertEqual(result["alignment_mode"], "reference_geometry_only")
            self.assertTrue(np.array_equal(aligned_array, mask_array > 0))
            self.assertEqual(aligned.GetOrigin(), reference.GetOrigin())
            self.assertEqual(aligned.GetDirection(), reference.GetDirection())

    def test_fresh_same_size_mask_still_uses_physical_space_alignment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            reference_path = temp / "volume.nii.gz"
            mask_path = temp / "mask.nii.gz"
            output_path = temp / "aligned.nii.gz"

            reference = sitk.Image([4, 3, 5], sitk.sitkInt16)
            reference.SetOrigin((0.0, 0.0, 0.0))
            reference.SetDirection(
                (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
            )
            sitk.WriteImage(reference, str(reference_path))

            mask_array = np.zeros((5, 3, 4), dtype=np.uint8)
            mask_array[4, 1, 2] = 1
            mask = sitk.GetImageFromArray(mask_array)
            mask.SetOrigin((0.0, 0.0, 4.0))
            mask.SetDirection(
                (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, -1.0)
            )
            sitk.WriteImage(mask, str(mask_path))

            result = align_and_binarize_mask(
                str(mask_path),
                str(reference_path),
                str(output_path),
            )
            aligned_array = sitk.GetArrayFromImage(sitk.ReadImage(str(output_path)))

            self.assertEqual(result["alignment_mode"], "physical_resample")
            self.assertEqual(int(aligned_array[0, 1, 2]), 1)
            self.assertEqual(int(aligned_array[4, 1, 2]), 0)


class SegmentationReuseAlignmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_reused_masks_enable_index_order_compatibility(self):
        for reused in (True, False):
            with self.subTest(reused=reused):
                module = SegmentationModule(session_id="session")
                module._persist_processing_state = MagicMock()
                module._workspace_volume_is_valid = MagicMock(return_value=True)
                item = {
                    "pair_id": "volume__spleen",
                    "artifact_key": "mask-key",
                    "volume_uid": "volume",
                    "target_id": "spleen",
                    "model": "voxtell",
                    "source_segmentation_uid": "segmentation",
                    "reused": reused,
                    "is_empty_mask": False,
                    "inference_result": None,
                }
                download = AsyncMock(
                    return_value=(
                        [
                            {
                                "pair_id": item["pair_id"],
                                "geometry_adjusted": False,
                                "geometry": None,
                            }
                        ],
                        [],
                    )
                )
                publisher = SimpleNamespace(publish_ready=AsyncMock())

                with (
                    patch(
                        "agent_system.agents.execution_agents.segmentation.module."
                        "download_and_pair_volumes",
                        new=download,
                    ),
                    patch(
                        "agent_system.agents.execution_agents.segmentation.module."
                        "generate_gif_overlays",
                        return_value=[
                            {
                                "gifPath": "overlays/mask-key.gif",
                                "isEmptyMask": False,
                            }
                        ],
                    ),
                ):
                    prepared = await module._prepare_pair_preview(
                        item,
                        workspace_dir="workspace",
                        resolution_outcome=SimpleNamespace(),
                        work_items=[item],
                        run_id="run",
                        publisher=publisher,
                    )

                requested_pair = download.await_args.args[1][0]
                self.assertTrue(prepared)
                self.assertEqual(
                    requested_pair["preserve_index_order"],
                    reused,
                )


class DicomSegGeometryAlignmentTests(unittest.TestCase):
    @staticmethod
    def _source_images(*, columns=4, rows=3, slices=2):
        images = []
        for z_index in range(slices):
            dataset = Dataset()
            dataset.Columns = columns
            dataset.Rows = rows
            dataset.PixelSpacing = [1.0, 1.0]
            dataset.ImageOrientationPatient = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0]
            dataset.ImagePositionPatient = [0.0, 0.0, float(z_index)]
            dataset.InstanceNumber = z_index + 1
            images.append(dataset)
        return images

    @staticmethod
    def _legacy_voxtell_header(*, columns=4, rows=3, slices=2):
        return {
            "dimension": 4,
            "sizes": np.asarray([1, columns, rows, slices]),
            "space directions": np.eye(4),
            "space origin": np.zeros(4),
        }

    def test_voxtell_legacy_4d_header_uses_validated_source_index_grid(self):
        mask = np.zeros((2, 3, 4), dtype=np.uint8)
        mask[1, 1, 2] = 1

        aligned, flips, details = (
            SegmentationService._align_labelmap_axes_to_source_slices(
                mask,
                self._legacy_voxtell_header(),
                self._source_images(),
                allow_source_index_fallback=True,
            )
        )

        self.assertTrue(np.array_equal(aligned, mask))
        self.assertEqual(flips, {"x": False, "y": False, "z": False})
        self.assertEqual(details["mode"], "source_index_grid_fallback")
        self.assertTrue(details["geometry_adjusted"])
        self.assertTrue(details["index_order_preserved"])
        self.assertEqual(details["foreground_retention_ratio"], 1.0)

    def test_invalid_nrrd_geometry_stays_rejected_without_voxtell_contract(self):
        with self.assertRaisesRegex(ValueError, "NRRD physical geometry"):
            SegmentationService._align_labelmap_axes_to_source_slices(
                np.zeros((2, 3, 4), dtype=np.uint8),
                self._legacy_voxtell_header(),
                self._source_images(),
            )

    def test_voxtell_fallback_rejects_a_shape_mismatch(self):
        with self.assertRaisesRegex(ValueError, "NRRD physical geometry"):
            SegmentationService._align_labelmap_axes_to_source_slices(
                np.zeros((3, 3, 4), dtype=np.uint8),
                self._legacy_voxtell_header(),
                self._source_images(),
                allow_source_index_fallback=True,
            )


class PipelineFailureTests(unittest.IsolatedAsyncioTestCase):
    class _SessionService:
        def __init__(self):
            self.broadcasts = []

        async def handle_pipeline_update(self, _session, agents):
            self.broadcasts.append(agents)

        async def update_tool_views(self, *_args, **_kwargs):
            return None

    @staticmethod
    def _monitor():
        service = PipelineFailureTests._SessionService()
        session = SimpleNamespace(session_id="test-session")
        return PipelineMonitor(service, session), service

    async def test_plain_execution_error_marks_segmentation_failed(self):
        monitor, _service = self._monitor()
        await monitor.on_agent_active("segmentation_agent")

        failed = await monitor.on_tool_result(
            "segmentation_agent",
            "segmentation_tool",
            "ResolvedTarget validation failed",
            "/tmp/workspace",
            execution_error=True,
        )

        self.assertTrue(failed)
        self.assertEqual(
            monitor.agents["segmentation_agent"].status,
            AgentStatus.FAILED,
        )

    async def test_resource_wait_status_is_visible_and_cleared_on_acquire(self):
        monitor, service = self._monitor()

        await monitor.on_resource_status(
            {
                "agent": "segmentation_agent",
                "state": "WAITING_FOR_RESOURCE",
                "resources": ["gpu_segmentation", "cpu_heavy"],
                "queue_position": 2,
            }
        )

        agent = monitor.agents["segmentation_agent"]
        self.assertEqual(agent.status, AgentStatus.WAITING_FOR_RESOURCE)
        self.assertEqual(agent.resources, ["gpu_segmentation", "cpu_heavy"])
        self.assertEqual(agent.queue_position, 2)
        self.assertTrue(service.broadcasts)

        await monitor.on_resource_status(
            {
                "agent": "segmentation_agent",
                "state": "ACQUIRED",
                "resources": ["gpu_segmentation", "cpu_heavy"],
            }
        )

        self.assertEqual(agent.status, AgentStatus.RUNNING)
        self.assertIsNone(agent.resources)
        self.assertIsNone(agent.queue_position)

    async def test_pipeline_error_clears_resource_wait_metadata(self):
        monitor, _service = self._monitor()
        await monitor.on_resource_status(
            {
                "agent": "feature_extraction_agent",
                "state": "WAITING_FOR_RESOURCE",
                "resources": ["cpu_heavy"],
                "queue_position": 1,
            }
        )

        await monitor.on_pipeline_error(RuntimeError("pipeline stopped"))

        agent = monitor.agents["feature_extraction_agent"]
        self.assertEqual(agent.status, AgentStatus.FAILED)
        self.assertIsNone(agent.resources)
        self.assertIsNone(agent.queue_position)

    async def test_structured_failure_is_fatal_but_partial_is_not(self):
        failed_monitor, _service = self._monitor()
        await failed_monitor.on_agent_active("segmentation_agent")
        failed = await failed_monitor.on_tool_result(
            "segmentation_agent",
            "segmentation_tool",
            json.dumps({"status": "failed"}),
            "/tmp/workspace",
        )

        partial_monitor, _service = self._monitor()
        await partial_monitor.on_agent_active("segmentation_agent")
        partial = await partial_monitor.on_tool_result(
            "segmentation_agent",
            "segmentation_tool",
            json.dumps({"status": "partial"}),
            "/tmp/workspace",
        )

        self.assertTrue(failed)
        self.assertFalse(partial)
        self.assertEqual(
            partial_monitor.agents["segmentation_agent"].status,
            AgentStatus.RUNNING,
        )

    def test_failed_segmentation_stops_before_feature_event(self):
        events = [
            (
                "segmentation_agent",
                "segmentation_tool",
                True,
                "planner validation failed",
            ),
            (
                "feature_extraction_agent",
                "radiomics_extract_features",
                False,
                "{}",
            ),
        ]
        processed = []

        with self.assertRaisesRegex(RuntimeError, "segmentation_tool failed"):
            for source, tool_name, failed, content in events:
                processed.append(source)
                raise_for_fatal_tool_failure(
                    source,
                    tool_name,
                    failed,
                    content,
                )

        self.assertEqual(processed, ["segmentation_agent"])


class SegmentationIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_generated_uid_is_selected(self):
        module = SegmentationModule(session_id="test-session")

        async def check_having_mask(_volume_uid):
            return [
                {"label_series_instance_uid": "different-new-mask"},
                {
                    "label_series_instance_uid": "expected-segmentation-uid",
                    "target_label": "liver",
                },
            ]

        module._check_having_mask = check_having_mask
        record = await module._wait_for_exact_mask(
            "volume-uid",
            "expected-segmentation-uid",
            timeout_seconds=0.1,
        )
        self.assertEqual(
            record["label_series_instance_uid"],
            "expected-segmentation-uid",
        )


class SegmentationDicomGeometryTests(unittest.TestCase):
    @staticmethod
    def _slice(position):
        dataset = Dataset()
        dataset.Rows = 4
        dataset.Columns = 5
        dataset.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        dataset.ImagePositionPatient = [0, 0, position]
        dataset.InstanceNumber = 100 - position
        return dataset

    def test_slices_are_sorted_by_physical_position(self):
        datasets = [self._slice(4), self._slice(0), self._slice(2)]
        ordered = SegmentationService._sort_source_datasets(datasets)
        self.assertEqual(
            [float(item.ImagePositionPatient[2]) for item in ordered],
            [0.0, 2.0, 4.0],
        )


class EmptySegmentationResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_zero_prediction_skips_dicom_seg_creation(self):
        client = httpx.AsyncClient()
        service = SegmentationService(client=client)
        response = httpx.Response(200, content=b"multipart-result")
        service._await_inference_job = AsyncMock(
            return_value=(response, {"status": "SUCCEEDED", "attempt": 1})
        )
        service.fetch_source_datasets_from_orthanc = AsyncMock()

        try:
            with (
                patch(
                    "agent_system.agents.execution_agents.segmentation.services.parse_multipart_response",
                    return_value=(b"nrrd-result", {}),
                ),
                patch(
                    "agent_system.agents.execution_agents.segmentation.services.parse_nrrd_data",
                    return_value=(
                        np.zeros((1, 5, 4, 3), dtype=np.uint8),
                        {"sizes": [1, 5, 4, 3]},
                    ),
                ),
            ):
                result = await service.run_segmentation(
                    model="voxtell",
                    series_instance_uid="volume-uid",
                    label_info="liver lesion",
                    params={"text_prompts": ["liver lesion"]},
                    job_id="empty-result-job",
                )
        finally:
            await client.aclose()

        self.assertFalse(result["error"])
        self.assertTrue(result["is_empty_mask"])
        self.assertEqual(result["foreground_voxels"], 0)
        self.assertNotIn("segmentation_series_uid", result)
        service.fetch_source_datasets_from_orthanc.assert_not_awaited()

    async def test_empty_result_creates_geometry_matched_local_mask(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            volumes = workspace_subpath(workspace, "volumes")
            volumes.mkdir(parents=True)
            reference = sitk.Image([5, 4, 3], sitk.sitkInt16)
            reference.SetSpacing((0.8, 0.9, 2.5))
            reference.SetOrigin((10.0, 20.0, -5.0))
            volume_path = volumes / "volume-uid.nii.gz"
            sitk.WriteImage(reference, str(volume_path))

            successful, skipped = await download_and_pair_volumes(
                str(workspace),
                [
                    {
                        "pair_id": "volume-uid__lesion",
                        "artifact_key": "empty-mask-key",
                        "volume_uid": "volume-uid",
                        "target_id": "lesion",
                        "label_uid": None,
                        "is_empty_mask": True,
                    }
                ],
                download_volumes=False,
            )

            mask = sitk.ReadImage(
                str(workspace_subpath(workspace, "masks/empty-mask-key.nii.gz"))
            )
            self.assertEqual(len(successful), 1)
            self.assertEqual(skipped, [])
            self.assertEqual(mask.GetSize(), reference.GetSize())
            self.assertTrue(
                np.allclose(mask.GetSpacing(), reference.GetSpacing(), atol=1e-6)
            )
            self.assertTrue(
                np.allclose(mask.GetOrigin(), reference.GetOrigin(), atol=1e-6)
            )
            self.assertEqual(int(sitk.GetArrayFromImage(mask).sum()), 0)

    async def test_empty_inference_result_becomes_ready_volume_preview(self):
        module = SegmentationModule(session_id="session")
        module.segmentation_service = SimpleNamespace(
            run_segmentation=AsyncMock(
                return_value={
                    "error": False,
                    "stage": "completed",
                    "message": "No foreground",
                    "is_empty_mask": True,
                    "foreground_voxels": 0,
                    "inference_job_id": "empty-job",
                    "status_code": 200,
                }
            ),
            purge_inference_job=AsyncMock(),
        )
        module._persist_processing_state = MagicMock()
        item = {
            "pair_id": "volume-uid__lesion",
            "artifact_key": "empty-mask-key",
            "sequence_index": 0,
            "status": "pending",
            "reused": False,
            "volume_uid": "volume-uid",
            "target_id": "lesion",
            "canonical_label": "liver lesion",
            "model": "voxtell",
            "inference_prompt": "liver lesion",
            "inference_job_id": "empty-job",
        }
        resolution = SimpleNamespace(
            plan=SimpleNamespace(chosen_path="automatic")
        )
        publisher = SimpleNamespace(
            publish_ready=AsyncMock(),
            publish_failure=AsyncMock(),
        )

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch(
                "agent_system.agents.execution_agents.segmentation.module.download_and_pair_volumes",
                new=AsyncMock(
                    return_value=(
                        [
                            {
                                "pair_id": item["pair_id"],
                                "geometry_adjusted": False,
                                "geometry": None,
                            }
                        ],
                        [],
                    )
                ),
            ),
            patch(
                "agent_system.agents.execution_agents.segmentation.module.generate_gif_overlays",
                return_value=[
                    {
                        "pairId": item["pair_id"],
                        "gifPath": "overlays/empty-mask-key.gif",
                        "isEmptyMask": True,
                    }
                ],
            ),
            patch(
                "agent_system.agents.execution_agents.segmentation.module.emit_tool_events",
                new=AsyncMock(),
            ),
            patch(
                "agent_system.agents.execution_agents.segmentation.pipelines.automatic.emit_tool_event",
                new=AsyncMock(),
            ),
            patch(
                "agent_system.agents.execution_agents.segmentation.pipelines.automatic.emit_tool_events",
                new=AsyncMock(),
            ),
        ):
            await module._execute_work_items(
                temp_dir,
                resolution,
                [item],
                "run-id",
                publisher,
            )

        self.assertEqual(item["status"], "ready")
        self.assertTrue(item["is_empty_mask"])
        self.assertIsNone(item["source_segmentation_uid"])
        self.assertIsNone(item["error"])
        publisher.publish_failure.assert_not_awaited()
        publisher.publish_ready.assert_awaited()


class SegmentationRecoveryTests(unittest.TestCase):
    def test_review_checkpoint_survives_interrupted_agent_reset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace_session"
            mask_path = workspace_subpath(workspace, "masks/volume__liver.nii.gz")
            volume_path = workspace_subpath(workspace, "volumes/volume.nii.gz")
            output_path = workspace_subpath(workspace, "outputs/segmentation_agent_output.json")
            for path in (mask_path, volume_path, output_path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")

            manifest_path = workspace_subpath(workspace, "segmentation_manifest.json")
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "stage": "awaiting_review",
                        "pairs": [
                            {
                                "status": "ready",
                                "volume_uid": "volume",
                                "mask_path": "masks/volume__liver.nii.gz",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            removed = reset_interrupted_agent(root, "session", "segmentation_agent")

            self.assertEqual(removed, ["outputs/segmentation_agent_output.json"])
            self.assertTrue(manifest_path.exists())
            self.assertTrue(mask_path.exists())
            self.assertTrue(volume_path.exists())

    def test_processing_checkpoint_preserves_persisted_inference_uid(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace_session"
            manifest_path = workspace_subpath(workspace, "segmentation_manifest.json")
            output_path = workspace_subpath(workspace, "outputs/segmentation_agent_output.json")
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"test")
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(
                json.dumps(
                    {
                        "stage": "processing",
                        "pairs": [
                            {
                                "pair_id": "volume__liver",
                                "status": "inference_succeeded",
                                "volume_uid": "volume",
                                "source_segmentation_uid": "segmentation-uid",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            removed = reset_interrupted_agent(root, "session", "segmentation_agent")

            self.assertEqual(removed, ["outputs/segmentation_agent_output.json"])
            self.assertTrue(manifest_path.exists())


if __name__ == "__main__":
    unittest.main()
