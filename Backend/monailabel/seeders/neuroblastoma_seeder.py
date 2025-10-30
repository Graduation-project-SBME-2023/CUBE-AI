import random
from datetime import datetime, timedelta, date
from core.database import Base
from core.config import Settings
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import patient, model, study, volume, segmentation, radiomics
import os

database_url = os.getenv("DATABASE_URL")
sync_url = database_url.replace("+asyncpg", "") if database_url else None
sync_engine = create_engine(sync_url)

SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False)

def rand_date(days_ago=1000):
    return (datetime.now() - timedelta(days=random.randint(0, days_ago))).date()

def create_dummy_data():
    Base.metadata.create_all(bind=sync_engine)
    db = SessionLocal()

    # --- 1. Models ---
    model_entries = [
        model.Model(
            model_name="NeuroNet v1",
            description="U-Net model trained on pediatric neuroblastoma MRI scans",
            configuration={"input_size": [256, 256], "architecture": "U-Net"},
            created_at=datetime.now(),
        ),
        model.Model(
            model_name="RadiomicsExtractor",
            description="Feature extraction pipeline for neuroblastoma analysis",
            configuration={"library": "PyRadiomics", "features": ["GLCM", "GLRLM"]},
            created_at=datetime.now(),
        ),
    ]
    if db.query(model.Model).count() == 0:
        db.add_all(model_entries)
        db.commit()

# --- 2. Patients ---
    patients = [
        patient.Patient(
            name=f"Patient {i}",
            birth_date=datetime(2010 + i % 5, random.randint(1, 12), random.randint(1, 28)),
            gender=random.choice(["M", "F"]),
            created_at=rand_date(),
        )
        for i in range(1, 6)
    ]
    if db.query(patient.Patient).count() == 0:
        db.add_all(patients)
        db.commit()

# --- 3. Studies ---
    studies = []
    patients = db.query(patient.Patient)
    for p in patients:
        for s in range(2):  # 2 studies per patient
            studies.append(
                study.Study(
                    patient_id=p.patient_id,
                    description=f"MRI study {s + 1} for {p.name}",
                    modality="MR",
                    created_at=rand_date(),
                    study_uid=f"1.2.840.113619.{p.patient_id}{s}",
                )
            )

    if db.query(study.Study).count() == 0:
        db.add_all(studies)
        db.commit()

# --- 4. Volume Series ---
    series = []
    studies = db.query(study.Study)
    for s in studies:
        for m in ["T1", "T2", "T2-FLAIR"]:
            series.append(
                volume.Volume(
                    study_uid=s.study_uid,
                    series_description=m,
                    modality="MR",
                    num_slices=random.randint(100, 300),
                    created_at=rand_date(),
                    volume_series_instance_uid=f"{s.study_uid}.{random.randint(100,999)}",
                )
            )
    if db.query(volume.Volume).count() == 0:
        db.add_all(series)
        db.commit()

# --- 5. Segmentations ---
    segmentations = []
    series = db.query(volume.Volume)
    for s in series:
        seg = segmentation.Segmentation(
            label_series_instance_uid=f"{s.study_uid}.{random.randint(100,999)}",
            volume_series_instance_uid=s.volume_series_instance_uid,
            segmentation_type="Tumor Mask",
            method="Automatic",
            model_name="NeuroNet v1",
            created_at=rand_date(),
        )
        segmentations.append(seg)
    if db.query(segmentation.Segmentation).count() == 0:
        db.add(seg)
        db.commit()

# --- 6. Radiomics Features ---
    if db.query(radiomics.Radiomics).count() == 0:
        segmentations = db.query(segmentation.Segmentation)
        for seg in segmentations:
            features = {
                "mean_intensity": round(random.uniform(20, 150), 2),
                "entropy": round(random.uniform(0.5, 5.0), 3),
                "glcm_contrast": round(random.uniform(0, 50), 3),
            }
            rf = radiomics.Radiomics(
                label_series_instance_uid=seg.label_series_instance_uid,
                features=features,
                created_at=datetime.now(),
            )
            db.add(rf)
        db.commit()

    print("✅ Dummy Neuroblastoma data seeded successfully!")
    db.close()

def create_dummy_neuroblastoma_cases():
    Base.metadata.create_all(bind=sync_engine)
    db = SessionLocal()

    studies = []
    patients = db.query(patient.Patient)
    for p in patients:
        for s in range(2):  # 2 studies per patient
            studies.append(
                study.Study(
                    patient_id=p.patient_id,
                    description=f"CT study, {random.choice(['Venous', 'Arterial'])}, Suspicion of Neuroblastoma",
                    modality="CT",
                    created_at=rand_date(),
                    study_uid=f"1.2.840.113619.{p.patient_id}{s}{random.randint(1000, 1999)}",
                )
            )
    db.add_all(studies)
    db.commit()

    series = []
    studies = db.query(study.Study).filter(study.Study.modality == "CT").all()
    for s in studies:
        for m in ["Arterial", "Venous", "Contrast"]:
            series.append(
                volume.Volume(
                    study_uid=s.study_uid,
                    series_description=m,
                    modality="CT",
                    num_slices=random.randint(100, 300),
                    created_at=rand_date(),
                    volume_series_instance_uid=f"{s.study_uid}.{random.randint(100,999)}",
                )
            )
    db.add_all(series)
    db.commit()
