"""The PyRadiomics settings of one extraction, and its identity.

The configuration digest covers the extractor version and every setting — the
adaptive bin width included — so a changed setting is a different
content-addressed row, never a reused earlier one.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .. import digests as provenance

logger = logging.getLogger(__name__)

EXTRACTOR_NAME = "pyradiomics"
CONFIG_SCHEMA_VERSION = 1

# Canonical PyRadiomics names, by lower-case spelling.
IMAGE_TYPES = {
    "original": "Original", "wavelet": "Wavelet", "log": "LoG", "gradient": "Gradient",
    "exponential": "Exponential", "squareroot": "SquareRoot", "lbp2d": "LBP2D", "lbp3d": "LBP3D",
}
FEATURE_CLASSES = {
    "firstorder": "firstorder", "shape": "shape", "glcm": "glcm", "glrlm": "glrlm",
    "glszm": "glszm", "gldm": "gldm", "ngtdm": "ngtdm", "shape2d": "shape2D",
}
DEFAULT_IMAGE_TYPES = ("Original", "Wavelet")
DEFAULT_FEATURE_CLASSES = ("firstorder", "shape", "glcm", "glrlm", "glszm", "gldm", "ngtdm")
IMAGE_TYPE_SETTINGS: dict[str, dict[str, Any]] = {"LoG": {"sigma": [1.0, 2.0, 3.0, 4.0, 5.0]}}
DEFAULT_PIXEL_SPACING = (1.0, 1.0, 1.0)

# Adaptive bin width: CT is in standardised Hounsfield units, so the requested
# width (25 HU by default) fits; an MRI-like range is binned into ~128 bins.
BIN_WIDTH_SAMPLES = 5
MRI_BINS = 128.0
MIN_BIN_WIDTH = 0.5


@dataclass(frozen=True)
class FeatureChoice:
    """Which image types and features to compute."""

    image_types: list[str]
    feature_classes: list[str] | None
    specific_features: list[str] | None
    notes: list[str] = field(default_factory=list)


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def choose_features(
    image_types: list[str] | None,
    feature_classes: list[str] | None,
    specific_features: list[str] | None,
) -> FeatureChoice:
    """Normalise the requested names. An image filter passed as a feature class
    (models often send 'wavelet' or 'LoG' there) is moved to the image types;
    unknown names are dropped and said so."""
    notes: list[str] = []
    requested_types = list(image_types or DEFAULT_IMAGE_TYPES)
    classes = None
    if feature_classes:
        moved = [name for name in feature_classes if name.lower() in IMAGE_TYPES]
        if moved:
            notes.append(f"moved {moved} from feature_classes to image_types: they are image filters")
            requested_types += moved
        classes = _unique(
            FEATURE_CLASSES[name.lower()] for name in feature_classes if name.lower() in FEATURE_CLASSES
        ) or None

    unknown = [name for name in requested_types if name.lower() not in IMAGE_TYPES]
    if unknown:
        notes.append(f"ignored unsupported image type(s) {unknown}")
    types = _unique(IMAGE_TYPES[name.lower()] for name in requested_types if name.lower() in IMAGE_TYPES)
    if classes is None and not specific_features:
        classes = list(DEFAULT_FEATURE_CLASSES)
    return FeatureChoice(
        image_types=types or list(DEFAULT_IMAGE_TYPES),
        feature_classes=classes,
        specific_features=specific_features or None,
        notes=notes,
    )


def adaptive_bin_width(image_paths: Sequence[str], requested: float) -> float:
    """The bin width for these images: ``requested`` for a CT-like intensity
    range, range/128 for an MRI-like one. Reads up to ``BIN_WIDTH_SAMPLES``
    headers; any failure keeps ``requested``."""
    try:
        import nibabel
    except ImportError:
        logger.info("nibabel is not installed; using bin_width=%s", requested)
        return requested
    minima: list[float] = []
    maxima: list[float] = []
    for path in list(image_paths)[:BIN_WIDTH_SAMPLES]:
        try:
            data = nibabel.load(path).get_fdata(dtype="float32")
        except Exception as exc:
            logger.debug("bin width sample %s unreadable: %s", path, exc)
            continue
        minima.append(float(data.min()))
        maxima.append(float(data.max()))
    if not minima:
        return requested
    low, high = min(minima), max(maxima)
    if abs(low) < 1500 and high < 5000 and high - low < 3000:
        width = round(max(MIN_BIN_WIDTH, (high - low) / MRI_BINS), 2)
        logger.info("MRI-like intensity range (%.1f to %.1f): bin_width=%s", low, high, width)
        return width
    logger.info("CT-like intensity range (%.1f to %.1f): bin_width=%s", low, high, requested)
    return requested


def pyradiomics_settings(
    choice: FeatureChoice,
    *,
    bin_width: float,
    normalize: bool,
    resample: bool,
    pixel_spacing: list[float],
    force_2d: bool,
) -> dict[str, Any]:
    """The PyRadiomics parameter document."""
    settings: dict[str, Any] = {
        "imageType": {name: IMAGE_TYPE_SETTINGS.get(name, {}) for name in choice.image_types},
        "featureClass": {},
        "setting": {
            "force2D": force_2d,
            "normalize": normalize,
            "normalizeScale": 100,
            "binWidth": bin_width,
            "interpolator": "sitkBSpline",
            "correctMask": True,
            "padDistance": 10,
            "removeOutliers": 3,
        },
    }
    if resample:
        settings["setting"]["resampledPixelSpacing"] = pixel_spacing
    if choice.specific_features is None:
        settings["featureClass"] = {name: [] for name in choice.feature_classes or ()}
    else:
        # "original_firstorder_Mean" -> featureClass firstorder, feature Mean.
        for feature in choice.specific_features:
            parts = feature.split("_")
            if len(parts) >= 2:
                names = settings["featureClass"].setdefault(parts[-2], [])
                if parts[-1] not in names:
                    names.append(parts[-1])
    return settings


@dataclass(frozen=True)
class ExtractionConfig:
    """What makes two extractions the same extraction."""

    settings: dict[str, Any]
    extractor_version: str
    extractor_name: str = EXTRACTOR_NAME

    @property
    def document(self) -> dict[str, Any]:
        return {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "pyradiomics": self.settings,
            # One reviewed pair is one target on one SEG segment.
            "mask_label_policy": "pair_segment",
            # A null feature value means the extractor returned NaN/Infinity.
            "missing_value_policy": "non_finite_to_null",
        }

    @property
    def digest(self) -> str:
        return provenance.config_digest(self.extractor_name, self.extractor_version, self.document)


def extractor_version() -> str:
    """The installed PyRadiomics version (imports it in this process)."""
    import radiomics

    return str(getattr(radiomics, "__version__", "unknown"))


def write_settings(settings: dict[str, Any], directory: str) -> str:
    """The parameter file the extractor reads; returns its path."""
    import yaml

    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, "radiomics_params.yaml")
    with open(path, "w", encoding="utf-8") as stream:
        yaml.dump(settings, stream)
    return path


__all__ = [
    "DEFAULT_PIXEL_SPACING",
    "ExtractionConfig",
    "FeatureChoice",
    "adaptive_bin_width",
    "choose_features",
    "extractor_version",
    "pyradiomics_settings",
    "write_settings",
]
