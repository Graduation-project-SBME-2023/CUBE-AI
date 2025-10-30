import random
from datetime import datetime, timedelta
from core.database import get_db, Base, engine
import models

def rand_date(days_ago=1000):
    return datetime.now() - timedelta(days=random.randint(0, days_ago))

def create_dummy_data():
    Base.metadata.create_all(bind=engine)
    db = get_db()

    # --- 1. Models ---
    if db.query(models.Model).count() == 0:
        model_entries = [
            models.Model(
                name="NeuroNet v1",
                description="U-Net model trained on pediatric neuroblastoma MRI scans",
                configuration={"input_size": [256, 256], "architecture": "U-Net"},
                created_at=datetime.now(),
            ),
            models.Model(
                name="RadiomicsExtractor",
                description="Feature extraction pipeline for neuroblastoma analysis",
                configuration={"library": "PyRadiomics", "features": ["GLCM", "GLRLM"]},
                created_at=datetime.now(),
            ),
        ]
        db.add_all(model_entries)
        db.commit()

    # --- 2. Patients ---
    if db.query(models.Patient).count() == 0:
        patients = [
            models.Patient(
                name=f"Patient {i}",
                birth_date=datetime(2010 + i % 5, random.randint(1, 12), random.randint(1, 28)),
                gender=random.choice(["M", "F"]),
                created_at=rand_date(),
            )
            for i in range(1, 6)
        ]
        db.add_all(patients)
        db.commit()

    # --- 3. Studies ---
    if db.query(models.Study).count() == 0:
        studies = []
        for patient in patients:
            for s in range(2):  # 2 studies per patient
                studies.append(
                    models.Study(
                        patient_id=patient.patient_id,
                        description=f"MRI study {s + 1} for {patient.name}",
                        modality="MR",
                        created_at=rand_date(),
                        study_instance_uid=f"1.2.840.113619.{patient.patient_id}{s}",
                    )
                )
        db.add_all(studies)
        db.commit()

    # --- 4. Volume Series ---
    if db.query(models.Volume).count() == 0:
        series = []
        for study in studies:
            for m in ["T1", "T2", "T2-FLAIR"]:
                series.append(
                    models.VolumeSeries(
                        study_instance_uid=study.study_instance_uid,
                        series_description=m,
                        modality="MR",
                        num_slices=random.randint(100, 300),
                        created_at=rand_date(),
                        series_instance_uid=f"{study.study_instance_uid}.{random.randint(100,999)}",
                    )
                )
        db.add_all(series)
        db.commit()

    # --- 5. Segmentations ---
    if db.query(model.Segmentation).count() == 0:
        segmentations = []
        for s in series:
            seg = models.Segmentation(
                series_instance_uid=s.series_instance_uid,
                segmentation_type="Tumor Mask",
                method="Automatic",
                model=random.choice(["NeuroNet v1", "Manual"]),
                created_at=rand_date(),
            )
            segmentations.append(seg)
        db.add(seg)
        db.commit()

        # --- 6. Radiomics Features ---
        if db.query(model.Radiomics).count() == 0:
            for seg in segmentations:
                features = {
                    "mean_intensity": round(random.uniform(20, 150), 2),
                    "entropy": round(random.uniform(0.5, 5.0), 3),
                    "glcm_contrast": round(random.uniform(0, 50), 3),
                }
                rf = models.RadiomicsFeature(
                    segmentation_id=seg.segmentation_id,
                    features=features,
                    created_at=datetime.now(),
                )
                db.add(rf)
            db.commit()

    print("✅ Dummy Neuroblastoma data seeded successfully!")
    db.close()
