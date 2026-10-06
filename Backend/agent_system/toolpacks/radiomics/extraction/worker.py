"""PyRadiomics, in a worker process.

PyRadiomics can crash its process (out of memory, a segfault in a C
extension) or hang, so it never runs in the Agent System process. One worker
process is started per extraction and reused for every mask (starting one per
mask re-imported SimpleITK and PyRadiomics each time); it is replaced after
``MAX_TASKS_PER_WORKER`` masks to bound leaks, and killed and restarted when
a mask times out or crashes it. The event loop only awaits the result.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import multiprocessing
from collections.abc import Callable
from typing import Any

from ...common.settings import positive_float

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 600.0
SETTINGS_CHECK_TIMEOUT_S = 120.0
MAX_TASKS_PER_WORKER = 25


class ExtractionTimeout(TimeoutError):
    """One mask did not finish within its time budget."""


class ExtractionCrashed(RuntimeError):
    """The worker process died while measuring a mask."""


def timeout_s() -> float:
    """``FEATURE_EXTRACTION_TIMEOUT_SECONDS`` per mask, read when used."""
    return positive_float("FEATURE_EXTRACTION_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_S)


# --- run in the worker process --------------------------------------------

def _extract(image_path: str, mask_path: str, params_file: str) -> dict[str, Any]:
    from radiomics import featureextractor

    extractor = featureextractor.RadiomicsFeatureExtractor(params_file)
    result = extractor.execute(image_path, mask_path)
    return {str(key): value for key, value in result.items() if not str(key).startswith("diagnostics_")}


def _check_settings(params_file: str) -> None:
    from radiomics import featureextractor

    featureextractor.RadiomicsFeatureExtractor(params_file)


# --- the pool --------------------------------------------------------------

class ExtractorPool:
    """One reusable PyRadiomics worker process. Close it when done."""

    def __init__(self, max_tasks_per_worker: int = MAX_TASKS_PER_WORKER) -> None:
        self._max_tasks = max_tasks_per_worker
        self._executor: concurrent.futures.ProcessPoolExecutor | None = None

    def _pool(self) -> concurrent.futures.ProcessPoolExecutor:
        if self._executor is None:
            self._executor = concurrent.futures.ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn"),
                max_tasks_per_child=self._max_tasks,
            )
        return self._executor

    def _kill(self) -> None:
        """Stop the worker now: a hung or crashed one cannot finish on its own."""
        executor, self._executor = self._executor, None
        if executor is None:
            return
        terminate = getattr(executor, "terminate_workers", None)  # Python 3.14+
        if terminate is not None:
            terminate()
            return
        for process in list((getattr(executor, "_processes", None) or {}).values()):
            if process.is_alive():
                process.terminate()
        executor.shutdown(wait=False, cancel_futures=True)

    async def _run(self, fn: Callable[..., Any], *args: Any, timeout: float) -> Any:
        future = self._pool().submit(fn, *args)
        try:
            return await asyncio.wait_for(asyncio.wrap_future(future), timeout)
        except TimeoutError as exc:
            self._kill()
            raise ExtractionTimeout(f"no result within {timeout:.0f}s") from exc
        except concurrent.futures.process.BrokenProcessPool as exc:
            self._kill()
            raise ExtractionCrashed("the extraction worker died (out of memory or a crash)") from exc
        except asyncio.CancelledError:
            self._kill()
            raise

    async def check_settings(self, params_file: str) -> None:
        """Raise when PyRadiomics rejects the parameter file."""
        await self._run(_check_settings, params_file, timeout=SETTINGS_CHECK_TIMEOUT_S)

    async def extract(self, image_path: str, mask_path: str, params_file: str, timeout: float) -> dict[str, Any]:
        """The features of one mask (diagnostics left out)."""
        return await self._run(_extract, image_path, mask_path, params_file, timeout=timeout)

    async def close(self) -> None:
        executor, self._executor = self._executor, None
        if executor is not None:
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=True)


__all__ = ["ExtractionCrashed", "ExtractionTimeout", "ExtractorPool", "timeout_s"]
