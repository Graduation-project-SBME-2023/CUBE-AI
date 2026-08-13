"""Radiology application package.

The MONAI Label loader requires the application directory's ``main.py`` to
expose a direct ``MONAILabelApp`` subclass.  Keeping the import lazy avoids
loading the radiology runtime when lightweight tooling imports this package.
"""

import sys
from pathlib import Path


def get_app_class():
    """Return the MONAI Label application class expected by the loader."""
    # Containers expose the canonical module through PYTHONPATH. For a source
    # checkout, append Backend/ so ``storage_paths`` remains single-source
    # without keeping a duplicate module in the radiology app root.
    backend_dir = Path(__file__).resolve().parents[3]
    if (
        (backend_dir / "storage_paths.py").is_file()
        and str(backend_dir) not in sys.path
    ):
        sys.path.append(str(backend_dir))

    from radiology_app.application.app import RadiologyApp

    return RadiologyApp


__all__ = ["get_app_class"]
