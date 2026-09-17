"""The single Orthanc entry point for the agent system.

Before this module the agent system reached Orthanc from three places with two
different HTTP libraries and no credentials, while the Application API used
aiohttp with Basic auth.  Orthanc currently runs with
``AuthenticationEnabled: false``, so the unauthenticated calls happen to work --
they would all break the moment authentication is enabled.  Everything here
sends credentials when ``ORTHANC_USER``/``ORTHANC_PASSWORD`` are configured and
behaves exactly as before when they are not.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Any, Awaitable, Callable, TypeVar

import httpx
import pydicom

logger = logging.getLogger(__name__)

DEFAULT_ORTHANC_URL = "http://localhost:8043"
QUICK_TIMEOUT = 30.0
TRANSFER_TIMEOUT = 120.0

# A large series is hundreds of sequential requests (one per slice, on both the
# upload and the read side). Orthanc closing one idle keep-alive connection, or
# briefly saturating under several concurrent uploads, used to fail the entire
# job on the first transient hiccup. Three attempts with backoff is the same
# tolerance the rest of the codebase already gives a flaky network call (see
# segmentation/utils.py's trigger_download).
_RETRY_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 0.5

T = TypeVar("T")


class OrthancError(RuntimeError):
    """Raised when Orthanc rejects a request or returns something unusable."""


def _configured_auth() -> tuple[str, str] | None:
    user = os.environ.get("ORTHANC_USER")
    password = os.environ.get("ORTHANC_PASSWORD")
    if user and password:
        return (user, password)
    return None


class OrthancClient:
    """Async REST client for Orthanc.

    The client is deliberately thin: it speaks Orthanc's REST vocabulary and
    returns bytes or ``pydicom`` datasets.  Anything that interprets pixels or
    geometry belongs in :mod:`imaging`, not here.
    """

    def __init__(
        self,
        base_url: str | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("ORTHANC_URL", DEFAULT_ORTHANC_URL)
        ).rstrip("/")
        self._client = client or httpx.AsyncClient(
            auth=_configured_auth(),
            timeout=TRANSFER_TIMEOUT,
        )
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> "OrthancClient":
        return self

    async def __aexit__(self, *_exc_info: object) -> None:
        await self.close()

    async def _with_retry(self, description: str, call: Callable[[], Awaitable[T]]) -> T:
        """Retry a single Orthanc call across transient network failures.

        Only ``httpx.TransportError`` (dropped/reset/refused connections) and
        5xx responses are retried -- a 4xx means Orthanc understood and
        rejected the request, which a retry cannot fix.
        """
        last_error: Exception | None = None
        for attempt in range(1, _RETRY_ATTEMPTS + 1):
            try:
                return await call()
            except httpx.TransportError as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code < 500:
                    raise
                last_error = exc
            if attempt < _RETRY_ATTEMPTS:
                logger.warning(
                    "Orthanc call %s failed (attempt %s/%s): %s; retrying",
                    description,
                    attempt,
                    _RETRY_ATTEMPTS,
                    last_error,
                )
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
        assert last_error is not None  # the loop always sets it before exhausting
        raise last_error

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    async def _find(self, level: str, query: dict[str, str]) -> list[str]:
        async def _call() -> list[str]:
            response = await self._client.post(
                f"{self.base_url}/tools/find",
                json={"Level": level, "Query": query},
                timeout=QUICK_TIMEOUT,
            )
            response.raise_for_status()
            found = response.json()
            return [str(item) for item in found] if isinstance(found, list) else []

        return await self._with_retry(f"find {level}", _call)

    async def find_series(self, series_instance_uid: str) -> str | None:
        """Orthanc id for a DICOM SeriesInstanceUID, or None when absent."""
        found = await self._find("Series", {"SeriesInstanceUID": series_instance_uid})
        return found[0] if found else None

    async def find_study(self, study_instance_uid: str) -> str | None:
        """Orthanc id for a DICOM StudyInstanceUID, or None when absent."""
        found = await self._find("Study", {"StudyInstanceUID": study_instance_uid})
        return found[0] if found else None

    async def series_info(self, orthanc_series_id: str) -> dict[str, Any]:
        async def _call() -> dict[str, Any]:
            response = await self._client.get(
                f"{self.base_url}/series/{orthanc_series_id}",
                timeout=QUICK_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()

        return await self._with_retry(f"series_info {orthanc_series_id}", _call)

    async def study_info(self, orthanc_study_id: str) -> dict[str, Any]:
        async def _call() -> dict[str, Any]:
            response = await self._client.get(
                f"{self.base_url}/studies/{orthanc_study_id}",
                timeout=QUICK_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()

        return await self._with_retry(f"study_info {orthanc_study_id}", _call)

    async def instance_info(self, instance_id: str) -> dict[str, Any]:
        async def _call() -> dict[str, Any]:
            response = await self._client.get(
                f"{self.base_url}/instances/{instance_id}",
                timeout=QUICK_TIMEOUT,
            )
            response.raise_for_status()
            return response.json()

        return await self._with_retry(f"instance_info {instance_id}", _call)

    async def instance_ids(self, orthanc_series_id: str) -> list[str]:
        info = await self.series_info(orthanc_series_id)
        return [str(item) for item in (info.get("Instances") or [])]

    # ------------------------------------------------------------------
    # Transfer
    # ------------------------------------------------------------------

    async def instance_file(self, instance_id: str) -> bytes:
        async def _call() -> bytes:
            response = await self._client.get(
                f"{self.base_url}/instances/{instance_id}/file",
                timeout=TRANSFER_TIMEOUT,
            )
            response.raise_for_status()
            return response.content

        return await self._with_retry(f"instance_file {instance_id}", _call)

    async def read_instance(
        self,
        instance_id: str,
        *,
        stop_before_pixels: bool = False,
    ) -> pydicom.Dataset:
        blob = await self.instance_file(instance_id)
        return pydicom.dcmread(io.BytesIO(blob), stop_before_pixels=stop_before_pixels)

    async def read_series_datasets(
        self,
        series_instance_uid: str,
        *,
        stop_before_pixels: bool = False,
    ) -> list[pydicom.Dataset]:
        """Every instance of a series, as datasets.

        Callers that need physical ordering must pass the result through
        ``imaging.geometry.sort_dicom_series`` -- Orthanc does not promise any
        particular instance order.
        """
        orthanc_series_id = await self.find_series(series_instance_uid)
        if not orthanc_series_id:
            raise OrthancError(f"Series {series_instance_uid} was not found in Orthanc")

        instance_ids = await self.instance_ids(orthanc_series_id)
        if not instance_ids:
            raise OrthancError(f"Series {series_instance_uid} has no Orthanc instances")

        datasets: list[pydicom.Dataset] = []
        for instance_id in instance_ids:
            datasets.append(
                await self.read_instance(
                    instance_id, stop_before_pixels=stop_before_pixels
                )
            )
        return datasets

    async def upload_dicom(self, blob: bytes) -> str:
        """Store one DICOM instance and return its Orthanc id.

        Retrying a failed upload is safe here specifically because every
        instance this codebase writes carries a deterministic, content-derived
        SOPInstanceUID (see ingestion.provenance and imaging.dicom_seg): if
        Orthanc actually stored the instance before the connection dropped, a
        retry with the identical bytes resolves to the same instance instead
        of creating a duplicate.
        """

        async def _call() -> str:
            response = await self._client.post(
                f"{self.base_url}/instances",
                headers={"Content-Type": "application/dicom"},
                content=blob,
                timeout=TRANSFER_TIMEOUT,
            )
            if response.status_code >= 500:
                response.raise_for_status()
            if response.status_code not in (200, 201):
                raise OrthancError(
                    f"Orthanc rejected the upload with HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )
            payload = response.json()
            instance_id = payload.get("ID")
            if not instance_id:
                raise OrthancError(f"Orthanc upload response carried no instance id: {payload}")
            return str(instance_id)

        return await self._with_retry("upload_dicom", _call)

    async def upload_dataset(self, dataset: pydicom.Dataset) -> str:
        """Serialise a dataset in memory and store it.

        The dataset must already carry ``file_meta`` and a preamble; ``save_as``
        is called without version-specific format flags so this works the same
        on pydicom 2.x and 3.x.
        """
        if not getattr(dataset, "file_meta", None):
            raise OrthancError("Dataset has no file_meta; cannot serialise a DICOM file")

        buffer = io.BytesIO()
        dataset.save_as(buffer)
        buffer.seek(0)
        return await self.upload_dicom(buffer.read())

    async def delete_series(self, orthanc_series_id: str) -> bool:
        """Remove a series; used to roll back a failed ingestion.

        Retried like every other call here: a cleanup call that fails on a
        transient network error is exactly how an incomplete series gets left
        behind for Orthanc's own stable-series hook to pick up and register as
        if it were a real study.
        """

        async def _call() -> bool:
            response = await self._client.delete(
                f"{self.base_url}/series/{orthanc_series_id}",
                timeout=QUICK_TIMEOUT,
            )
            if response.status_code == 404:
                return True
            if response.status_code >= 500:
                response.raise_for_status()
            if response.status_code not in (200, 204):
                logger.warning(
                    "Failed to delete Orthanc series %s: HTTP %s",
                    orthanc_series_id,
                    response.status_code,
                )
                return False
            return True

        try:
            return await self._with_retry(f"delete_series {orthanc_series_id}", _call)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            logger.warning(
                "Failed to delete Orthanc series %s after retries: %s",
                orthanc_series_id,
                exc,
            )
            return False
