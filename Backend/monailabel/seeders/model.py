from datetime import datetime
from models import model
from datetime import datetime
from models import model
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from core.database import Base
import os

database_url = os.getenv("DATABASE_URL")
sync_url = database_url.replace("+asyncpg", "") if database_url else None
sync_engine = create_engine(sync_url)
SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False)



def seed_models():
    Base.metadata.create_all(bind=sync_engine)
    db = SessionLocal()

    if db.query(model.Model).count() != 10:
        print("Models already seeded. Skipping.")
        db.close()
        return

    model_entries = [
        model.Model(
            name="deepedit",
            type="deepedit",
            dimension=3,
            labels={
                "background": 0, "spleen": 1, "right kidney": 2, "left kidney": 3,
                "liver": 6, "stomach": 7, "aorta": 8, "inferior vena cava": 9,
            },
            description="A DeepEdit model for volumetric (3D) segmentation over 3D Images",
            config={"cache_transforms": True, "cache_transforms_in_memory": True, "cache_transforms_ttl": 300},
            created_at=datetime.now(),
        ),
        model.Model(
            name="deepedit_seg",
            type="segmentation",
            dimension=3,
            labels={
                "background": 0, "spleen": 1, "right kidney": 2, "left kidney": 3,
                "liver": 6, "stomach": 7, "aorta": 8, "inferior vena cava": 9,
            },
            description="A DeepEdit model for volumetric (3D) segmentation over 3D Images",
            config={},
            created_at=datetime.now(),
        ),
        model.Model(
            name="deepgrow_2d",
            type="deepgrow",
            dimension=2,
            labels=["spleen", "right kidney", "left kidney", "liver", "stomach", "aorta", "inferior vena cava"],
            description="A pre-trained DeepGrow model based on UNET",
            config={"cache_transforms": True, "cache_transforms_in_memory": True, "cache_transforms_ttl": 300},
            created_at=datetime.now(),
        ),
        model.Model(
            name="deepgrow_3d",
            type="deepgrow",
            dimension=3,
            labels=["spleen", "right kidney", "left kidney", "liver", "stomach", "aorta", "inferior vena cava"],
            description="A pre-trained DeepGrow model based on UNET",
            config={"cache_transforms": True, "cache_transforms_in_memory": True, "cache_transforms_ttl": 300},
            created_at=datetime.now(),
        ),
        model.Model(
            name="localization_vertebra",
            type="segmentation",
            dimension=3,
            labels={
                "C1": 1, "C2": 2, "C3": 3, "C4": 4, "C5": 5, "C6": 6, "C7": 7,
                "Th1": 8, "Th2": 9, "Th3": 10, "Th4": 11, "Th5": 12, "Th6": 13,
                "Th7": 14, "Th8": 15, "Th9": 16, "Th10": 17, "Th11": 18, "Th12": 19,
                "L1": 20, "L2": 21, "L3": 22, "L4": 23, "L5": 24,
            },
            description="A pre-trained model for volumetric (3D) vertebra localization from CT image",
            config={},
            created_at=datetime.now(),
        ),
        model.Model(
            name="nninteractive",
            type="segmentation",
            dimension=2,
            labels={"seg": 1},
            description="A pre-trained model for volumetric segmentation of the monai nnInteractive",
            config={},
            created_at=datetime.now(),
        ),
        model.Model(
            name="sam",
            type="segmentation",
            dimension=3,
            labels={"Seg": 1},
            description="Pretrained SAM Model for video segmentation",
            config={},
            created_at=datetime.now(),
        ),
        model.Model(
            name="segmentation_spleen",
            type="segmentation",
            dimension=3,
            labels={"spleen": 1},
            description="A pre-trained model for volumetric (3D) segmentation of the spleen from CT image",
            config={},
            created_at=datetime.now(),
        ),
        model.Model(
            name="vista",
            type="segmentation",
            dimension=2,
            labels={
                "spleen": 1, "kidney_right": 2, "kidney_left": 3, "gallbladder": 4,
                "liver": 5, "stomach": 6, "aorta": 7, "inferior_vena_cava": 8,
                "portal_vein_and_splenic_vein": 9, "pancreas": 10,
                "adrenal_gland_right": 11, "adrenal_gland_left": 12,
                "lung_upper_lobe_left": 13, "lung_lower_lobe_left": 14,
                "lung_upper_lobe_right": 15, "lung_middle_lobe_right": 16,
                "lung_lower_lobe_right": 17, "vertebrae_L5": 18, "vertebrae_L4": 19,
                "vertebrae_L3": 20, "vertebrae_L2": 21, "vertebrae_L1": 22,
                "vertebrae_T12": 23, "vertebrae_T11": 24, "vertebrae_T10": 25,
                "vertebrae_T9": 26, "vertebrae_T8": 27, "vertebrae_T7": 28,
                "vertebrae_T6": 29, "vertebrae_T5": 30, "vertebrae_T4": 31,
                "vertebrae_T3": 32, "vertebrae_T2": 33, "vertebrae_T1": 34,
                "vertebrae_C7": 35, "vertebrae_C6": 36, "vertebrae_C5": 37,
                "vertebrae_C4": 38, "vertebrae_C3": 39, "vertebrae_C2": 40,
                "vertebrae_C1": 41, "esophagus": 42, "trachea": 43,
                "heart_myocardium": 44, "heart_atrium_left": 45,
                "heart_ventricle_left": 46, "heart_atrium_right": 47,
                "heart_ventricle_right": 48, "pulmonary_artery": 49, "brain": 50,
                "iliac_artery_left": 51, "iliac_artery_right": 52,
                "iliac_vena_left": 53, "iliac_vena_right": 54, "small_bowel": 55,
                "duodenum": 56, "colon": 57, "rib_left_1": 58, "rib_left_2": 59,
                "rib_left_3": 60, "rib_left_4": 61, "rib_left_5": 62,
                "rib_left_6": 63, "rib_left_7": 64, "rib_left_8": 65,
                "rib_left_9": 66, "rib_left_10": 67, "rib_left_11": 68,
                "rib_left_12": 69, "rib_right_1": 70, "rib_right_2": 71,
                "rib_right_3": 72, "rib_right_4": 73, "rib_right_5": 74,
                "rib_right_6": 75, "rib_right_7": 76, "rib_right_8": 77,
                "rib_right_9": 78, "rib_right_10": 79, "rib_right_11": 80,
                "rib_right_12": 81, "humerus_left": 82, "humerus_right": 83,
                "scapula_left": 84, "scapula_right": 85, "clavicula_left": 86,
                "clavicula_right": 87, "femur_left": 88, "femur_right": 89,
                "hip_left": 90, "hip_right": 91, "sacrum": 92, "face": 93,
                "gluteus_maximus_left": 94, "gluteus_maximus_right": 95,
                "gluteus_medius_left": 96, "gluteus_medius_right": 97,
                "gluteus_minimus_left": 98, "gluteus_minimus_right": 99,
                "autochthon_left": 100, "autochthon_right": 101,
                "iliopsoas_left": 102, "iliopsoas_right": 103, "urinary_bladder": 104,
            },
            description="A pre-trained model for volumetric (2.5D) segmentation of the monai vista",
            config={},
            created_at=datetime.now(),
        ),
        model.Model(
            name="voxtell",
            type="segmentation",
            dimension=3,
            labels={"Seg": 1},
            description="Pretrained Voxtell Model for video segmentation",
            config={},
            created_at=datetime.now(),
        ),
    ]

    db.add_all(model_entries)
    db.commit()
    print(f"✅ {len(model_entries)} models seeded successfully!")
    db.close()
