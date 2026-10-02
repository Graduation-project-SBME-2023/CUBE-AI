"""Content digests for radiomics provenance."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping, Sequence

DIGEST_SCHEMA_VERSION = 1
# Geometry is compared at nanometre resolution: tighter than any scanner, loose
# enough that a float round-trip through a NIfTI header does not change it.
_GEOMETRY_DECIMALS = 9


def canonical_json(value: Any) -> bytes:
    """The one byte encoding every document digest is taken over."""
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _plain(value: Any) -> Any:
    """numpy scalars/arrays and tuples as plain JSON types, deterministically."""
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        return _plain(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("a digested document cannot contain NaN or Infinity")
    return value


def document_digest(value: Any) -> str:
    return sha256_hex(canonical_json(_plain(value)))


def bytes_digest(payload: bytes) -> str:
    """For inputs the tool only has as bytes (a generic CSV)."""
    return sha256_hex(payload)


def config_digest(
    extractor_name: str, extractor_version: str, parameters: Mapping[str, Any]
) -> str:
    """Over the extractor identity and every setting (binWidth, resampling, image types, feature classes …): changing any of them must produce a new key, never a cached earlier extraction."""
    return document_digest(
        {
            "schema_version": DIGEST_SCHEMA_VERSION,
            "extractor_name": extractor_name,
            "extractor_version": extractor_version,
            "parameters": parameters,
        }
    )


def _geometry(values: Iterable[float]) -> list[float]:
    return [round(float(value), _GEOMETRY_DECIMALS) for value in values]


def array_digest(
    array: Any,
    *,
    spacing: Sequence[float],
    origin: Sequence[float],
    direction: Sequence[float],
    role: str,
    identity: Mapping[str, Any] | None = None,
) -> str:
    """Voxels plus the geometry that gives them meaning."""
    import numpy as np

    data = np.asarray(array)
    data = np.ascontiguousarray(data.astype(data.dtype.newbyteorder("<"), copy=False))
    header = {
        "schema_version": DIGEST_SCHEMA_VERSION,
        "role": role,
        "dtype": data.dtype.str,
        "shape": [int(size) for size in data.shape],
        "spacing": _geometry(spacing),
        "origin": _geometry(origin),
        "direction": _geometry(direction),
        "identity": _plain(identity or {}),
    }
    digest = hashlib.sha256()
    digest.update(canonical_json(header))
    digest.update(b"\x00")
    digest.update(data.tobytes(order="C"))
    return digest.hexdigest()


def image_digest(image: Any) -> str:
    """A SimpleITK image's decoded voxels and geometry."""
    import SimpleITK as sitk

    return array_digest(
        sitk.GetArrayViewFromImage(image),
        spacing=image.GetSpacing(),
        origin=image.GetOrigin(),
        direction=image.GetDirection(),
        role="image",
    )


def segmentation_digest(
    binary_mask: Any,
    reference: Any,
    *,
    label_series_instance_uid: str,
    segment_number: int,
) -> str:
    """One segment's binary mask, on the geometry of the SEG it came from."""
    return array_digest(
        binary_mask,
        spacing=reference.GetSpacing(),
        origin=reference.GetOrigin(),
        direction=reference.GetDirection(),
        role="segmentation",
        identity={
            "label_series_instance_uid": str(label_series_instance_uid),
            "segment_number": int(segment_number),
        },
    )


def source_digest(
    *,
    image_digest: str,
    segmentation_digest: str,
    config_digest: str,
    label_series_instance_uid: str,
    segment_number: int,
) -> str:
    """Source image identity + selected SEG content/segment + configuration."""
    return document_digest(
        {
            "schema_version": DIGEST_SCHEMA_VERSION,
            "image_digest": image_digest,
            "segmentation_digest": segmentation_digest,
            "config_digest": config_digest,
            "label_series_instance_uid": str(label_series_instance_uid),
            "segment_number": int(segment_number),
        }
    )


def rows_source_digest(source_digests: Sequence[str]) -> str:
    """A feature set's source identity: the set of radiomics rows it read."""
    return document_digest(
        {
            "schema_version": DIGEST_SCHEMA_VERSION,
            "radiomics_source_digests": sorted(str(item) for item in source_digests),
        }
    )


__all__ = [
    "DIGEST_SCHEMA_VERSION",
    "array_digest",
    "bytes_digest",
    "canonical_json",
    "config_digest",
    "document_digest",
    "image_digest",
    "rows_source_digest",
    "segmentation_digest",
    "sha256_hex",
    "source_digest",
]
