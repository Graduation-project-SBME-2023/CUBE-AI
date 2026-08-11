"""Safely repair contradictory DICOM-SEG SegmentLabel metadata in Orthanc.

The tool is dry-run by default. With ``--apply`` it backs up the original
DICOM instance, replaces only SegmentSequence[0].SegmentLabel, uploads the
corrected instance with the same DICOM UIDs, updates the application database,
and validates both stores. A failed replacement automatically restores the
original DICOM and database label.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import pydicom


DESCRIPTION_PATTERN = re.compile(
    r"^\s*\d{2}/\d{2}/\d{4}\s+-\s+.+?\s+-\s+(.+?)\s*$",
    re.IGNORECASE,
)


def normalize_label(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").split()).casefold()


def parse_repairs(values: list[str]) -> dict[str, str]:
    repairs: dict[str, str] = {}
    for value in values:
        uid, separator, label = value.partition("=")
        uid = uid.strip()
        label = " ".join(label.split())
        if not separator or not uid or not label:
            raise ValueError(f"Invalid --repair value {value!r}; expected UID=label")
        repairs[uid] = label
    return repairs


def dicom_segment_label(dataset: pydicom.dataset.Dataset) -> str:
    sequence = getattr(dataset, "SegmentSequence", None)
    if not sequence or len(sequence) != 1:
        raise ValueError("Expected exactly one DICOM SegmentSequence item")
    return str(sequence[0].SegmentLabel).strip()


def description_target(dataset: pydicom.dataset.Dataset) -> str:
    description = str(getattr(dataset, "SeriesDescription", "")).strip()
    match = DESCRIPTION_PATTERN.match(description)
    if not match:
        raise ValueError(f"SeriesDescription has no explicit target: {description!r}")
    return match.group(1).strip()


def encode_dataset(dataset: pydicom.dataset.Dataset) -> bytes:
    output = io.BytesIO()
    dataset.save_as(output, enforce_file_format=True)
    return output.getvalue()


class RepairSession:
    def __init__(self, backup_dir: Path):
        orthanc_url = os.environ.get("ORTHANC_URL", "http://localhost:8043").rstrip("/")
        app_url = os.environ.get(
            "APP_API_URL",
            os.environ.get("CONTROL_API_URL", "http://localhost:8001"),
        ).rstrip("/")
        auth = (
            os.environ.get("ORTHANC_USER", "orthanc"),
            os.environ.get("ORTHANC_PASSWORD", "orthanc"),
        )
        self.orthanc = httpx.Client(base_url=orthanc_url, auth=auth, timeout=60.0)
        self.app = httpx.Client(base_url=app_url, timeout=30.0)
        self.backup_dir = backup_dir
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def close(self) -> None:
        self.orthanc.close()
        self.app.close()

    def locate(self, series_uid: str) -> tuple[str, str]:
        response = self.orthanc.post("/tools/lookup", content=series_uid)
        response.raise_for_status()
        matches = [item for item in response.json() if item.get("Type") == "Series"]
        if len(matches) != 1:
            raise ValueError(f"Expected one Orthanc series for {series_uid}, got {matches}")
        series_id = str(matches[0]["ID"])
        response = self.orthanc.get(f"/series/{series_id}")
        response.raise_for_status()
        series = response.json()
        instances = series.get("Instances") or []
        if len(instances) != 1:
            raise ValueError(f"Expected one SEG instance for {series_uid}, got {len(instances)}")
        if str(series.get("MainDicomTags", {}).get("Modality")) != "SEG":
            raise ValueError(f"Series {series_uid} is not a DICOM SEG")
        return series_id, str(instances[0])

    def download(self, instance_id: str) -> bytes:
        response = self.orthanc.get(f"/instances/{instance_id}/file")
        response.raise_for_status()
        return response.content

    def upload(self, payload: bytes) -> str:
        response = self.orthanc.post(
            "/instances",
            content=payload,
            headers={"Content-Type": "application/dicom"},
        )
        response.raise_for_status()
        result = response.json()
        return str(result.get("ID") or result.get("ParentSeries") or "")

    def delete_instance(self, instance_id: str) -> None:
        response = self.orthanc.delete(f"/instances/{instance_id}")
        response.raise_for_status()

    def update_database_label(self, series_uid: str, label: str) -> None:
        self.wait_for_database_record(series_uid)
        response = self.app.put(
            f"/segmentations/{quote(series_uid, safe='')}",
            json={"target_label": label},
        )
        response.raise_for_status()
        stored = response.json()
        if normalize_label(stored.get("target_label")) != normalize_label(label):
            raise ValueError(f"Database did not persist target label {label!r}")

    def wait_for_database_record(
        self, series_uid: str, timeout_seconds: float = 150.0
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = self.app.get(f"/segmentations/{quote(series_uid, safe='')}")
            if response.status_code == 200:
                return response.json()
            if response.status_code != 404:
                response.raise_for_status()
            time.sleep(0.5)
        raise TimeoutError(
            f"Segmentation {series_uid} did not reappear in the application database"
        )

    def validate(self, series_uid: str, expected_label: str) -> dict[str, str]:
        series_id, instance_id = self.locate(series_uid)
        dataset = pydicom.dcmread(io.BytesIO(self.download(instance_id)))
        actual = dicom_segment_label(dataset)
        response = self.app.get(f"/segmentations/{quote(series_uid, safe='')}")
        response.raise_for_status()
        database_label = str(response.json().get("target_label") or "")
        if normalize_label(actual) != normalize_label(expected_label):
            raise ValueError(f"PACS label is {actual!r}, expected {expected_label!r}")
        if normalize_label(database_label) != normalize_label(expected_label):
            raise ValueError(
                f"Database label is {database_label!r}, expected {expected_label!r}"
            )
        return {
            "series_uid": series_uid,
            "orthanc_series_id": series_id,
            "instance_id": instance_id,
            "segment_label": actual,
            "database_label": database_label,
        }

    def repair(self, series_uid: str, expected_label: str, apply: bool) -> dict[str, Any]:
        series_id, instance_id = self.locate(series_uid)
        original = self.download(instance_id)
        dataset = pydicom.dcmread(io.BytesIO(original))
        if str(dataset.SeriesInstanceUID) != series_uid:
            raise ValueError("Downloaded DICOM SeriesInstanceUID does not match requested UID")

        current_label = dicom_segment_label(dataset)
        described_target = description_target(dataset)
        if normalize_label(described_target) != normalize_label(expected_label):
            raise ValueError(
                f"Requested label {expected_label!r} contradicts description target "
                f"{described_target!r}"
            )

        result: dict[str, Any] = {
            "series_uid": series_uid,
            "orthanc_series_id": series_id,
            "instance_id": instance_id,
            "series_description": str(dataset.SeriesDescription),
            "old_segment_label": current_label,
            "new_segment_label": expected_label,
            "status": "already_correct" if normalize_label(current_label) == normalize_label(expected_label) else "dry_run",
        }
        if result["status"] == "already_correct" or not apply:
            return result

        original_path = self.backup_dir / f"{series_uid}.original.dcm"
        repaired_path = self.backup_dir / f"{series_uid}.repaired.dcm"
        original_path.write_bytes(original)
        dataset.SegmentSequence[0].SegmentLabel = expected_label
        repaired = encode_dataset(dataset)
        repaired_path.write_bytes(repaired)
        result["backup"] = str(original_path)
        result["repaired_copy"] = str(repaired_path)

        deleted_instance = False
        replacement_instance_id: str | None = None
        try:
            self.delete_instance(instance_id)
            deleted_instance = True
            replacement_instance_id = self.upload(repaired)
            self.update_database_label(series_uid, expected_label)
            result.update(self.validate(series_uid, expected_label))
            result["status"] = "repaired"
            return result
        except Exception:
            if replacement_instance_id:
                try:
                    _, current_instance_id = self.locate(series_uid)
                    self.delete_instance(current_instance_id)
                except Exception:
                    pass
            if deleted_instance:
                self.upload(original)
                self.wait_for_database_record(series_uid)
            self.update_database_label(series_uid, current_label)
            raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repair", action="append", required=True, metavar="UID=LABEL")
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    repairs = parse_repairs(args.repair)
    session = RepairSession(args.backup_dir)
    results: list[dict[str, Any]] = []
    try:
        for series_uid, expected_label in repairs.items():
            results.append(session.repair(series_uid, expected_label, args.apply))
    finally:
        session.close()

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "apply": args.apply,
        "results": results,
    }
    manifest_path = args.backup_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
