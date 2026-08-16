import ast
from pathlib import Path


VISTA_INFER = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "radiology"
    / "lib"
    / "infers"
    / "vista.py"
)


def test_bundle_command_explicitly_supplies_metadata_file():
    """Temporary configs must not make MONAI look for temp/vista/metadata.json."""
    tree = ast.parse(VISTA_INFER.read_text(encoding="utf-8"))
    string_literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert "--meta_file" in string_literals
