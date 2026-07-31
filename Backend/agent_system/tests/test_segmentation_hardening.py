import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import SimpleITK as sitk
from pydicom.dataset import Dataset
from pydantic import ValidationError

from agent_system.agents.execution_agents.segmentation.contracts import (
    ClinicalPlanAudit,
    ClinicalTargetIntent,
    ClinicalTargetIntentPlan,
    ResolvedTarget,
    TargetCandidateDecision,
    TargetResolutionPlan,
    VoxTellPromptDecision,
    validate_audited_plan_identity,
)
from agent_system.agents.execution_agents.segmentation.module import SegmentationModule
from agent_system.agents.execution_agents.segmentation.pipelines.common import (
    SegmentationPipelineError,
    raise_for_segmentation_error,
)
from agent_system.agents.execution_agents.segmentation.services import (
    SegmentationService,
)
from agent_system.agents.execution_agents.segmentation.utils import (
    _validated_nrrd_array,
    align_and_binarize_mask,
    parse_multipart_response,
)
from agent_system.services.resume_recovery import reset_interrupted_agent


class SegmentationContractTests(unittest.TestCase):
    def test_reuse_requires_confidence_of_at_least_point_six(self):
        prompt = VoxTellPromptDecision(mode="generate", prompt="liver")
        with self.assertRaises(ValidationError):
            TargetCandidateDecision(
                target_id="liver",
                accepted_mask_candidate_ids=["mask_1"],
                voxtell_prompt=prompt,
                selected_model="voxtell",
                confidence=0.59,
                rationale="Candidate looks similar.",
            )

        decision = TargetCandidateDecision(
            target_id="liver",
            accepted_mask_candidate_ids=["mask_1"],
            voxtell_prompt=prompt,
            selected_model="voxtell",
            confidence=0.6,
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

    def test_auditor_cannot_rewrite_locked_clinical_identity(self):
        intent = ClinicalTargetIntentPlan(
            targets=[
                ClinicalTargetIntent(
                    target_id="liver",
                    canonical_label="liver",
                    anatomy="liver",
                    confidence=1.0,
                    rationale="Explicitly requested.",
                )
            ]
        )
        changed_plan = TargetResolutionPlan(
            chosen_path="interactive",
            selected_models=["voxtell"],
            targets=[
                ResolvedTarget(
                    target_id="liver",
                    canonical_label="spleen",
                    anatomy="spleen",
                    voxtell_prompt=VoxTellPromptDecision(
                        mode="generate",
                        prompt="spleen",
                    ),
                    confidence=1.0,
                    reason="Incorrect correction.",
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "locked clinical identity"):
            validate_audited_plan_identity(changed_plan, intent)

    def test_invalid_locked_identity_fails_without_replacement(self):
        audit = ClinicalPlanAudit(
            approved=False,
            clinical_identity_invalid=True,
            issues=["Requested anatomy is clinically ambiguous."],
            corrected_plan=None,
        )
        self.assertTrue(audit.clinical_identity_invalid)

        with self.assertRaises(ValidationError):
            ClinicalPlanAudit(
                approved=False,
                clinical_identity_invalid=True,
                issues=["Requested anatomy is clinically ambiguous."],
                corrected_plan=TargetResolutionPlan(
                    chosen_path="interactive",
                    selected_models=["voxtell"],
                    targets=[
                        ResolvedTarget(
                            target_id="liver",
                            canonical_label="liver",
                            anatomy="liver",
                            voxtell_prompt=VoxTellPromptDecision(
                                mode="generate",
                                prompt="liver",
                            ),
                            confidence=1.0,
                            reason="Replacement must be forbidden.",
                        )
                    ],
                ),
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


class SegmentationUtilityTests(unittest.TestCase):
    def test_short_explicit_instruction_is_preserved(self):
        instruction = SegmentationModule._build_instruction(
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


class SegmentationRecoveryTests(unittest.TestCase):
    def test_review_checkpoint_survives_interrupted_agent_reset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace_session"
            mask_path = workspace / "masks" / "volume__liver.nii.gz"
            volume_path = workspace / "volumes" / "volume.nii.gz"
            output_path = workspace / "outputs" / "segmentation_agent_output.json"
            for path in (mask_path, volume_path, output_path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"test")

            manifest_path = workspace / "segmentation_manifest.json"
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
            manifest_path = workspace / "segmentation_manifest.json"
            output_path = workspace / "outputs" / "segmentation_agent_output.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(b"test")
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
