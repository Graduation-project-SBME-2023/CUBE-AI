
import os
import logging
import shutil
import threading
from typing import Any, Dict, List, Optional

from cachetools import cached, TTLCache
from monailabel.datastore.dicom import DICOMWebDatastore
from monailabel.datastore import dicom
from monailabel.datastore.utils.convert import dicom_to_nifti

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Shared TTL caches defined at class/module level.
#
# Why module-level instead of instance-level?
# -------------------------------------------
# In main.py, the datastore is created as a DICOMWebDatastore instance and
# then its class is hot-swapped:  datastore.__class__ = MyDICOMWebDatastore
# This means __init__ of MyDICOMWebDatastore is NEVER called, so instance
# attributes set in __init__ will be missing.
#
# By using module-level TTLCache objects the @cached decorator always finds
# a ready-made cache regardless of how the instance was created.
#
# TTL is set to 300 s (5 min).  Orthanc data changes infrequently during an
# active segmentation session, so this trades a tiny staleness risk for a
# large performance gain.
# ----------------------------------------------------------------------------
_LABELED_IMAGES_CACHE: TTLCache = TTLCache(maxsize=16, ttl=300)
_DOWNLOAD_LABELED_DATA_CACHE: TTLCache = TTLCache(maxsize=1, ttl=300)
_DOWNLOAD_LABELED_DATA_LOCK = threading.Lock()


class MyDICOMWebDatastore(DICOMWebDatastore):

    @staticmethod
    def _clear_partial_export(series_dir: str, nifti_path: str) -> None:
        """Remove a poisoned partial cache so the next attempt is truly fresh."""
        shutil.rmtree(series_dir, ignore_errors=True)
        try:
            if os.path.isfile(nifti_path):
                os.unlink(nifti_path)
        except OSError:
            logger.warning("Could not remove stale NIfTI export: %s", nifti_path)

    def _download_series_with_retry(
        self,
        series_id: str,
        series_dir: str,
        nifti_path: str,
        *,
        is_seg: bool,
    ) -> str:
        """Download and convert one DICOM series, repairing partial caches once."""
        last_error: Exception | None = None
        for attempt in range(1, 3):
            try:
                if not os.path.isdir(series_dir) or not os.listdir(series_dir):
                    dicom.dicom_web_download_series(
                        None,
                        series_id,
                        series_dir,
                        self._client,
                        self._fetch_by_frame,
                    )

                if not os.path.isdir(series_dir) or not os.listdir(series_dir):
                    raise FileNotFoundError(
                        f"DICOMweb returned no instances for series {series_id}"
                    )

                if not self._convert_to_nifti:
                    return series_dir

                if not os.path.isfile(nifti_path) or os.path.getsize(nifti_path) <= 0:
                    converted = dicom_to_nifti(series_dir, is_seg=is_seg)
                    if not converted or not os.path.isfile(converted):
                        raise RuntimeError(
                            f"DICOM-to-NIfTI conversion produced no file for {series_id}"
                        )
                    if os.path.realpath(converted) != os.path.realpath(nifti_path):
                        os.makedirs(os.path.dirname(nifti_path), exist_ok=True)
                        shutil.copy2(converted, nifti_path)
                        try:
                            os.unlink(converted)
                        except OSError:
                            logger.debug(
                                "Could not remove temporary converted file: %s",
                                converted,
                            )

                if os.path.getsize(nifti_path) <= 0:
                    raise RuntimeError(
                        f"DICOM-to-NIfTI conversion produced an empty file for {series_id}"
                    )
                return nifti_path
            except Exception as exc:
                last_error = exc
                logger.exception(
                    "Series export failed for %s (SEG=%s, attempt %s/2)",
                    series_id,
                    is_seg,
                    attempt,
                )
                self._clear_partial_export(series_dir, nifti_path)

        raise RuntimeError(
            f"Could not export DICOM series {series_id} after cache repair"
        ) from last_error

    def get_image_uri(self, image_id: str) -> str:
        """Export a source series while recovering from incomplete cache folders."""
        logger.info("Fetching source image series: %s", image_id)
        image_dir = os.path.realpath(
            os.path.join(self._datastore.image_path(), image_id)
        )
        image_nii_gz = os.path.realpath(
            os.path.join(self._datastore.image_path(), f"{image_id}.nii.gz")
        )
        image_was_exported = os.path.isfile(image_nii_gz) and os.path.getsize(image_nii_gz) > 0
        exported = self._download_series_with_retry(
            image_id,
            image_dir,
            image_nii_gz,
            is_seg=False,
        )

        if self._convert_to_nifti and not image_was_exported:
            # The converted file already lives at the datastore destination.
            # Calling LocalDatastore.add_image() here would copy the file onto
            # itself and raise SameFileError. Reconcile the datastore metadata
            # from disk, then enrich it with DICOM metadata when available.
            self.refresh()
            try:
                image_info = self._dicom_info(image_id)
            except Exception:
                logger.warning(
                    "Could not load DICOM metadata for %s; keeping minimal info",
                    image_id,
                    exc_info=True,
                )
                image_info = {"SeriesInstanceUID": image_id}
            try:
                self.update_image_info(image_id, image_info)
            except Exception:
                logger.warning(
                    "Could not update datastore metadata for %s",
                    image_id,
                    exc_info=True,
                )
        return exported

    # ------------------------------------------------------------------
    # Custom helper used by my_endpoints_datastore.py
    # ------------------------------------------------------------------
    def get_label_dir_dcm(self, image_id: str) -> str:
        """
        Retrieves the directory path for the DICOM image label.
        Downloads the series from DICOMweb if it does not already exist
        in the local cache directory.
        """
        logger.info("Fetching segmentation series: %s", image_id)
        # Preserve the path contract used by the custom datastore endpoint:
        # it expects a label-SeriesInstanceUID export under image_path().
        label_root = self._datastore.image_path()
        label_dir = os.path.realpath(os.path.join(label_root, image_id))
        label_nii_gz = os.path.realpath(
            os.path.join(label_root, f"{image_id}.nii.gz")
        )
        return self._download_series_with_retry(
            image_id,
            label_dir,
            label_nii_gz,
            is_seg=True,
        )

    # ------------------------------------------------------------------
    # status() — called by MONAILabelApp.info() via datastore.status()
    # ------------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        """
        Return a lightweight status dict without making any DICOMweb calls.

        The base LocalDatastore.status() calls list_images() and
        get_labeled_images(), both of which hit the DICOM server.  Even
        though those calls are cached, the cache key includes the instance
        id so the very first call per process is still slow.  Returning a
        static dict here eliminates that cold-start latency entirely and
        makes every subsequent /info/ request instantaneous.
        """
        return {
            "total": 0,
            "completed": 0,
            "label_tags": {},
        }

    # ------------------------------------------------------------------
    # info() — not normally called directly, but kept for safety
    # ------------------------------------------------------------------
    def info(self) -> Dict[str, Any]:
        """Lightweight info; avoids any DICOMweb round-trip."""
        return {
            "name": "MyDICOMWebDatastore",
            "description": "Custom DICOMWebDatastore (fast, no metadata pre-fetch)",
            "objects": {},
        }

    # ------------------------------------------------------------------
    # get_labeled_images() — cached with module-level TTLCache
    # ------------------------------------------------------------------
    @cached(cache=_LABELED_IMAGES_CACHE)
    def get_labeled_images(
        self,
        label_tag: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> List[str]:
        """
        Return a list of image series UIDs that have a corresponding SEG.

        The base-class version is already decorated with @cached, but that
        cache is bound to the *base class* dict entry.  After the class swap
        (``datastore.__class__ = MyDICOMWebDatastore``) method resolution
        finds *our* override first, so we must apply @cached here too.

        We use the module-level _LABELED_IMAGES_CACHE (TTL = 300 s) so the
        expensive DICOMweb walk only runs once per 5-minute window.
        """
        logger.info("get_labeled_images: cache miss — fetching from DICOMweb")
        return super().get_labeled_images(label_tag=label_tag, labels=labels)

    # ------------------------------------------------------------------
    # _download_labeled_data() — throttled with module-level TTLCache
    # ------------------------------------------------------------------
    def _download_labeled_data(self) -> None:
        """
        Synchronise locally-cached labels with the remote DICOMweb store.

        The base-class method iterates over every SEG series and calls
        retrieve_series_metadata() for each one — an O(n) network fan-out
        that can take many seconds.  It is not cached at all in the base
        class.

        We throttle it with a module-level TTLCache (TTL = 300 s).  A
        threading lock guarantees only one thread runs the sync at a time;
        all others skip immediately when a recent result is in cache.
        """
        cache_key = "singleton"
        if cache_key in _DOWNLOAD_LABELED_DATA_CACHE:
            logger.info("_download_labeled_data: cache hit — skipping DICOMweb sync")
            return

        with _DOWNLOAD_LABELED_DATA_LOCK:
            # Double-checked locking: another thread may have populated the
            # cache while we were waiting for the lock.
            if cache_key in _DOWNLOAD_LABELED_DATA_CACHE:
                logger.info("_download_labeled_data: cache hit (after lock) — skipping")
                return

            logger.info("_download_labeled_data: cache miss — running DICOMweb sync")
            super()._download_labeled_data()
            _DOWNLOAD_LABELED_DATA_CACHE[cache_key] = True