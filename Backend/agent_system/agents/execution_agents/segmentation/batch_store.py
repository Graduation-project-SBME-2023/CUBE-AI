"""Segmentation checkpoints in PostgreSQL."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from services.timeout_policy import CONTROL_HTTP_TIMEOUT

logger = logging.getLogger(__name__)

BATCH_KEY = "segmentation"
CONFIG_SCHEMA_VERSION = 1

_PAIR_STATUS = {
    "pending": "pending",
    "reusing": "running",
    "preparing": "running",
    "inferencing": "running",
    "inference_succeeded": "running",
    "resolved": "running",
    "ready": "ready",
    "rejected": "ready",
    "failed": "failed",
}
# Carried by the row itself, never duplicated into `quality`.
_ROW_KEYS = {
    "pair_id",
    "volume_uid",
    "target_id",
    "review_status",
    "inference_job_id",
}
_STAGE_REVIEW_STATUS = {"processing": "not_ready", "awaiting_review": "pending"}


class BatchReconciliationRequired(RuntimeError):
    """The stored batch cannot be continued by this code as it stands."""


def _app_api_base_url() -> str:
    import os

    return os.environ.get(
        "APP_API_URL", os.environ.get("CONTROL_API_URL", "http://localhost:8001")
    ).rstrip("/")


def pair_status(item: dict[str, Any]) -> str:
    return _PAIR_STATUS.get(str(item.get("status") or "pending"), "running")


def pair_payload(item: dict[str, Any]) -> dict[str, Any]:
    quality = {
        key: value for key, value in item.items() if key not in _ROW_KEYS
    }
    quality["schema_version"] = CONFIG_SCHEMA_VERSION
    quality["work_state"] = item.get("status")
    candidate = item.get("source_segmentation_uid") if item.get("candidate_confirmed") else None
    return {
        "pair_key": str(item["pair_id"]),
        "status": pair_status(item),
        "external_job_id": item.get("inference_job_id") or None,
        "candidate_segmentation_uid": candidate or None,
        "quality": quality,
    }


def work_items_from_batch(batch: dict[str, Any]) -> list[dict[str, Any]]:
    """Rebuild the module's work items from the stored pairs."""
    items: list[dict[str, Any]] = []
    for pair in batch.get("pairs") or []:
        quality = dict(pair.get("quality") or {})
        quality.pop("schema_version", None)
        work_state = quality.pop("work_state", None)
        item = dict(quality)
        item["pair_id"] = pair["pair_key"]
        item["volume_uid"] = pair["volume_series_instance_uid"]
        item["target_id"] = pair["target_key"]
        item["inference_job_id"] = pair.get("external_job_id") or item.get("inference_job_id")
        if pair.get("candidate_segmentation_uid"):
            item["source_segmentation_uid"] = pair["candidate_segmentation_uid"]
            item["candidate_confirmed"] = True
        decision = pair.get("review_decision")
        item["review_status"] = decision or "pending"
        status = work_state or pair.get("status") or "pending"
        if pair.get("status") == "failed":
            status = "failed"
        if decision == "rejected" and batch.get("review_status") == "submitted":
            status = "rejected"
        item["status"] = status
        items.append(item)
    return items


def batch_config(resolution_outcome, *, correlation_run_id: str, schema_version: str) -> dict[str, Any]:
    plan = resolution_outcome.plan
    return {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "segmentation_schema_version": schema_version,
        "correlation_run_id": correlation_run_id,
        "chosen_path": plan.chosen_path,
        "policy_fingerprint": resolution_outcome.policy_fingerprint,
        "intent_plan": resolution_outcome.intent_plan.model_dump(mode="json"),
        "candidate_plan": resolution_outcome.candidate_plan.model_dump(mode="json"),
        "final_plan": plan.model_dump(mode="json"),
    }


def batch_targets(plan, model_for_target) -> list[dict[str, Any]]:
    targets = []
    for index, target in enumerate(plan.targets):
        targets.append(
            {
                "target_key": target.target_id,
                "label": target.canonical_label or target.target_id,
                "model_name": model_for_target(plan, index),
                "specification": {
                    "schema_version": CONFIG_SCHEMA_VERSION,
                    "inference_prompt": target.inference_prompt,
                    "anatomy": target.anatomy,
                    "pathology": target.pathology,
                    "scope": target.scope,
                    "subregion": target.subregion,
                    "laterality": target.laterality,
                    "target_defining_modifiers": target.target_defining_modifiers,
                    "intent_confidence": target.intent_confidence,
                    "match_confidence": target.match_confidence,
                    "reason": target.reason,
                },
            }
        )
    return targets


class SegmentationBatchStore:
    """The Application API calls behind the module's checkpoints."""

    def __init__(self, agent_run_id: str | None, client: httpx.AsyncClient | None = None):
        if not agent_run_id:
            raise RuntimeError(
                "Segmentation has no agent_run_id: its batch cannot be recorded, and v1 "
                "does not fall back to segmentation_manifest.json."
            )
        self.agent_run_id = str(agent_run_id)
        self._client = client

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._client is not None:
            return await self._client.request(
                method, f"{_app_api_base_url()}{path}", timeout=CONTROL_HTTP_TIMEOUT, **kwargs
            )
        async with httpx.AsyncClient(timeout=CONTROL_HTTP_TIMEOUT) as client:
            return await client.request(method, f"{_app_api_base_url()}{path}", **kwargs)

    async def load(self) -> dict[str, Any] | None:
        response = await self._request(
            "GET", f"/segmentation-runs/run/{self.agent_run_id}/{BATCH_KEY}"
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def create(
        self,
        *,
        cohort_id: str,
        config: dict[str, Any],
        targets: list[dict[str, Any]],
        work_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        pairs = []
        for item in work_items:
            payload = pair_payload(item)
            pairs.append(
                {
                    "target_key": str(item["target_id"]),
                    "volume_series_instance_uid": str(item["volume_uid"]),
                    "status": payload["status"],
                    "external_job_id": payload["external_job_id"],
                    "candidate_segmentation_uid": payload["candidate_segmentation_uid"],
                    "quality": payload["quality"],
                }
            )
        response = await self._request(
            "PUT",
            f"/segmentation-runs/run/{self.agent_run_id}/{BATCH_KEY}",
            json={"cohort_id": cohort_id, "config": config, "targets": targets, "pairs": pairs},
        )
        if response.status_code == 409:
            raise BatchReconciliationRequired(
                f"The segmentation batch for run {self.agent_run_id} already exists with "
                f"different content: {response.text[:400]}"
            )
        response.raise_for_status()
        return response.json()

    async def sync(
        self,
        segmentation_run_id: str,
        work_items: list[dict[str, Any]],
        *,
        stage: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"pairs": [pair_payload(item) for item in work_items]}
        review_status = _STAGE_REVIEW_STATUS.get(stage or "")
        if review_status:
            body["review_status"] = review_status
        response = await self._request(
            "PATCH", f"/segmentation-runs/{segmentation_run_id}/pairs", json=body
        )
        response.raise_for_status()
        return response.json()

    async def read(self, segmentation_run_id: str) -> dict[str, Any]:
        response = await self._request("GET", f"/segmentation-runs/{segmentation_run_id}")
        response.raise_for_status()
        return response.json()
