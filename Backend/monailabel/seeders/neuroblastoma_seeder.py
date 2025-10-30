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

    # --- 7. Workflows ---
    tasks = [
        models.ListWorkflow(task_name="Segmentation"),
        models.ListWorkflow(task_name="Radiomics Extraction"),
    ]
    db.add_all(tasks)
    db.commit()

    for s in series:
        wf = models.Workflow(
            case_id=f"CASE-{s.series_instance_uid[-3:]}",
            task_name=random.choice(["Segmentation", "Radiomics Extraction"]),
            series_instance_uid=s.series_instance_uid,
            instance_uid=f"WID-{random.randint(1000,9999)}",
            annotated=random.choice([True, False]),
            train=random.choice([True, False]),
            modality=s.modality,
        )
        db.add(wf)
    db.commit()

    # --- 8. Chat Sessions ---
    for study in studies:
        chat = models.ChatSession(
            study_instance_uid=study.study_instance_uid,
            title=f"Discussion of {study.description}",
            started_at=rand_date(),
            last_activity=datetime.now(),
            is_active=random.choice([True, False]),
            summary="Preliminary review of tumor segmentation quality.",
        )
        db.add(chat)
    db.commit()

    print("✅ Dummy Neuroblastoma data seeded successfully!")
    db.close()
