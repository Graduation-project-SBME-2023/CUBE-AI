"""
cleanup_empty_seg.py
====================
Finds every DICOM-SEG series in Orthanc, checks whether its pixel data is
all-zero (empty mask), and optionally deletes those series from PACS.

Usage
-----
    # Dry-run (default) — lists empty SEG series, deletes nothing:
    python cleanup_empty_seg.py

    # Actually delete empty SEG series:
    python cleanup_empty_seg.py --delete

    # Point at a different Orthanc instance:
    python cleanup_empty_seg.py --url http://localhost:8042 --user orthanc --password orthanc --delete

Dependencies: requests, pydicom, numpy  (all already in your requirements.txt)
"""

import argparse
import io
import sys
import logging

import numpy as np
import pydicom
import requests
from requests.auth import HTTPBasicAuth

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def get(session: requests.Session, base: str, path: str) -> requests.Response:
    r = session.get(f"{base}{path}")
    r.raise_for_status()
    return r


def delete(session: requests.Session, base: str, path: str) -> requests.Response:
    r = session.delete(f"{base}{path}")
    r.raise_for_status()
    return r


def is_empty_seg(session: requests.Session, base: str, instance_id: str) -> bool:
    """
    Download the DICOM file for *instance_id* and return True if every pixel
    in the PixelData is zero (i.e. the segmentation mask is blank).
    """
    raw = session.get(f"{base}/instances/{instance_id}/file")
    raw.raise_for_status()

    ds = pydicom.dcmread(io.BytesIO(raw.content), force=True)

    if not hasattr(ds, "PixelData"):
        # No pixel data at all — treat as empty
        return True

    pixel_array = ds.pixel_array  # shape: (frames, rows, cols) or (rows, cols)
    return bool(np.all(pixel_array == 0))


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Remove empty DICOM-SEG series from Orthanc.")
    parser.add_argument("--url",      default="http://localhost:8042", help="Orthanc base URL")
    parser.add_argument("--user",     default="orthanc",              help="Orthanc username")
    parser.add_argument("--password", default="orthanc",              help="Orthanc password")
    parser.add_argument("--delete",   action="store_true",            help="Actually delete (default: dry-run)")
    args = parser.parse_args()

    base = args.url.rstrip("/")
    session = requests.Session()
    session.auth = HTTPBasicAuth(args.user, args.password)

    mode = "DELETE MODE" if args.delete else "DRY-RUN MODE (pass --delete to remove)"
    log.info("=" * 60)
    log.info("Orthanc empty-SEG cleanup  |  %s", mode)
    log.info("Target: %s", base)
    log.info("=" * 60)

    # 1. Get all series IDs
    all_series_ids: list[str] = get(session, base, "/series").json()
    log.info("Total series in Orthanc: %d", len(all_series_ids))

    # 2. Filter to SEG modality only
    seg_series: list[dict] = []
    log.info("Scanning for SEG series...")
    for sid in all_series_ids:
        info = get(session, base, f"/series/{sid}").json()
        main_tags = info.get("MainDicomTags", {})
        modality = main_tags.get("Modality", "")
        if modality == "SEG":
            seg_series.append({
                "orthanc_id":   sid,
                "series_uid":   main_tags.get("SeriesInstanceUID", "unknown"),
                "description":  main_tags.get("SeriesDescription", ""),
                "instances":    info.get("Instances", []),
            })

    log.info("Found %d SEG series total.", len(seg_series))

    if not seg_series:
        log.info("Nothing to do.")
        sys.exit(0)

    # 3. Check each SEG series for empty pixel data
    empty_series: list[dict] = []
    for i, series in enumerate(seg_series, 1):
        sid   = series["orthanc_id"]
        desc  = series["description"]
        uid   = series["series_uid"]
        insts = series["instances"]

        log.info("[%d/%d] Checking series: %s | %s", i, len(seg_series), desc, uid)

        if not insts:
            log.warning("  → No instances found, treating as empty.")
            empty_series.append(series)
            continue

        # Check the first instance (all frames are in one file for SEG)
        try:
            empty = is_empty_seg(session, base, insts[0])
        except Exception as exc:
            log.error("  → Failed to read pixel data: %s — skipping.", exc)
            continue

        if empty:
            log.warning("  → EMPTY MASK detected.")
            empty_series.append(series)
        else:
            log.info("  → Has foreground — keeping.")

    # 4. Report
    log.info("=" * 60)
    log.info("Empty SEG series found: %d / %d", len(empty_series), len(seg_series))
    log.info("=" * 60)

    if not empty_series:
        log.info("No empty masks to remove.")
        sys.exit(0)

    for s in empty_series:
        log.info("  [EMPTY] %s | %s", s["description"], s["series_uid"])

    # 5. Delete (or skip in dry-run)
    if not args.delete:
        log.info("")
        log.info("DRY-RUN: No series were deleted.")
        log.info("Run again with --delete to remove the %d empty series above.", len(empty_series))
        sys.exit(0)

    deleted = 0
    failed  = 0
    for s in empty_series:
        sid  = s["orthanc_id"]
        desc = s["description"]
        try:
            delete(session, base, f"/series/{sid}")
            log.info("  DELETED: %s (%s)", desc, sid)
            deleted += 1
        except Exception as exc:
            log.error("  FAILED to delete %s: %s", sid, exc)
            failed += 1

    log.info("=" * 60)
    log.info("Done. Deleted: %d  |  Failed: %d", deleted, failed)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
