from __future__ import annotations

import json
from pathlib import Path

import pytest

from radiology_app.workflows.repository import (
    DynamicModelSpec,
    WorkflowRepository,
    WorkflowStoreError,
)


def _spec(name: str = "brain_tumor") -> DynamicModelSpec:
    return DynamicModelSpec.create(
        model_name=name,
        labels='{"tumor": 1, "edema": 2}',
        fold="0",
        plan_type="3d_fullres",
    )


def test_repository_round_trip_and_upsert(tmp_path: Path) -> None:
    repository = WorkflowRepository(tmp_path / "state" / "workflows.json")
    repository.upsert(_spec())
    repository.upsert(_spec("liver_lesion"))

    loaded = repository.load()

    assert [item.model_name for item in loaded] == ["brain_tumor", "liver_lesion"]
    assert loaded[0].labels == {"tumor": 1, "edema": 2}


def test_repository_migrates_legacy_state(tmp_path: Path) -> None:
    legacy = tmp_path / "workflows_data.json"
    legacy.write_text(
        json.dumps({"brain_tumor": _spec().to_record()}),
        encoding="utf-8",
    )
    canonical = tmp_path / "state" / "workflows.json"
    repository = WorkflowRepository(canonical, legacy_paths=(legacy,))

    assert repository.load() == [_spec()]
    assert canonical.is_file()
    assert legacy.is_file()


def test_repository_rejects_corrupt_state(tmp_path: Path) -> None:
    path = tmp_path / "workflows.json"
    path.write_text("not-json", encoding="utf-8")

    with pytest.raises(WorkflowStoreError, match="Could not read"):
        WorkflowRepository(path).load()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("model_name", "../unsafe", "model_name"),
        ("labels", "[]", "labels"),
        ("fold", -1, "fold"),
        ("plan_type", "", "plan_type"),
    ],
)
def test_dynamic_model_spec_validation(field: str, value: object, message: str) -> None:
    values = {
        "model_name": "brain_tumor",
        "labels": {"tumor": 1},
        "fold": 0,
        "plan_type": "3d_fullres",
    }
    values[field] = value

    with pytest.raises(ValueError, match=message):
        DynamicModelSpec.create(**values)
