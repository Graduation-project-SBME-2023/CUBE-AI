from __future__ import annotations

import pytest

from radiology_app.application.registry import DEFAULT_MODEL_REGISTRY
from radiology_app.application.settings import RadiologySettings, parse_bool


def test_settings_parse_and_deduplicate_models() -> None:
    settings = RadiologySettings.from_conf(
        {
            "models": "deepedit, deepedit, sam",
            "scribbles": "false",
            "heuristic_planner": "true",
            "spatial_size": "[64, 64, 32]",
        },
        available_models=DEFAULT_MODEL_REGISTRY.available_names,
    )

    assert settings.requested_models == ("deepedit", "sam")
    assert settings.scribbles is False
    assert settings.heuristic_planner is True
    assert settings.spatial_size == (64.0, 64.0, 32.0)


@pytest.mark.parametrize("value", ["invalid", "truthy", 2])
def test_parse_bool_rejects_ambiguous_values(value: object) -> None:
    with pytest.raises(ValueError, match="Invalid boolean"):
        parse_bool(value)


def test_settings_reject_unknown_model() -> None:
    with pytest.raises(ValueError, match="Unknown radiology model"):
        RadiologySettings.from_conf(
            {"models": "does-not-exist"},
            available_models=DEFAULT_MODEL_REGISTRY.available_names,
        )
