import asyncio
import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from .contracts import CachePromptCandidate, MaskLabelCandidate, ResolvedTarget

logger = logging.getLogger(__name__)


def _stable_candidate_id(prefix: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


@dataclass
class MaskInventory:
    records_by_volume: dict[str, list[dict[str, Any]]]
    candidates: list[MaskLabelCandidate]
    candidate_id_by_record_uid: dict[str, str]


def is_reusable_mask_record(record: dict[str, Any]) -> bool:
    """Return whether a database mask record is eligible for reuse."""
    record_uid = str(record.get("label_series_instance_uid") or "").strip()
    if not record_uid:
        return False

    archive_status = str(record.get("archive_status") or "available").strip().casefold()
    if archive_status != "available":
        return False

    origin = str(record.get("origin") or "").strip().casefold()
    review_decision = str(record.get("review_decision") or "").strip().casefold()
    if origin == "assisted":
        # No review gate on the assisted path; an explicit rejection still wins.
        if review_decision == "rejected":
            return False
    elif origin == "pipeline":
        if review_decision not in {"accepted", "modified"}:
            return False
    else:
        return False

    empty_value = record.get("is_empty_mask")
    if empty_value is not False and empty_value != 0:
        # True, 1, "true", None and "" all land here: only an explicit
        # not-empty record is reusable.
        return False

    return True


def _record_label(record: dict[str, Any]) -> tuple[str, bool]:
    target_label = str(record.get("target_label") or "").strip()
    if target_label:
        return target_label, True
    return str(record.get("series_description") or "").strip(), False


def build_mask_inventory(
    records_by_volume: dict[str, list[dict[str, Any]]],
) -> MaskInventory:
    reusable_records_by_volume = {
        volume_uid: [
            record for record in records if is_reusable_mask_record(record)
        ]
        for volume_uid, records in records_by_volume.items()
    }

    unique_labels: dict[tuple[bool, str], str] = {}
    for records in reusable_records_by_volume.values():
        for record in records:
            label, structured = _record_label(record)
            if label:
                unique_labels.setdefault((structured, label), "")

    candidates: list[MaskLabelCandidate] = []
    for structured, label in sorted(
        unique_labels, key=lambda value: (not value[0], value[1].casefold())
    ):
        identity = f"{'structured' if structured else 'legacy'}\0{label}"
        candidate_id = _stable_candidate_id("mask", identity)
        unique_labels[(structured, label)] = candidate_id
        candidates.append(
            MaskLabelCandidate(
                candidate_id=candidate_id,
                display_label=label,
                structured=structured,
            )
        )

    candidate_id_by_record_uid: dict[str, str] = {}
    for records in reusable_records_by_volume.values():
        for record in records:
            label, structured = _record_label(record)
            uid = str(record.get("label_series_instance_uid") or "")
            if uid and label:
                candidate_id_by_record_uid[uid] = unique_labels[(structured, label)]

    return MaskInventory(
        records_by_volume=reusable_records_by_volume,
        candidates=candidates,
        candidate_id_by_record_uid=candidate_id_by_record_uid,
    )


async def fetch_mask_inventory(
    volume_uids: list[str],
    fetcher: Callable[[str], Awaitable[list[dict[str, Any]]]],
) -> MaskInventory:
    results = await asyncio.gather(*(fetcher(uid) for uid in volume_uids))
    return build_mask_inventory(dict(zip(volume_uids, results)))


async def fetch_cache_candidates(
    monai_url: str,
    timeout_seconds: float = 10.0,
    *,
    client=None,
) -> list[CachePromptCandidate]:
    import httpx

    url = f"{monai_url.rstrip('/')}/voxtell/cache-prompts"
    request_client = client or httpx.AsyncClient()
    try:
        response = await request_client.get(url, timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("available"):
            logger.warning("VoxTell cache inventory unavailable: %s", payload.get("message"))
            return []
        keys = payload.get("keys") or []
        candidates = [
            CachePromptCandidate(
                candidate_id=_stable_candidate_id("cache", str(key)),
                prompt=str(key),
            )
            for key in keys
        ]
        return candidates
    except Exception as exc:
        logger.warning(
            "VoxTell cache inventory request failed; exact prompt generation will "
            "be used: %s",
            exc,
        )
        return []
    finally:
        if client is None:
            await request_client.aclose()


def resolve_voxtell_prompt(
    target: ResolvedTarget,
    cache_candidates: list[CachePromptCandidate],
) -> tuple[str, bool]:
    # Use inference_prompt (the explicit VoxTell field) rather than
    # canonical_label. They are equal by the ResolvedTarget invariant, but
    # inference_prompt is the semantically correct source for what is sent to
    # the model.
    if target.voxtell_prompt.mode == "generate":
        return target.inference_prompt, False
    prompt_by_id = {
        candidate.candidate_id: candidate.prompt for candidate in cache_candidates
    }
    return prompt_by_id[str(target.voxtell_prompt.cache_candidate_id)], True


def _created_at_sort_value(record: dict[str, Any]) -> float:
    value = record.get("created_at")
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return float("-inf")
    else:
        return float("-inf")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def select_newest_matching_mask(
    volume_uid: str,
    target: ResolvedTarget,
    inventory: MaskInventory,
) -> dict[str, Any] | None:
    accepted = set(target.accepted_mask_candidate_ids)
    matching: list[dict[str, Any]] = []
    for record in inventory.records_by_volume.get(volume_uid, []):
        record_uid = str(record.get("label_series_instance_uid") or "")
        candidate_id = inventory.candidate_id_by_record_uid.get(record_uid)
        if candidate_id in accepted:
            matching.append(record)

    if not matching:
        return None
    return max(
        matching,
        key=lambda record: (
            _created_at_sort_value(record),
            str(record.get("label_series_instance_uid") or ""),
        ),
    )
