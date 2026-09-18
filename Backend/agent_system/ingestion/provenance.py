"""Identity and provenance for DICOM series synthesised from volume files.

Four independent layers mark a converted series, so "was this converted?" can be
answered by whichever one the asking tool can see:

1. **A dedicated UID branch.**  ``...511.4.`` is reserved for ingested images the
   same way ``...511.3.`` is already used for CUBE.AI segmentations.  A prefix
   test answers the question in SQL or in an Orthanc query, without reading a
   single tag.
2. **Standard DICOM tags.**  ``ImageType`` starts with ``DERIVED`` and
   ``ConversionType`` is set, which is what any third-party viewer or PACS reads.
3. **A private block.**  Source filename, checksum, converter version and the
   voxel-to-LPS affine that was actually used, for auditing a conversion after
   the fact without asking the uploader for the file again.
4. **Research identity tags.**  ``PatientIdentityRemoved`` plus the Clinical
   Trial module, so a synthesised identity is never mistaken for a real one.

Every UID is derived from the file checksum, so re-uploading the same file
produces the same UIDs, Orthanc rejects it as a duplicate, and no phantom
patient appears.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Sequence

import numpy as np
import pydicom
from pydicom.uid import generate_uid

INGEST_VERSION = "cubeai-ingest-v1"

# Sibling of the segmentation branch in services.py, which uses ...511.3.
CUBEAI_UID_ROOT = "1.2.826.0.1.3680043.10.511."
NIFTI_UID_ROOT = CUBEAI_UID_ROOT + "4."

PRIVATE_GROUP = 0x0099
PRIVATE_CREATOR = "CUBEAI"

DERIVATION_DESCRIPTION = "Converted from a volume file by CUBE.AI ingestion"
CLINICAL_TRIAL_PROTOCOL_ID = "CUBEAI-RESEARCH"

# SimpleITK reads all of these with the same call and reports geometry the same
# way, so supporting them costs nothing beyond this list.
SUPPORTED_EXTENSIONS = (".nii.gz", ".nii", ".nrrd", ".nhdr", ".mha", ".mhd")

# Shown in the OHIF study list, so it reads as a format name rather than a
# file extension.
SOURCE_LABELS = {
    ".nii.gz": "NIfTI",
    ".nii": "NIfTI",
    ".nrrd": "NRRD",
    ".nhdr": "NRRD",
    ".mha": "MetaImage",
    ".mhd": "MetaImage",
}

SOP_CLASS_BY_MODALITY = {
    "CT": "1.2.840.10008.5.1.4.1.1.2",
    "MR": "1.2.840.10008.5.1.4.1.1.4",
    "PT": "1.2.840.10008.5.1.4.1.1.128",
    # DICOM has no "OT Image Storage"; Secondary Capture is where an
    # unidentified modality belongs.  The cost is real and the caller must be
    # told: the Secondary Capture IOD has no Image Plane module, so highdicom
    # may refuse to build a DICOM SEG against these images and the pipeline can
    # stop at segmentation.  OT is for viewing and archiving, not for analysis.
    "OT": "1.2.840.10008.5.1.4.1.1.7",
}

# Modalities whose SOP class carries the Image Plane module, and can therefore
# act as ``source_images`` when a DICOM SEG is created.
SEGMENTABLE_MODALITIES = frozenset({"CT", "MR", "PT"})

EXPLICIT_VR_LITTLE_ENDIAN = "1.2.840.10008.1.2.1"


def normalise_extension(filename: str) -> str:
    """Return the supported extension of ``filename``, longest match first."""
    lowered = str(filename).strip().lower()
    for extension in SUPPORTED_EXTENSIONS:
        if lowered.endswith(extension):
            return extension
    raise ValueError(
        f"Unsupported file type {filename!r}; expected one of {', '.join(SUPPORTED_EXTENSIONS)}"
    )


def sop_class_for_modality(modality: str) -> str:
    """SOP Class UID for a caller-declared modality."""
    key = str(modality or "").strip().upper()
    if key not in SOP_CLASS_BY_MODALITY:
        raise ValueError(
            f"Unsupported modality {modality!r}; expected one of "
            f"{', '.join(sorted(SOP_CLASS_BY_MODALITY))}"
        )
    return SOP_CLASS_BY_MODALITY[key]


def ingested_uid(checksum: str, *parts: str) -> str:
    """A UID under the ingestion branch, reproducible from the same inputs."""
    if not checksum:
        raise ValueError("a checksum is required to derive reproducible UIDs")
    return generate_uid(prefix=NIFTI_UID_ROOT, entropy_srcs=[checksum, *parts])


def is_ingested_uid(uid: str | None) -> bool:
    """True when a UID was minted by this module."""
    return bool(uid) and str(uid).startswith(NIFTI_UID_ROOT)


def synthetic_identity(checksum: str) -> dict[str, str]:
    """A stable pseudonymous identity for a file that carries no patient data.

    Derived from the checksum rather than randomly, so uploading the same file
    twice resolves to one patient instead of two.
    """
    if not checksum:
        raise ValueError("a checksum is required to derive a synthetic identity")
    short = checksum[:12].upper()
    return {
        "PatientID": f"NIFTI-{short}",
        # DICOM person names are Family^Given; a blank given name is valid.
        "PatientName": f"NIFTI^{short}",
        # An invented birth date would look like real demographics. Leave empty.
        "PatientBirthDate": "",
        "PatientSex": "O",
    }


def apply_provenance(
    dataset: pydicom.Dataset,
    *,
    checksum: str,
    source_filename: str,
    source_extension: str,
    declared_modality: str,
    voxel_to_lps_affine: Sequence[Sequence[float]] | np.ndarray | None = None,
    converted_at: datetime | None = None,
) -> pydicom.Dataset:
    """Stamp one slice with every provenance layer described in the module docstring."""
    timestamp = converted_at or datetime.now()

    # Layer 2 -- standard tags any DICOM reader understands.
    dataset.ImageType = ["DERIVED", "SECONDARY", "VOLUME_IMPORT"]
    dataset.ConversionType = "WSD"
    dataset.DerivationDescription = DERIVATION_DESCRIPTION
    dataset.Manufacturer = "CUBE.AI"
    dataset.ManufacturerModelName = "Volume Import"
    dataset.SoftwareVersions = INGEST_VERSION

    # Layer 4 -- research identity, kept out of the real demographic tags.
    dataset.PatientIdentityRemoved = "YES"
    dataset.DeidentificationMethod = (
        "Synthetic identity assigned during volume-file ingestion"
    )
    dataset.ClinicalTrialProtocolID = CLINICAL_TRIAL_PROTOCOL_ID
    dataset.ClinicalTrialSubjectID = str(dataset.get("PatientID", ""))

    # Layer 3 -- the details that have no standard tag.
    block = dataset.private_block(PRIVATE_GROUP, PRIVATE_CREATOR, create=True)
    block.add_new(0x01, "LO", source_extension)
    block.add_new(0x02, "LT", str(source_filename)[:1024])
    block.add_new(0x03, "LO", str(checksum)[:64])
    block.add_new(0x04, "LO", INGEST_VERSION)
    block.add_new(0x05, "DT", timestamp.strftime("%Y%m%d%H%M%S"))
    block.add_new(0x07, "LO", str(declared_modality).upper()[:16])
    if voxel_to_lps_affine is not None:
        # The affine the conversion actually used, so a suspected geometry bug
        # can be re-derived from the archived series alone.
        affine = np.asarray(voxel_to_lps_affine, dtype=float).tolist()
        block.add_new(0x06, "LT", json.dumps(affine)[:1024])

    return dataset


def provenance_summary(
    *,
    checksum: str,
    source_filename: str,
    source_extension: str,
    declared_modality: str,
    series_instance_uid: str,
    study_instance_uid: str,
    patient_id: str,
) -> dict[str, Any]:
    """The same facts as a JSON record, for job status and verification reports."""
    return {
        "converter": INGEST_VERSION,
        "checksum": checksum,
        "source_filename": source_filename,
        "source_extension": source_extension,
        "declared_modality": str(declared_modality).upper(),
        "series_instance_uid": series_instance_uid,
        "study_instance_uid": study_instance_uid,
        "patient_id": patient_id,
        "uid_root": NIFTI_UID_ROOT,
    }
