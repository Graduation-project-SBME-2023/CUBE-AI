import logging
logger = logging.getLogger(__name__)
from datetime import datetime
import os
import uuid
import importlib.util
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core.database import Base
from models import model


database_url = os.getenv("DATABASE_URL")
sync_url = database_url.replace("+asyncpg", "") if database_url else None

sync_engine = create_engine(sync_url)
SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False)


def _load_voxtell_supported_labels() -> dict[str, float]:
    catalog_path = (
        Path(__file__).resolve().parents[2]
        / "apps"
        / "radiology"
        / "lib"
        / "configs"
        / "voxtell_supported_labels.py"
    )
    spec = importlib.util.spec_from_file_location("voxtell_supported_labels", catalog_path)
    if not spec or not spec.loader:
        logger.warning("Unable to load VoxTell supported-label catalog at %s", catalog_path)
        return {}

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "VOXTELL_SUPPORTED_LABELS", {})


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
            labels={
                "spleen": 0.96
            },
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
            labels=VOXTELL_SUPPORTED_LABELS,
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
            anatomy=[
                "spleen",
                "kidney_right",
                "kidney_left",
                "gallbladder",
                "liver",
                "stomach",
                "aorta",
                "inferior_vena_cava",
                "portal_vein_and_splenic_vein",
                "pancreas",
                "adrenal_gland_right",
                "adrenal_gland_left",
                "lung_upper_lobe_left",
                "lung_lower_lobe_left",
                "lung_upper_lobe_right",
                "lung_middle_lobe_right",
                "lung_lower_lobe_right",
                "vertebrae_L5",
                "vertebrae_L4",
                "vertebrae_L3",
                "vertebrae_L2",
                "vertebrae_L1",
                "vertebrae_T12",
                "vertebrae_T11",
                "vertebrae_T10",
                "vertebrae_T9",
                "vertebrae_T8",
                "vertebrae_T7",
                "vertebrae_T6",
                "vertebrae_T5",
                "vertebrae_T4",
                "vertebrae_T3",
                "vertebrae_T2",
                "vertebrae_T1",
                "vertebrae_C7",
                "vertebrae_C6",
                "vertebrae_C5",
                "vertebrae_C4",
                "vertebrae_C3",
                "vertebrae_C2",
                "vertebrae_C1",
                "esophagus",
                "trachea",
                "heart_myocardium",
                "heart_atrium_left",
                "heart_ventricle_left",
                "heart_atrium_right",
                "heart_ventricle_right",
                "pulmonary_artery",
                "brain",
                "iliac_artery_left",
                "iliac_artery_right",
                "iliac_vena_left",
                "iliac_vena_right",
                "small_bowel",
                "duodenum",
                "colon",
                "rib_left_1",
                "rib_left_2",
                "rib_left_3",
                "rib_left_4",
                "rib_left_5",
                "rib_left_6",
                "rib_left_7",
                "rib_left_8",
                "rib_left_9",
                "rib_left_10",
                "rib_left_11",
                "rib_left_12",
                "rib_right_1",
                "rib_right_2",
                "rib_right_3",
                "rib_right_4",
                "rib_right_5",
                "rib_right_6",
                "rib_right_7",
                "rib_right_8",
                "rib_right_9",
                "rib_right_10",
                "rib_right_11",
                "rib_right_12",
                "humerus_left",
                "humerus_right",
                "scapula_left",
                "scapula_right",
                "clavicula_left",
                "clavicula_right",
                "femur_left",
                "femur_right",
                "hip_left",
                "hip_right",
                "sacrum",
                "face",
                "gluteus_maximus_left",
                "gluteus_maximus_right",
                "gluteus_medius_left",
                "gluteus_medius_right",
                "gluteus_minimus_left",
                "gluteus_minimus_right",
                "autochthon_left",
                "autochthon_right",
                "iliopsoas_left",
                "iliopsoas_right",
                "urinary_bladder",
            ],
            labels={
                "spleen": 0.94,
                "kidney_right": 0.93,
                "kidney_left": 0.93,
                "gallbladder": 0.88,
                "liver": 0.95,
                "stomach": 0.90,
                "aorta": 0.91,
                "inferior_vena_cava": 0.88,
                "portal_vein_and_splenic_vein": 0.86,
                "pancreas": 0.87,

                "adrenal_gland_right": 0.78,
                "adrenal_gland_left": 0.78,

                "lung_upper_lobe_left": 0.93,
                "lung_lower_lobe_left": 0.92,
                "lung_upper_lobe_right": 0.93,
                "lung_middle_lobe_right": 0.91,
                "lung_lower_lobe_right": 0.92,

                "vertebrae_L5": 0.89,
                "vertebrae_L4": 0.89,
                "vertebrae_L3": 0.88,
                "vertebrae_L2": 0.88,
                "vertebrae_L1": 0.88,

                "vertebrae_T12": 0.87,
                "vertebrae_T11": 0.87,
                "vertebrae_T10": 0.87,
                "vertebrae_T9": 0.86,
                "vertebrae_T8": 0.86,
                "vertebrae_T7": 0.86,
                "vertebrae_T6": 0.85,
                "vertebrae_T5": 0.85,
                "vertebrae_T4": 0.85,
                "vertebrae_T3": 0.84,
                "vertebrae_T2": 0.84,
                "vertebrae_T1": 0.84,

                "vertebrae_C7": 0.84,
                "vertebrae_C6": 0.84,
                "vertebrae_C5": 0.83,
                "vertebrae_C4": 0.83,
                "vertebrae_C3": 0.82,
                "vertebrae_C2": 0.82,
                "vertebrae_C1": 0.82,

                "esophagus": 0.83,
                "trachea": 0.88,

                "heart_myocardium": 0.88,
                "heart_atrium_left": 0.89,
                "heart_ventricle_left": 0.90,
                "heart_atrium_right": 0.88,
                "heart_ventricle_right": 0.89,

                "pulmonary_artery": 0.84,
                "brain": 0.96,

                "iliac_artery_left": 0.86,
                "iliac_artery_right": 0.86,
                "iliac_vena_left": 0.85,
                "iliac_vena_right": 0.85,

                "small_bowel": 0.80,
                "duodenum": 0.78,
                "colon": 0.82,

                "rib_left_1": 0.83,
                "rib_left_2": 0.83,
                "rib_left_3": 0.83,
                "rib_left_4": 0.83,
                "rib_left_5": 0.83,
                "rib_left_6": 0.83,
                "rib_left_7": 0.83,
                "rib_left_8": 0.83,
                "rib_left_9": 0.83,
                "rib_left_10": 0.83,
                "rib_left_11": 0.83,
                "rib_left_12": 0.83,

                "rib_right_1": 0.83,
                "rib_right_2": 0.83,
                "rib_right_3": 0.83,
                "rib_right_4": 0.83,
                "rib_right_5": 0.83,
                "rib_right_6": 0.83,
                "rib_right_7": 0.83,
                "rib_right_8": 0.83,
                "rib_right_9": 0.83,
                "rib_right_10": 0.83,
                "rib_right_11": 0.83,
                "rib_right_12": 0.83,

                "humerus_left": 0.86,
                "humerus_right": 0.86,
                "scapula_left": 0.85,
                "scapula_right": 0.85,
                "clavicula_left": 0.84,
                "clavicula_right": 0.84,

                "femur_left": 0.90,
                "femur_right": 0.90,
                "hip_left": 0.88,
                "hip_right": 0.88,

                "sacrum": 0.87,
                "face": 0.92,

                "gluteus_maximus_left": 0.84,
                "gluteus_maximus_right": 0.84,
                "gluteus_medius_left": 0.83,
                "gluteus_medius_right": 0.83,
                "gluteus_minimus_left": 0.82,
                "gluteus_minimus_right": 0.82,

                "autochthon_left": 0.81,
                "autochthon_right": 0.81,
                "iliopsoas_left": 0.83,
                "iliopsoas_right": 0.83,

                "urinary_bladder": 0.90

            },
            params_schema={
                "Class": {
                    "mapping": {
                        "spleen": 1,
                        "kidney_right": 2,
                        "kidney_left": 3,
                        "gallbladder": 4,
                        "liver": 5,
                        "stomach": 6,
                        "aorta": 7,
                        "inferior_vena_cava": 8,
                        "portal_vein_and_splenic_vein": 9,
                        "pancreas": 10,
                        "adrenal_gland_right": 11,
                        "adrenal_gland_left": 12,
                        "lung_upper_lobe_left": 13,
                        "lung_lower_lobe_left": 14,
                        "lung_upper_lobe_right": 15,
                        "lung_middle_lobe_right": 16,
                        "lung_lower_lobe_right": 17,
                        "vertebrae_L5": 18,
                        "vertebrae_L4": 19,
                        "vertebrae_L3": 20,
                        "vertebrae_L2": 21,
                        "vertebrae_L1": 22,
                        "vertebrae_T12": 23,
                        "vertebrae_T11": 24,
                        "vertebrae_T10": 25,
                        "vertebrae_T9": 26,
                        "vertebrae_T8": 27,
                        "vertebrae_T7": 28,
                        "vertebrae_T6": 29,
                        "vertebrae_T5": 30,
                        "vertebrae_T4": 31,
                        "vertebrae_T3": 32,
                        "vertebrae_T2": 33,
                        "vertebrae_T1": 34,
                        "vertebrae_C7": 35,
                        "vertebrae_C6": 36,
                        "vertebrae_C5": 37,
                        "vertebrae_C4": 38,
                        "vertebrae_C3": 39,
                        "vertebrae_C2": 40,
                        "vertebrae_C1": 41,
                        "esophagus": 42,
                        "trachea": 43,
                        "heart_myocardium": 44,
                        "heart_atrium_left": 45,
                        "heart_ventricle_left": 46,
                        "heart_atrium_right": 47,
                        "heart_ventricle_right": 48,
                        "pulmonary_artery": 49,
                        "brain": 50,
                        "iliac_artery_left": 51,
                        "iliac_artery_right": 52,
                        "iliac_vena_left": 53,
                        "iliac_vena_right": 54,
                        "small_bowel": 55,
                        "duodenum": 56,
                        "colon": 57,
                        "rib_left_1": 58,
                        "rib_left_2": 59,
                        "rib_left_3": 60,
                        "rib_left_4": 61,
                        "rib_left_5": 62,
                        "rib_left_6": 63,
                        "rib_left_7": 64,
                        "rib_left_8": 65,
                        "rib_left_9": 66,
                        "rib_left_10": 67,
                        "rib_left_11": 68,
                        "rib_left_12": 69,
                        "rib_right_1": 70,
                        "rib_right_2": 71,
                        "rib_right_3": 72,
                        "rib_right_4": 73,
                        "rib_right_5": 74,
                        "rib_right_6": 75,
                        "rib_right_7": 76,
                        "rib_right_8": 77,
                        "rib_right_9": 78,
                        "rib_right_10": 79,
                        "rib_right_11": 80,
                        "rib_right_12": 81,
                        "humerus_left": 82,
                        "humerus_right": 83,
                        "scapula_left": 84,
                        "scapula_right": 85,
                        "clavicula_left": 86,
                        "clavicula_right": 87,
                        "femur_left": 88,
                        "femur_right": 89,
                        "hip_left": 90,
                        "hip_right": 91,
                        "sacrum": 92,
                        "face": 93,
                        "gluteus_maximus_left": 94,
                        "gluteus_maximus_right": 95,
                        "gluteus_medius_left": 96,
                        "gluteus_medius_right": 97,
                        "gluteus_minimus_left": 98,
                        "gluteus_minimus_right": 99,
                        "autochthon_left": 100,
                        "autochthon_right": 101,
                        "iliopsoas_left": 102,
                        "iliopsoas_right": 103,
                        "urinary_bladder": 104,
                    },
                    "examples": [
                        {
                            "input_labels": ["spleen"],
                            "params": {"Class": 1}
                        },
                        {
                            "input_labels": ["liver"],
                            "params": {"Class": 5}
                        },
                        {
                            "input_labels": ["pancreas"],
                            "params": {"Class": 10}
                        }
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

