from __future__ import annotations

import ast
from pathlib import Path


def test_radiology_main_is_a_loader_shim() -> None:
    app_dir = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "radiology"
    )
    path = app_dir / "main.py"
    module = ast.parse(path.read_text(encoding="utf-8"))

    assert len(module.body) == 2
    assignment = module.body[1]
    assert isinstance(assignment, ast.Assign)
    assert isinstance(assignment.value, ast.Call)
    assert isinstance(assignment.value.func, ast.Name)
    assert assignment.value.func.id == "get_app_class"

    app_module = ast.parse(
        (app_dir / "radiology_app" / "application" / "app.py").read_text(
            encoding="utf-8"
        )
    )
    app_class = next(
        node
        for node in app_module.body
        if isinstance(node, ast.ClassDef) and node.name == "RadiologyApp"
    )
    assert any(
        isinstance(base, ast.Name) and base.id == "MONAILabelApp"
        for base in app_class.bases
    )

    assert {item.name for item in app_dir.glob("*.py")} == {
        "__init__.py",
        "main.py",
    }
