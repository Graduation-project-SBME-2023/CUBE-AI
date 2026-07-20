import logging
logger = logging.getLogger(__name__)
from datetime import datetime
import os
import uuid
import importlib.util
from pathlib import Path


def _load_module_from_configs(module_filename: str):
    """
    Load a Python module from the radiology app configs directory.
    Returns the module object on success, or None if the file is missing
    or cannot be loaded.
    """
    catalog_path = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "radiology"
        / "lib"
        / "configs"
        / module_filename
    )
    spec = importlib.util.spec_from_file_location(module_filename[:-3], catalog_path)
    if not spec or not spec.loader:
        logger.warning("Unable to load catalog module at %s", catalog_path)
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_vista_labels() -> dict:
    module = _load_module_from_configs("vista_labels.py")
    if module is None:
        return {}
    return getattr(module, "VISTA_CLASS_MAP", {})


VISTA_CLASS_MAP = _load_vista_labels()

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from models import model


database_url = os.getenv("DATABASE_URL")
sync_url = database_url.replace("+asyncpg", "") if database_url else None

sync_engine = create_engine(sync_url)
SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False)


def _load_voxtell_supported_labels() -> list:
    module = _load_module_from_configs("voxtell_supported_labels.py")
    if module is None:
        return {}
    return getattr(module, "VOXTELL_SUPPORTED_LABELS", [])


VOXTELL_SUPPORTED_LABELS = _load_voxtell_supported_labels()


def seed_models():
    Base.metadata.create_all(bind=sync_engine)
    db = SessionLocal()

    if db.query(model.Model).count() != 0:
        logger.info("Models already seeded. Skipping.")
        db.close()
        return

    model_entries = [

       model.Model(
            name="segmentation_spleen",
            description="A pre-trained model for volumetric (3D) segmentation of the spleen from CT image",
            config={},
            type="automatic",
            modality="CT",
            dimension=3,
            anatomy=["spleen"],
            params_schema=None,
            model_id=str(uuid.uuid4()),
            created_at=datetime.now(),
        ),

        model.Model(
            name="voxtell",
            description="text_prompted interactive model for volumetric segmentation",
            config={},
            type="interactive",
            modality="any",
            dimension=3,
            anatomy=["any"],
            params_schema={
                "text_prompts": {
                    "type": "list[string]",
                    "examples": [
                        {
                            "text_prompts": ["spleen"]
                        }
                    ]
                }
            },
            model_id=str(uuid.uuid4()),
            created_at=datetime.now(),
        ),
        
        model.Model(
            name="vista",
            description="A pre-trained model for volumetric (2.5D) segmentation of the monai vista",
            config={},
            type="automatic",
            modality="CT",
            dimension=2,
            # anatomy list derived from VISTA_CLASS_MAP — single source of truth
            anatomy=list(VISTA_CLASS_MAP.keys()),
            params_schema={
                "Class": {
                    # Class index mapping from VISTA_CLASS_MAP — single source of truth
                    "mapping": VISTA_CLASS_MAP,
                    "examples": [
                        {"input_labels": ["spleen"],  "params": {"Class": 1}},
                        {"input_labels": ["liver"],   "params": {"Class": 5}},
                        {"input_labels": ["pancreas"], "params": {"Class": 10}},
                    ]
                }
            },
            model_id=str(uuid.uuid4()),
            created_at=datetime.now(),
        ),
    ]

    db.add_all(model_entries)
    db.commit()

    logger.info(f"{len(model_entries)} models seeded successfully!")
    db.close()

