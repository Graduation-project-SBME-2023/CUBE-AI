from __future__ import annotations

import threading
from types import SimpleNamespace

from pydicom.dataset import Dataset

from radiology_app.datastore.dicomweb import (
    RadiologyDICOMWebDatastore,
    _dicom_value,
)


def test_dicom_value_handles_scalar_and_sequence_values() -> None:
    reference = Dataset()
    reference.SeriesInstanceUID = "1.2.3.4"

    metadata = Dataset()
    metadata.ReferencedSeriesSequence = [reference]

    references = _dicom_value(metadata, "ReferencedSeriesSequence")

    assert references is not None
    assert _dicom_value(references[0], "SeriesInstanceUID") == "1.2.3.4"


def test_dicom_value_returns_none_for_missing_keyword() -> None:
    assert _dicom_value(Dataset(), "ReferencedSeriesSequence") is None


def test_status_does_not_load_remote_seg_metadata() -> None:
    datastore = object.__new__(RadiologyDICOMWebDatastore)
    datastore._radiology_cache_ttl = 300.0
    datastore._radiology_cache_lock = threading.RLock()
    datastore._status_cache = None
    datastore._labeled_images_cache = {}
    datastore._datastore = SimpleNamespace(
        objects={
            "image-1": SimpleNamespace(labels={"final": object()}),
            "image-2": SimpleNamespace(labels={}),
        }
    )
    datastore.list_images = lambda: ["image-1", "image-2"]

    def fail_if_called(*args, **kwargs):
        raise AssertionError("status must not resolve remote SEG metadata")

    datastore.get_labeled_images = fail_if_called

    assert datastore.status() == {
        "total": 2,
        "completed": 1,
        "label_tags": {"final": 1},
    }
