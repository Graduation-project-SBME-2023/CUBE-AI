"""Smoke test of the agents image: run inside it, never on a developer machine.

    docker run --rm -v "$PWD:/workspace" -w /workspace/Backend cubeai1/agents-app:latest \
        python -m pytest -c pytest.ini tests/runtime -m docker

CI runs it in the image it has just built (backend-tests.yml). Outside a
container the module skips: a host interpreter says nothing about the image.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not Path("/.dockerenv").exists(), reason="runs inside the agents image only"),
]


def test_the_image_runs_python_3_13():
    assert sys.version_info[:2] == (3, 13)


def test_pycaret_is_not_installed():
    with pytest.raises(ImportError):
        importlib.import_module("pycaret")


@pytest.mark.parametrize(
    "module",
    [
        "numpy",
        "pandas",
        "scipy",
        "sklearn",
        "lightgbm",
        "xgboost",
        "shap",
        "SimpleITK",
        "radiomics",
        # PyRadiomics' compiled C extensions: a source build that silently
        # fell back to pure Python would only show up here.
        "radiomics._cmatrices",
        "radiomics._cshape",
        # Backend packages the image copies next to agent_system.
        "jobs_core.polling",
    ],
)
def test_the_runtime_imports(module):
    importlib.import_module(module)


def test_a_synthetic_extraction_returns_features(tmp_path):
    import numpy as np
    import SimpleITK as sitk
    import yaml
    from radiomics import featureextractor

    rng = np.random.default_rng(0)
    volume = rng.normal(100.0, 20.0, size=(24, 24, 24)).astype(np.float32)
    zz, yy, xx = np.mgrid[:24, :24, :24]
    sphere = ((zz - 12) ** 2 + (yy - 12) ** 2 + (xx - 12) ** 2 <= 36).astype(np.uint8)

    image_path, mask_path = tmp_path / "image.nii.gz", tmp_path / "mask.nii.gz"
    sitk.WriteImage(sitk.GetImageFromArray(volume), str(image_path))
    sitk.WriteImage(sitk.GetImageFromArray(sphere), str(mask_path))

    # The settings the radiomics tool pack writes, with its defaults, and the
    # two image types it uses by default.
    params = {
        "imageType": {"Original": {}, "Wavelet": {}},
        "featureClass": {"firstorder": [], "glcm": [], "shape": []},
        "setting": {
            "force2D": False,
            "normalize": True,
            "normalizeScale": 100,
            "binWidth": 25.0,
            "interpolator": "sitkBSpline",
            "correctMask": True,
            "padDistance": 10,
            "removeOutliers": 3,
        },
    }
    params_file = tmp_path / "radiomics_params.yaml"
    params_file.write_text(yaml.dump(params), encoding="utf-8")

    result = featureextractor.RadiomicsFeatureExtractor(str(params_file)).execute(
        str(image_path), str(mask_path)
    )
    features = {k: v for k, v in result.items() if not k.startswith("diagnostics_")}

    assert any(k.startswith("original_firstorder_") for k in features)
    assert any(k.startswith("original_shape_") for k in features)
    assert any(k.startswith("wavelet-") for k in features)
