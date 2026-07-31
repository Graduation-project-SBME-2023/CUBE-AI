import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from agent_system.agents.execution_agents.segmentation.pipelines.automatic import (
    run_automatic_pipeline,
)
from agent_system.agents.execution_agents.segmentation.progressive import (
    artifact_key_for_pair,
    build_segmentation_snapshot,
)


class _FakeSegmentationService:
    def __init__(self):
        self.calls = []

    async def run_segmentation(
        self,
        *,
        model,
        series_instance_uid,
        label_info,
        params,
    ):
        self.calls.append(
            {
                "model": model,
                "volume_uid": series_instance_uid,
                "label": label_info,
                "prompt": params["text_prompts"][0],
            }
        )
        return {
            "segmentation_series_uid": f"seg-{label_info}",
            "status_code": 200,
        }


class PairPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_volume_targets_keep_their_own_model_and_prompt(self):
        service = _FakeSegmentationService()
        events = []
        items = [
            {
                "pair_id": "volume__liver",
                "volume_uid": "volume",
                "target_id": "liver",
                "canonical_label": "liver",
                "model": "model-liver",
                "inference_prompt": "prompt-liver",
            },
            {
                "pair_id": "volume__tumor",
                "volume_uid": "volume",
                "target_id": "tumor",
                "canonical_label": "liver tumor",
                "model": "model-tumor",
                "inference_prompt": "prompt-tumor",
            },
        ]

        async def started(event):
            events.append(("started", event["pair_id"]))

        async def completed(event):
            events.append(("completed", event["pair_id"]))

        with (
            patch(
                "agent_system.agents.execution_agents.segmentation.pipelines.automatic.emit_tool_event",
                new=AsyncMock(),
            ),
            patch(
                "agent_system.agents.execution_agents.segmentation.pipelines.automatic.emit_tool_events",
                new=AsyncMock(),
            ),
        ):
            result = await run_automatic_pipeline(
                items,
                service,
                "session",
                on_pair_started=started,
                on_pair_completed=completed,
            )

        self.assertEqual(
            service.calls,
            [
                {
                    "model": "model-liver",
                    "volume_uid": "volume",
                    "label": "liver",
                    "prompt": "prompt-liver",
                },
                {
                    "model": "model-tumor",
                    "volume_uid": "volume",
                    "label": "liver tumor",
                    "prompt": "prompt-tumor",
                },
            ],
        )
        self.assertEqual(
            events,
            [
                ("started", "volume__liver"),
                ("completed", "volume__liver"),
                ("started", "volume__tumor"),
                ("completed", "volume__tumor"),
            ],
        )
        self.assertEqual(len(result["outcomes"]), 2)

    async def test_completion_callback_exception_does_not_stop_later_pairs(self):
        service = _FakeSegmentationService()
        items = [
            {
                "pair_id": f"volume__target_{index}",
                "volume_uid": "volume",
                "target_id": f"target_{index}",
                "canonical_label": f"target {index}",
                "model": "model",
                "inference_prompt": f"prompt {index}",
            }
            for index in range(2)
        ]
        callback_calls = []

        async def completed(event):
            callback_calls.append(event["pair_id"])
            if len(callback_calls) == 1:
                raise RuntimeError("preview failed")

        with (
            patch(
                "agent_system.agents.execution_agents.segmentation.pipelines.automatic.emit_tool_event",
                new=AsyncMock(),
            ),
            patch(
                "agent_system.agents.execution_agents.segmentation.pipelines.automatic.emit_tool_events",
                new=AsyncMock(),
            ),
        ):
            await run_automatic_pipeline(
                items,
                service,
                "session",
                on_pair_completed=completed,
            )

        self.assertEqual(callback_calls, ["volume__target_0", "volume__target_1"])
        self.assertEqual(len(service.calls), 2)
        self.assertEqual(items[0]["status"], "failed")
        self.assertEqual(items[0]["error_stage"], "preview_callback")


class ProgressiveSnapshotTests(unittest.TestCase):
    def test_ready_and_failed_pairs_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace_session"
            overlays = workspace / "overlays"
            overlays.mkdir(parents=True)
            ready_pair = "volume__liver"
            ready_key = artifact_key_for_pair(ready_pair)
            (overlays / f"{ready_key}.gif").write_bytes(b"GIF89a")
            items = [
                {
                    "pair_id": ready_pair,
                    "artifact_key": ready_key,
                    "sequence_index": 0,
                    "volume_uid": "volume",
                    "target_id": "liver",
                    "canonical_label": "liver",
                    "status": "ready",
                    "gif_path": f"overlays/{ready_key}.gif",
                },
                {
                    "pair_id": "volume__tumor",
                    "artifact_key": artifact_key_for_pair("volume__tumor"),
                    "sequence_index": 1,
                    "volume_uid": "volume",
                    "target_id": "tumor",
                    "canonical_label": "tumor",
                    "status": "failed",
                    "gif_path": "overlays/diagnostic.gif",
                    "error": "Generated segmentation mask is empty",
                },
            ]
            plan = SimpleNamespace(
                target_labels=["liver", "tumor"],
                selected_models=["model"],
                targets=[
                    SimpleNamespace(target_id="liver", canonical_label="liver"),
                    SimpleNamespace(target_id="tumor", canonical_label="tumor"),
                ],
            )

            snapshot = build_segmentation_snapshot(
                plan=plan,
                work_items=items,
                workspace_dir=workspace,
                base_url="http://localhost:8150",
                is_finished=True,
            )

            self.assertEqual(snapshot["progress"], {"total": 2, "generated": 1, "failed": 1})
            self.assertEqual([entry["pairId"] for entry in snapshot["volumes"]], [ready_pair])
            self.assertEqual(
                [entry["pairId"] for entry in snapshot["failedVolumes"]],
                ["volume__tumor"],
            )

    def test_empty_mask_remains_a_repairable_ready_preview(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace_session"
            overlays = workspace / "overlays"
            overlays.mkdir(parents=True)
            pair_id = "volume__tumor"
            artifact_key = artifact_key_for_pair(pair_id)
            (overlays / f"{artifact_key}.gif").write_bytes(b"GIF89a")
            item = {
                "pair_id": pair_id,
                "artifact_key": artifact_key,
                "sequence_index": 0,
                "volume_uid": "volume",
                "target_id": "tumor",
                "canonical_label": "tumor",
                "status": "ready",
                "gif_path": f"overlays/{artifact_key}.gif",
                "is_empty_mask": True,
                "warning": "Volume mask is empty; modify it before saving if needed",
            }
            plan = SimpleNamespace(
                target_labels=["tumor"],
                selected_models=["model"],
                targets=[SimpleNamespace(target_id="tumor", canonical_label="tumor")],
            )

            snapshot = build_segmentation_snapshot(
                plan=plan,
                work_items=[item],
                workspace_dir=workspace,
                base_url="http://localhost:8150",
                is_finished=True,
            )

            self.assertEqual(snapshot["progress"], {"total": 1, "generated": 1, "failed": 0})
            self.assertTrue(snapshot["volumes"][0]["isEmptyMask"])
            self.assertEqual(snapshot["failedVolumes"], [])

    def test_artifact_key_is_filesystem_safe_and_deterministic(self):
        pair_id = "1.2.3__left_kidney_tumor"
        first = artifact_key_for_pair(pair_id)
        self.assertEqual(first, artifact_key_for_pair(pair_id))
        self.assertRegex(first, r"^[a-f0-9]{64}$")


if __name__ == "__main__":
    unittest.main()
