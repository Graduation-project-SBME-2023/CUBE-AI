from __future__ import annotations

import torch

import lib.infers.localization_vertebra as localization_module
from lib.infers.localization_vertebra import LocalizationVertebra


def test_inverse_reduces_logits_before_restoring(monkeypatch) -> None:
    observed = {}

    class FakeInverter:
        def __init__(self, **kwargs):
            observed["options"] = kwargs

        def __call__(self, data):
            observed["shape"] = tuple(data["pred"].shape)
            return data

    monkeypatch.setattr(localization_module, "Invertd", FakeInverter)
    monkeypatch.setattr(localization_module, "Compose", lambda transforms: transforms)

    task = object.__new__(LocalizationVertebra)
    task.input_key = "image"
    task.output_label_key = "pred"
    data = {
        "image": torch.zeros((1, 8, 8, 8)),
        "pred": torch.randn((25, 8, 8, 8)),
    }

    result = task.run_invert_transforms(data, [], [])

    assert observed["shape"] == (1, 8, 8, 8)
    assert observed["options"]["nearest_interp"] is True
    assert result["pred"].device.type == "cpu"
    assert result["pred"].dtype == torch.float32
