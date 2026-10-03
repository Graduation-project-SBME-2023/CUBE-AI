import logging
logger = logging.getLogger(__name__)
from datetime import datetime, timezone
import os
import uuid
import importlib.util
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


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

from application_api.db.models.inference_models import InferenceModel


def _sync_url_from_env() -> str:
    """Build the sync (psycopg2) DSN from DATABASE_URL.

    Raises loudly rather than silently constructing create_engine(None) if
    DATABASE_URL is unset, and rewrites only the driver segment of the DSN
    instead of a blind substring replace.
    """
    database_url = os.environ["DATABASE_URL"]
    parsed = urlsplit(database_url)
    # Name the driver explicitly: SQLAlchemy 2.1 made psycopg (v3) the default
    # for a bare "postgresql://", and the image only ships psycopg2.
    scheme = parsed.scheme.replace("+asyncpg", "+psycopg2")
    return urlunsplit(parsed._replace(scheme=scheme))


def _load_voxtell_supported_labels() -> list:
    module = _load_module_from_configs("voxtell_supported_labels.py")
    if module is None:
        return {}
    return getattr(module, "VOXTELL_SUPPORTED_LABELS", [])


VOXTELL_SUPPORTED_LABELS = _load_voxtell_supported_labels()


def _model_catalog() -> list:
    """Return the seed catalog as plain dicts, keyed by name.

    Kept as a function (rather than module-level InferenceModel instances) so the
    per-name idempotency check in seed_models() can compare against it
    without needing a live session first.
    """
    return [
        dict(
            name="segmentation_spleen",
            description="A pre-trained model for volumetric (3D) segmentation of the spleen from CT image",
            config={},
            type="automatic",
            modality="CT",
            dimension=3,
            anatomy=["spleen"],
            params_schema=None,
        ),
        dict(
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
                    "examples": [{"text_prompts": ["spleen"]}],
                }
            },
        ),
        dict(
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
                        {"input_labels": ["spleen"], "params": {"Class": 1}},
                        {"input_labels": ["liver"], "params": {"Class": 5}},
                        {"input_labels": ["pancreas"], "params": {"Class": 10}},
                    ],
                }
            },
        ),
    ]


def seed_models():
    """Idempotently insert the built-in model catalog.

    Schema is Alembic's responsibility alone — this function must never call
    Base.metadata.create_all(), which would create tables without recording
    them in alembic_version and let the ORM and migration history diverge.
    Idempotency is checked per model name rather than "table is non-empty",
    so a model added to the catalog later still gets seeded into databases
    that already have earlier entries (e.g. synced in from MONAI Label).
    """
    sync_engine = create_engine(_sync_url_from_env())
    SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False)
    db = SessionLocal()
    try:
        existing_names = {row[0] for row in db.query(InferenceModel.name).all()}
        to_insert = [
            InferenceModel(
                inference_model_id=uuid.uuid4(),
                created_at=datetime.now(timezone.utc),
                **entry,
            )
            for entry in _model_catalog()
            if entry["name"] not in existing_names
        ]

        if not to_insert:
            logger.info("Model catalog already up to date. Skipping.")
            return

        db.add_all(to_insert)
        db.commit()
        logger.info(
            "%d model(s) seeded successfully: %s",
            len(to_insert),
            ", ".join(m.name for m in to_insert),
        )
    finally:
        db.close()
        sync_engine.dispose()
