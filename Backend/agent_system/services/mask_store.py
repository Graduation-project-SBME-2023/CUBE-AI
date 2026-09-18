"""The one place masks are read from and written to.

Today a mask exists in three forms that can silently disagree:

* the DICOM SEG in Orthanc, which is the permanent archive;
* a NIfTI in the session workspace, produced by asking MONAI Label to re-export
  the SEG through its own converter;
* a clinician's edit, which is copied over the workspace NIfTI and **never
  reaches Orthanc at all**.

So the mask a study's radiomics were computed from can be one that does not
exist in the PACS.  This module makes Orthanc the source of truth: masks are
published there, read back from there, and the local copy becomes a disposable
per-session cache under ``cache/masks`` that a session teardown can delete
without losing anything.

Consumers should call :meth:`MaskStore.ensure_local` instead of assembling a
path under ``data/masks``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import pydicom
import SimpleITK as sitk

from clients.backend_client import BackendClient
from clients.orthanc_client import OrthancClient, OrthancError
from imaging.dicom_seg import (
    SEG_UID_ROOT,
    build_seg_dataset,
    deterministic_seg_uids,
    manual_series_description,
    nifti_to_mask,
    seg_to_mask_array,
    write_mask_nifti,
)
from imaging.geometry import (
    geometries_match,
    geometry_from_dicom_series,
    sitk_image_from_array,
    sort_dicom_series,
    volume_array_from_dicom_series,
)
from storage_paths import HYPOTHESES_DIR, ws_sub

# The ingestion sync-webhook call is not ingestion-specific -- it is just
# "tell the Application API a series exists now instead of waiting for
# Orthanc's stable-series hook" -- so it is reused here rather than
# duplicated.  ingestion.service does not import this module, so there is no
# import cycle.
from ingestion.service import sync_series_to_database as _sync_segmentation_now

logger = logging.getLogger(__name__)

MASK_CACHE = "mask_cache"
VOLUME_CACHE = "volume_cache"
_DEFAULT_CACHE_TTL_HOURS = 24.0

LOCAL_FIRST = "local"
ORTHANC_FIRST = "orthanc"


def mask_source() -> str:
    """Which copy of a mask consumers should read.

    ``local`` keeps today's behaviour -- the NIfTI in the session workspace.
    ``orthanc`` reads the archived DICOM SEG instead.  Both paths stay in place
    until the two have been proved identical on real studies, so switching is a
    one-variable change and reverting costs nothing.
    """
    value = str(os.environ.get("MASK_SOURCE", LOCAL_FIRST)).strip().lower()
    return ORTHANC_FIRST if value == ORTHANC_FIRST else LOCAL_FIRST


def orthanc_first() -> bool:
    return mask_source() == ORTHANC_FIRST


def cache_ttl_seconds() -> float:
    try:
        hours = float(os.environ.get("MASK_CACHE_TTL_HOURS", _DEFAULT_CACHE_TTL_HOURS))
    except (TypeError, ValueError):
        hours = _DEFAULT_CACHE_TTL_HOURS
    return max(0.0, hours) * 3600.0


@dataclass(frozen=True)
class SegRef:
    """A mask, identified the way DICOM identifies it."""

    seg_series_uid: str
    volume_uid: str
    target_label: str | None = None
    method: str = "auto"
    created_at: datetime | None = None
    orthanc_series_id: str | None = None

    @property
    def is_manual(self) -> bool:
        return str(self.method or "").lower() == "manual"


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _to_ref(record: dict[str, Any]) -> SegRef | None:
    seg_uid = record.get("label_series_instance_uid")
    volume_uid = record.get("volume_series_instance_uid")
    if not seg_uid or not volume_uid:
        return None
    return SegRef(
        seg_series_uid=str(seg_uid),
        volume_uid=str(volume_uid),
        target_label=record.get("target_label"),
        method=str(record.get("method") or "auto"),
        created_at=_parse_timestamp(record.get("created_at")),
        orthanc_series_id=record.get("orthanc_series_id"),
    )


def _newest(refs: Sequence[SegRef]) -> SegRef | None:
    if not refs:
        return None
    # Records predating the created_at column sort last rather than crashing.
    return max(refs, key=lambda ref: (ref.created_at is not None, ref.created_at or datetime.min))


def _is_own_seg(ref: SegRef) -> bool:
    """Was this SEG written by :mod:`imaging.dicom_seg`?

    Pushing a mask to MONAI Label's datastore makes MONAI re-export its own copy
    of the same segmentation into Orthanc, so a volume ends up with two SEG
    series for one target. That copy is written by a different encoder: it omits
    empty frames and does not declare that spatial locations are preserved, so
    highdicom refuses to index it against the source instances and the read
    fails outright. Since it also lands *later* than ours, a plain "newest wins"
    rule picks the unreadable duplicate every time -- which silently sent every
    lookup back to the workspace copy that ``MASK_SOURCE=orthanc`` exists to stop
    using.

    Our own UIDs come from :data:`SEG_UID_ROOT`, so the prefix identifies them
    without a further Orthanc round-trip.
    """
    return str(ref.seg_series_uid or "").startswith(SEG_UID_ROOT)


class MaskStore:
    """Read and write masks through Orthanc, cached per session."""

    def __init__(
        self,
        orthanc: OrthancClient | None = None,
        backend: type[BackendClient] | Any = BackendClient,
    ) -> None:
        self._orthanc = orthanc or OrthancClient()
        self._owns_orthanc = orthanc is None
        self._backend = backend

    async def close(self) -> None:
        if self._owns_orthanc:
            await self._orthanc.close()

    async def __aenter__(self) -> "MaskStore":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    async def list_masks(
        self,
        volume_uid: str,
        *,
        target_label: str | None = None,
    ) -> list[SegRef]:
        records = await self._backend.get_segmentations_by_volume(volume_uid)
        refs = [ref for ref in (_to_ref(record) for record in records) if ref]
        if target_label:
            wanted = str(target_label).strip().casefold()
            refs = [
                ref
                for ref in refs
                if str(ref.target_label or "").strip().casefold() == wanted
            ]
        return refs

    async def resolve(
        self,
        volume_uid: str,
        *,
        target_label: str | None = None,
    ) -> SegRef | None:
        """Pick the mask that should be used for this volume and target.

        A clinician's edit always wins over the model's output. Within one kind,
        a SEG this system wrote wins over a re-export by another tool (see
        :func:`_is_own_seg`), and only then does the newest win.
        """
        refs = await self.list_masks(volume_uid, target_label=target_label)
        manual = [ref for ref in refs if ref.is_manual]
        for candidates in (manual, refs):
            own = [ref for ref in candidates if _is_own_seg(ref)]
            chosen = _newest(own) or _newest(candidates)
            if chosen is not None:
                return chosen
        return None

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def cache_path(self, session_id: str, seg_series_uid: str) -> Path:
        return ws_sub(session_id, f"{MASK_CACHE}/{seg_series_uid}.nii.gz")

    async def ensure_local(self, session_id: str, ref: SegRef) -> Path:
        """Return a NIfTI path for this mask, fetching it from Orthanc if absent.

        The file is a cache: deleting it costs a re-download and nothing else.
        """
        target = self.cache_path(session_id, ref.seg_series_uid)
        if target.is_file():
            return target

        mask, geometry = await self.read_mask(ref)
        await asyncio.to_thread(write_mask_nifti, mask, geometry, target)
        logger.info(
            "Materialised mask %s for session %s (%s foreground voxels)",
            ref.seg_series_uid,
            session_id,
            int(mask.sum()),
        )
        return target

    def volume_cache_path(self, session_id: str, volume_uid: str) -> Path:
        return ws_sub(session_id, f"{VOLUME_CACHE}/{volume_uid}.nii.gz")

    async def ensure_local_volume(self, session_id: str, volume_uid: str) -> Path:
        """Return a NIfTI path for a source volume, rebuilding it if absent.

        The workspace copy of a volume is the largest thing a session leaves
        behind and is reproducible from the DICOM in Orthanc, so it is a cache
        like the masks rather than something to keep. Rebuilding is not free --
        it reads every instance in the series -- so it happens only when the
        copy is actually gone.

        Geometry comes from the DICOM, which is also what publish() validates a
        mask against, so a mask saved against this file lines up with the
        archive by construction.
        """
        target = self.volume_cache_path(session_id, volume_uid)
        if target.is_file():
            return target

        datasets = await self._source_series(volume_uid)
        array = await asyncio.to_thread(volume_array_from_dicom_series, datasets)
        geometry = geometry_from_dicom_series(datasets)

        def _write() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            sitk.WriteImage(sitk_image_from_array(array, geometry), str(target), useCompression=True)

        await asyncio.to_thread(_write)
        logger.info(
            "Rebuilt volume %s for session %s from Orthanc (%s slices)",
            volume_uid,
            session_id,
            array.shape[0],
        )
        return target

    async def read_mask(self, ref: SegRef) -> tuple[np.ndarray, Any]:
        """Fetch a SEG and its source series from Orthanc and decode the mask."""
        source = await self._source_series(ref.volume_uid)
        seg_datasets = await self._orthanc.read_series_datasets(ref.seg_series_uid)
        if not seg_datasets:
            raise OrthancError(f"Segmentation {ref.seg_series_uid} has no instances")

        mask = await asyncio.to_thread(seg_to_mask_array, seg_datasets[0], source)
        return mask, geometry_from_dicom_series(source)

    async def _source_series(self, volume_uid: str) -> list[pydicom.Dataset]:
        datasets = await self._orthanc.read_series_datasets(volume_uid)
        if not datasets:
            raise OrthancError(f"Source series {volume_uid} has no instances")
        return sort_dicom_series(datasets)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def publish(
        self,
        *,
        volume_uid: str,
        target_label: str,
        mask_path: Path | None = None,
        mask_array: np.ndarray | None = None,
        method: str = "manual",
        model_name: str | None = None,
        derived_from: str | None = None,
        session_id: str | None = None,
        entropy: Sequence[str] | None = None,
    ) -> SegRef:
        """Encode a mask as DICOM SEG and store it in Orthanc.

        A published edit is a *new* series that references the same source
        images, not a replacement: DICOM has no update, and overwriting would
        destroy the model output the edit was based on.
        """
        if (mask_path is None) == (mask_array is None):
            raise ValueError("provide exactly one of mask_path or mask_array")

        source = await self._source_series(volume_uid)
        source_geometry = geometry_from_dicom_series(source)

        if mask_array is not None:
            mask = np.asarray(mask_array)
        else:
            mask, mask_geometry = await asyncio.to_thread(nifti_to_mask, Path(mask_path))
            if not geometries_match(source_geometry, mask_geometry):
                # Publishing a mask that does not sit on the source grid would
                # archive a segmentation of the wrong voxels.
                raise ValueError(
                    f"Mask at {mask_path} does not share the geometry of series {volume_uid}"
                )

        seed = list(entropy) if entropy else [volume_uid, target_label, method]
        if derived_from:
            seed.append(derived_from)
        # The mask content is always part of the identity.  Without it a second
        # edit of the same target would derive the UID of the first, be treated
        # as "already published", and never reach the archive -- while an
        # unchanged re-publish correctly stays a no-op.
        seed.append(_content_digest(mask))
        series_uid, sop_uid = deterministic_seg_uids(*seed)

        existing = await self._orthanc.find_series(series_uid)
        if existing:
            # Deterministic UIDs make a repeat publish a no-op rather than a
            # duplicate mask.
            logger.info("Segmentation %s already exists in Orthanc; reusing it", series_uid)
            return SegRef(
                seg_series_uid=series_uid,
                volume_uid=volume_uid,
                target_label=target_label,
                method=method,
                orthanc_series_id=existing,
            )

        description = (
            manual_series_description(target_label)
            if method == "manual"
            else f"Segmentation - {target_label}"[:64]
        )
        segmentation = await asyncio.to_thread(
            build_seg_dataset,
            mask_zyx=mask,
            source_images=source,
            segment_label=target_label,
            series_instance_uid=series_uid,
            sop_instance_uid=sop_uid,
            series_description=description,
            method="manual" if method == "manual" else "auto",
            model_name=model_name,
            derived_from_seg_uid=derived_from,
        )

        await self._orthanc.upload_dataset(segmentation)
        orthanc_series_id = await self._orthanc.find_series(series_uid)
        logger.info(
            "Published %s segmentation %s for volume %s", method, series_uid, volume_uid
        )

        if orthanc_series_id:
            # Orthanc's own Lua hook only fires once the series has been
            # idle for StableAge (30s by default), which is a real blind
            # window: resolve() called right after this publish -- for
            # instance when the QC screen reloads immediately after a save --
            # would not see the new mask yet. Ask the Application API to
            # register it now instead of waiting.
            synced = await _sync_segmentation_now(orthanc_series_id, series_uid, "SEG")
            if not synced:
                logger.warning(
                    "Direct sync for segmentation %s did not succeed; it will "
                    "still be picked up by Orthanc's stable-series hook",
                    series_uid,
                )

        return SegRef(
            seg_series_uid=series_uid,
            volume_uid=volume_uid,
            target_label=target_label,
            method=method,
            orthanc_series_id=orthanc_series_id,
        )

    # ------------------------------------------------------------------
    # Cache lifecycle
    # ------------------------------------------------------------------

    async def clear_session(self, session_id: str) -> int:
        """Drop everything this session holds that Orthanc can reproduce.

        Masks and source volumes both exist in the archive, so the workspace
        copies are caches: deleting them costs a rebuild and nothing else.
        Reports, scratchpads and a clinician's saved edits are not reproducible
        and are left alone.
        """
        return await asyncio.to_thread(_remove_reproducible_copies, session_id)

    @staticmethod
    def sweep_stale_caches(ttl_seconds: float | None = None) -> int:
        """Remove caches left behind by sessions that never finished.

        A safety net for a worker that dies mid-run, which never reaches the
        teardown that would have cleared them. Only cache/ is swept: it holds
        copies of what Orthanc stores, so the worst case is a rebuild. The
        workspace's own volumes/ is left alone here -- an unfinished run may
        still be using it.
        """
        limit = cache_ttl_seconds() if ttl_seconds is None else ttl_seconds
        if limit <= 0 or not HYPOTHESES_DIR.is_dir():
            return 0

        cutoff = time.time() - limit
        removed = 0
        for workspace_dir in HYPOTHESES_DIR.glob("workspace_*"):
            for name in ("masks", "volumes"):
                cache_dir = workspace_dir / "cache" / name
                if not cache_dir.is_dir():
                    continue
                try:
                    if cache_dir.stat().st_mtime >= cutoff:
                        continue
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    removed += 1
                except OSError:
                    continue
        if removed:
            logger.info("Swept %s stale cache directories", removed)
        return removed


def _content_digest(mask: np.ndarray) -> str:
    """A short, stable fingerprint of a binary mask."""
    binary = np.ascontiguousarray((np.asarray(mask) > 0).astype(np.uint8))
    return hashlib.sha256(binary.tobytes()).hexdigest()[:32]


# Workspace trees that only ever hold copies of what Orthanc stores. "volumes"
# is the converted source imaging and is by far the largest; the two caches are
# what materialising from the archive writes.
REPRODUCIBLE_SUBTREES = (MASK_CACHE, VOLUME_CACHE, "volumes")


def _remove_reproducible_copies(session_id: str) -> int:
    removed = 0
    for name in REPRODUCIBLE_SUBTREES:
        directory = ws_sub(session_id, name)
        if not directory.is_dir():
            continue
        removed += sum(1 for _ in directory.glob("*.nii.gz"))
        shutil.rmtree(directory, ignore_errors=True)
    if removed:
        logger.info(
            "Cleared %s reproducible file(s) for session %s", removed, session_id
        )
    return removed
