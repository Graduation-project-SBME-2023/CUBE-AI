"""Human-in-the-loop segmentation review: drafts, triage, edited masks, long-poll."""

import asyncio
import json
import logging
import os
from uuid import uuid4
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from starlette.requests import Request

from storage_paths import workspace, workspace_subpath, ws_sub
from socket_store import manager as ws_manager
from services import population_review
from services.artifact_client import register_render, render_logical_key
from services.mask_store import MaskStore, orthanc_first
from clients.orthanc_client import OrthancClient
from agents.execution_agents.segmentation.utils import (
    align_and_binarize_mask,
    describe_legacy_repair,
)
from agents.execution_agents.segmentation.progressive import artifact_key_for_pair
from agents.execution_agents.segmentation.services import SegmentationService
from api.review_state import review_events
from api.runtime import domain_http_error, session_service

logger = logging.getLogger(__name__)

router = APIRouter()

REVIEW_RECHECK_SECONDS = float(os.environ.get("REVIEW_RECHECK_SECONDS", "30"))


# ----------------------------------------------------------------------
# Segmentation review
# ----------------------------------------------------------------------

async def _review_batch_or_404(session_id: str) -> dict:
    batch = await population_review.current_batch(session_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="Segmentation review is not available")
    return batch


@router.post("/segmentation-review/draft")
async def segmentation_review_draft(data: dict):
    """Commit UI review choices durably without resolving the agent's wait."""
    session_id = str(data.get("session_id") or "")
    decisions = data.get("decisions")
    if not session_id or "/" in session_id or "\\" in session_id or ".." in session_id:
        raise HTTPException(status_code=400, detail="A valid session_id is required")
    if not isinstance(decisions, dict):
        raise HTTPException(status_code=400, detail="decisions must be an object")
    allowed_statuses = {"accepted", "rejected", "modified"}
    if any(str(status) not in allowed_statuses for status in decisions.values()):
        raise HTTPException(status_code=400, detail="Review contains an invalid status")
    try:
        batch = await _review_batch_or_404(session_id)
        # The review UI sends the batch correlation id it was shown as `run_id`.
        request_correlation_id = str(data.get("run_id") or "")
        batch_correlation_id = str((batch.get("config") or {}).get("batch_correlation_id") or "")
        if (
            request_correlation_id
            and batch_correlation_id
            and request_correlation_id != batch_correlation_id
        ):
            raise HTTPException(status_code=409, detail="Segmentation batch has changed")
        await population_review._api(
            "PUT",
            f"/segmentation-batches/{batch['segmentation_batch_id']}/review/draft",
            json={"decisions": {str(key): str(value) for key, value in decisions.items()}},
        )
    except population_review.DomainError as exc:
        raise domain_http_error(exc)
    return {"status": "ok"}


@router.post("/segmentation-review")
async def segmentation_review(data: dict):
    """The one final review submission."""
    from agents.execution_agents.segmentation.progressive import artifact_key_for_pair as _key

    session_id = str(data.get("session_id") or "")
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")
    accepted = [str(item) for item in data.get("accepted", []) or []]
    rejected = [str(item) for item in data.get("rejected", []) or []]
    modified = [str(item) for item in data.get("modified", []) or []]
    logger.info(
        "[Review] Received triage for session=%s: %s accepted, %s rejected, %s modified",
        session_id, len(accepted), len(rejected), len(modified),
    )

    import hashlib
    import json as _json

    request_hash = hashlib.sha256(
        _json.dumps(
            {
                "accepted": sorted(set(accepted)),
                "rejected": sorted(set(rejected)),
                "modified": sorted(set(modified)),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    try:
        batch = await _review_batch_or_404(session_id)
        batch_id = batch["segmentation_batch_id"]
        batch = await population_review._api(
            "POST",
            f"/segmentation-batches/{batch_id}/review/begin",
            json={"request_hash": request_hash},
        )
    except population_review.DomainError as exc:
        raise domain_http_error(exc)

    selected: dict[str, str] = {}
    if batch["review_status"] != "submitted" and modified:
        pairs = {pair["pair_key"]: pair for pair in batch.get("pairs") or []}
        volume_keys = {}
        for pair in pairs.values():
            volume_keys.setdefault(pair["volume_series_instance_uid"], []).append(pair["pair_key"])
        targets = {target["target_key"]: target for target in batch.get("targets") or []}
        service = SegmentationService()
        try:
            for raw_key in modified:
                key = raw_key if raw_key in pairs else (volume_keys.get(raw_key) or [None])[0]
                pair = pairs.get(key or "")
                if pair is None:
                    raise HTTPException(status_code=400, detail=f"Unknown segmentation pair {raw_key!r}")
                temp_mask = ws_sub(session_id, f"temp_segmentations/seg_{_key(pair['pair_key'])}_modified.nii.gz")
                if not os.path.isfile(temp_mask):
                    raise HTTPException(
                        status_code=409,
                        detail=f"Pair {key!r} is marked modified but no edited mask was uploaded",
                    )
                published = await service.publish_modified_mask(
                    volume_uid=pair["volume_series_instance_uid"],
                    mask_path=str(temp_mask),
                    target_label=(targets.get(pair["target_key"]) or {}).get("label") or pair["target_key"],
                    pair_key=pair["pair_key"],
                )
                selected[key] = published["segmentation_series_uid"]
        except Exception as exc:
            try:
                await population_review._api(
                    "POST",
                    f"/segmentation-batches/{batch_id}/review/abort",
                    json={"request_hash": request_hash},
                )
            except population_review.DomainError:
                logger.exception("Could not return review %s to pending", batch_id)
            if isinstance(exc, HTTPException):
                raise
            logger.exception("Publishing edited masks failed for session %s", session_id)
            raise HTTPException(
                status_code=502,
                detail=f"The edited mask could not be published to Orthanc: {exc}",
            )
        finally:
            await service.close()

    try:
        result = await population_review._api(
            "POST",
            f"/segmentation-batches/{batch_id}/review/complete",
            json={"accepted": accepted, "rejected": rejected, "modified": modified, "selected": selected},
        )
    except population_review.DomainError as exc:
        raise domain_http_error(exc)

    event = result.get("event")
    if event:
        await ws_manager.send_ws_message(
            event["event_type"], event["payload"], session_id=session_id, event_id=event["event_id"]
        )
    waiter = review_events.get(session_id)
    if waiter is not None:
        waiter.set()

    active_session = session_service.get_session(session_id)
    if (
        active_session
        and active_session.execution_task
        and not active_session.execution_task.done()
    ):
        await session_service.persist_agent_status(
            session_id,
            "segmentation_agent",
            "RUNNING",
        )
    await ws_manager.send_ws_message(
        "agent_waiting_resolved",
        {"agent": "segmentation_agent"},
        session_id=session_id,
    )
    return {"status": "ok"}


@router.post("/segmentation-review/upload-mask")
async def upload_modified_mask(
    request: Request,
    session_id: str = Form(...),
    series_instance_uid: str = Form(...),
    target_id: str | None = Form(None),
    file: UploadFile = File(...)
):
    workspace_folder = str(workspace(session_id))
    output_dir = str(ws_sub(session_id, "temp_segmentations"))

    try:
        os.makedirs(output_dir, exist_ok=True)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create directories: {str(e)}")

    pair_id = (
        f"{series_instance_uid}__{target_id}"
        if target_id
        else series_instance_uid
    )
    artifact_key = artifact_key_for_pair(pair_id)
    file_path = os.path.join(output_dir, f"seg_{artifact_key}_modified.nii.gz")
    # The frontend mask matches the full DICOM volume space.
    # The workspace stores volumes in 'volumes/' and masks in 'masks/'; once a
    # run has finished that copy is gone, so this rebuilds it from Orthanc.
    base_volume_path = await _local_volume_path(session_id, series_instance_uid)
    original_mask_path = str(ws_sub(session_id, f"masks/{artifact_key}.nii.gz"))
    legacy_pair_name_is_safe = "/" not in pair_id and "\\" not in pair_id
    if not os.path.exists(original_mask_path) and legacy_pair_name_is_safe:
        original_mask_path = str(ws_sub(session_id, f"masks/{pair_id}.nii.gz"))

    import nibabel as nib
    import numpy as np

    raw_bytes = await file.read()
    actual_bytes = len(raw_bytes)
    if base_volume_path and os.path.exists(base_volume_path):
        ref_nii = nib.load(base_volume_path)
    elif os.path.exists(original_mask_path):
        ref_nii = nib.load(original_mask_path)
    else:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Neither the base volume nor the original mask exists for {pair_id}; "
                "there is no reference grid to place the edited mask on."
            ),
        )
    ref_shape = tuple(int(value) for value in ref_nii.shape[:3])
    expected_voxels = int(np.prod(ref_shape))
    dtype = {
        expected_voxels: np.uint8,
        expected_voxels * 2: np.uint16,
        expected_voxels * 4: np.float32,
    }.get(actual_bytes)
    if dtype is None:
        # An edited mask with a different grid is rejected, never re-shaped
        # into something that merely has the right number of bytes.
        raise HTTPException(
            status_code=422,
            detail=(
                f"Edited mask size mismatch: the reference grid {ref_shape} holds "
                f"{expected_voxels} voxels, but {actual_bytes} bytes were uploaded. "
                "The mask must be edited on the source volume's own grid."
            ),
        )
    try:
        reshaped_array = np.frombuffer(raw_bytes, dtype=dtype).reshape(ref_shape, order="F")
        nib.save(nib.Nifti1Image(reshaped_array, ref_nii.affine), file_path)
    except Exception as e:
        logger.error(f"Failed to process and save modified mask: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to process mask: {str(e)}")

    from agents.execution_agents.segmentation.utils import generate_gif_overlays

    overlay_results = await asyncio.to_thread(
        generate_gif_overlays,
        workspace_folder,
        [
            {
                "pair_id": pair_id,
                "artifact_key": artifact_key,
                "volume_uid": series_instance_uid,
                "target_id": target_id or "",
                "mask_path": file_path,
                "volume_path": base_volume_path or "",
            }
        ],
    )
    if not overlay_results:
        raise HTTPException(
            status_code=500,
            detail="Modified mask was saved, but its preview could not be regenerated.",
        )

    overlay = overlay_results[0]
    gif_path = str(overlay.get("gifPath") or f"overlays/{artifact_key}.gif")
    # Catalogue the regenerated preview (a render is a cache entry).
    await register_render(
        session_id,
        render_logical_key(pair_id, artifact_key),
        gif_path,
        metadata={
            "schema_version": 1,
            "pair_id": pair_id,
            "volume_uid": series_instance_uid,
            "target_id": target_id or None,
            "source": "user_modified_mask",
        },
    )
    overlay["gifUrl"] = (
        f"{str(request.base_url).rstrip('/')}/files/"
        f"{os.path.basename(workspace_folder)}/{gif_path}"
    )
    overlay["updatedAt"] = os.stat(
        workspace_subpath(workspace_folder, gif_path)
    ).st_mtime_ns

    archive = await _archive_edited_mask(
        session_id, series_instance_uid, target_id, file_path
    )

    return {
        "message": "Modified mask saved successfully", 
        "session_id": session_id,
        "status": "saved",
        "overlay": overlay,
        "archive": archive,
    }


async def _manifest_pair(
    session_id: str,
    series_uid: str,
    target_id: str | None,
) -> dict[str, Any] | None:
    """Find this pair in the durable batch, with legacy file compatibility."""
    if not target_id:
        return None
    try:
        batch = await population_review.current_batch(session_id)
        pairs = population_review.snapshot_manifest(batch)["pairs"] if batch else []
    except Exception:
        logger.warning("Could not read segmentation batch for session %s", session_id, exc_info=True)
        pairs = []
    if not pairs:
        manifest_path = ws_sub(session_id, "segmentation_manifest.json")
        try:
            pairs = json.loads(manifest_path.read_text(encoding="utf-8")).get("pairs") or []
        except (OSError, json.JSONDecodeError):
            return None
    for pair in pairs:
        if (
            str(pair.get("volume_uid")) == series_uid
            and str(pair.get("target_id")) == str(target_id)
        ):
            return pair
    return None


async def _canonical_label_for_pair(
    session_id: str,
    series_uid: str,
    target_id: str | None,
) -> str | None:
    """Translate the review pair's target id into its archived SegmentLabel."""
    pair = await _manifest_pair(session_id, series_uid, target_id)
    label = (pair or {}).get("canonical_label")
    return str(label) if label else None


async def _local_volume_path(session_id: str, series_uid: str) -> str | None:
    """A readable NIfTI for this source volume, rebuilt from Orthanc if needed.

    The workspace copy is deleted when a run finishes -- it is the largest
    thing a session leaves behind and Orthanc holds the DICOM it was made from.
    Reopening a finished session to review or edit a mask still needs it, so it
    is rebuilt on demand rather than kept.

    Returns None instead of raising: both callers already have a path for the
    volume being absent, and neither should fail because Orthanc is briefly
    unreachable.
    """
    workspace_copy = ws_sub(session_id, f"volumes/{series_uid}.nii.gz")
    if workspace_copy.is_file():
        return str(workspace_copy)
    try:
        async with MaskStore() as store:
            return str(await store.ensure_local_volume(session_id, series_uid))
    except Exception:  # noqa: BLE001 - callers handle a missing volume
        logger.exception(
            "Could not rebuild volume %s for session %s from Orthanc",
            series_uid,
            session_id,
        )
        return None


async def _archive_edited_mask(
    session_id: str,
    series_uid: str,
    target_id: str | None,
    mask_path: str,
) -> dict[str, Any]:
    """Publish a clinician's saved edit to Orthanc as its own DICOM SEG.

    Saving used to write the workspace NIfTI and stop there; the edit only
    reached Orthanc later, when the segmentation agent resumed after the review
    was *submitted*. Anyone who saved an edit and then looked at the archive --
    or at the viewer, which reads the archive -- saw the model's mask instead,
    and an edit saved but never submitted never reached Orthanc at all.

    Publishing here makes the save itself the thing that updates the archive.
    The edit is a new series that references the original rather than replacing
    it: DICOM has no update, and the original is what the correction has to stay
    comparable against. The SEG UID covers the mask's content, so saving the
    same mask twice is a no-op while a genuinely changed edit is archived as its
    own series -- and ``resolve`` then reads back the newest manual one.

    Never raises -- the local copy is still saved, so a failure here degrades to
    exactly the old behaviour, except now it is reported instead of silent.
    """
    pair = await _manifest_pair(session_id, series_uid, target_id) or {}
    label = str(
        pair.get("canonical_label") or target_id or ""
    ).strip()
    if not label:
        return {"status": "skipped", "reason": "no target label for this pair"}

    derived_from = (pair.get("inference_result") or {}).get("segmentation_series_uid")
    try:
        async with MaskStore() as store:
            ref = await store.publish(
                volume_uid=series_uid,
                target_label=label,
                mask_path=Path(mask_path),
                method="manual",
                derived_from=derived_from,
            )
            if orthanc_first():
                # Subsequent reads must see the edit that was just archived, not
                # the model's mask it was derived from.
                await store.ensure_local(session_id, ref)
        logger.info(
            "Archived edited mask for session=%s series=%s target=%s as %s",
            session_id,
            series_uid,
            target_id or "",
            ref.seg_series_uid,
        )
        return {"status": "ok", "seg_series_uid": ref.seg_series_uid}
    except Exception as exc:  # noqa: BLE001 - reported, never fatal
        logger.exception(
            "Failed to archive the edited mask for session=%s series=%s target=%s",
            session_id,
            series_uid,
            target_id or "",
        )
        return {"status": "failed", "error": str(exc)}


async def _mask_from_orthanc(
    session_id: str,
    series_uid: str,
    target_id: str | None,
) -> str | None:
    """Materialise the archived mask for this pair, or None if unavailable.

    Never raises: the workspace copy remains a valid fallback while the two
    sources are still being compared.
    """
    try:
        label = await _canonical_label_for_pair(session_id, series_uid, target_id)
        if target_id and not label:
            # Without a label a multi-target series could resolve to the mask of
            # a different organ, which is worse than falling back.
            logger.warning(
                "No canonical label for target %s on series %s; using the "
                "workspace copy",
                target_id,
                series_uid,
            )
            return None
        async with MaskStore() as store:
            ref = await store.resolve(series_uid, target_label=label)
            if ref is None:
                return None
            archived_path = await store.ensure_local(session_id, ref)

        # The SEG cache uses the source DICOM grid. Align it to the exact
        # volume shown in the editor, and repair only proven legacy SEG pixels.
        reference_path = await _local_volume_path(session_id, series_uid)
        if not reference_path:
            return None
        async with OrthancClient() as orthanc:
            async def first_dataset(uid: str):
                orthanc_id = await orthanc.find_series(uid)
                if not orthanc_id:
                    raise ValueError(f"Series {uid} is missing from Orthanc")
                instance_ids = await orthanc.instance_ids(orthanc_id)
                if not instance_ids:
                    raise ValueError(f"Series {uid} has no instances")
                return await orthanc.read_instance(instance_ids[0], stop_before_pixels=True)

            seg_dataset = await first_dataset(ref.seg_series_uid)
            source_dataset = await first_dataset(series_uid)
        repair = await asyncio.to_thread(
            describe_legacy_repair,
            ref.seg_series_uid,
            seg_dataset,
            source_dataset,
            reference_path,
        )
        aligned_path = ws_sub(
            session_id, f"mask_cache/review_{ref.seg_series_uid}.nii.gz"
        )
        temporary_path = aligned_path.with_name(
            f"review_{ref.seg_series_uid}_{uuid4().hex}.nii.gz"
        )
        try:
            await asyncio.to_thread(
                align_and_binarize_mask,
                str(archived_path),
                reference_path,
                str(temporary_path),
                legacy_repair=repair,
            )
            os.replace(temporary_path, aligned_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return str(aligned_path)
    except Exception:  # noqa: BLE001 - fall back to the workspace copy
        logger.exception(
            "Could not read the archived mask for session=%s series=%s target=%s",
            session_id,
            series_uid,
            target_id or "",
        )
        return None


@router.get("/segmentation-review/mask/{session_id}/{series_uid}")
async def get_segmentation_mask(
    session_id: str,
    series_uid: str,
    target_id: str | None = Query(None),
):
    """
    Serve the agent-generated mask for a given session and series UID as an NRRD file.
    The mask is converted from NIfTI (.nii.gz) to NRRD on-the-fly so the existing
    frontend SegmentationReader.parseNrrdData() pipeline works without modification.

    A pending edit always wins. With MASK_SOURCE=orthanc, the newest archived
    SEG is aligned to the editor volume first; otherwise the workspace mask is
    used first. Either path falls back to the other when unavailable.
    """
    import io
    import numpy as np

    try:
        import nibabel as nib
        import nrrd
    except ImportError as e:
        raise HTTPException(status_code=500, detail=f"Required library missing: {e}")

    workspace_folder = str(workspace(session_id))

    # Search locations in priority order
    pair_id = f"{series_uid}__{target_id}" if target_id else series_uid
    artifact_key = artifact_key_for_pair(pair_id)
    legacy_name_is_safe = "/" not in pair_id and "\\" not in pair_id

    # An unsaved edit always wins: it is the work in progress, not yet archived.
    pending_edits = [
        str(ws_sub(session_id, f"temp_segmentations/seg_{artifact_key}_modified.nii.gz")),
    ]
    workspace_masks = [str(ws_sub(session_id, f"masks/{artifact_key}.nii.gz"))]
    if legacy_name_is_safe:
        pending_edits.append(
            str(ws_sub(session_id, f"temp_segmentations/seg_{pair_id}_modified.nii.gz"))
        )
        workspace_masks.append(str(ws_sub(session_id, f"masks/{pair_id}.nii.gz")))

    def _first_existing(paths):
        return next((path for path in paths if os.path.isfile(path)), None)

    nifti_path = _first_existing(pending_edits)
    archive_preferred = orthanc_first()
    if not nifti_path and archive_preferred:
        nifti_path = await _mask_from_orthanc(session_id, series_uid, target_id)
    if not nifti_path:
        nifti_path = _first_existing(workspace_masks)
    if not nifti_path and not archive_preferred:
        nifti_path = await _mask_from_orthanc(session_id, series_uid, target_id)

    # Failed or rejected segmentations may still be advertised in the QC grid
    # even though cleanup removed their mask file. Keep those entries editable
    # by initializing an empty mask from the source volume geometry.
    is_empty_fallback = False
    if not nifti_path:
        volume_path = await _local_volume_path(session_id, series_uid)
        if target_id and volume_path and os.path.isfile(volume_path):
            nifti_path = volume_path
            is_empty_fallback = True
            logger.warning(
                "Mask missing for session=%s, pair=%s; serving an empty editable "
                "mask using source volume geometry: %s",
                session_id,
                pair_id,
                volume_path,
            )
        else:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"No mask or source volume found for series '{series_uid}' "
                    f"in session '{session_id}'"
                ),
            )

    logger.info(
        "Serving %smask (as NRRD) for session=%s, series=%s, target=%s: %s",
        "empty editable " if is_empty_fallback else "",
        session_id,
        series_uid,
        target_id or "",
        nifti_path,
    )

    try:
        # Load NIfTI and convert to uint16 (matches what the frontend Uint16Array expects)
        nii = nib.load(nifti_path)
        data = (
            np.zeros(nii.shape[:3], dtype=np.uint16)
            if is_empty_fallback
            else nii.get_fdata().astype(np.uint16)
        )

        # Build NRRD header from NIfTI metadata to preserve orientation
        affine = nii.affine.copy()
        
        # NIfTI uses RAS+ coordinates, NRRD here uses LPS+.
        # We negate the X and Y axes in the affine matrix to map correctly.
        affine[0, :] = -affine[0, :]
        affine[1, :] = -affine[1, :]
        
        header = {
            "type": "uint16",
            "dimension": 3,
            "space": "left-posterior-superior",
            "sizes": list(data.shape),
            "space directions": [
                [float(affine[0,0]), float(affine[1,0]), float(affine[2,0])],
                [float(affine[0,1]), float(affine[1,1]), float(affine[2,1])],
                [float(affine[0,2]), float(affine[1,2]), float(affine[2,2])],
            ],
            "space origin": [float(affine[0,3]), float(affine[1,3]), float(affine[2,3])],
            "kinds": ["domain", "domain", "domain"],
            "endian": "little",
            "encoding": "gzip",
        }

        # Serialize to NRRD bytes in memory
        buffer = io.BytesIO()
        nrrd.write(buffer, data, header)
        nrrd_bytes = buffer.getvalue()

        return Response(
            content=nrrd_bytes,
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename={pair_id}.nrrd",
                "Cache-Control": "no-store",
                "X-Segmentation-Empty-Fallback": (
                    "true" if is_empty_fallback else "false"
                ),
            },
        )

    except Exception as e:
        logger.error(f"Failed to convert mask to NRRD: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to convert mask: {str(e)}")


@router.get("/segmentation-review/wait/{session_id}")
async def wait_for_review(session_id: str, timeout: int = 3600):
    """Long-poll until this session's segmentation review is submitted."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(1, timeout)
    waiter = review_events.setdefault(session_id, asyncio.Event())
    while True:
        try:
            batch = await population_review.current_batch(session_id)
        except population_review.DomainError as exc:
            raise domain_http_error(exc)
        if batch is not None and batch["review_status"] == "submitted":
            review_events.pop(session_id, None)
            return population_review.review_payload(batch)
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise HTTPException(status_code=408, detail="Review timeout")
        try:
            await asyncio.wait_for(
                asyncio.shield(waiter.wait()), timeout=min(REVIEW_RECHECK_SECONDS, remaining)
            )
            waiter.clear()
        except asyncio.TimeoutError:
            pass
