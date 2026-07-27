
import os
import logging
import threading
from typing import Any, Dict, List, Optional

from cachetools import cached, TTLCache
from monailabel.datastore.dicom import DICOMWebDatastore
from monailabel.datastore import dicom

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

    # ------------------------------------------------------------------
    # Custom helper used by my_endpoints_datastore.py
    # ------------------------------------------------------------------
    def get_label_dir_dcm(self, image_id: str) -> str:
        """
        Retrieves the directory path for the DICOM image label.
        Downloads the series from DICOMweb if it does not already exist
        in the local cache directory.
        """
        logger.info(f"Fetching Image ID: {image_id}")
        image_dir = os.path.realpath(os.path.join(self._datastore.image_path(), image_id))
        logger.info(f"Image Directory Cache: {image_dir}")

        if not os.path.exists(image_dir) or not os.listdir(image_dir):
            dicom.dicom_web_download_series(None, image_id, image_dir, self._client, self._fetch_by_frame)

        if not self._convert_to_nifti:
            return image_dir

        image_nii_gz = os.path.realpath(os.path.join(self._datastore.image_path(), f"{image_id}.nii.gz"))
        return image_nii_gz

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
