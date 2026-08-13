from __future__ import annotations

import logging
import os
import shutil
import threading
import time
from typing import Any, Dict, List, Optional

from monailabel.datastore import dicom
from monailabel.datastore.dicom import DICOMWebDatastore
from monailabel.datastore.utils.convert import dicom_to_nifti
from pydicom.dataset import Dataset
from requests import RequestException

logger = logging.getLogger(__name__)


def _dicom_value(dataset: Dataset, keyword: str) -> Any | None:
    """Return a pydicom value across Dataset.get() API variations."""
    item = dataset.get(keyword)
    return getattr(item, "value", item)


class RadiologyDICOMWebDatastore(DICOMWebDatastore):
    """DICOMweb datastore with repairable exports and bounded metadata caching."""

    def __init__(self, *args, cache_ttl: float = 300.0, **kwargs):
        super().__init__(*args, **kwargs)
        self._radiology_cache_ttl = cache_ttl
        self._radiology_cache_lock = threading.RLock()
        self._labeled_images_cache: dict[
            tuple[Optional[str], tuple[str, ...]], tuple[float, list[str]]
        ] = {}
        self._status_cache: tuple[float, dict[str, Any]] | None = None
        self._labeled_data_sync_deadline = 0.0

    @staticmethod
    def _clear_partial_export(series_dir: str, nifti_path: str) -> None:
        """Remove a poisoned partial cache so the next attempt is fresh."""
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
            except (OSError, RequestException, RuntimeError, ValueError) as exc:
                last_error = exc
                logger.warning(
                    "Series export failed for %s (SEG=%s, attempt %s/2): %s",
                    series_id,
                    is_seg,
                    attempt,
                    exc,
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
        image_was_exported = (
            os.path.isfile(image_nii_gz) and os.path.getsize(image_nii_gz) > 0
        )
        exported = self._download_series_with_retry(
            image_id,
            image_dir,
            image_nii_gz,
            is_seg=False,
        )

        if self._convert_to_nifti and not image_was_exported:
            # The converted file already lives at the datastore destination;
            # refresh metadata instead of copying the file onto itself.
            self.refresh()
            try:
                image_info = self._dicom_info(image_id)
            except (
                IndexError,
                KeyError,
                OSError,
                RequestException,
                RuntimeError,
                TypeError,
                ValueError,
            ):
                logger.warning(
                    "Could not load DICOM metadata for %s; keeping minimal info",
                    image_id,
                    exc_info=True,
                )
                image_info = {"SeriesInstanceUID": image_id}
            try:
                self.update_image_info(image_id, image_info)
            except (KeyError, OSError, RuntimeError, ValueError):
                logger.warning(
                    "Could not update datastore metadata for %s",
                    image_id,
                    exc_info=True,
                )
        return exported

    def get_label_dir_dcm(self, image_id: str) -> str:
        """Download and export a DICOM SEG series used by custom endpoints."""
        logger.info("Fetching segmentation series: %s", image_id)
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

    def status(self) -> Dict[str, Any]:
        """Return UI status without resolving metadata for every remote SEG.

        MONAI's default implementation calls ``get_labeled_images()``, which
        performs one metadata request per SEG series. The UI only needs a
        lightweight summary here, so completion data comes from an existing
        remote-label cache when available and otherwise from local objects.
        """
        now = time.monotonic()
        with self._radiology_cache_lock:
            if self._status_cache and self._status_cache[0] > now:
                return dict(self._status_cache[1])

            tags: dict[str, int] = {}
            locally_completed = 0
            for item in self._datastore.objects.values():
                item_labels = getattr(item, "labels", {}) or {}
                if item_labels:
                    locally_completed += 1
                for tag in item_labels:
                    tags[tag] = tags.get(tag, 0) + 1

            cached_labels = self._labeled_images_cache.get((None, ()))
            completed = (
                len(cached_labels[1])
                if cached_labels and cached_labels[0] > now
                else locally_completed
            )
            result = {
                # list_images() is one cached QIDO request. It does not call
                # retrieve_series_metadata() for every segmentation.
                "total": len(self.list_images()),
                "completed": completed,
                "label_tags": tags,
            }
            self._status_cache = (
                time.monotonic() + self._radiology_cache_ttl,
                dict(result),
            )
            return result

    def info(self) -> Dict[str, Any]:
        """Describe this datastore using the same cached status information."""
        return {
            "name": type(self).__name__,
            "description": "Radiology DICOMweb datastore",
            "objects": self.status(),
        }

    def get_labeled_images(
        self,
        label_tag: Optional[str] = None,
        labels: Optional[List[str]] = None,
    ) -> List[str]:
        """Return remotely-labeled image IDs with a bounded instance cache."""
        key = (label_tag, tuple(labels or ()))
        now = time.monotonic()
        with self._radiology_cache_lock:
            cached_result = self._labeled_images_cache.get(key)
            if cached_result and cached_result[0] > now:
                return list(cached_result[1])
            logger.info("get_labeled_images: cache miss; fetching from DICOMweb")
            datasets = self._client.search_for_series(
                search_filters={"Modality": "SEG"}
            )
            image_ids = set(self.list_images())
            result: list[str] = []
            for raw_seg in datasets:
                try:
                    seg = Dataset.from_json(raw_seg)
                    study_id = _dicom_value(seg, "StudyInstanceUID")
                    series_id = _dicom_value(seg, "SeriesInstanceUID")
                    if not study_id or not series_id:
                        raise ValueError("SEG is missing its study or series UID")

                    metadata = self._client.retrieve_series_metadata(
                        str(study_id),
                        str(series_id),
                    )
                    if not metadata:
                        raise ValueError("DICOMweb returned no SEG metadata")

                    seg_metadata = Dataset.from_json(metadata[0])
                    references = _dicom_value(
                        seg_metadata,
                        "ReferencedSeriesSequence",
                    )
                    if not references:
                        raise ValueError("SEG has no ReferencedSeriesSequence")

                    referenced_id = _dicom_value(
                        references[0],
                        "SeriesInstanceUID",
                    )
                    if not referenced_id:
                        raise ValueError(
                            "SEG reference is missing its SeriesInstanceUID"
                        )
                    referenced_id = str(referenced_id)
                except (
                    AttributeError,
                    IndexError,
                    KeyError,
                    RequestException,
                    TypeError,
                    ValueError,
                ) as exc:
                    logger.warning(
                        "Ignoring malformed or unavailable DICOM SEG metadata: %s",
                        exc,
                    )
                    continue

                if referenced_id in image_ids:
                    result.append(referenced_id)
                else:
                    logger.warning(
                        "Ignoring SEG %s whose source is outside the image filter",
                        series_id,
                    )
            self._labeled_images_cache[key] = (
                time.monotonic() + self._radiology_cache_ttl,
                list(result),
            )
            return result

    def _download_labeled_data(self) -> None:
        """Synchronize remote labels at most once per cache window."""
        now = time.monotonic()
        with self._radiology_cache_lock:
            if self._labeled_data_sync_deadline > now:
                logger.info("_download_labeled_data: recent sync; skipping")
                return

            logger.info("_download_labeled_data: running DICOMweb sync")
            super()._download_labeled_data()
            self._labeled_data_sync_deadline = (
                time.monotonic() + self._radiology_cache_ttl
            )

    def save_label(
        self,
        image_id: str,
        label_filename: str,
        label_tag: str,
        label_info: Dict[str, Any],
        label_id: str = "",
    ) -> str:
        result = super().save_label(
            image_id,
            label_filename,
            label_tag,
            label_info,
            label_id,
        )
        with self._radiology_cache_lock:
            self._labeled_images_cache.clear()
            self._status_cache = None
            self._labeled_data_sync_deadline = 0.0
        return result
